# Wall-residual report: the last 40 `unmatched_wall` and 2 `invented_texture` (2026-10-30)

Brief: `docs/FREEBUFF-TASK-WALL-RESIDUAL.md`. Everything below was measured read-only against the
live repair output `/ai/wasteland-hd/repair-lightning4` (log summary `clean 8818,
minor_invention 562, invented_texture 2, unmatched_wall 40`) and the production root
`/ai/wasteland-hd/production-lightning4`. Nothing under `/ai` was written, no GPU or ComfyUI call
was made, no service was used, the branch is `agent/walls`.

## Result

The 42 residuals are two different defects, and neither of them is a wall pair that still breaks:

1. **The 40 `unmatched_wall` directions ship fine art; their stored verdict was stale.** All 40 are
   `accepted=True` with `provenance={'kind': 'junction_lock', 'unmatched_neighbour': True}` — they
   took the `pending` branch of `process_group`, whose gate snapshot was measured *before the other
   side of that junction was corrected later in the same run*. `reconcile_walls` fixed the pairs but
   never measured the pending side again, so the final report counted an `unmatched_wall` the
   shipping frames no longer have. Re-measured today, **every junction of every one of the 40 frames
   is at or below 16.25** (limit 18.0) and all 40 frames score `clean` on their own texture.
2. **The 2 `invented_texture` directions ship nothing at all** (`art/intrface/bomb1`,
   `art/intrface/pipx`: the frames are missing from both the production and the repair archive).
   The AI frames are rightly rejected (QA ratio 5.07 and 6.19 against a limit of 1), but the
   *original-art fallback itself* was then rejected by the invention gate, so the direction never
   resolved. This is a measurement bug, described below.

Fixes (CPU-only, in `wasteland_hd/invention.py` and `wasteland_hd/repair.py`):

| Change | What it does |
|---|---|
| `invention.source_exact()` + `score()` exemption | A frame that is *exactly* the original art resampled 2x (nearest) cannot contain invention, so its verdict is `clean` — unless it is a wall whose junction is over the limit (still `unmatched_wall`) or a floor tile (the calibrated floor path is untouched). |
| `repair.Controller.refresh_wall_gates()`, called at the end of `reconcile_walls` | Re-gates every accepted wall direction whose stored gate still carries issues, against the frames that actually ship, and drops the `unmatched_neighbour` marker. The report then counts the shipping art. |

Projected live summary after the change, replayed offline against the read-only artifacts
(`tmp-walls/after.json`, produced by running the real `refresh_wall_gates` and the real `gate()` on
the shipped frames):

| | clean | minor_invention | invented_texture | unmatched_wall |
|---|---:|---:|---:|---:|
| live today | 8,818 | 562 | 2 | 40 |
| after this change | **8,860** | 562 | **0** | **0** |

*(Measured with the colour half of `wall_seam_delta` only. The follow-up section at the end of this
report adds a line term to that same number, which re-flags 9 of the 40 and 254 other frames — all
cleared by the corrective path it describes, so the summary above is the converged state, reached
through `_repair_wall_group` instead of unchanged shipping art.)*

## The 40 wall directions, one row each

`delta then` is the largest historical seam delta for that frame recorded in the live
`wall-seam-cache.json` (measured during the ladder/reconcile, against an earlier version of the
frames), `delta now` is that same junction with the frames that ship, `worst now` is the worst of
*every* junction the frame takes part in today (any partner, any map, any offset — including
mixed HD/original pairs). Rungs: every one of the 40 went production candidate → batch denoise
0.55 → batch denoise 0.4 → `junction_lock` (locked from the production candidate except `dorwygk1`,
locked from `denoise-0-g2`); all ladder rungs failed the gate with `unmatched_wall`, the lock could
not clear the junction alone because the *other* side still carried AI pixels, so the direction was
kept with the pending marker. Contact sheets: `tmp-walls/sheets/<job>-<frame>.png`.

