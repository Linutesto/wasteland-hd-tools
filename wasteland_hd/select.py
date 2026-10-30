"""CPU-only, gate-first selection of the best HD version of each direction."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import urllib.request
import zipfile

import numpy as np
from PIL import Image, ImageDraw

from . import invention, qa
from .gamefiles import Game
from .production import integrity

BAD = {'invented_texture', 'unmatched_floor', 'unmatched_wall'}
# The two names fallback() gives original art; a pack source in here is not HD.
FALLBACK = frozenset({'original_game_art', 'source_floor_2x'})
CRITTER_ART = 'art/critters/'
# A classic ZIP addresses 65535 entries; the Android updater refuses ZIP64.
MAX_ZIP_ENTRIES = 0xFFFF
MODEL = 'nvidia/nemotron-3.5-super-vl-120b-a12b'
FALLBACK_MODEL = 'meta/llama-3.2-90b-vision-instruct'


def stage_root(attempt):
    """The post-process (temporal stage) tree an attempt finished in, or None.

    Production copies a critter direction into ``hd`` from the tree the post-process
    checkpoint names; the raw ``attempt/hd`` is the pre-temporal render.  Critter
    candidates are gate inputs only from the stage output, so an attempt without a
    usable checkpoint is not a root at all.
    """
    record = attempt/'post.json'
    if not record.is_file():
        return None
    try:
        target = Path(json.loads(record.read_text()).get('hd') or '')
    except (OSError, ValueError, TypeError):
        return None
    return target if target.is_dir() and target.is_relative_to(attempt) else None


def flicker_verdict(state, key):
    """Flicker-gate verdict of one direction recorded in production ``state.json``.

    ``accept()`` stores it on the group (``flicker``); a finished job also carries the
    temporal stage's per-direction table under ``temporal.directions``.  None means
    no verdict was recorded for the direction (the temporal stage never judged it).
    """
    group = (state.get('groups') or {}).get(key) or {}
    verdict = group.get('flicker')
    if not isinstance(verdict, dict) or 'pass' not in verdict:
        verdict = ((state.get('temporal') or {}).get('directions') or {}).get(key)
    return verdict if isinstance(verdict, dict) and 'pass' in verdict else None


def production_refuser(verdict):
    """Gate input: a failed flicker verdict refuses every production root of the direction.

    The verdict describes production's staged frames, so repair candidates (re-renders
    gated by integrity/QA/invention) stay eligible; when they pass they win, and only
    when nothing passes does the direction fall back to original art.
    """
    if not verdict or verdict.get('pass'):
        return None
    return lambda name, root: 'flicker_gate' if name.startswith('production') else None


def source_roots(prod, repair, job, critter=False):
    p, r = prod/'jobs'/job, repair/'jobs'/job
    roots = [('production', p/'hd')]
    if critter:
        roots += [(f'production_{x.name}', staged) for x in sorted(p.glob('attempt-*'))
                  if (staged := stage_root(x)) is not None]
    else:
        roots += [(f'production_{x.name}', x/'hd') for x in sorted(p.glob('attempt-*'))]
    roots += [(f'repair_{x.relative_to(r).as_posix()}', x) for x in sorted((r/'attempts').glob('**/candidate/hd'))]
    roots += [('repair_accepted', r/'hd')]
    roots += [(f'repair_{x.name}', x) for x in (r/'edge-lock', r/'junction-lock', r/'edge-restore') if x.exists()]
    return roots


def features(hd, original, novelty, badness):
    """Residual detail near original edges, discounted by native-scale drift."""
    a = np.asarray(hd.convert('RGB'), np.float32)
    base = np.asarray(original.resize(hd.size, Image.Resampling.NEAREST).convert('RGB'), np.float32)
    mask = np.asarray(hd.getchannel('A')) > 0
    if not mask.any():
        return {'detail': 0., 'novelty': 0., 'drift': 0.}
    gray = a.mean(2); ref = base.mean(2)
    lap = lambda v: 4*v - np.roll(v, 1, 0) - np.roll(v, -1, 0) - np.roll(v, 1, 1) - np.roll(v, -1, 1)
    edge = np.abs(lap(ref))
    # Native edges are the only trusted places for high-frequency gain.
    support = edge > max(6., float(np.percentile(edge[mask], 60)))
    gain = np.maximum(0, np.abs(lap(gray)) - edge)
    # Source edges get full credit; fine texture elsewhere gets partial credit
    # and remains subject to the invention and native-scale QA penalties.
    weight = np.where(support, 1., .35)
    detail = float(np.mean(np.minimum(gain[mask], 80)*weight[mask]) / 80)
    high = novelty['scale2']
    return {'detail': detail, 'novelty': float(high['novel_fraction']),
            'drift': float(badness)}


def score(f, weights):
    return weights[0]*f['detail'] - weights[1]*f['novelty'] - weights[2]*f['drift']


def rank(found, weights):
    """Score the gate-passing candidates; original art never joins the ranking."""
    passing, fallback = [], None
    for item in found:
        if item['frames']:
            item['score'] = round(sum(score(f['features'], weights)
                                      for f in item['frames'].values()) / max(1, len(item['frames'])), 6)
            passing.append(item)
        else:
            item['score'] = 0.0
            fallback = item
    passing.sort(key=lambda x: x['score'], reverse=True)
    return passing, fallback


def choose(found, weights):
    """Gate first: original art only when NO candidate passed; the score ranks passing HD art.

    Returns ``(chosen, reason, candidates)`` with candidates ordered best-passing first
    and the original-art fallback last, which is the order ``selection.json`` records and
    ``tools/hd-pack-build.py`` reads (it skips fallback entries and takes the first other).
    """
    passing, fallback = rank(found, weights)
    ordered = passing + ([fallback] if fallback else [])
    if passing:
        return passing[0], 'gate then fitted detail score', ordered
    return fallback, 'no candidate passed the gate', ordered


def candidates(group, roots, originals, wall_pairs=None, tile_pairs=None, reference=None, refuse=None):
    found = []; seen = set()
    for name, root in roots:
        # A direction is one candidate: every frame must live in this single root
        # (never a mix of roots), and a refused root contributes none of its frames.
        if root is None or not all((root/f['rel']).is_file() for f in group['frames']):
            continue
        if refuse and refuse(name, root):
            continue
        fingerprint = tuple(hashlib.sha256((root/f['rel']).read_bytes()).hexdigest() for f in group['frames'])
        if fingerprint in seen: continue
        measurements = {}; why = None
        for f in group['frames']:
            rel = f['rel']; path = root/rel
            why = integrity(path, f)
            if why: break
            with Image.open(path) as hd:
                orig = originals[rel]
                badness = qa.badness(hd, orig, rel, qa.COLOUR_LIMIT)
                if badness > 1: why = 'qa_badness'; break
                seam = 0.0
                for other, placement in (wall_pairs or {}).get(rel, []):
                    other_path = (reference or {}).get(other)
                    if other == rel:
                        neighbor = hd
                    elif other_path and other_path.is_file():
                        neighbor = Image.open(other_path)
                    else:
                        continue
                    try:
                        if other not in originals:
                            continue
                        seam = max(seam, invention.wall_seam_delta(hd, neighbor, orig,
                                                                    originals[other], placement))
                    except (ValueError, OSError):
                        pass
                    finally:
                        if neighbor is not hd: neighbor.close()
                    if seam > invention.WALL_SEAM_DELTA_LIMIT:
                        break
                for other, side, forward in (tile_pairs or {}).get(rel, []):
                    other_path = (reference or {}).get(other)
                    if not other_path or not other_path.is_file() or other not in originals:
                        continue
                    try:
                        with Image.open(other_path) as neighbor:
                            delta = (invention.seam_delta(hd, neighbor, orig, originals[other], side)
                                     if forward else invention.seam_delta(neighbor, hd, originals[other], orig, side))
                            seam = max(seam, delta)
                    except (ValueError, OSError):
                        pass
                    if seam > invention.SEAM_DELTA_LIMIT:
                        break
                verdict = invention.score(hd, orig, seam_delta=seam,
                                          floor=rel.startswith('art/tiles/'),
                                          kind=invention.kind_of(rel))
                if verdict['verdict'] in BAD: why = verdict['verdict']; break
                measurements[rel] = {'gate': verdict, 'qa_badness': round(float(badness), 5),
                                     'features': features(hd, orig, verdict, badness)}
        if not why:
            found.append({'source': name, 'root': str(root), 'frames': measurements})
            seen.add(fingerprint)
    return found


def fallback(group, originals, out):
    name = 'source_floor_2x' if group['frames'][0]['rel'].startswith('art/tiles/') else 'original_game_art'
    for f in group['frames']:
        path = out/f['rel']; path.parent.mkdir(parents=True, exist_ok=True)
        originals[f['rel']].resize((f['width'], f['height']), Image.Resampling.NEAREST).save(path)
    return {'source': name, 'root': str(out), 'frames': {}}


def load_key():
    path = Path.home()/'.config/nvidia-nim/env'
    if not path.is_file(): return None
    for line in path.read_text().splitlines():
        if line.startswith('NVIDIA_API_KEY='):
            return line.split('=', 1)[1].strip().strip('"\'')
    return os.environ.get('NVIDIA_API_KEY')


def judge_image(original, a, b):
    width = max(original.width*2, a.width, b.width)
    height = max(original.height*2, a.height, b.height)
    canvas = Image.new('RGB', (width*3, height+28), '#303030')
    draw = ImageDraw.Draw(canvas)
    for i, (label, image) in enumerate((('Original', original.resize((original.width*2, original.height*2), Image.Resampling.NEAREST)), ('A', a), ('B', b))):
        canvas.paste(image.convert('RGBA'), (width*i, 28), image.getchannel('A'))
        draw.text((width*i+5, 5), label, fill='white')
    buf = io.BytesIO(); canvas.save(buf, format='PNG'); return buf.getvalue()


def ask_judge(png, key, model=MODEL):
    import base64
    payload = {'model': model, 'temperature': 0, 'max_tokens': 400,
               'messages': [{'role': 'user', 'content': [
                   {'type': 'text', 'text': 'Compare Original, A and B. Which is the better faithful HD version of Original: same objects and materials, sharper detail, no invented objects? Reply only JSON {"winner":"A"} or B or tie.'},
                   {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,'+base64.b64encode(png).decode()}}]}]}
    request = urllib.request.Request('https://integrate.api.nvidia.com/v1/chat/completions',
        data=json.dumps(payload).encode(), headers={'Authorization': 'Bearer '+key, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=90) as response:
        content = json.load(response)['choices'][0]['message']['content']
    try:
        if not content: raise ValueError('empty judge response')
        value = json.loads(content.strip().removeprefix('```json').removesuffix('```').strip())['winner']
    except (ValueError, KeyError):
        value = 'tie'
    return value if value in ('A', 'B', 'tie') else 'tie'


def judge_pair(original, a, b, cache, budget, key, judge=ask_judge, swap=False):
    if budget['used'] >= budget['limit'] or not key: return None
    image = judge_image(original, a, b)
    digest = hashlib.sha256(image).hexdigest()
    path = cache/(digest+'.json')
    if path.is_file(): return json.loads(path.read_text())['winner']
    answer = None
    for model in (MODEL, FALLBACK_MODEL):
        if budget['used'] >= budget['limit']:
            break
        budget['used'] += 1
        try:
            answer = judge(image, key, model)
            break
        except Exception:
            continue
    if answer is None: return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'winner': answer, 'model': model, 'swapped': swap}))
    return answer


def fit_weights(examples):
    grid = [(1., n, d) for n in (0., .25, .5, 1., 2., 4.) for d in (0., .02, .05, .1, .2)]
    if not examples: return (1., 1., .05), None
    def agree(w):
        return sum((score(a,w) > score(b,w)) == (answer == 'A') for a,b,answer in examples if answer != 'tie')
    best = max(grid, key=agree)
    return best, agree(best)/max(1, sum(x[2] != 'tie' for x in examples))


def contact_sheets(out, spot, originals, limit=20):
    buckets = {'switched-to-production': [], 'switched-to-repair': [], 'kept': []}
    for row in spot:
        target = 'production' if row['chosen_source'].startswith('production') else 'repair'
        bucket = 'kept' if row['baseline'] == target else 'switched-to-'+target
        if bucket in buckets and len(buckets[bucket]) < limit:
            buckets[bucket].append(row)
    for name, rows in buckets.items():
        if not rows: continue
        sheet = Image.new('RGB', (720, len(rows)*150), '#252525')
        draw = ImageDraw.Draw(sheet)
        for index, row in enumerate(rows):
            rel = row['preview']; chosen = out/'jobs'/row['job']/'hd'/rel
            if rel not in originals or not chosen.is_file(): continue
            with Image.open(chosen) as hd:
                images = (originals[rel].resize(hd.size, Image.Resampling.NEAREST), hd)
                for col, image in enumerate(images):
                    thumb = image.copy(); thumb.thumbnail((345, 115))
                    sheet.paste(thumb.convert('RGBA'), (col*360, index*150+25), thumb.getchannel('A'))
            draw.text((5, index*150+4), row['job']+' '+row['direction']+' '+row['chosen_source'][:45], fill='white')
        sheet.save(out/(name+'.png'))


def check_frame(path, original):
    """The pack builder's integrity check (hd-pack-build.verify): RGBA PNG, exact 2x, alpha byte-exact."""
    with Image.open(path) as img:
        img.load()
        if img.format != 'PNG' or img.mode != 'RGBA':
            return 'not RGBA PNG'
        if img.size != (original.width*2, original.height*2):
            return 'not 2x'
        expected = original.getchannel('A').resize(img.size, Image.Resampling.NEAREST)
        if img.getchannel('A').tobytes() != expected.tobytes():
            return 'alpha mismatch'
    return None


