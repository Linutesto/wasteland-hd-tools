# Freebuff task: critter animation flicker measurement (2026-10-29)

Repo `~/wasteland-hd-tools` (branch main, 78 tests). CPU only — no GPU, no model, no ComfyUI.

## Why

Critter generation (224,365 frames, ~5 GPU-days at full steps) is **gated**: it must not start
until the animation flicker issue is understood. Right now nobody can measure flicker — eyeballing
GIFs is the only tool. Build the measurement, so the go/no-go on critters becomes a number.

Background (read these first):
- `docs/ANIMATION-STABILITY.md` and `wasteland_hd/temporal.py` (the existing stabiliser, which
  "gains little" — understand why before adding anything).
- `docs/AUTONOMOUS-PRODUCTION.md`, `wasteland_hd/{sheets,qa}.py`.
- Evidence already on disk (read-only): `/ai/wasteland-hd/critter-test/` — `rows4/` (fast) and
  `rows4full/` (full 30 steps) sheets, the `hmwarra*/nmpeas*` GIFs and `*-strip.png` pairs, and
  `zoom-*.png`. Same animation upscaled both ways.

## Do

1. Define a flicker metric for one animation (a walk cycle: frames × directions) that separates
   "real frame-to-frame change the original sprite has" from "per-frame independent generation
   noise". Per-pixel colour drift is not enough by itself — a walking critter is *supposed* to
   change. Candidates to evaluate and compare: change outside the moving silhouette, change in
   static regions (background, equipment), high-frequency temporal variance on pixels the original
   frames leave identical, edge/outline jitter. Say why each one you keep is the right signal.
2. Implement it as `wasteland_hd/flicker.py` with a `python -m wasteland_hd flicker ...` CLI
   (match the style of the existing subcommands: explicit paths, refuses to write into its source
   tree, JSON output). Input: an upscaled animation dir + the original sprite frames from
   `~/fallout2/gamedata`.
3. Calibrate it on the existing evidence: run it over `rows4/` and `rows4full/` and over the
   original (non-upscaled) sprites as a control. It must rank the originals as the low end, and it
   must reproduce the known verdict (the full-30-step rows were judged better than fast) or explain
   precisely which metric disagrees with the eyeball call and why.
4. Tests: `python3 -m pytest -q` must stay green, with real tests for the metric (synthesised
   frames with a known injected perturbation must score higher than untouched frames).
5. Produce a small contact sheet or two under `tmp-flicker/` (git-ignored, never committed) showing
   the highest-scoring offenders, so a human can spot-check the metric.

## Finish

Write `docs/FLICKER-TEST-REPORT.md`: the metric definition, the calibration table
(original / fast / full), what the numbers say about whether the `pack(by_animation=True)` +
`--prompt animation` recipe is enough, and the concrete threshold you would put on the critter
go/no-go gate.

## Rules

No GPU, no model calls, no services; do not restart `hd-production`, `hd-repair`, ComfyUI or
anything else. Read-only outside this repo — no writes under `/ai`, no edits in `~/fallout2`, no
touching production/repair state. Do NOT commit; Claude/Yan review and commit. No network, no adb,
no tokens printed.