| frame | job | junction that fired (partner) | map(s) | delta then | delta now | worst now | decision |
|---|---|---|---|---:|---:|---:|---|
| bar005 | walls-0000 | dv1021 | geckpwpl.map | 29.9 | 0.0 | 7.3 | (a) re-gate |
| ca003 | walls-0000 | dv1021 | mbase34.map | 30.9 | 0.0 | 9.0 | (a) re-gate |
| ca005 | walls-0000 | dv1021 | mbase12.map | 31.5 | 0.4 | 9.9 | (a) re-gate |
| ca007 | walls-0000 | dv1019 | gecktunl.map | 113.7 | 0.7 | 8.4 | (a) re-gate |
| ca010 | walls-0000 | dv1020 | mbase12.map | 32.5 | 0.0 | 6.0 | (a) re-gate |
| ca012 | walls-0000 | dv1021 | mbase12.map | 38.9 | 0.0 | 5.8 | (a) re-gate |
| ca013 | walls-0000 | dv1021 | mbase12.map | 36.4 | 0.0 | 7.0 | (a) re-gate |
| ca025 | walls-0000 | dv1019 | mbase12.map | 89.4 | 0.0 | 6.7 | (a) re-gate |
| ca032 | walls-0000 | dv1021 | mbase34.map | 24.2 | 0.0 | 6.1 | (a) re-gate |
| ca044 | walls-0000 | dv1021 | mbase12.map | 22.8 | 0.9 | 5.2 | (a) re-gate |
| ca051 | walls-0000 | dv1062 | mbase12.map | 23.4 | 0.0 | 3.8 | (a) re-gate |
| ca053 | walls-0000 | dv1019 | mbase34.map, newrst.map | 114.4 | 0.0 | 14.5 | (a) re-gate |
| ca054 | walls-0000 | dv1019 | mbase12.map, mbase34.map | 46.9 | 0.0 | 9.2 | (a) re-gate |
| ca055 | walls-0000 | dv1019 | vault15.map | 141.8 | 0.0 | 9.4 | (a) re-gate |
| ca056 | walls-0000 | dv1019 | newrst.map | 105.1 | 7.2 | 14.2 | (a) re-gate |
| ca057 | walls-0000 | dv1019 | depolv1.map, newrst.map | 115.2 | 0.0 | 16.2 | (a) re-gate |
| ca058 | walls-0000 | dv1019 | mbase12.map | 91.8 | 0.0 | 14.9 | (a) re-gate |
| ca059 | walls-0000 | dv1019 | mbase12.map | 77.1 | 0.0 | 10.7 | (a) re-gate |
| ca060 | walls-0000 | dv1019 | mbase12.map | 65.5 | 0.0 | 8.6 | (a) re-gate |
| ca061 | walls-0000 | dv1020 | mbase12.map, mbase34.map | 25.3 | 0.0 | 11.3 | (a) re-gate |
| ca071 | walls-0000 | dv1019 | klatoxcv.map | 60.1 | 0.0 | 9.7 | (a) re-gate |
| ca079 | walls-0001 | dv1021 | newrst.map | 33.6 | 0.0 | 9.3 | (a) re-gate |
| ca080 | walls-0001 | dv1021 | gecktunl.map | 27.9 | 0.7 | 10.0 | (a) re-gate |
| cath16 | walls-0001 | fen2001 | denbus2.map | 28.0 | 0.6 | 2.9 | (a) re-gate |
| dorwygk1 | walls-0001 | dv1019 | geckjunk.map, geckpwpl.map, gecktunl.map | 36.5 | 0.0 | 7.4 | (a) re-gate |
| dorwygk3 | walls-0001 | dv1019 | geckpwpl.map | 38.9 | 0.0 | 1.7 | (a) re-gate |
| dorwygk4 | walls-0001 | dv1021 | geckpwpl.map | 44.6 | 0.1 | 11.0 | (a) re-gate |
| dv1000 | walls-0001 | dv1019 | newrst.map, vault15.map | 43.8 | 0.0 | 11.4 | (a) re-gate |
| dv1001 | walls-0001 | dv1019 | depolv1.map | 35.9 | 0.0 | 2.7 | (a) re-gate |
| dv1003 | walls-0001 | dv1019 | mbase12.map, newrst.map | 34.5 | 0.0 | 4.6 | (a) re-gate |
| dv1004 | walls-0001 | dv1019 | klatoxcv.map, vault15.map, mbase12/34.map, geck*.map, newrst.map, v15_orig.map | 56.8 | 0.0 | 10.8 | (a) re-gate |
| dv1005 | walls-0001 | dv1019 | klatoxcv.map | 36.2 | 0.0 | 3.3 | (a) re-gate |
| dv1011 | walls-0001 | dv1039 | v15_orig.map, vault15.map | 25.5 | 0.0 | 1.8 | (a) re-gate |
| dv1020 | walls-0001 | ca071 | mbase34.map | 44.8 | 0.0 | 3.9 | (a) re-gate |
| dv1021 | walls-0001 | dv1039 | klatoxcv.map, mbase12.map | 48.5 | 0.0 | 7.0 | (a) re-gate |
| dv1049 | walls-0001 | dv1019 | gecktunl.map | 28.5 | 0.0 | 6.4 | (a) re-gate |
| fen2001 | walls-0002 | ware003 | geckjunk.map | 80.8 | 1.8 | 6.8 | (a) re-gate |
| fence01 | walls-0002 | nec106 | sfchina.map | 45.8 | 1.6 | 13.5 | (a) re-gate |
| fence11 | walls-0002 | fence15 | newrst.map, redment.map | 28.6 | 0.0 | 9.2 | (a) re-gate |
| fence15 | walls-0002 | nec139 | sfchina.map | 55.1 | 0.0 | 16.2 | (a) re-gate |

