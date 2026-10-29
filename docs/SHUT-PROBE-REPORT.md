# Shut probe report: why `art/tiles/shut*` never passes, and the recipe that does (2026-10-29)

Brief: `docs/FREEBUFF-TASK-SHUT-PROBE.md`. All 95 `shut*` tiles in production job `tiles-0007`
failed every Lightning attempt and every repair rung and shipped as `source_floor_2x`.
Tool: `tools/shut_probe.py` (new; the only code I wrote). Nothing was committed.

## Outcome

- **The gate is not wrong. The model is wrong.** Every model attempt changes the picture; the
  closest recipe still fails the QA structure gate at 1.31x the limit, while deterministic resamples
  of the same art pass with 5-20x margin.
- **The family is not texture art at all.** All 95 files are the *same* 42x18, fully opaque,
  two-colour placeholder panel reading **"UNUSED ART"** in white pixel type on dark red. No map
  places any of the 95 on a floor, roof or object grid. `tiles.lst` indices 2422-2516.
- **Recommendation: keep the soft fallback.** `source_floor_2x` (already shipped) is the best
  available "HD" for this family: `qa_badness` 0.173 vs a limit of 1.0. If an exact look is wanted,
  plain nearest-2x scores 0.000. No model recipe is close.
- Side finding worth a separate fix: because all 95 tiles share one image, production spent
  **475 candidate frames** (3 attempts x 95) and repair another **190** deciding one placeholder.
  Deduplicating identical source art (or exempting placeholder tiles) would have made the family
  free.

## What the tiles are (step 1, before any render)

| property | value |
|---|---|
| files | `art/tiles/shut001.frm` ... `shut095.frm`, one frame each |
| frame | 42x18, alpha 100% opaque, 756 px |
| colours | exactly 2: `(168,0,0)` x 612 px, `(236,236,236)` x 144 px |
| mean RGB / std | (181, 45, 45) / 100.37 - identical for all 95 |
| distinct original images | **1** (all 95 decode to the same 756-px buffer) |
| map placements | **0** - no floor, roof or object grid in any `maps/*.map` uses indices 2422-2516 |
| map-adjacency neighbours | **0 of 95** (`invention.cached_adjacency` has no pair) |

Zoomed (`tmp-shut/rungs-x6.png`, `tmp-shut/orig-x8.png`): a dark-red panel, 1-px red border, and two
lines of white block letters spelling `UNUSED ART`. This is the placeholder sheet artists get for
unused tile slots, not a texture. Nothing in it has detail for a diffusion model to restore - the
only content is 1-bit typography.

## Step 1: which gate rejects them, and with which metric

Contact sheets: `tmp-shut/shut-contact.png` (24 tiles x: original 2x | production attempt-00 |
denoise 0.55 | denoise 0.40 | `source-floor` fallback), `tmp-shut/rungs-x6.png` (one tile, 6x zoom).
I looked at both, plus `tmp-shut/recipes-contact.png` for the new renders.

Full per-tile table: `tmp-shut/scan-summary.txt`; raw metrics: `tmp-shut/scan.json`
(`tools/shut_probe.py scan`, 95 tiles x 6 rungs, 1.6 s, offline).

| rung | n | gate verdicts | QA failures | median novel_frac | median flat_damage | median border_error | median qa_badness |
|---|---|---|---|---|---|---|---|
| production attempt-00 (denoise 1.0) | 95 | `unmatched_floor` 95 | 95 | 0.128 | 0.177 | **64.76** | **7.534** |
| production attempt-01 (seed 7) | 95 | `unmatched_floor` 95 | 95 | 0.156 | 0.198 | 67.61 | 7.688 |
| production attempt-02 (seed 99) | 95 | `unmatched_floor` 95 | 95 | 0.137 | 0.197 | 61.47 | 6.460 |
| repair denoise 0.55 | 95 | `clean` 94, `unmatched_floor` 1 | 95 | 0.000 | 0.000 | 9.14 | **1.435** |
| repair denoise 0.40 | 95 | `clean` 95 | 95 | 0.000 | 0.000 | 7.99 | **1.226** |
| shipped fallback (`source_floor_2x`) | 95 | `clean` 95 | 0 | 0.000 | 0.000 | 9.64 | 0.173 |

Where the frames actually die, by rule:

1. **Production (denoise 1.0):** the verdict is `unmatched_floor`, fired by
   `border_error > BORDER_ERROR_LIMIT (12)` at 2x - median 64.8, all 95 above the limit. `seam_delta`
   is 0.0 for all 95, because these tiles have no map neighbours, so no seam was ever measured: the
   verdict name is misleading here. The same frames also clear the floor `invented_texture`
   conditions (`largest_novel_region` ~0.18 > 0.055, `flat_damage` ~0.18 > 0.045), but
   `invention.score` checks the floor border rule first, so the reported label is the seam one.
   QA independently rejects all 95 (median 7.53): at 2x the panel is not red any more - the model
   draws a gold/wooden sign with sand, a cast shadow and a second invented object behind it
   (`tmp-shut/rungs-x6.png`, second cell).