def assert_standard_zip(archive):
    """The Android updater rejects ZIP64 (hd-pack-build.assert_standard_zip)."""
    with open(archive, 'rb') as handle:
        handle.seek(0, 2)
        handle.seek(max(0, handle.tell() - 128))
        tail = handle.read()
    end = tail.rfind(b'PK\x05\x06')
    if end < 0:
        raise ValueError(f'no end-of-central-directory record: {archive}')
    # A ZIP64 EOCD locator sits exactly 20 bytes before the classic record.
    if end >= 20 and tail[end - 20:end - 16] == b'PK\x06\x07':
        raise ValueError(f'ZIP64 end-of-central-directory: {archive}')
    with zipfile.ZipFile(archive) as z:
        infos = z.infolist()
    if len(infos) >= MAX_ZIP_ENTRIES:
        raise ValueError(f'too many entries for a standard ZIP: {len(infos)}')
    for info in infos:
        if max(info.file_size, info.compress_size, info.header_offset) >= 0xFFFFFFFE:
            raise ValueError(f'ZIP64 needed for {info.filename}')
    return len(infos)


def write_zip(archive, entries):
    """One classic ZIP; a set that cannot fit it must be split, not grown into ZIP64."""
    if len(entries) >= MAX_ZIP_ENTRIES:
        raise ValueError(f'{archive.name}: {len(entries)} entries do not fit a standard ZIP; '
                         'split the job range')
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for arcname, data in entries:
            z.writestr(arcname, data)
    return assert_standard_zip(archive)


