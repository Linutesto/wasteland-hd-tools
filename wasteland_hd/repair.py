"""Independent repair pass over completed production jobs; writes only --out."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import sys
import tempfile
import zipfile

from PIL import Image

from . import invention, qa, sheets
from .floor_fidelity import source_floor_2x
from .gamefiles import Game
from .production import (atomic_copy, atomic_json, controller_lock, file_digest,
                         integrity, read_json, SETTINGS)
from .upscale import Comfy, render_matches, write_render_receipt, LIGHTNING, PROMPT, ANIMATION_PROMPT

DENOISE_LADDER = (0.55, 0.4)
REPAIR_SEEDS = (1009, 1013)


class RepairModel(Comfy):
    def __init__(self, *, denoise, seed, animated=False, url='http://127.0.0.1:8188'):
        super().__init__(url=url, steps=SETTINGS['steps'], cfg=SETTINGS['cfg'], seed=seed,
                         lora=LIGHTNING[8], prompt=ANIMATION_PROMPT if animated else PROMPT)
        self.denoise = denoise

    def graph(self, image_name, width, height):
        graph = super().graph(image_name, width, height)
        graph['ks']['inputs']['denoise'] = self.denoise
        return graph


def edge_lock(hd, source, width=12):
    """Copy the source boundary exactly, feathering inward through width px."""
    import numpy as np
    from scipy.ndimage import distance_transform_edt
    a = np.asarray(hd.convert('RGBA'), np.float32)
    b = np.asarray(source.convert('RGBA'), np.float32)
    mask = b[..., 3] > 0
    if a.shape != b.shape or not np.array_equal(a[..., 3], b[..., 3]):
        raise ValueError('edge lock needs same size and alpha')
    distance = distance_transform_edt(np.pad(mask, 1))[1:-1, 1:-1]-1
    y,x = np.indices(mask.shape)
    distance = np.minimum.reduce((distance, x, y, mask.shape[1]-1-x, mask.shape[0]-1-y))
    weight = np.clip((distance-2)/max(width-2, 1), 0, 1)
    weight = weight*weight*(3-2*weight)
    rgb = np.clip(np.rint(b[..., :3]*(1-weight[..., None])+a[..., :3]*weight[..., None]),0,255).astype('uint8')
    return Image.fromarray(np.dstack((rgb,b[..., 3].astype('uint8'))))


class Controller:
    def __init__(self, root, out, *, audit_only=False, comfy='http://127.0.0.1:8188',
                 ready_timeout=600, command_retries=2):
        self.root = Path(root).resolve()
        self.out = Path(out).resolve()
        if self.root == self.out or self.root in self.out.parents or self.out in self.root.parents:
            raise ValueError('production and repair roots must be disjoint')
        plan = read_json(self.root/'plan.json')
        if not plan:
            raise ValueError('production plan missing')
        self.game = Game(plan['game'])
        self.audit_only = audit_only
        self.args = argparse.Namespace(comfy=comfy, ready_timeout=ready_timeout,
                                       command_retries=command_retries)
        self.plan = {'version': 1, 'production_root': str(self.root),
                     'production_plan_sha256': file_digest(self.root/'plan.json'),
                     'denoise': list(DENOISE_LADDER), 'seeds': list(REPAIR_SEEDS)}
        self.originals = {}
        self.hd_index = {}
        self.seams = {}

    def jobs(self):
        for work in sorted((self.root/'jobs').iterdir()):
            if not work.is_dir():
                continue
            state = read_json(work/'state.json')
            inventory = read_json(work/'inventory.json')
            coverage = read_json(work/'coverage.json')
            if state and inventory and coverage and state.get('status') == 'needs_review':
                yield work, state, inventory

    def original(self, rel):
        if rel not in self.originals:
            self.originals[rel] = qa.original_frame(self.game, {}, rel)
        return self.originals[rel]

    def _hd(self, rel, overrides=None):
        if overrides and rel in overrides:
            return overrides[rel]
        return self.hd_index.get(rel)

    def seam_index(self):
        pairs = invention.cached_adjacency(self.game, self.out, write=not self.audit_only)
        by_rel = defaultdict(list)
        for a,b,side in pairs:
            if a != b:
                by_rel[a].append((a,b,side))
                by_rel[b].append((a,b,side))
        self.seams = by_rel

    def seam_for(self, rel, hd, overrides=None):
        if not rel.startswith('art/tiles/'):
            return 0.0
        worst = 0.0
        for a,b,side in self.seams.get(rel, []):
            other = b if a == rel else a
            other_path = self._hd(other, overrides)
            if other_path is None:
                continue
            with Image.open(other_path) as other_hd:
                try:
                    delta = invention.seam_delta(hd if a == rel else other_hd,
                                                  other_hd if a == rel else hd,
                                                  self.original(a), self.original(b), side)
                    worst = max(worst, delta)
                except (ValueError, OSError):
                    continue
        return worst

    def gate(self, work, group, source, *, overrides=None):
        """Whole direction: integrity, existing QA, then invention/seam gate."""
        result = {'complete': False, 'frames': {}, 'issues': {}}
        for expected in group['frames']:
            rel = expected['rel']
            error = integrity(source/rel, expected)
            if error:
                result['issues'][rel] = error
        if result['issues']:
            return result
        for expected in group['frames']:
            rel = expected['rel']
            with Image.open(source/rel) as image:
                original = self.original(rel)
                bad = qa.badness(image, original, rel, qa.COLOUR_LIMIT)
                if bad > 1:
                    result['issues'][rel] = 'QA colour/structure rejected'
                score = invention.score(image, original,
                                         seam_delta=self.seam_for(rel, image, overrides),
                                         floor=rel.startswith('art/tiles/'))
                score['qa_badness'] = round(float(bad), 5)
                result['frames'][rel] = score
                if score['verdict'] in ('invented_texture','unmatched_floor'):
                    result['issues'][rel] = score['verdict']
        result['complete'] = not result['issues']
        return result

    def wait_ready(self):
        # Same readiness check used by the production controller, without
        # constructing it or touching its root.
        from .production import Controller as ProductionController
        return ProductionController.wait_ready(self)

    def render(self, job, key, group, step, denoise):
        seed = REPAIR_SEEDS[step]
        attempt = self.out/'jobs'/job/'attempts'/key/f'denoise-{step}'
        model = RepairModel(denoise=denoise, seed=seed, animated=group['animated'], url=self.args.comfy)
        # Sheet renderer needs indexed pixels from the original FRM.
        from .gamefiles import read_frm
        art = group['art']
        frm = read_frm(self.game.read(art))
        d = 0 if art.lower().endswith(tuple(f'.fr{i}' for i in range(6))) else group['direction']
        frames = []
        for expected in group['frames']:
            frame_no = int(Path(expected['rel']).stem.split('_')[1])
            frames.append((art, group['direction'], frame_no, frm.directions[d][frame_no]))
        plans = sheets.pack(frames, sheet=256, by_animation=group['animated'])
        candidate = attempt/'candidate'
        for index, items in enumerate(plans):
            native = attempt/f'sheet-{index}.png'
            native.parent.mkdir(parents=True, exist_ok=True)
            if not native.exists():
                sheets.render_sheet(items, self.game.palette(), str(native), sheet=256)
            rendered = attempt/f'render-{index}.png'
            valid = rendered.exists() and render_matches(str(rendered), str(native), 4, model)
            checkpoint = attempt/f'render-{index}.json'
            budget = read_json(checkpoint, {'calls': 0})
            while not valid and budget['calls'] < self.args.command_retries:
                self.wait_ready()
                budget['calls'] += 1
                budget['status'] = 'running'
                atomic_json(checkpoint, budget)
                try:
                    model.upscale(str(native), str(rendered), scale=4)
                    valid = render_matches(str(rendered), str(native), 4, model)
                    if not valid:
                        raise ValueError('render graph differs from repair settings')
                    write_render_receipt(str(rendered), str(native), model)
                    budget['status'] = 'ok'
                    budget['sha256'] = file_digest(rendered)
                    atomic_json(checkpoint, budget)
                except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
                    budget['status'] = 'failed'
                    budget['error'] = str(exc)
                    atomic_json(checkpoint, budget)
            if not valid:
                raise RuntimeError(f'repair render budget exhausted: {checkpoint}')
            sheets.cut_sheet(str(native), str(rendered), str(candidate))
        return candidate/'hd', {'kind':'reduced_denoise', 'denoise':denoise, 'seed':seed,
                           'settings': {**SETTINGS, 'denoise':denoise}, 'graph_verified':True}

    def render_batch(self, job, groups, pending, step, denoise):
        """Render a fixed snapshot of a job's pending directions on shared sheets.

        The snapshot is durable: a restart can omit already accepted directions
        without changing any sheet's contents or index.
        """
        attempt = self.out/'jobs'/job/'attempts'/'batch'/f'denoise-{step}'
        marker = attempt/'sheets.json'
        saved = read_json(marker)
        if saved is None:
            keys = list(pending)
            attempt.mkdir(parents=True, exist_ok=True)
            atomic_json(marker, {'keys': keys})
        else:
            keys = saved['keys']
            if not set(pending).issubset(keys):
                raise ValueError(f'repair batch changed after checkpoint: {marker}')
        from .gamefiles import read_frm
        frames = {False: [], True: []}
        decoded = {}
        for key in keys:
            group = groups[key]
            art = group['art']
            if art not in decoded:
                decoded[art] = read_frm(self.game.read(art))
            frm = decoded[art]
            direction = (0 if art.lower().endswith(tuple(f'.fr{i}' for i in range(6)))
                         else group['direction'])
            for expected in group['frames']:
                frame_no = int(Path(expected['rel']).stem.split('_')[1])
                frames[group['animated']].append((art, group['direction'], frame_no,
                                                  frm.directions[direction][frame_no]))
        candidate = attempt/'candidate'
        failed_rels = set()
        for animated in (False, True):
            plans = sheets.pack(frames[animated], sheet=256, by_animation=animated)
            model = RepairModel(denoise=denoise, seed=REPAIR_SEEDS[step], animated=animated,
                                url=self.args.comfy)
            for index, items in enumerate(plans):
                name = f"{'anim' if animated else 'static'}-{index:05d}"
                native = attempt/f'{name}.png'
                if not (native.is_file() and Path(str(native)+'.json').is_file()
                        and Path(str(native)+'.mask.png').is_file()):
                    sheets.render_sheet(items, self.game.palette(), str(native), sheet=256)
                rendered = attempt/f'{name}-render.png'
                checkpoint = attempt/f'{name}-render.json'
                budget = read_json(checkpoint, {'calls': 0})
                valid = rendered.is_file() and render_matches(str(rendered), str(native), 4, model)
                while not valid and budget['calls'] < self.args.command_retries:
                    self.wait_ready()
                    budget['calls'] += 1
                    budget['status'] = 'running'
                    atomic_json(checkpoint, budget)
                    try:
                        model.upscale(str(native), str(rendered), scale=4)
                        valid = render_matches(str(rendered), str(native), 4, model)
                        if not valid:
                            raise ValueError('render graph differs from repair settings')
                        write_render_receipt(str(rendered), str(native), model)
                        budget['status'] = 'ok'
                        budget['sha256'] = file_digest(rendered)
                        atomic_json(checkpoint, budget)
                    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
                        budget['status'] = 'failed'
                        budget['error'] = str(exc)
                        atomic_json(checkpoint, budget)
                        # The download may have finished before a later error.
                        # A verified PNG is a completed render even if the call raised.
                        valid = rendered.is_file() and render_matches(str(rendered), str(native), 4, model)
                        if valid:
                            write_render_receipt(str(rendered), str(native), model)
                            budget['status'] = 'ok'
                            budget['sha256'] = file_digest(rendered)
                            atomic_json(checkpoint, budget)
                if valid:
                    sheets.cut_sheet(str(native), str(rendered), str(candidate))
                else:
                    failed_rels.update(sheets.hd_frame_path('', p).removeprefix('hd/')
                                       for p, _ in items)
        provenance = {'kind':'reduced_denoise', 'denoise':denoise,
                      'seed':REPAIR_SEEDS[step],
                      'settings': {**SETTINGS, 'denoise':denoise}, 'graph_verified':True}
        result = {}
        for key in pending:
            group = groups[key]
            if any(f['rel'] in failed_rels or not (candidate/'hd'/f['rel']).is_file()
                   for f in group['frames']):
                result[key] = RuntimeError(f'repair sheet render budget exhausted: {attempt}')
            else:
                result[key] = (candidate/'hd', provenance)
        return result

    def candidate_from_production(self, work, group):
        if all((work/'hd'/f['rel']).is_file() for f in group['frames']):
            return work/'hd'
        for attempt in sorted(work.glob('attempt-*/hd'), reverse=True):
            if all((attempt/f['rel']).is_file() for f in group['frames']):
                return attempt
        return None

    def _save_images(self, job, group, images, name):
        dest = self.out/'jobs'/job/name
        for expected, image in zip(group['frames'], images):
            target = dest/expected['rel']
            target.parent.mkdir(parents=True, exist_ok=True)
            image.save(target)
        return dest

    def precheck(self, work, key, group, accepted, initial):
        """Return an already completed direction without invoking ComfyUI."""
        job = work.name
        if accepted and initial and initial['complete']:
            return {'source':'production', 'gate':initial, 'fallback':None, 'accepted':True}
        if self.audit_only:
            return {'source':'production' if accepted else 'rejected_candidate',
                    'gate':initial, 'fallback':None, 'accepted':bool(initial and initial['complete'])}
        saved = read_json(self.out/'jobs'/job/'state.json', {'groups': {}})['groups'].get(key)
        if saved:
            if saved.get('source') == 'production' and initial and initial['complete']:
                return saved
            if saved.get('accepted') and all(
                (self.out/'jobs'/job/'hd'/f['rel']).is_file() and
                saved.get('hashes', {}).get(f['rel']) == file_digest(self.out/'jobs'/job/'hd'/f['rel'])
                for f in group['frames']):
                for f in group['frames']:
                    self.hd_index[f['rel']] = self.out/'jobs'/job/'hd'/f['rel']
                return saved
        return None

    def process_group(self, work, key, group, accepted, prepared=None, initial=None):
        job = work.name
        original_source = self.candidate_from_production(work, group)
        if initial is None and original_source:
            initial = self.gate(work, group, original_source)
        done = self.precheck(work, key, group, accepted, initial)
        if done is not None:
            return done
        state_path = self.out/'jobs'/job/'state.json'
        state = read_json(state_path, {'groups': {}})
        last = initial
        source = None
        method = None
        candidates = []
        for step, denoise in enumerate(DENOISE_LADDER):
            try:
                if prepared is None:
                    candidate, provenance = self.render(job, key, group, step, denoise)
                    measured = self.gate(work, group, candidate)
                else:
                    outcome = prepared[step]
                    if isinstance(outcome, RuntimeError):
                        raise outcome
                    candidate, provenance, measured = outcome
                candidates.append(candidate)
                last = measured
                if measured['complete']:
                    source, method = candidate, provenance
                    break
            except RuntimeError as exc:
                last = {'complete': False, 'issues': {'render': str(exc)}, 'frames': {}}
        if source is None and group['art'].startswith('art/tiles/'):
            # Border correction is meaningful only for a geometrically valid
            # unmatched floor candidate. It cannot repair invented interiors.
            for candidate in [original_source] + candidates:
                if candidate is None or not all((candidate/f['rel']).is_file() for f in group['frames']):
                    continue
                measured = self.gate(work, group, candidate)
                if any(v['verdict'] != 'unmatched_floor' for v in measured['frames'].values()):
                    continue
                images = []
                for f in group['frames']:
                    with Image.open(candidate/f['rel']) as hd:
                        images.append(edge_lock(hd, source_floor_2x(self.original(f['rel']))))
                fixed = self._save_images(job, group, images, 'edge-lock')
                measured = self.gate(work, group, fixed)
                last = measured
                if measured['complete']:
                    source, method = fixed, {'kind':'edge_lock','source':str(candidate)}
                    break
        if source is None and group['art'].startswith('art/tiles/'):
            images = [source_floor_2x(self.original(f['rel'])) for f in group['frames']]
            fallback = self._save_images(job, group, images, 'source-floor')
            last = self.gate(work, group, fallback)
            if last['complete']:
                source, method = fallback, {'kind':'source_floor_2x'}
        accepted = source is not None and last['complete']
        if accepted:
            for f in group['frames']:
                atomic_copy(source/f['rel'], self.out/'jobs'/job/'hd'/f['rel'])
        if accepted:
            for f in group['frames']:
                self.hd_index[f['rel']] = self.out/'jobs'/job/'hd'/f['rel']
        result = {'source':str(source) if source else None, 'gate':last,
                  'hashes': {f['rel']:file_digest(self.out/'jobs'/job/'hd'/f['rel']) for f in group['frames']} if accepted else {},
                  'fallback':method['kind'] if method and method['kind'] in ('edge_lock','source_floor_2x') else None,
                  'provenance':method, 'accepted':accepted}
        state['groups'][key] = result
        atomic_json(state_path, state)
        return result

    def package(self, work, inventory, results):
        staging = self.out/'staging'
        staging.mkdir(parents=True, exist_ok=True)
        archive = staging/(work.name+'.zip')
        fd, temp = tempfile.mkstemp(prefix='.pack-', suffix='.zip', dir=staging)
        os.close(fd)
        try:
            replacements = {f['rel']: self.out/'jobs'/work.name/'hd'/f['rel']
                            for key,g in inventory['groups'].items() for f in g['frames']
                            if results[key]['accepted'] and results[key]['source'] != 'production'}
            allowed = {f['rel'] for key,g in inventory['groups'].items() for f in g['frames']
                       if results[key]['accepted']}
            with zipfile.ZipFile(temp,'w',compression=zipfile.ZIP_DEFLATED) as dest:
                source_zip = self.root/'staging'/(work.name+'.zip')
                if source_zip.exists():
                    with zipfile.ZipFile(source_zip) as src:
                        for name in src.namelist():
                            if not name.startswith('hd/'):
                                continue
                            rel = name[3:]
                            if rel in allowed and rel not in replacements:
                                dest.writestr(name, src.read(name))
                for rel,path in sorted(replacements.items()):
                    dest.write(path,'hd/'+rel)
            os.replace(temp,archive)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        receipt = {'sha256':file_digest(archive),'bytes':archive.stat().st_size,
                   'visual_review':'pending','production_root':str(self.root),
                   'accepted_directions':sum(v['accepted'] for v in results.values())}
        atomic_json(archive.with_suffix('.json'),receipt)
        return receipt

    def run(self):
        jobs = list(self.jobs())
        for work,state,manifest in jobs:
            for key,group in manifest['groups'].items():
                if state['groups'].get(key,{}).get('complete'):
                    for f in group['frames']:
                        path = work/'hd'/f['rel']
                        if path.is_file():
                            self.hd_index[f['rel']] = path
        # Audit produces one report JSON only; live repair caches map pairs.
        if self.audit_only:
            self.seam_index()
            self.out.mkdir(parents=True,exist_ok=True)
            return self._run_jobs(jobs)
        with controller_lock(self.out):
            previous = read_json(self.out/'plan.json')
            if previous is not None and previous != self.plan:
                raise ValueError('repair plan changed; use a new --out')
            self.seam_index()
            atomic_json(self.out/'plan.json', self.plan)
            return self._run_jobs(jobs)

    def _batch_jobs(self, jobs):
        """Run each ladder round in job order, then resolve remaining fallbacks."""
        resolved, initials, outcomes = {}, {}, defaultdict(list)
        for work, state, manifest in jobs:
            for key, group in manifest['groups'].items():
                accepted = bool(state['groups'].get(key, {}).get('complete'))
                original = self.candidate_from_production(work, group)
                initial = self.gate(work, group, original) if original else None
                initials[(work.name, key)] = initial
                done = self.precheck(work, key, group, accepted, initial)
                if done is not None:
                    resolved[(work.name, key)] = done
        for step, denoise in enumerate(DENOISE_LADDER):
            for work, state, manifest in jobs:
                pending = [key for key in manifest['groups']
                           if (work.name, key) not in resolved]
                if not pending:
                    continue
                rendered = self.render_batch(work.name, manifest['groups'], pending, step, denoise)
                for key in pending:
                    group = manifest['groups'][key]
                    item = rendered[key]
                    if isinstance(item, RuntimeError):
                        outcome = item
                    else:
                        candidate, provenance = item
                        outcome = (candidate, provenance, self.gate(work, group, candidate))
                    outcomes[(work.name, key)].append(outcome)
                    if not isinstance(outcome, RuntimeError) and outcome[2]['complete']:
                        resolved[(work.name, key)] = self.process_group(
                            work, key, group, bool(state['groups'].get(key, {}).get('complete')),
                            prepared=outcomes[(work.name, key)], initial=initials[(work.name, key)])
        for work, state, manifest in jobs:
            for key, group in manifest['groups'].items():
                if (work.name, key) not in resolved:
                    resolved[(work.name, key)] = self.process_group(
                        work, key, group, bool(state['groups'].get(key, {}).get('complete')),
                        prepared=outcomes[(work.name, key)], initial=initials[(work.name, key)])
        return resolved

    def _run_jobs(self,jobs):
        report = {'jobs': {}, 'summary': {}, 'audit_only': self.audit_only,
                  'production_root': str(self.root), 'verdict_examples': {}}
        examples = defaultdict(list)
        total = Counter()
        batched = {} if self.audit_only else self._batch_jobs(jobs)
        for work,state,manifest in jobs:
            results = {}
            counts = Counter()
            for key,group in manifest['groups'].items():
                accepted = bool(state['groups'].get(key,{}).get('complete'))
                result = (self.process_group(work,key,group,accepted) if self.audit_only
                          else batched[(work.name, key)])
                results[key] = result
                gate = result['gate']
                for rel,score in (gate or {}).get('frames',{}).items():
                    verdict = score['verdict']
                    counts[verdict] += 1
                    if len(examples[verdict]) < 8:
                        examples[verdict].append({'job':work.name,'rel':rel})
                if gate is None or not gate.get('frames'):
                    counts['unscored_rejected'] += len(group['frames'])
            total.update(counts)
            row = {'verdict_counts':dict(counts),'directions':len(results),
                   'accepted':sum(bool(v['accepted']) for v in results.values()),
                   'groups':results}
            if not self.audit_only:
                row['receipt'] = self.package(work,manifest,results)
                coverage = {'groups':{key:{'accepted':v['accepted'], 'fallback':v['fallback'],
                                          'verdicts':{rel:s['verdict'] for rel,s in (v['gate'] or {}).get('frames',{}).items()},
                                          'minor_invention':any(s['verdict']=='minor_invention' for s in (v['gate'] or {}).get('frames',{}).values())}
                                     for key,v in results.items()},'verdict_counts':dict(counts)}
                atomic_json(self.out/'jobs'/work.name/'coverage.json',coverage)
            report['jobs'][work.name] = row
            atomic_json(self.out/'report.json',report)
        report['summary'] = dict(total)
        report['verdict_examples'] = dict(examples)
        atomic_json(self.out/'report.json',report)
        return report


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',required=True)
    p.add_argument('--out',required=True)
    p.add_argument('--audit-only',action='store_true')
    p.add_argument('--comfy',default='http://127.0.0.1:8188')
    p.add_argument('--ready-timeout',type=float,default=600)
    p.add_argument('--command-retries',type=int,default=2)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command_retries < 1 or args.ready_timeout < 0:
        parser().error('invalid retry budget or timeout')
    try:
        report = Controller(args.root,args.out,audit_only=args.audit_only,comfy=args.comfy,
                            ready_timeout=args.ready_timeout,command_retries=args.command_retries).run()
        print(json.dumps({'jobs':{k:v['verdict_counts'] for k,v in report['jobs'].items()},
                          'summary':report['summary']},indent=2))
        return 0
    except (OSError,ValueError,RuntimeError,TimeoutError) as exc:
        print(f'repair: {exc}',file=sys.stderr)
        return 75


if __name__ == '__main__':
    sys.exit(main())