2. **Repair rungs (0.55 / 0.40):** the invention gate passes them (`novel_fraction` and
   `flat_damage` both 0.000; median border 9.1 / 8.0 is under 12). They die on **QA**, at the floor
   structure test (`qa.FLOOR_STRUCTURE_LIMIT = 6.0`), not on colour and not on invention. Split on
   the 8 probe tiles (`tmp-shut/qa-split.json`):

   | rung | mean colour shift (limit 12) | structure difference (limit 6) | qa_badness |
   |---|---|---|---|
   | production | 41.4 - 72.6 (3.5 - 6.1x) | 56.7 - 75.0 (9.5 - 12.5x) | 9.5 - 12.5 |
   | denoise 0.55 | 8.2 - 10.2 (0.69 - 0.85x) | 8.2 - 10.2 (1.36 - 1.70x) | 1.36 - 1.70 |
   | denoise 0.40 | 6.5 - 7.4 (0.54 - 0.61x) | 6.9 - 7.6 (1.14 - 1.27x) | 1.14 - 1.27 |

   Colour passes at both rungs with room to spare; the blur-based structure test is what fails.
   On 1-bit typography that means the model's letter edges are measurably softer than the source,
   which is visible in `tmp-shut/rungs-x6.png` cells 3-4 (hazy outlines, slightly lifted red).

So the repair ladder did the right thing: it kept the least damaged candidates, found neither
acceptable, and fell back. All 95 groups in
`/ai/wasteland-hd/repair-lightning4/jobs/tiles-0007/state.json` are `accepted: true` with
`fallback: source_floor_2x`.

## Step 2: alternative recipes (13 of the 20 allowed renders)

All through the repo path: `sheets.render_sheet` -> `upscale.Comfy` (8-step Lightning, cfg 1, the
same graph the CLI builds, with only `denoise` changed) -> `sheets.cut_sheet`, each render preceded
by `production.Controller.wait_ready`, i.e. an empty ComfyUI queue. No interrupt/free endpoint was
called; nothing under `/ai` outside `/ai/wasteland-hd/shut-probe/` was written.

Tiles: the 8 worst production frames (`shut092, shut094, shut023, shut056, shut076, shut093,
shut043, shut075`) - any 8 are equivalent, since the sources are byte-identical.

| recipe | what changed | renders | gate verdicts (8 tiles) | median novel / flat | median border | median QA (colour x/12, structure x/6) |
|---|---|---|---|---|---|---|
| A-dn030-art-per-tile | denoise 0.30, default art prompt, **one tile per 256 sheet** | 8 | `clean` 8 | 0.000 / 0.000 | 9.08 | **1.428** (0.72, 1.43) |
| B-dn020-art | denoise 0.20, default prompt, 8 tiles on one sheet | 1 | `clean` 8 | 0.000 / 0.000 | 8.39 | **1.306** (0.63, 1.31) |
| C-dn100-shut | denoise 1.00, **"UNUSED ART" prompt**, 8 tiles on one sheet | 1 | `unmatched_floor` 8 | 0.000 / 0.000 | 20.12 | 2.953 (1.48, 2.95) |
| D-dn030-shut | denoise 0.30, "UNUSED ART" prompt, 8 tiles on one sheet | 1 | `clean` 8 | 0.000 / 0.000 | 8.49 | **1.349** (0.64, 1.35) |
| F-dn030-shut-rs2 | denoise 0.30, "UNUSED ART" prompt, **render-scale 2** | 1 | `unmatched_floor` 8 | 0.000 / 0.001 | 16.05 | 2.516 (1.24, 2.52) |
| *(no model)* nearest 2x | deterministic, 95 tiles | 0 | `clean` 95 | 0.000 / 0.000 | 9.93 | **0.000** |
| *(no model)* lanczos 2x | deterministic, 95 tiles | 0 | `clean` 95 | 0.000 / 0.000 | 2.57 | **0.952** (0.48, 0.95) |
| *(no model)* soft fallback | `source_floor_2x`, 95 tiles | 0 | `clean` 95 | 0.000 / 0.000 | 9.63 | **0.173** (0.09, 0.17) |

Per-tile values: `tmp-shut/recipes.json`; QA split: `tmp-shut/recipe-qa-split.json`; visual:
`tmp-shut/recipes-contact.png`; budget ledger: `/ai/wasteland-hd/shut-probe/budget.json` (13 calls).

What the numbers say:

- **Every axis moved the wrong way or barely at all.** Lowering denoise from 1.0 to 0.30 takes QA
  from 7.5 to 1.35 and stops the invented background, but it does not get under 1.0 - the floor is
  the VAE round-trip, which cannot reproduce 1-px glyph edges at any denoise. Denoise 0.20 is only
  1.31. All of them remain *worse than a plain lanczos resize* (0.95) and 8x worse than the shipped
  soft fallback (0.17).
- **A prompt that names the content does help at high denoise** (C vs production: border 20 vs 65,
  QA 2.95 vs 7.5 - no invented sand, the panel stays red), but it still rewrites glyphs: at denoise
  1.0 the letters get eaten and a few are re-shaped into holes. At 0.30 the same prompt is
  indistinguishable from the default prompt (D 1.349 vs B 1.306).
- **One tile per sheet** (A) removes any neighbour context but does not help: 1.428, the *worst* of
  the low-denoise group. These tiles are too small (42x18) for the sheet layout to be the problem.
- **Render-scale 2 is worse** (F 2.516 with border 16.05): with a 2x sheet the model has fewer
  pixels of context per letter and blobs the glyphs together.

I did not spend the last 7 renders: no recipe was within 30% of the QA limit, and every candidate is
the same 42x18 image, so another seed or denoise step cannot produce the missing detail - there is
no detail in the source to restore.

## Step 3: recommendation

**Keep the soft fallback for `shut*`.** Concretely, what is already shipped is the right answer:

- `source_floor_2x` - QA 0.173, clean invention verdict, exact nearest-neighbour alpha and locked
  panel colours. It is visually a faithful 2x of the placeholder sign.
- If a sharper read is preferred, plain nearest-2x (QA 0.000) is exact; plain lanczos (0.952) passes
  but is already at 95% of the colour limit and softens the 1-px border.
- Do **not** spend further model attempts on this family. Best model result: QA 1.306, and visually
  softer than lanczos (`tmp-shut/recipes-contact.png`, column B).

Concrete changes worth making elsewhere (all proposed only - I did not touch
`invention.py`, `repair.py` or `production.py`, which other agents own):

1. **Deduplicate identical source art in production.** `shut001-095` decode to one 756-px image.
   Hashing original frames (FRM bytes or decoded RGBA) before planning would have cut 285 production
   candidates and 190 repair candidates to 3 + 2. This is the largest win available here.
2. **Skip the model for 1-bit placeholder art.** A tile whose opaque pixels contain <= 2 distinct
   colours and which no map places on any grid gets nothing from diffusion; route it straight to
   `source_floor_2x`. That reproduces today's shipped result without the retry ladder.
3. **Fix the verdict name, not the threshold.** `invention.score` reports `unmatched_floor` for
   `art/tiles/*` when `border_error > 12`, even with no map adjacency and `seam_delta == 0.0`
   (95/95 frames here). That label sent this task looking for a seam problem that cannot exist: the
   border measurement is of the sprite perimeter, not of a neighbour contact. Suggested rule in
   `invention.score`: only use the border/seam branch when the frame has at least one map-adjacent
   neighbour (`seam_delta` supplied), and otherwise report the existing `invented_texture` branch -
   which these production frames independently satisfy (`largest_novel_region` 0.18 > 0.055).
   This is a diagnostics change: it keeps rejecting exactly the same frames (they fail QA anyway,
   7.53) and only stops mislabelling them.
4. **No threshold was found that should be loosened.** Loosening the structure limit from 6.0 to
   8.2 would admit recipe D (structure 8.09) - but lanczos at 5.71 and the soft fallback at 1.04
   show the limit is doing real work: it separates a faithful resample from a diffusion redraw of
   typography, which is exactly what this gate was calibrated to catch.

## Rules followed / verification

- 13 sheet renders total (`budget.json`), all on 8 tiles; the first attempt at the batch was stopped
  at 8 renders when I found it packed one sheet per tile, then continued under the same ledger.
- No service was stopped or restarted, no interrupt/free endpoint called; renders waited for an
  empty queue (`production.Controller.wait_ready`). The live roots were only read.
- Writes: `/ai/wasteland-hd/shut-probe/` (renders + sheets) and `tmp-shut/` (analysis, gitignored).
- `python3 -m py_compile tools/shut_probe.py` passes; `scan`, `sheets`, `baselines`, `render`,
  `summary`, `recipe-sheet` were each run to produce the files cited above. No test in the repo
  suite touches this tool, and no repo module was modified.

## Limitations

- 8 of 95 tiles were rendered (their sources are byte-identical, so this is not a sampling risk, but
  the report states 8, not 95).
- Only production's 8-step Lightning LoRA at cfg 1 was tested (that is what production and repair
  use); the 30-step full mode was not, and it is the one setting that could conceivably render a
  pixel-font better - though the QA structure limit, not step count, is the binding constraint at
  denoise 0.4 already.
- `seam_delta` was never exercised: these tiles have no map neighbours.
- 7 renders of the allowed 20 were left unspent, with the reason recorded above.