Decision for all 40: **(a) a CPU-only fix, no art change.** The junction correction already
happened on both sides during reconciliation (`junction_lock` pulls each side's own junction band
back to exact source colours, so a locked/locked or locked/original pair measures `hd_gap −
src_gap ≈ 0` by construction); what was missing is a measurement of the pair *after* that. Re-gating
the shipping frames — what `refresh_wall_gates()` now does — turns all 40 into `clean`. Permanent
original-art fallback (option b) was not needed for any of them and would have thrown away art that
passes every junction check, including the mixed pairs (`fence01` next to production-shipped
`nec106`, `ca071` next to `dv1019`, and so on — the "worst now" column is measured across every
partner each frame actually touches, whatever that partner ships).

The contact sheets (`tmp-walls/sheets/`, one per direction: original pair on the left of the red
junction box, what ships on the right, zoomed strip below) were reviewed one by one. In every sheet
the two columns continue across the junction the same way the source art does; the sheet captions
carry the then/now deltas above.

## The 2 interface directions

| frame | job | what the AI produced | what the gate said | decision |
|---|---|---|---|---|
| `art/intrface/bomb1/0_0.png` | intrface-0000 | scanline icon redrawn on a lifted grey backdrop; production QA ratio 5.07 | ladder rungs `invented_texture` (novel_fraction 0.51, flat_damage 0.57); then the **original-art fallback** was also rejected `invented_texture` | **(b) permanent original-art fallback** |
| `art/intrface/pipx/0_0.png` | intrface-0000 | "Pip-Boy 2000" lettering redrawn with a grey backdrop and new strokes; production QA ratio 6.19 | ladder rungs `invented_texture` (novel_fraction 0.38, flat_damage 0.47); original-art fallback also rejected | **(b) permanent original-art fallback** |

Why the fallback was rejected: both sprites are 1-px scanlines with transparent rows, so they have
*no interior at all* (`distance_transform_edt(opaque)` never exceeds 3 px, so the gate's mask falls
back to the whole silhouette). For non-floor frames there is no alpha band to exclude the
silhouette, and the nearest-neighbour 2x step (opaque 228 next to invisible black) is far sharper
than any bicubic resample of the source, so the *untouched* game art measured `novel_fraction`
0.51/0.38 — invented texture where nothing was generated. `invention.source_exact()` fixes exactly
this: a frame identical to the original resampled 2x is `clean`. With the fix, the next repair run
takes the existing non-floor fallback path (`original_game_art`, `repair.py`) and these two
directions resolve and ship the original art instead of shipping nothing. Both are interface icons,
so there are no wall junctions to re-check on them; the mixed-pair rule cannot be violated by a
frame with no neighbours.

## GPU retry recipe (described, not run — and not needed)

