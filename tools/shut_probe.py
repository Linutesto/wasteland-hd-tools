"""Probe why the `shut*` tile family fails the HD gate, and test alternative recipes.

Read-only over the production/repair evidence; all new files go to
`tmp-shut/` (gitignored) or `/ai/wasteland-hd/shut-probe/` (renders).

  python3 tools/shut_probe.py scan      # gate every shut candidate, offline
  python3 tools/shut_probe.py sheets    # contact sheets: orig 2x | each rung
  python3 tools/shut_probe.py render    # alternative recipes (budgeted renders)
  python3 tools/shut_probe.py summary   # recipe comparison table

Only repo code paths are used for rendering: sheets.render_sheet/cut_sheet and
upscale.Comfy with the same graph the CLI builds, after
production.Controller.wait_ready (an empty ComfyUI queue). No interrupt/free
endpoint is ever called, and the live roots are never written.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wasteland_hd import invention, qa, sheets  # noqa: E402
from wasteland_hd.gamefiles import Game  # noqa: E402
from wasteland_hd.upscale import LIGHTNING, NEGATIVE, PROMPT, Comfy, write_render_receipt  # noqa: E402

GAME = '/home/yan/fallout2/gamedata'
PROD = Path('/ai/wasteland-hd/production-lightning4/jobs/tiles-0007')
REPAIR = Path('/ai/wasteland-hd/repair-lightning4/jobs/tiles-0007')
ADJACENCY = Path('/ai/wasteland-hd/repair-lightning4/map-adjacency.json')
OUT = Path('/ai/wasteland-hd/shut-probe')
HERE = Path('tmp-shut')
REL = 'art/tiles/{name}/0_0.png'

# The repair ladder and production attempts, in the order they were tried.
RUNGS = [
    ('production-a0', PROD / 'attempt-00/hd'),
    ('production-a1', PROD / 'attempt-01/hd'),
    ('production-a2', PROD / 'attempt-02/hd'),
    ('denoise-0.55', REPAIR / 'attempts/batch/denoise-0/candidate/hd'),
    ('denoise-0.40', REPAIR / 'attempts/batch/denoise-1/candidate/hd'),
    ('fallback', REPAIR / 'source-floor'),
]

# A shut-specific prompt line: what the tiles actually depict (see scan/sheets).
# All 95 shut* files are the same 42x18 placeholder panel: a flat dark-red
# rectangle with the white pixel-font words "UNUSED ART" on two lines. It is
# not a texture, so the prompt says so instead of asking for texture detail.
SHUT_PROMPT = (
    "This is a sheet of separate sprites from the isometric 1990s game Fallout 2 on a flat grey background. "
    "Each sprite is a placeholder panel graphic, not a texture: a flat dark red rectangle (RGB 168,0,0) "
    "with the white pixel-font words UNUSED ART written across it in two lines, letter by letter. "
    "Upscale it to high resolution, like remastered pixel art. Reproduce exactly the same two flat colours, "
    "the same panel border, and the same letters in the same positions with the same pixel-aligned shapes; "
    "keep the letter strokes crisp and square, without anti-aliasing, glow, gradients, shading or texture. "
    "Do not add anything else, do not draw neighbouring sprites, dirt, cracks, metal, shadows or new objects. "
    "Keep every sprite exactly where it is, with the same outline and size. Keep the background flat grey."
)


def shut_names():
    names = sorted(p.name for p in (REPAIR / 'source-floor/art/tiles').glob('shut*'))
    return names


def adjacency():
    """Shut tile rel paths and their map neighbours (side), from the cached index."""
    doc = json.loads(ADJACENCY.read_text())
    out = {}
    for a, b, side in doc['pairs']:
        if '/shut' in a or '/shut' in b:
            out.setdefault(a, []).append((b, side))
            out.setdefault(b, []).append((a, side))
    return out


def neighbour_seam(root, rel, adj, originals):
    """Worst floor seam delta of this frame against neighbours cut from `root`."""
    worst = 0.0
    for other, side in adj.get(rel, []):
        path = root / other
        if not path.is_file():
            continue
        with Image.open(root / rel) as hd:
            with Image.open(path) as other_hd:
                a, b = (rel, other) if side in ('right', 'bottom') else (other, rel)
                try:
                    worst = max(worst, invention.seam_delta(
                        hd if a == rel else other_hd, other_hd if a == rel else hd,
                        originals(a), originals(b), side))
                except (ValueError, OSError):
                    continue
    return worst


def scan(args):
    game = Game(GAME)
    adj = adjacency()
    cache = {}
    originals = lambda rel: qa.original_frame(game, cache, rel)  # noqa: E731
    names = shut_names()
    rows = {}
    for name in names:
        rel = REL.format(name=name)
        orig = originals(rel)
        alpha = np.asarray(orig.getchannel('A'))
        opaque = alpha > 0
        rgb = np.asarray(orig.convert('RGB'), np.float32)[opaque]
        rows[name] = {'rel': rel, 'size': list(orig.size),
                      'opaque_fraction': round(float(opaque.mean()), 4),
                      'opaque_pixels': int(opaque.sum()),
                      'mean_rgb': [round(float(v), 1) for v in rgb.mean(axis=0)] if opaque.any() else [],
                      'std_rgb': round(float(rgb.std()), 2) if opaque.any() else 0.0,
                      'neighbours': sorted(adj.get(rel, [])), 'rungs': {}}
        for label, root in RUNGS:
            path = root / rel
            if not path.is_file():
                rows[name]['rungs'][label] = None
                continue
            with Image.open(path) as hd:
                try:
                    bad = qa.badness(hd, orig, rel, qa.COLOUR_LIMIT)
                    seam = neighbour_seam(root, rel, adj, originals)
                    score = invention.score(hd, orig, seam_delta=seam, floor=True,
                                            kind='tiles')
                except (ValueError, OSError) as exc:
                    rows[name]['rungs'][label] = {'error': str(exc)}
                    continue
            rows[name]['rungs'][label] = {
                'verdict': score['verdict'], 'qa_badness': round(float(bad), 3),
                'seam_delta': score['seam_delta'],
                'novel_fraction': score['scale2']['novel_fraction'],
                'largest_novel_region': score['scale2']['largest_novel_region'],
                'novel_window': score['scale2']['novel_window'],
                'flat_damage': score['scale2']['flat_damage'],
                'dark_novel': score['scale2']['dark_novel'],
                'border_error': score['scale2']['border_error'],
                'native_novel': score['native']['novel_fraction'],
                'native_border': score['native']['border_error']}
    HERE.mkdir(exist_ok=True)
    (HERE / 'scan.json').write_text(json.dumps(rows, indent=1))
    summarise(rows, name='scan-summary.txt')
    return rows


def summarise(rows, *, name):
    """Compact per-rung table: how many of each verdict, and which metric fires."""
    lines = []
    labels = [label for label, _ in RUNGS]
    lines.append(f"{'rung':16s} {'n':>3s} {'clean':>5s} {'minor':>5s} {'invented':>8s} "
                 f"{'unmatched':>9s} {'qa_fail':>7s} {'med_novel':>9s} {'med_flat':>8s} "
                 f"{'med_border':>10s} {'med_qa':>7s}")
    for label in labels:
        vals = [r['rungs'][label] for r in rows.values() if r['rungs'].get(label)]
        vals = [v for v in vals if 'error' not in v]
        if not vals:
            lines.append(f'{label:16s}   0  (missing)')
            continue
        count = lambda k: sum(1 for v in vals if v['verdict'] == k)  # noqa: E731
        med = lambda k: float(np.median([v[k] for v in vals]))  # noqa: E731
        lines.append(f"{label:16s} {len(vals):3d} {count('clean'):5d} "
                     f"{count('minor_invention'):5d} {count('invented_texture'):8d} "
                     f"{count('unmatched_floor'):9d} "
                     f"{sum(1 for v in vals if v['qa_badness'] > 1):7d} "
                     f"{med('novel_fraction'):9.3f} {med('flat_damage'):8.3f} "
                     f"{med('border_error'):10.2f} {med('qa_badness'):7.3f}")
    lines.append('')
    lines.append('per-tile rungs (verdict / novel / flat / border / qa):')
    for name_, r in rows.items():
        cells = []
        for label in labels:
            v = r['rungs'].get(label)
            cells.append('-' if not v else
                         f"{v['verdict'][:4]} {v['novel_fraction']:.2f} {v['flat_damage']:.2f} "
                         f"{v['border_error']:.1f} {v['qa_badness']:.2f}")
        lines.append(f"{name_} {r['size'][0]}x{r['size'][1]} op={r['opaque_fraction']:.2f} "
                     f"sd={r['std_rgb']:.1f} | " + ' | '.join(cells))
    text = '\n'.join(lines)
    (HERE / name).write_text(text + '\n')
    print(text)


# ------------------------------------------------------------------ sheets

def sheet_tiles(count=24):
    """A spread of the family: every 4th shut tile plus the first few."""
    names = shut_names()
    step = max(1, len(names) // count)
    chosen = names[::step][:count]
    return chosen


def label_cell(draw, xy, text, w, h):
    x, y = xy
    draw.rectangle((x, y, x + w, y + h), fill=(0, 0, 0))
    draw.text((x + 2, y + 1), text, fill=(230, 230, 230))


def contact_sheets(rows, count=24, zoom=2):
    chosen = sheet_tiles(count)
    cell_w = 240  # 42x18 tiles at zoom 2 leave room for the metric label
    cell_h = 150 * zoom // 2 + 30
    cols = len(RUNGS)
    label_w = 90
    width = label_w + cols * cell_w
    height = 40 + len(chosen) * cell_h
    canvas = Image.new('RGB', (width, height), (24, 24, 24))
    draw = ImageDraw.Draw(canvas)
    heads = [label for label, _ in RUNGS]
    for c, head in enumerate(heads):
        draw.text((label_w + c * cell_w + 4, 8), head, fill=(255, 255, 0))
    for r, name in enumerate(chosen):
        rel = rows[name]['rel']
        orig = qa.original_frame(Game(GAME), {}, rel)
        draw.text((4, 40 + r * cell_h + cell_h // 2), name, fill=(255, 255, 255))
        cells = [('orig 2x NEAREST', orig.resize((orig.width * zoom, orig.height * zoom),
                                                 Image.Resampling.NEAREST), None)]
        for label, root in RUNGS:
            path = root / rel
            img = Image.open(path).resize((orig.width * zoom, orig.height * zoom),
                                          Image.Resampling.NEAREST) if path.is_file() else None
            cells.append((label, img, rows[name]['rungs'].get(label)))
        for c, (label, img, metrics) in enumerate(cells):
            x = label_w + c * cell_w
            y = 40 + r * cell_h
            if img is not None:
                canvas.paste(img.convert('RGB'), (x + 2, y + 2), img.convert('RGBA'))
            if metrics:
                text = (f"{metrics['verdict'][:14]}\nn {metrics['novel_fraction']:.3f} "
                        f"f {metrics['flat_damage']:.3f}\nb {metrics['border_error']:.1f} "
                        f"qa {metrics['qa_badness']:.2f}")
            else:
                text = '-'
            label_cell(draw, (x + 2, y + cell_h - 34), text, cell_w - 4, 32)
    HERE.mkdir(exist_ok=True)
    path = HERE / 'shut-contact.png'
    canvas.save(path)
    print(f'contact sheet: {path} ({width}x{height})')
    return path


# ------------------------------------------------------------------ render recipes

def _model(*, denoise, seed, prompt, render_scale):
    """The CLI's Comfy model (8-step Lightning, cfg 1) with repair's denoise knob."""
    model = Comfy(steps=8, cfg=1.0, lora=LIGHTNING[8], seed=seed, prompt=prompt)
    model.denoise = denoise
    model.render_scale = render_scale
    graph = model.graph
    def patched(image_name, width, height):
        g = graph(image_name, width, height)
        g['ks']['inputs']['denoise'] = denoise
        return g
    model.graph = patched
    return model


def _wait_ready(args_ns):
    from wasteland_hd import production
    class Fake:
        pass
    fake = Fake()
    fake.args = argparse.Namespace(comfy=args_ns.comfy, ready_timeout=args_ns.ready_timeout)
    production.Controller.wait_ready(fake)


def render(args):
    """Run up to --budget sheet renders of alternative recipes on N shut tiles."""
    game = Game(GAME)
    rows = json.loads((HERE / 'scan.json').read_text())
    names = args.tiles.split(',') if args.tiles else pick_failures(rows, args.count)
    budget_path = OUT / 'budget.json'
    spent = json.loads(budget_path.read_text())['calls'] if budget_path.exists() else 0
    recipes = {r['name']: r for r in RECIPES}
    chosen = [recipes[n] for n in args.recipes.split(',')]
    print(f'{len(names)} tiles: {", ".join(names)}')
    print(f'renders already spent: {spent}/{args.budget}')
    for recipe in chosen:
        names_for = names[:recipe.get('tiles_limit', len(names))]
        need = len(names_for) if recipe['per_tile'] else 1
        if spent + need > args.budget:
            print(f"skip {recipe['name']}: needs {need}, budget {args.budget - spent} left",
                  flush=True)
            continue
        root = OUT / 'recipes' / recipe['name']
        root.mkdir(parents=True, exist_ok=True)
        sheet_dir, up_dir = root / 'sheets', root / 'upscaled'
        sheet_dir.mkdir(exist_ok=True)
        up_dir.mkdir(exist_ok=True)
        work = root / 'work'
        model = _model(denoise=recipe['denoise'], seed=recipe['seed'],
                       prompt=SHUT_PROMPT if recipe['prompt'] == 'shut' else PROMPT,
                       render_scale=recipe['render_scale'])
        # One sheet for the whole recipe (that is what the CLI does for a
        # normal job); per_tile packing gives each frame a sheet of context.
        if recipe['per_tile']:
            plans = [s for name in names_for
                     for f in sheets.frames_of(game, [f'art/tiles/{name}.frm'])
                     for s in sheets.pack([f], sheet=recipe['sheet'])]
        else:
            frames = [f for name in names_for
                      for f in sheets.frames_of(game, [f'art/tiles/{name}.frm'])]
            plans = sheets.pack(frames, sheet=recipe['sheet'])
        for index, items in enumerate(plans):
            if spent >= args.budget:
                print(f"{recipe['name']}: render budget {args.budget} reached, stopping",
                      flush=True)
                return
            native = sheet_dir / f"sheet-{index:03d}.png"
            # Never reuse a sheet whose placements changed (a killed run may
            # leave a sheet from an older packing with the same file name).
            expected = [sheets.asdict(p) for p, _ in items]
            saved = None
            try:
                saved = json.loads(Path(str(native) + '.json').read_text())
            except (OSError, ValueError):
                pass
            if not native.exists() or saved != expected:
                sheets.render_sheet(items, game.palette(), str(native), sheet=recipe['sheet'])
            rendered = up_dir / f"sheet-{index:03d}.png"
            if not rendered.exists():
                _wait_ready(args)
                t = __import__('time').time()
                model.upscale(str(native), str(rendered) + '.part.png',
                              scale=recipe['render_scale'])
                os.replace(str(rendered) + '.part.png', str(rendered))
                write_render_receipt(str(rendered), str(native), model)
                spent += 1
                json.dump({'calls': spent}, open(budget_path, 'w'))
                print(f"[{recipe['name']}] sheet-{index:03d} "
                      f"{len(items)} frames {__import__('time').time() - t:.0f}s "
                      f"({spent}/{args.budget})", flush=True)
            sheets.cut_sheet(str(native), str(rendered), str(work))
        json.dump({'calls': spent}, open(budget_path, 'w'))


def pick_failures(rows, count=8):
    """The worst rejected tiles by border/flat evidence at production denoise 1.0."""
    scored = []
    for name, r in rows.items():
        v = r['rungs'].get('production-a0')
        if not v or 'error' in v:
            continue
        if v['verdict'] == 'clean' and v['qa_badness'] <= 1:
            continue
        scored.append((v['qa_badness'], v['border_error'], name))
    scored.sort(reverse=True)
    return [n for _qa, _b, n in scored[:count]]


RECIPES = [
    {'name': 'A-dn030-art-per-tile', 'denoise': 0.30, 'prompt': 'art', 'sheet': 256,
     'render_scale': 4, 'seed': 42, 'per_tile': True, 'tiles_limit': 8},
    {'name': 'B-dn020-art', 'denoise': 0.20, 'prompt': 'art', 'sheet': 256,
     'render_scale': 4, 'seed': 42, 'per_tile': False},
    {'name': 'C-dn100-shut', 'denoise': 1.00, 'prompt': 'shut', 'sheet': 256,
     'render_scale': 4, 'seed': 42, 'per_tile': False},
    {'name': 'D-dn030-shut', 'denoise': 0.30, 'prompt': 'shut', 'sheet': 256,
     'render_scale': 4, 'seed': 42, 'per_tile': False},
    {'name': 'E-dn030-shut-per-tile', 'denoise': 0.30, 'prompt': 'shut', 'sheet': 256,
     'render_scale': 4, 'seed': 42, 'per_tile': True, 'tiles_limit': 4},
    {'name': 'F-dn030-shut-rs2', 'denoise': 0.30, 'prompt': 'shut', 'sheet': 256,
     'render_scale': 2, 'seed': 42, 'per_tile': False},
    {'name': 'G-dn100-art-rs2', 'denoise': 1.00, 'prompt': 'art', 'sheet': 256,
     'render_scale': 2, 'seed': 42, 'per_tile': False},
]


def baselines(args):
    """Deterministic no-model options for the same frames, gated identically.

    nearest: exact 2x of the source pixels (is what the engine can always use).
    lanczos: plain resample, no AI.
    soft:     floor_fidelity.source_floor_2x (the shipped fallback, softness 0.35).
    """
    from wasteland_hd.floor_fidelity import source_floor_2x

    game = Game(GAME)
    cache = {}
    rows = {}
    for name in shut_names():
        rel = REL.format(name=name)
        orig = qa.original_frame(game, cache, rel)
        opts = {'nearest': orig.resize((orig.width * 2, orig.height * 2), Image.Resampling.NEAREST),
                'lanczos': orig.resize((orig.width * 2, orig.height * 2), Image.Resampling.LANCZOS),
                'soft': source_floor_2x(orig)}
        rows[name] = {}
        for label, image in opts.items():
            bad = qa.badness(image, orig, rel, qa.COLOUR_LIMIT)
            score = invention.score(image, orig, floor=True, kind='tiles')
            rows[name][label] = {'verdict': score['verdict'], 'qa_badness': round(float(bad), 3),
                                 **{k: score['scale2'][k] for k in
                                    ('novel_fraction', 'flat_damage', 'border_error')}}
    (HERE / 'baselines.json').write_text(json.dumps(rows, indent=1))
    lines = [f"{'option':10s} {'verdicts':38s} {'med_qa':>7s} {'med_border':>10s} {'max_qa':>7s}"]
    for label in ('nearest', 'lanczos', 'soft'):
        vals = [r[label] for r in rows.values()]
        verdicts = sorted({v['verdict'] for v in vals})
        lines.append(f"{label:10s} {','.join(verdicts):38s} "
                     f"{float(np.median([v['qa_badness'] for v in vals])):7.3f} "
                     f"{float(np.median([v['border_error'] for v in vals])):10.2f} "
                     f"{max(v['qa_badness'] for v in vals):7.3f}")
    text = '\n'.join(lines)
    (HERE / 'baselines.txt').write_text(text + '\n')
    print(text)


def recipe_sheet(args):
    """Visual rows: original | each recipe's cut frame, with its gate numbers."""
    game = Game(GAME)
    cache = {}
    base = OUT / 'recipes'
    recipes = sorted(p for p in base.glob('*/work/hd/art/tiles'))
    scored = json.loads((HERE / 'recipes.json').read_text()) if (HERE / 'recipes.json').exists() else {}
    names = sorted(p.name for p in (base / 'A-dn030-art-per-tile/work/hd/art/tiles').glob('shut*'))
    z = 5
    cw, ch = 42 * z + 12, 18 * z + 26
    canvas = Image.new('RGB', (cw * (len(recipes) + 1) + 8, ch * len(names) + 26), (20, 20, 20))
    draw = ImageDraw.Draw(canvas)
    draw.text((6, 6), 'original (2x nearest) vs recipes (2x)', fill=(255, 255, 0))
    for c, root in enumerate([None] + recipes):
        head = 'orig' if root is None else root.relative_to(base).parts[0]
        draw.text((8 + c * cw, 20), head[:30], fill=(255, 255, 0))
        for r, name in enumerate(names):
            rel = REL.format(name=name)
            path = None if root is None else root / name / '0_0.png'
            if path is not None and not path.is_file():
                continue
            image = qa.original_frame(game, cache, rel) if path is None else Image.open(path)
            x, y = 8 + c * cw, 26 + r * ch
            canvas.paste(image.convert('RGB').resize((42 * z, 18 * z), 0), (x, y))
            if path is not None and scored:
                v = scored.get(head, {}).get(name)
                if v:
                    draw.text((x, y + 18 * z + 1),
                              f"{v['verdict'][:4]} q{v['qa_badness']:.2f} b{v['border_error']:.0f}",
                              fill=(220, 220, 220))
    HERE.mkdir(exist_ok=True)
    out_path = HERE / 'recipes-contact.png'
    canvas.save(out_path)
    print(f'recipe sheet: {out_path} {canvas.size}')


