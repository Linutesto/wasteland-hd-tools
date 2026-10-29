# Codex task D: calibrate the invention gate for walls, scenery and items (2026-10-29)

From Claude. Repo `~/wasteland-hd-tools` (main, HEAD 5fc760e, 78 tests). Read
`docs/REPAIR-GATE-REPORT.md`, `docs/REPAIR-BATCH-REPORT.md`, `wasteland_hd/invention.py`, `repair.py`.

## Why
The gate (`invention.score`) was calibrated on floor tiles only. Production
(`/ai/wasteland-hd/production-lightning4`, running) finishes the 11 tile jobs around 11:00 and then
does walls (1673 files), scenery (1863), items, misc, interface... The repair loop will then gate
sprites with alpha silhouettes, thin parts, text, and lit/emissive details using floor thresholds.

Owner policy (Yan): small local additions (a plant, a pipe, a skull) are acceptable → `minor_invention`.
Totally invented textures/materials or replaced structure → fail and retry. For sprites add:
silhouette/alpha is already forced exact; text must not be invented or garbled where the original
had legible text (flag `text_changed` as invented_texture only if confident, else minor + flag).

## Material
`/ai/wasteland-hd/world-seam/hd/art/{walls,scenery,items,misc}` — 4,600+ older full-Qwen HD frames
(same 2x size/alpha contract), originals via `qa.original_frame(Game('/home/yan/fallout2/gamedata'), cache, rel)`.
Use them read-only for calibration (they are not production output).

## Do
1. Score all of them with the current gate per category; write distributions.
2. Build contact sheets (orig 2x nearest | HD pairs, labelled) into the repo's ignored `tmp-audit/nonfloor/`:
   top-60 by each metric per category and a random 60 per category. LOOK at them (view the images).
3. Make `score(..., kind=<art type>)` use per-category thresholds/rules where needed: ignore
   novel edges in a band next to the alpha boundary (silhouette anti-aliasing is not invention),
   handle tiny sprites (minimum opaque area before ratios mean anything), emissive/lit pixels.
   Floor behaviour for `art/tiles` must stay byte-identical (existing tests + the 20 calibration labels).
   Wire `kind` through `repair.py`. For non-floor art the final fallback is the original art
   (no `source_floor_2x`), as production already does (`original_game_art_entire_direction`).
4. Write a labelled calibration table from your own visual review: ≥15 examples per category with
   your label, the verdict, and the numbers. Be conservative: when unsure, retry rather than ship.
5. Tests for the new kind handling; whole suite green. Commit if `.git` is writable, else leave unstaged.

Rules: no GPU, no services, no writes outside this repo, don't restart the live loops.
End by writing `docs/GATE-NONFLOOR-REPORT.md` with the table, contact sheet paths for Claude to
review, and per-category expected fail rates on world-seam.