No GPU retry beats the two decisions above: the 40 wall frames already pass every junction they
touch, and re-running the model on them can only risk re-breaking a pair that reconciliation had to
work to fix. For the two interface icons a retry *could* in principle preserve the scanlines (a
sheet of just those two frames, denoise 0.35–0.4, a prompt that pins "thin horizontal scanlines on
transparent background, no backdrop"), but a UI icon is exactly the case where the original art is
already the right answer, and the current model produced a lifted backdrop twice in a row at both
rungs. I recommend keeping the original-art fallback and spending the GPU budget elsewhere.

## Klamath interiors and two Arroyo buildings

`tmp-walls/interiors.py` renders every Klamath map/elevation that contains walls — 11 of them:
`klacanyn-e0`, `kladwtwn-e0`, `klagraz-e0`, `klamall-e0`, `klaratcv-e0/e1/e2`,
`klatoxcv-e0/e1/e2`, `klatrap-e0` — plus two Arroyo buildings, `artemple-e0` (Temple of Trials) and
`arvillag-e0` (the village), chosen from `ar*.map` by having real interiors rather than
`arcaves.map`, which is a cave. Each file `tmp-walls/klamath/<map>-e<elev>-compare.png` shows
**original 2x | what ships after the change** side by side, drawn from the map's own tile grid and
wall objects; downscaled review copies are in `tmp-walls/klamath/preview/`.

Junctions on those maps, measured against the frames that ship (`tmp-walls/klamath-junctions.json`):

| map set | junctions measured | over the 18.0 limit |
|---|---:|---:|
| all `kla*.map` + `artemple.map` + `arvillag.map` | 2,712 | **0** |

**Named Klamath junctions still above the limit: none.** The toxic-cave concentration the previous
report saw (`dv1019` against `dv1004`, `ca060`, `ca062` in `klatoxcv`) is now 0.0–10.8 on every
placement, and the four frames involved (`dv1004`, `dv1005`, `ca060`, `ca071`) are in the table
above. For Klamath specifically, this change alters no pixels: the wall art that ships is the same
art, only its verdict record is refreshed, so the right-hand column is what the game already
receives today.

## Tests

- New: `tests/test_invention.py::test_source_exact_art_is_not_invention` (synthetic 1-px scanline
  sprite: measured `novel_fraction` 0.90 before the exemption, `clean` with it; `unmatched_wall`
  still applies to untouched art with a bad junction; floor tiles keep `unmatched_floor`; any frame
  that differs from the source is measured exactly as before) and
  `tests/test_repair.py::test_reconciliation_refreshes_a_verdict_taken_before_the_neighbour_changed`
  (synthetic two-piece wall fixture: the pending side keeps its stale gate, reconciliation fixes
  the neighbour, `refresh_wall_gates` re-gates the pending side to a passing verdict and makes it
  durable in `state.json`).
- Whole suite: `python3 -m pytest -q` → **124 passed, 0 failed** (5.33 s).
- Tiles/floors: no floor code path changed. `score()` gained one branch that is unreachable for
  `art/tiles/*` because `repair.gate` (and `select`) always pass `floor=True` there, so the
  exemption can never fire for tiles; `edge_lock`, `source_floor_2x`, `BORDER_ERROR_LIMIT`,
  `SEAM_DELTA_LIMIT` and every floor threshold are untouched, and all existing calibration tests
  pass unmodified.

## Follow-up: the light 1-px line on every wall piece edge (2026-10-30, same branch)

A reviewer looking at `tmp-walls/klamath/klamall-e0-compare.png` found a faint 1-px **light
vertical line running the whole wall height on every wall piece edge** in the centre of the HD
column (x 3000–3300, y 1700–2000), including across the dark diagonal beam, with none of it in the
original column. The 6-px junction colour band had passed every one of those junctions.

**It is in the shipped frames, not in the compositor.** The line lands exactly on a placed sprite's
edge (`nec015`'s left edge over `nec094`, canvas x 3000, box `(3000, 1904)-(3032, 2146)`), and the
frame itself carries the rim:

| `art/walls/nec015/0_0.png` | left-edge step (outer − inward, median over rows) | right-edge step |
|---|---:|---:|
| production candidate | −1.0 | +9.3 |
| **what repair ships** | **+21.3** | **+15.7** |
| original art | 0.0 | 0.0 |

The alpha is binary and matches the source exactly (`partial=0`, 218 opaque rows = 2 × 109), so it
is not a partial-alpha or premultiplied halo: the **redraw brightens the outermost opaque column**
itself, and wherever that edge overlaps a neighbour it reads as a light line. Measured over all
1,673 shipped wall frames (median outer-inner step of the HD minus the same step on the original,
positive part only, so a missing dark outline does not count): **415 frames over 6, 281 over 10,
132 over 15**, p90 13.9, worst `block2` +31 / `dv1023` +48 — while the same measurement on the
original FRMs is 0 everywhere. Its own junctions measure a colour gap of only 0.00–3.19 across all
188 partners, which is what let it ship.

### Why the old metric could not see it

`wall_seam_delta` compares each junction-band pixel with the **nearest pixel of the other sprite**.
A bright rim on a frame's own edge is a discontinuity *within* one piece, one pixel from the
contact: averaged over the 6-px band and both sides, it dilutes to a colour gap of 0–12 (nec015's
junctions measured 0.00–2.28 colour while carrying a 21-px rim).

### The new term

`invention.wall_junction_terms()` now returns two numbers and `wall_seam_delta` takes the worse of
both against the same `WALL_SEAM_DELTA_LIMIT` (both are extra brightness breaks, 0-255):

- **colour** — the existing band gap, unchanged;
- **line** — for each junction row, the sharpest 1-2 px column step within ±2 px of *that row's own*
  contact (so a diagonal junction is measured where its pieces meet, not over the bounding box),
  aggregated as the median over rows (a line running the whole contact lifts nearly every row at
  the same column; texture that changes column from row to row cancels), less the same statistic
  on the original pair at 2x, and taken in both draw orders since which piece covers which is the
  engine's.

`invention.WALL_SEAM_METRIC = 2` versions the measured quantity and is part of the seam-cache key
(`repair.wall_delta`), so a repair run re-measures every junction instead of trusting deltas the
previous metric produced.

Calibrated on **all 26,401 unique shipped junctions** (`tmp-walls/calibrate_line.py`,
`tmp-walls/line-calib.json`):

| term | median | p90 | p99 | max | over 18.0 |
|---|---:|---:|---:|---:|---:|
| colour | 0.00 | 3.80 | 10.53 | 17.96 | **0** |
| line | 0.00 | 7.33 | 19.83 | 50.33 | **351 (1.33%)** |

The colour term is unchanged (still nothing over the limit); the 351 are all line and touch **263
distinct wall frames**. A 1,500-junction random sample flags 5 (0.3%), so the term is not measuring
generic HD sharpness. Spot-checks are real lines: `tfenc13 | nec137` has isolated bright pixels of
169 and 205 where the original is a smooth 85/96 for the same rows; `nec015 | nec094` steps 42 → 21
across the piece edge where the original is flat at 30.

**What this changes in the section above.** Of the 40 directions re-gated there, 9 (`bar005`,
`ca053`, `ca054`, `ca057`, `ca058`, `ca060`, `dv1000`, `fence11`, `fence15`) also carry a junction
whose line term is over the limit (19.2–32.2); the `delta now` column there is the colour term and
is unchanged, still at most 16.25. A live run would therefore re-flag those directions
`unmatched_wall` at the first gate and then correct them, rather than shipping them as-is.

### The fix

`repair.edge_restore(hd, original)` pulls the outermost opaque pixel of each of the four sides back
to the 2x source colour, **only where the HD edge steps up away from its inward neighbour more than
the source's edge does there** (`EDGE_RESTORE_TOLERANCE = 4.0`), one pixel layer deep, alpha
untouched. A frame whose edge already agrees with the source — or one that steps *down* at the
silhouette the way the source's own outline does — is returned unchanged, so the source's dark
outline is never transplanted onto an edge that has none and no interior texture is touched
(`nec015`: 517 of 7,744 px changed, rim 21.3 → 9.0, alpha byte-identical).

It is wired in as the **first, gentlest step of `_repair_wall_group`** (edge-restore →
junction-lock → original art, each re-gated before it is accepted), so `reconcile_walls` corrects a
rim before it reverts a whole band or a whole direction; `select.source_roots` also offers the
`edge-restore` directory, and the provenance/fallback kind is `edge_restore`.

Verification, replayed offline against the read-only artifacts:

| | what happens |
|---|---|
| chain over all 351 flagged junctions (`tmp-walls/verify_chain.py --all`) | **114 clear at `edge_restore`, 229 at `junction_lock`, 8 at original art, 0 uncleared** |
| Klamath + 2 Arroyo buildings, 2,712 junctions (`tmp-walls/interiors.py`) | **21 over the limit as shipped** (0 under the old metric — the lines were invisible to it), **15 after `edge_restore`**; the rest clear at `junction_lock` |
| the reported `klamall-e0` region, light-line columns (bright 1-px ridge running ≥ 50 rows) | original **0**, shipped **12** (longest run 170 rows), shipped + `edge_restore` **1** (68 rows) |

`tmp-walls/region-check.png` shows that crop three ways (original | ships today | ships with
`edge_restore`) and `tmp-walls/region-zoom3.jpg` is the 3x-nearest zoom on the worst line; the
review of the zoom reads the seams in the middle panel as absent from the left and right panels,
which is what the counts above say.

### Open: the purple/blue tint on the stained wall

Not fixed here, measured: over the same region, the shipped column is slightly darker (−3.0 per
channel) and slightly cooler — mean chroma shift **B−G +0.18**, and **1.7% of wall pixels are
bluer than the original by more than 8 in B−G (mean shift +14.9)** against 0.8% shifted the other
way. The colour gates (`colour_shift`, `qa.badness`) only see the regional mean, which is far under
their limits, so a chroma-specific check on large flat stains is the right follow-up; it is a
colour-fidelity issue rather than a geometry one and does not belong in the edge/rim fix.

## Tests

- New: `tests/test_invention.py::test_wall_seam_delta_sees_a_rim_the_colour_band_cannot`
  (both pieces bright at the contact: colour term 0.0 — the old measurement is structurally blind
  to it — while the line term is 70 and `wall_seam_delta` exceeds the limit; a 1-px rim on one
  frame is caught too; the same lift in the source pair is still the baseline),
  `tests/test_repair.py::test_edge_restore_pulls_a_bright_rim_back_to_the_source` and
  `test_edge_restore_keeps_a_step_down_at_the_silhouette`.
- Whole suite: `python3 -m pytest -q` → **127 passed, 0 failed** (5.5 s).
- Unchanged: tiles/floors, `edge_lock`, `source_floor_2x`, `SEAM_DELTA_LIMIT`, and the colour half
  of `wall_seam_delta` (same code path, same values; the term it is now maxed against only fires
  on junctions the colour band already passes).

## Reproduction (follow-up)

```bash
python3 tmp-walls/calibrate_line.py            # both terms over 26,401 junctions -> line-calib.json
python3 tmp-walls/verify_chain.py --all        # edge_restore -> junction_lock -> original art
python3 tmp-walls/interiors.py                 # re-rendered Klamath/Arroyo + junctions JSON
python3 tmp-walls/region_check.py klamall.map 0 2900 1600 3700 2400   # the reported crop, 3 ways
python3 tmp-walls/junction_crops.py art/walls/tfenc13/0_0.png art/walls/nec137/0_0.png 14 -4
python3 -m pytest -q                           # 127 passed
```

Everything above is read-only against `/ai`; the fix ships as code, and the renders apply it in
memory so the right-hand column shows what a repair run would now send.

## Reproduction

```bash
python3 tmp-walls/analyze.py        # junctions of the 42 as they ship -> analysis.json
python3 tmp-walls/cache_mine.py     # historical over-limit rows from the live cache -> cache-mine.json
python3 tmp-walls/sheets.py         # 42 contact sheets (orig pair | shipped pair)
python3 tmp-walls/verify_after.py   # offline replay of the new code -> after.json
python3 tmp-walls/interiors.py      # Klamath + Arroyo compares and klamath-junctions.json
python3 -m pytest -q                # 124 passed
```

Notes for resuming are in `tmp-walls/PROGRESS.md`.
