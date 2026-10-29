# Freebuff task: temporal stage for critter animations (2026-10-29)

Repo `~/wasteland-hd-tools` (branch main, 92 tests). Goal: make critter animations pass the flicker
gate so the 224,365-frame critter generation can start. Production is paused before critters by
`~/fallout2/tools/hd-critter-guard.sh` until this works.

## Read first
- `docs/FLICKER-TEST-REPORT.md` — the metric (`python -m wasteland_hd flicker`), the gate
  (per animation: `broad` ≤ 2.5, `material` ≤ 3.0, `flicker` ≤ 6.0; direction cap 2x the animation
  mean) and the NO-GO verdict: rows/by_animation packing removes catastrophic frames but each
  frame still re-invents patterns/colours (hfprimab trouser pattern, hmwarrab costume colours,
  nmpeasab speckle).
- `docs/ANIMATION-STABILITY.md`, `wasteland_hd/temporal.py` (existing CPU stabiliser, gains little).
- `wasteland_hd/{sheets,upscale,production,flicker}.py`, `docs/AUTONOMOUS-PRODUCTION.md`.
- Evidence (read-only): `/ai/wasteland-hd/critter-test/{rows4,rows4full,fast4,...}/hd` and how they were
  made (`/ai/wasteland-hd/critter-test/run4.sh`, `python -m wasteland_hd upscale --work … && … cut --work …`).
  Calibration animations: hmwarraa, hmwarrab, hfprimab, nmpeasab.

## Build a temporal stage (try in this order, measure each with the flicker gate)
1. **Keyframe-anchored identity transfer (CPU, deterministic):** per direction pick the best frame
   (lowest broad/material vs its neighbours, or the most frontal), then for every other frame map
   colour/texture from the keyframe onto the static/corresponding regions (use the original FRM
   correspondence like `temporal.py`'s matcher; per-material-class colour transfer for `material`,
   low-frequency residual transfer for `broad`), keeping each frame's own geometry and the exact
   source alpha. Must not blur detail into mush: compare contact sheets.
2. **Keyframe-conditioned re-render (GPU, small):** re-render failing directions with the keyframe as
   a reference image (Qwen Image Edit 2511 accepts multiple images — `TextEncodeQwenImageEditPlus`
   with image2 = keyframe; see `upscale.py` graphs), or as part of the same sheet row, so the model
   copies the keyframe's materials. Same Lightning settings otherwise.
3. Per-direction fallback when nothing passes: original art (`original_game_art_entire_direction`).

## GPU rules (strict)
ComfyUI at http://127.0.0.1:8188 is shared with the live `hd-production` and repair loop. You may use
it ONLY for step 2 on the four calibration animations, at most **60 sheet renders total**, only through
the repo's own upscale path (which waits for an empty queue), writing ONLY under the new directory
`/ai/wasteland-hd/critter-temporal/`. Never stop/restart services, never call ComfyUI's interrupt or
free endpoints, never touch other `/ai/wasteland-hd` folders, qwen38 stays off.

## Deliver
- `wasteland_hd/temporal_stage.py` (+ CLI subcommand `temporal`), tests with synthetic frames
  (`tests/test_temporal_stage.py`), whole suite green (`python3 -m pytest -q`).
- A results table: per calibration animation, before (rows4 / rows4full) vs after each method:
  broad / material / flicker / pass-fail, plus contact sheets and GIFs under `tmp-temporal/` (gitignored).
- A clear go/no-go for starting critter production with your stage, and the exact production
  integration you propose (where it plugs into `production.py`, budgets, fallbacks).
- Do NOT commit. Finish by writing `docs/CRITTER-TEMPORAL-REPORT.md`.