def summary(args):
    """Gate every recipe's cut frames the same way the repair gate would, and rank."""
    game = Game(GAME)
    adj = adjacency()
    cache = {}
    originals = lambda rel: qa.original_frame(game, cache, rel)  # noqa: E731
    out = {}
    base = OUT / 'recipes'
    for root in sorted(base.glob('*/work/hd/art/tiles')):
        recipe = root.relative_to(base).parts[0]
        vals = {}
        hd_root = root.parents[1]  # .../work/hd
        for hd_dir in sorted(root.glob('shut*')):
            rel = f'art/tiles/{hd_dir.name}/0_0.png'
            hd_path = hd_dir / '0_0.png'
            orig = originals(rel)
            with Image.open(hd_path) as hd:
                bad = qa.badness(hd, orig, rel, qa.COLOUR_LIMIT)
                seam = neighbour_seam(hd_root, rel, adj, originals)
                score = invention.score(hd, orig, seam_delta=seam, floor=True, kind='tiles')
            vals[hd_dir.name] = {'verdict': score['verdict'],
                                 'qa_badness': round(float(bad), 3),
                                 'seam_delta': score['seam_delta'],
                                 **{k: score['scale2'][k] for k in
                                    ('novel_fraction', 'largest_novel_region', 'novel_window',
                                     'flat_damage', 'dark_novel', 'border_error')}}
        out[recipe] = vals
    (HERE / 'recipes.json').write_text(json.dumps(out, indent=1))
    lines = [f"{'recipe':24s} {'n':>2s} {'clean':>5s} {'minor':>5s} {'invented':>8s} "
             f"{'unmatched':>9s} {'qa_pass':>7s} {'med_novel':>9s} {'med_flat':>8s} "
             f"{'med_border':>10s} {'med_qa':>7s}"]
    for recipe, vals in out.items():
        rows = list(vals.values())
        count = lambda k: sum(1 for v in rows if v['verdict'] == k)  # noqa: E731
        med = lambda k: float(np.median([v[k] for v in rows])) if rows else 0  # noqa: E731
        lines.append(f"{recipe:24s} {len(rows):2d} {count('clean'):5d} "
                     f"{count('minor_invention'):5d} {count('invented_texture'):8d} "
                     f"{count('unmatched_floor'):9d} "
                     f"{sum(1 for v in rows if v['qa_badness'] <= 1):7d} "
                     f"{med('novel_fraction'):9.3f} {med('flat_damage'):8.3f} "
                     f"{med('border_error'):10.2f} {med('qa_badness'):7.3f}")
        lines.append('    ' + ', '.join(f"{n}:{v['verdict'][:4]}/{v['qa_badness']:.2f}"
                                        for n, v in vals.items()))
    text = '\n'.join(lines)
    (HERE / 'recipe-summary.txt').write_text(text + '\n')
    print(text)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('scan'); p.set_defaults(fn=scan)
    p = sub.add_parser('sheets'); p.set_defaults(fn=lambda a: contact_sheets(scan(a)))
    p = sub.add_parser('render')
    p.add_argument('--recipes', default=','.join(r['name'] for r in RECIPES))
    p.add_argument('--tiles', default='')
    p.add_argument('--count', type=int, default=8)
    p.add_argument('--budget', type=int, default=20)
    p.add_argument('--comfy', default='http://127.0.0.1:8188')
    p.add_argument('--ready-timeout', type=int, default=3600)
    p.set_defaults(fn=render)
    p = sub.add_parser('summary'); p.set_defaults(fn=summary)
    p = sub.add_parser('recipe-sheet'); p.set_defaults(fn=recipe_sheet)
    p = sub.add_parser('baselines'); p.set_defaults(fn=baselines)
    args = ap.parse_args()
    args.fn(args)


if __name__ == '__main__':
    main()