def write_packs(out, jobs, originals):
    """Per-category and per-job standard ZIPs of the selected trees, entries under ``hd/``.

    A job is one range of the art list, so ``critters-0000.zip`` ships as production
    finishes that job.  Every frame is re-checked with the pack builder's integrity test
    against the original FRM frame before it enters an archive.
    """
    out.mkdir(parents=True, exist_ok=True)
    owners = {}; by_category = {}; written = {}
    for job_dir in sorted(jobs.iterdir()):
        tree = job_dir/'hd'
        if not job_dir.is_dir() or not tree.is_dir():
            continue
        job = job_dir.name
        entries = []
        for path in sorted(tree.rglob('*.png')):
            rel = path.relative_to(tree).as_posix()
            if not rel.startswith('art/'):
                raise ValueError(f'unexpected pack frame: {job} {rel}')
            original = originals.get(rel)
            if original is None:
                raise ValueError(f'no original to verify {rel}')
            error = check_frame(path, original)
            if error:
                raise ValueError(f'{error}: {rel}')
            owner = owners.setdefault(rel, job)
            if owner != job:
                raise ValueError(f'duplicate frame {rel} in {owner} and {job}')
            by_category.setdefault(rel.split('/')[1], []).append((rel, path))
            entries.append(('hd/' + rel, path.read_bytes()))
        if entries:
            archive = out/(job + '.zip')
            written[job] = {'group': 'job', 'frames': write_zip(archive, entries),
                            'bytes': archive.stat().st_size}
    for category, frames in sorted(by_category.items()):
        archive = out/(category + '.zip')
        if len(frames) >= MAX_ZIP_ENTRIES:
            # Too many frames for one classic ZIP (critters): the per-job packs,
            # each one art-list range, carry the category instead of ZIP64.
            print(f'pack {category}.zip skipped: {len(frames)} entries exceed a standard ZIP; '
                  'use the job-range packs')
            continue
        entries = [('hd/' + rel, path.read_bytes()) for rel, path in frames]
        written[category] = {'group': 'category', 'frames': write_zip(archive, entries),
                             'bytes': archive.stat().st_size}
    return written


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--production', type=Path, default=Path('/ai/wasteland-hd/production-lightning4'))
    p.add_argument('--repair', type=Path, default=Path('/ai/wasteland-hd/repair-lightning4'))
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--judge-budget', type=int, default=600)
    p.add_argument('--no-judge', action='store_true')
    p.add_argument('--pack-out', type=Path, default=None,
                   help='also write per-category and per-job standard ZIPs of the selected '
                        'trees (entries under hd/, no ZIP64) into this directory')
    args = p.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    plan = json.loads((args.production/'plan.json').read_text())
    game = Game(plan['game']); key = None if args.no_judge else load_key()
    budget = {'used': 0, 'limit': min(600, args.judge_budget)}
    weights_path = args.out/'weights.json'
    training = []; records = []; originals = {}; swaps = []
    wall_pairs = {}; tile_pairs = {}; reference = {}
    pair_file = args.repair/'wall-pairs.json'
    if pair_file.is_file():
        seen_pairs = set()
        for pair in json.loads(pair_file.read_text()).get('pairs', []):
            a, b = pair['a']['rel'], pair['b']['rel']
            pair_key = (a, b, pair['dx'], pair['dy'])
            if pair_key in seen_pairs: continue
            seen_pairs.add(pair_key)
            wall_pairs.setdefault(a, []).append((b, {'dx': pair['dx'], 'dy': pair['dy']}))
            wall_pairs.setdefault(b, []).append((a, {'dx': -pair['dx'], 'dy': -pair['dy']}))
    adjacency = args.repair/'map-adjacency.json'
    if adjacency.is_file():
        for a, b, side in json.loads(adjacency.read_text()).get('pairs', []):
            tile_pairs.setdefault(a, []).append((b, side, True))
            tile_pairs.setdefault(b, []).append((a, side, False))
    for root in (args.repair, args.production):
        for category in ('walls', 'tiles'):
            for path in (root/'jobs').glob(f'*/hd/art/{category}/**/*.png'):
                rel = path.as_posix().split('/hd/', 1)[1]
                reference.setdefault(rel, path)
    for rel in set(reference) | set(wall_pairs) | set(tile_pairs):
        try: originals[rel] = qa.original_frame(game, {}, rel)
        except (FileNotFoundError, IndexError, ValueError): pass
    for work in sorted((args.production/'jobs').iterdir()):
        if not work.is_dir() or not (work/'inventory.json').is_file(): continue
        state = json.loads((work/'state.json').read_text())
        repair_state_path = args.repair/'jobs'/work.name/'state.json'
        if state.get('status') != 'needs_review' or not repair_state_path.is_file(): continue
        inventory = json.loads((work/'inventory.json').read_text())
        repair_state = json.loads(repair_state_path.read_text()).get('groups', {})
        unresolved = {k for k, v in state['groups'].items() if v.get('fallback')}
        if not unresolved.issubset(repair_state): continue
        roots = source_roots(args.production, args.repair, work.name,
                             critter=work.name.split('-')[0] == 'critters')
        for direction, group in inventory['groups'].items():
            for f in group['frames']:
                if f['rel'] not in originals:
                    originals[f['rel']] = qa.original_frame(game, {}, f['rel'])
            # Critter directions are gated by the flicker verdict production recorded.
            flicker = (flicker_verdict(state, direction)
                       if group['frames'][0]['rel'].startswith(CRITTER_ART) else None)
            found = candidates(group, roots, originals, wall_pairs, tile_pairs, reference,
                               refuse=production_refuser(flicker))
            fb = args.out/'fallback'/work.name/'hd'
            found.append(fallback(group, originals, fb))
            prior = repair_state.get(direction, {})
            baseline = ('repair' if (prior.get('source') or '').startswith(str(args.repair))
                        else 'fallback' if prior.get('fallback') else 'production')
            records.append((work.name, direction, group, found, baseline, flicker))
    # Deterministic, category-stratified calibration sample.
    sampled = Counter()
    for job, direction, group, found, baseline, flicker in sorted(records, key=lambda r: hashlib.sha256(r[1].encode()).digest()):
        if not key: break
        category = job.split('-')[0]
        if sampled[category] >= 20: continue
        scored = [item for item in found if item['frames']]
        if not scored: continue
        a = scored[0]; b = found[-1]
        if b['frames']: continue
        rel = group['frames'][0]['rel']
        with Image.open(Path(a['root'])/rel) as ia, Image.open(Path(b['root'])/rel) as ib:
            answer = judge_pair(originals[rel], ia, ib, args.out/'judge-cache', budget, key)
            if answer:
                zero = {'detail': 0., 'novelty': 0., 'drift': 0.}
                training.append((a['frames'][rel]['features'], zero, answer))
                sampled[category] += 1
                if len(swaps) < 50:
                    reverse = judge_pair(originals[rel], ib, ia, args.out/'judge-cache', budget, key, swap=True)
                    if reverse: swaps.append((answer, reverse))
    for job, direction, group, found, baseline, flicker in records:
        scored = [item for item in found if item['frames']]
        if len(scored) < 2 or not key: continue
        category = job.split('-')[0]
        if sampled[category] >= 32: continue
        a,b = scored[:2]; rel = group['frames'][0]['rel']
        with Image.open(Path(a['root'])/rel) as ia, Image.open(Path(b['root'])/rel) as ib:
            answer = judge_pair(originals[rel], ia, ib, args.out/'judge-cache', budget, key)
            if answer:
                training.append((a['frames'][rel]['features'], b['frames'][rel]['features'], answer))
                sampled[category] += 1
                if len(swaps) < 50:
                    reverse = judge_pair(originals[rel], ib, ia, args.out/'judge-cache', budget, key, swap=True)
                    if reverse: swaps.append((answer, reverse))
    weights, agreement = fit_weights(training)
    consistent = sum((a == 'tie' and b == 'tie') or (a == 'A' and b == 'B') or (a == 'B' and b == 'A') for a,b in swaps)
    swap_rate = consistent/len(swaps) if swaps else None
    judge_trusted = swap_rate is not None and swap_rate >= .8
    if not judge_trusted:
        weights = (1., 1., .05)
    weights_path.write_text(json.dumps({'weights': weights, 'training_pairs': len(training), 'agreement': agreement,
                                        'swap_pairs': len(swaps), 'swap_consistency': swap_rate,
                                        'judge_trusted': judge_trusted}, indent=2))
    selections = {}; spot = []; switches = Counter()
    for job, direction, group, found, baseline, flicker in records:
        chosen, reason, ordered = choose(found, weights)
        passing = [item for item in ordered if item['frames']]
        # A judge tie-break may only pick between passing HD candidates.
        if judge_trusted and len(passing) > 1 and passing[0]['score']-passing[1]['score'] < .025 and budget['used'] < budget['limit']:
            rel = group['frames'][0]['rel']; a,b = passing[:2]
            with Image.open(Path(a['root'])/rel) as ia, Image.open(Path(b['root'])/rel) as ib:
                answer = judge_pair(originals[rel], ia, ib, args.out/'judge-cache', budget, key)
            if answer == 'B': chosen = b
        source = chosen['source']
        selected = 'production' if source.startswith('production') else 'repair' if source.startswith('repair') else 'fallback'
        switches[(job, baseline, selected)] += 1
        output = args.out/'jobs'/job/'hd'
        for f in group['frames']:
            src = Path(chosen['root'])/f['rel']; dst = output/f['rel']; dst.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(src, dst)
        row = {'chosen_source': source, 'chosen_root': chosen['root'], 'reason': reason,
               'score': chosen['score'], 'candidates': [{k:v for k,v in c.items() if k != 'frames'} for c in ordered],
               'frames': chosen['frames'], 'baseline': baseline}
        if flicker is not None:
            row['flicker'] = {k: flicker.get(k) for k in ('pass', 'reason', 'animation', 'keyframe')}
        selections.setdefault(job,{})[direction] = row
        spot.append({'job': job, 'direction': direction, 'chosen_source': source, 'baseline': baseline,
                     'score_gap': round(passing[0]['score']-passing[1]['score'],6) if len(passing)>1 else 0,
                     'preview': group['frames'][0]['rel']})
    for job, values in selections.items():
        (args.out/'jobs'/job/'selection.json').write_text(json.dumps({'groups': values}, indent=2))
    spot.sort(key=lambda x: (x['baseline'] == ('production' if x['chosen_source'].startswith('production') else 'repair'), -x['score_gap'], x['job']))
    stratified = []; per_category = Counter()
    for row in spot:
        category = row['job'].split('-')[0]
        if per_category[category] < 14:
            stratified.append(row); per_category[category] += 1
        if len(stratified) == 40: break
    if len(stratified) < 40:
        seen = {(r['job'], r['direction']) for r in stratified}
        stratified.extend(r for r in spot if (r['job'],r['direction']) not in seen)
    (args.out/'spotcheck.json').write_text(json.dumps(stratified[:40], indent=2))
    contact_sheets(args.out, spot, originals)
    fallback_by_category = Counter()
    for job, values in selections.items():
        # Accumulate: one category spans several jobs.
        fallback_by_category[job.split('-')[0]] += sum(
            1 for row in values.values() if row['chosen_source'] in FALLBACK)
    fallback_by_category = +fallback_by_category  # drop the zero entries
    summary = {'jobs': len(selections), 'directions': len(records), 'switches': [{'job':j,'from':a,'to':b,'count':n} for (j,a,b),n in sorted(switches.items())],
               'fallback': dict(sorted(fallback_by_category.items())),
               'fallback_total': sum(fallback_by_category.values()),
               'judge_calls': budget['used'], 'judge_agreement': agreement, 'swap_consistency': swap_rate, 'judge_trusted': judge_trusted}
    if args.pack_out:
        summary['packs'] = write_packs(args.pack_out, args.out/'jobs', originals)
        for name, info in sorted(summary['packs'].items()):
            print(f"pack {name}.zip {info['frames']} frames {info['bytes']} bytes")
    (args.out/'summary.json').write_text(json.dumps(summary, indent=2)); print(json.dumps(summary, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
