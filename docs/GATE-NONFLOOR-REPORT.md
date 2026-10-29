# Gate calibration report: walls / scenery / items / misc (2026-10-29)

Brief: `docs/FREEBUFF-TASK-GATE-NONFLOOR.md` (rules from `docs/CODEX-TASK-GATE-NONFLOOR.md`).
Work started by a Codex agent, finished by the previous Claude session, report written by this one.
I did not commit: while this report was being finished the reviewing session committed the gate
code plus an earlier snapshot of it as `a095e52` (13:14), so the corrections below are unstaged
on top of that commit.

## Outcome

- `invention.score(..., kind=...)` now has per-kind rules for sprites; `kind` is wired through
  `repair.py`; the non-floor fallback is the original game art (never `source_floor_2x`, never an
  invented texture).
- 92 visually labelled examples (walls 33, scenery 27, items 15, misc 17 — ≥15 per category),
  **87/92 agree with the gate**; all 5 disagreements are the gate being *stricter* than my label,
  so they cost a retry rather than shipping a bad frame.
- World-seam fail rate (4,997 frames, walls/scenery/items/misc): **274 `invented_texture`
  (5.48%) before → 123 (2.46%) after**; flagged (minor + invented) 307 (6.14%) → 251 (5.02%).
- Tiles/floors are untouched: all 20 tile calibration labels from `docs/REPAIR-GATE-REPORT.md`
  still hold with byte-identical numbers, and the floor code path is unchanged.
- Tests: gate files 18/18 green. Full suite `python3 -m pytest -q` = **101 passed, 0 failed**
  (final run at 13:17). While this report was being written the suite showed `93 passed,
  8 failed`, every failure in `tests/test_temporal_stage.py` — a different, still-in-progress
  task (`docs/FREEBUFF-TASK-CRITTER-TEMPORAL.md`, untracked files) whose module imports no
  `invention`/`repair` code; that session has since fixed it. Details in *Tests* below.

## What the per-kind rules do (`wasteland_hd/invention.py`)

| Where | Rule |
|---|---|
| `KINDS`, `kind_of()` (`invention.py:30`, `:42-47`) | `art/{walls,scenery,items,misc}/...` maps to its own kind; anything else (tiles, interface, critters, unknown paths) returns `'tiles'` and therefore keeps the floor behaviour. |
| `score()` (`invention.py:142-151`) | Validates `kind`, raises on unknown ones; `sprite = kind != 'tiles'`; `floor` is forced off for sprites so `unmatched_floor` can never be produced for a sprite. |
| Alpha-boundary band (`invention.py:72-83`) | The 4 px band (`SPRITE_BAND`) hugging the alpha boundary is ignored for novelty and flat damage — silhouette anti-aliasing is not invention. The band is only trusted when a core of at least `SPRITE_CORE_MIN=64` px *and* `SPRITE_CORE_SHARE=25%` of the mask survives it, otherwise it is disabled, so stroke art with no core at all (fences, grids, blades) still fails on wholly invented detail. |
| Absolute evidence for tiny/thin frames (`_sprite_verdict`, `invention.py:121-139`) | With `mask_pixels < SPRITE_MIN_AREA=400` a ratio means nothing: require `novel_pixels >= SPRITE_MIN_PIXELS_TEXTURE=40` for `invented_texture`, `>= SPRITE_MIN_PIXELS=12` for `minor_invention`, else `clean`. Flat-area damage is ignored there (a bicubic-resized thin stroke reads as flat source, so faithful wires would fail everywhere). |
| Emissive lift (`invention.py:98-103`) | Dark novelty is suppressed where the HD frame is at least `EMISSIVE_LIFT=25` brighter than the source over a neighbourhood: lamps, screens and other lit detail that gained glow are not "invented dark structure". |
| Sprite thresholds (`invention.py:135-139`) | `invented_texture` if `novel_fraction > 0.08` or `largest_novel_region > 0.055` or (`flat_damage > 0.045` and cluster `> 0.03`) or `dark_novel > 0.05`; `minor_invention` if `novel_fraction > 0.04` or cluster `> 0.015`; else `clean`. Sprites also skip `border_error`/`seam_delta` (`invention.py:151,159`), which are floor-neighbourhood measures. |

## Wiring (`wasteland_hd/repair.py`)

- `repair.py:142-145` — the single `invention.score(...)` call in the whole module (it scores
  every frame of every group in `gate()`), now passes `floor=rel.startswith('art/tiles/')` **and**
  `kind=invention.kind_of(rel)`. So walls, scenery, items, misc *and* tiles are all scored with
  their own kind.
- `repair.py:148` — `invented_texture` / `unmatched_floor` still go to the retry ladder.
- `repair.py:367-393` — `edge_lock` and `source_floor_2x` fallbacks stay behind
  `group['art'].startswith('art/tiles/')`, i.e. floor-only, as before.
- `repair.py:394-405` — **new**: a non-floor group with no accepted candidate re-sizes the
  untouched game frames 2x (nearest) and gates them; on success the provenance is
  `{'kind':'original_game_art'}`. No `source_floor_2x` for sprites.
- `repair.py:415` — `original_game_art` is reported as a `fallback`, next to `edge_lock` and
  `source_floor_2x`.

## Sheets reviewed

- Contact sheets (orig 2x nearest | HD pairs): `tmp-audit/nonfloor/sheets/<kind>/<metric>-N.png`,
  4 kinds × 7 metrics (`novel_fraction`, `largest_novel_region`, `novel_window`, `flat_damage`,
  `dark_novel`, `border_error`, `random`) × 60 pairs each = **84 sheets / 1,680 labelled pairs**;
  manifest `tmp-audit/nonfloor/sheet-manifest.json`.
- Zoomed label sheets used for the per-example labelling (integer zoom, segmented for tall
  sprites, gate numbers plus diagnostics printed under each cell):
  `tmp-audit/nonfloor/label/{walls,scenery,items,misc}-{1,2}.png` with per-cell diagnostics in
  `tmp-audit/nonfloor/label/<kind>.json`.
- My labels: `tmp-audit/nonfloor/labels.json` (92 entries). Raw scoring runs:
  `tmp-audit/nonfloor/baseline.json` (before, floor thresholds) and
  `tmp-audit/nonfloor/after.json` (after, per-kind) — both 4,997 rows, zero errors; regeneration
  log `tmp-audit/nonfloor/scan_after.log`.
- The table below is `tmp-audit/nonfloor/table.txt`, verified row-by-row against
  `labels.json`, `after.json` and `baseline.json`.

## Calibration table: 92 visually labelled examples

Columns: category | example | **my label** (visual review) | **gate verdict** (new per-kind rules) |
**floor-gate verdict** (old, floor thresholds — what the loop would have done) | `novel_fraction` |
`largest_novel_region` | `flat_damage` | `dark_novel` | novel px | mask px (the numbers are the
new per-kind measurements at 2x).

| # | category | example | my label | gate verdict (new) | floor gate (old) | novel_frac | largest_region | flat_damage | dark_novel | novel px | mask px |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | items | `spear/2_0` | invented_texture | invented_texture | invented_texture | 0.265 | 0.265 | 0.000 | 0.000 | 91 | 344 |
| 2 | items | `spear/3_0` | invented_texture | invented_texture | invented_texture | 0.134 | 0.131 | 0.000 | 0.000 | 46 | 344 |
| 3 | items | `bkshlf1/0_0` | minor_invention | minor_invention | invented_texture | 0.060 | 0.007 | 0.100 | 0.001 | 1877 | 31060 |
| 4 | items | `club/0_0` | minor_invention | minor_invention | invented_texture | 0.091 | 0.082 | 0.000 | 0.000 | 19 | 208 |
| 5 | items | `spear/0_0` | minor_invention | minor_invention | invented_texture | 0.113 | 0.038 | 0.000 | 0.000 | 39 | 344 |
| 6 | items | `spear/5_0` | minor_invention | minor_invention | invented_texture | 0.044 | 0.003 | 0.000 | 0.000 | 15 | 344 |
| 7 | items | `walllkr3/0_1` | minor_invention | minor_invention | invented_texture | 0.037 | 0.023 | 0.003 | 0.000 | 107 | 2871 |
| 8 | items | `wshelv1/0_0` | minor_invention | minor_invention | minor_invention | 0.041 | 0.008 | 0.045 | 0.002 | 1263 | 31083 |
| 9 | items | `ammobox1/0_3` | clean | clean | clean | 0.000 | 0.000 | 0.014 | 0.000 | 0 | 6414 |
| 10 | items | `ammobox1/0_4` | clean | clean | clean | 0.000 | 0.000 | 0.017 | 0.000 | 3 | 7447 |
| 11 | items | `geiger/0_0` | clean | clean | clean | 0.000 | 0.000 | 0.000 | 0.000 | 0 | 217 |
| 12 | items | `locker2/0_2` | clean | clean | clean | 0.013 | 0.004 | 0.021 | 0.004 | 125 | 9348 |
| 13 | items | `locker2/0_3` | clean | clean | clean | 0.015 | 0.012 | 0.027 | 0.011 | 167 | 11258 |
| 14 | items | `rock/5_0` | clean | clean | clean | 0.000 | 0.000 | 0.000 | 0.000 | 0 | 176 |
| 15 | items | `walllkr1/0_1` | clean | clean | clean | 0.000 | 0.000 | 0.000 | 0.000 | 0 | 2864 |
| 16 | misc | `exitgrd3/0_0` | invented_texture | invented_texture | invented_texture | 0.471 | 0.447 | 0.641 | 0.136 | 2895 | 6144 |
| 17 | misc | `exitgrd4/0_0` | invented_texture | invented_texture | invented_texture | 0.735 | 0.735 | 0.858 | 0.180 | 4516 | 6144 |
| 18 | misc | `spear/2_0` | invented_texture | invented_texture | invented_texture | 0.243 | 0.243 | 0.388 | 0.000 | 130 | 536 |
| 19 | misc | `spear/5_0` | invented_texture | invented_texture | invented_texture | 0.232 | 0.232 | 0.404 | 0.000 | 106 | 456 |
| 20 | misc | `plntspik/0_0` | minor_invention | minor_invention | invented_texture | 0.214 | 0.214 | 0.357 | 0.000 | 12 | 56 |
| 21 | misc | `plntspik/3_0` | minor_invention | minor_invention | invented_texture | 0.281 | 0.281 | 0.375 | 0.000 | 18 | 64 |
| 22 | misc | `exitgrd5/0_0` | clean | clean | clean | 0.012 | 0.001 | 0.685 | 0.000 | 75 | 6348 |
| 23 | misc | `exitgrd8/0_0` | clean | clean | clean | 0.007 | 0.001 | 0.637 | 0.000 | 46 | 6300 |
| 24 | misc | `explodeb/0_1` | clean | clean | clean | 0.000 | 0.000 | 0.000 | 0.000 | 0 | 968 |
| 25 | misc | `explodeb/0_5` | clean | clean | clean | 0.014 | 0.014 | 0.034 | 0.000 | 3 | 207 |
| 26 | misc | `explodeg/0_6` | clean | clean | minor_invention | 0.014 | 0.014 | 0.005 | 0.000 | 3 | 212 |
| 27 | misc | `explodeg/0_7` | clean | clean | minor_invention | 0.074 | 0.037 | 0.000 | 0.000 | 4 | 54 |
| 28 | misc | `grndtro/5_0` | clean | clean | clean | 0.000 | 0.000 | 0.000 | 0.000 | 0 | 34 |
| 29 | misc | `knife/2_5` | clean | clean | invented_texture | 0.067 | 0.067 | 0.000 | 0.000 | 4 | 60 |
| 30 | misc | `knife/3_6` | clean | clean | clean | 0.024 | 0.012 | 0.131 | 0.000 | 2 | 84 |
| 31 | misc | `molotov/0_1` | clean | clean | clean | 0.000 | 0.000 | 0.000 | 0.000 | 0 | 76 |
| 32 | misc | `trnblk/0_0` | clean | clean | clean | 0.000 | 0.000 | 0.000 | 0.000 | 0 | 984 |
| 33 | scenery | `bvs10/0_0` | invented_texture | invented_texture | invented_texture | 0.212 | 0.204 | 0.306 | 0.000 | 2556 | 12029 |
| 34 | scenery | `doorbars/0_3` | invented_texture | invented_texture | invented_texture | 0.094 | 0.028 | 0.452 | 0.049 | 211 | 2248 |
| 35 | scenery | `doorbars/0_5` | invented_texture | invented_texture | invented_texture | 0.163 | 0.085 | 0.457 | 0.029 | 687 | 4210 |
| 36 | scenery | `elev11/0_0` | invented_texture | invented_texture | invented_texture | 0.196 | 0.195 | 0.310 | 0.001 | 12688 | 64586 |
| 37 | scenery | `shuttl37/0_0` | invented_texture | invented_texture | invented_texture | 0.562 | 0.512 | 0.888 | 0.000 | 10040 | 17876 |
| 38 | scenery | `wdbeam29/0_0` | invented_texture | invented_texture | invented_texture | 0.360 | 0.360 | 0.675 | 0.108 | 936 | 2602 |
| 39 | scenery | `geckm06/0_3` | minor_invention | minor_invention | minor_invention | 0.043 | 0.012 | 0.036 | 0.006 | 603 | 13948 |
| 40 | scenery | `sfroad07/0_0` | minor_invention | minor_invention | minor_invention | 0.042 | 0.008 | 0.023 | 0.001 | 7163 | 170733 |
| 41 | scenery | `wire01/0_0` | minor_invention | minor_invention | invented_texture | 0.029 | 0.022 | 0.003 | 0.000 | 36 | 1263 |
| 42 | scenery | `curtain/0_5` | clean | clean | clean | 0.000 | 0.000 | 0.004 | 0.000 | 0 | 4987 |
| 43 | scenery | `door01/0_0` | clean | minor_invention | invented_texture | 0.049 | 0.009 | 0.421 | 0.006 | 498 | 10186 | <-- gate stricter
| 44 | scenery | `door10/0_1` | clean | clean | clean | 0.037 | 0.010 | 0.316 | 0.001 | 1578 | 43093 |
| 45 | scenery | `ffield02/0_0` | clean | clean | clean | 0.000 | 0.000 | 0.000 | 0.000 | 0 | 22015 |
| 46 | scenery | `firpit02/0_2` | clean | clean | clean | 0.004 | 0.001 | 0.008 | 0.000 | 47 | 12973 |
| 47 | scenery | `geckm02/0_5` | clean | clean | clean | 0.000 | 0.000 | 0.000 | 0.000 | 4 | 27431 |
| 48 | scenery | `geckm06/0_0` | clean | clean | clean | 0.039 | 0.011 | 0.031 | 0.004 | 546 | 13948 |
| 49 | scenery | `mdesk1/0_0` | clean | clean | clean | 0.000 | 0.000 | 0.006 | 0.000 | 1 | 6946 |
| 50 | scenery | `monitr4/0_0` | clean | clean | clean | 0.000 | 0.000 | 0.675 | 0.000 | 0 | 40 |
| 51 | scenery | `monitr6/0_0` | clean | clean | clean | 0.000 | 0.000 | 0.600 | 0.000 | 0 | 40 |
| 52 | scenery | `num02/0_0` | clean | minor_invention | invented_texture | 0.029 | 0.015 | 0.151 | 0.005 | 71 | 2414 | <-- gate stricter
| 53 | scenery | `rock04/0_0` | clean | clean | clean | 0.000 | 0.000 | 0.000 | 0.000 | 0 | 6037 |
| 54 | scenery | `shorcar2/0_0` | clean | clean | clean | 0.000 | 0.000 | 0.004 | 0.000 | 0 | 45569 |
| 55 | scenery | `stake08/0_0` | clean | clean | invented_texture | 0.000 | 0.000 | 0.000 | 0.000 | 0 | 752 |
| 56 | scenery | `v13secr2/0_10` | clean | clean | clean | 0.001 | 0.000 | 0.007 | 0.000 | 47 | 72073 |
| 57 | scenery | `wdoor2/0_4` | clean | clean | invented_texture | 0.013 | 0.010 | 0.012 | 0.000 | 19 | 1447 |
| 58 | scenery | `wdoor5/0_4` | clean | clean | invented_texture | 0.001 | 0.001 | 0.019 | 0.000 | 2 | 1447 |
| 59 | scenery | `wdoor7/0_5` | clean | clean | clean | 0.000 | 0.000 | 0.003 | 0.000 | 0 | 11794 |
| 60 | walls | `black06/0_0` | invented_texture | invented_texture | invented_texture | 0.365 | 0.363 | 0.385 | 0.277 | 5012 | 13738 |
| 61 | walls | `ca058/0_0` | invented_texture | invented_texture | invented_texture | 0.060 | 0.057 | 0.041 | 0.000 | 338 | 5642 |
| 62 | walls | `enclav01/0_0` | invented_texture | invented_texture | invented_texture | 0.285 | 0.271 | 0.512 | 0.016 | 3648 | 12814 |
| 63 | walls | `nec030/0_0` | invented_texture | invented_texture | invented_texture | 0.197 | 0.127 | 0.310 | 0.067 | 2500 | 12678 |
| 64 | walls | `nec075/0_0` | invented_texture | invented_texture | invented_texture | 0.249 | 0.127 | 0.406 | 0.010 | 3172 | 12726 |
| 65 | walls | `ship09/0_0` | invented_texture | invented_texture | invented_texture | 0.413 | 0.408 | 0.669 | 0.002 | 2822 | 6840 |
| 66 | walls | `shutl05/0_0` | invented_texture | invented_texture | invented_texture | 0.054 | 0.054 | 0.080 | 0.000 | 162 | 3017 |
| 67 | walls | `shutl09/0_0` | invented_texture | invented_texture | invented_texture | 0.050 | 0.036 | 0.064 | 0.000 | 152 | 3017 |
| 68 | walls | `shutl18/0_0` | invented_texture | invented_texture | invented_texture | 0.035 | 0.034 | 0.058 | 0.000 | 105 | 3017 |
| 69 | walls | `shutl20/0_0` | invented_texture | invented_texture | invented_texture | 0.070 | 0.070 | 0.101 | 0.000 | 212 | 3017 |
| 70 | walls | `vgat014/0_0` | invented_texture | invented_texture | invented_texture | 0.053 | 0.051 | 0.235 | 0.000 | 189 | 3555 |
| 71 | walls | `ship07/0_0` | minor_invention | minor_invention | minor_invention | 0.041 | 0.017 | 0.325 | 0.000 | 278 | 6840 |
| 72 | walls | `stb005/0_0` | minor_invention | minor_invention | minor_invention | 0.045 | 0.020 | 0.056 | 0.000 | 592 | 13285 |
| 73 | walls | `ybk1006/0_0` | minor_invention | minor_invention | invented_texture | 0.037 | 0.029 | 0.082 | 0.001 | 164 | 4379 |
| 74 | walls | `cath16/0_0` | clean | minor_invention | invented_texture | 0.028 | 0.020 | 0.067 | 0.004 | 185 | 6626 | <-- gate stricter
| 75 | walls | `cni2015/0_0` | clean | clean | clean | 0.000 | 0.000 | 0.000 | 0.000 | 0 | 9938 |
| 76 | walls | `enclav08/0_0` | clean | clean | clean | 0.032 | 0.011 | 0.299 | 0.000 | 216 | 6844 |
| 77 | walls | `fence03/0_0` | clean | clean | clean | 0.000 | 0.000 | 0.761 | 0.000 | 0 | 163 |
| 78 | walls | `fence04/0_0` | clean | clean | invented_texture | 0.000 | 0.000 | 0.181 | 0.000 | 0 | 149 |
| 79 | walls | `fence05/0_0` | clean | clean | clean | 0.017 | 0.011 | 0.949 | 0.000 | 3 | 177 |
| 80 | walls | `fence08/0_0` | clean | clean | clean | 0.023 | 0.006 | 0.845 | 0.000 | 4 | 174 |
| 81 | walls | `fence09/0_0` | clean | clean | invented_texture | 0.027 | 0.009 | 0.730 | 0.000 | 6 | 222 |
| 82 | walls | `fence10/0_0` | clean | clean | clean | 0.000 | 0.000 | 0.792 | 0.000 | 0 | 183 |
| 83 | walls | `fence11/0_0` | clean | clean | invented_texture | 0.027 | 0.009 | 0.830 | 0.000 | 6 | 224 |
| 84 | walls | `nec002/0_0` | clean | minor_invention | invented_texture | 0.033 | 0.025 | 0.106 | 0.001 | 212 | 6384 | <-- gate stricter
| 85 | walls | `nec073/0_0` | clean | clean | clean | 0.000 | 0.000 | 0.001 | 0.000 | 0 | 6038 |
| 86 | walls | `nec132/0_0` | clean | clean | clean | 0.000 | 0.000 | 0.008 | 0.000 | 0 | 4166 |
| 87 | walls | `nec149/0_0` | clean | minor_invention | invented_texture | 0.026 | 0.025 | 0.084 | 0.004 | 139 | 5364 | <-- gate stricter
| 88 | walls | `sada003/0_0` | clean | clean | clean | 0.000 | 0.000 | 0.000 | 0.000 | 0 | 5774 |
| 89 | walls | `ship10/0_0` | clean | clean | clean | 0.031 | 0.012 | 0.223 | 0.001 | 215 | 6844 |
| 90 | walls | `shutl04/0_0` | clean | clean | clean | 0.000 | 0.000 | 0.000 | 0.000 | 0 | 3017 |
| 91 | walls | `temple92/0_0` | clean | clean | clean | 0.000 | 0.000 | 0.000 | 0.000 | 0 | 9361 |
| 92 | walls | `vcn2002/0_0` | clean | clean | clean | 0.000 | 0.000 | 0.000 | 0.000 | 0 | 13158 |

## Agreement

| category | agreement with my label |
|---|---|
| walls | 30/33 |
| scenery | 25/27 |
| items | 15/15 |
| misc | 17/17 |
| **total** | **87/92 (94.6%)** |

All five disagreements are the conservative direction — my label `clean`, gate
`minor_invention`: `scenery/door01-0_0`, `scenery/num02-0_0`, `walls/cath16-0_0`,
`walls/nec002-0_0`, `walls/nec149-0_0`. In every one of them the new gate is stricter than my
visual read, so it costs a retry instead of shipping. There is **no row where the gate passed a
frame I labelled as invented**.

Read against the last column, **18 of the 92 labelled examples were actively wrong under the
floor thresholds and are right now**: e.g. `misc/knife/2_5` and `scenery/stake08`, `wdoor2`,
`wdoor5` were `invented_texture`, `walls/fence04`, `fence09`, `fence11` were `invented_texture`
on thin fence strokes, and `items/spear`, `items/club`, `items/bkshlf1`, `items/walllkr3`,
`misc/plntspik`, `scenery/wire01`, `walls/ybk1006` were `invented_texture` where they are really
`minor_invention`. `misc/explodeg/0_6`, `0_7` were flagged `minor_invention` for being clean.
Zero rows moved in the wrong direction (new verdict never cleaner than my label).

## Before/after fail rates per kind (world-seam, 4,997 frames)

`fail` = `invented_texture` (the verdict that rejects a frame and burns a retry);
`flagged` = `invented_texture` + `minor_invention` (minor additions are accepted by owner
policy but still worth watching).

| kind | n | before: invented | before: flagged | after: invented | after: flagged |
|---|---|---|---|---|---|
| walls | 1,657 | 77 (4.65%) | 87 (5.25%) | 59 (3.56%) | 84 (5.07%) |
| scenery | 2,707 | 115 (4.25%) | 131 (4.84%) | 57 (2.11%) | 116 (4.29%) |
| items | 298 | 7 (2.35%) | 8 (2.68%) | 2 (0.67%) | 8 (2.68%) |
| misc | 335 | 75 (22.39%) | 81 (24.18%) | 5 (1.49%) | 43 (12.84%) |
| **total** | **4,997** | **274 (5.48%)** | **307 (6.14%)** | **123 (2.46%)** | **251 (5.02%)** |

Verdict transitions (before → after, 4,997 rows): `invented_texture → invented_texture` 123,
`invented_texture → minor_invention` 101, `invented_texture → clean` 50,
`minor_invention → minor_invention` 27, `minor_invention → clean` 6,
`clean → clean` 4,690 — and **nothing moved towards failing** (no `clean` or `minor` became
`invented_texture`).

Expected behaviour on the next production run over this material: 123 of 4,997 non-floor frames
retry instead of 274; 151 frames leave the retry bucket (101 into `minor_invention`, 50 into
`clean`) and 6 more clear from `minor_invention` to `clean`, all within Yan's policy that small
local additions are acceptable. `misc` was the worst offender under floor thresholds (22%
invented on small sprite sheets like knives, spears, explosions) and is now 1.5%.

## Tiles behaviour unchanged

- For `kind='tiles'`, `sprite` is `False`, so the new band is `np.zeros_like(mask)` and both
  `novel & ~band` and `damage & ~band` are no-ops; `dark_novel` is computed exactly as before;
  no sprite-only keys are added to the result; `floor`, `BORDER_ERROR_LIMIT`, `SEAM_DELTA_LIMIT`
  and every floor threshold are untouched (`invention.py:148-168`).
- The 20 tile calibration labels from `docs/REPAIR-GATE-REPORT.md` were rescored with the new
  code: `tmp-audit/nonfloor/tile-labels.txt` ends with **`ALL 20 CALIBRATION LABELS HOLD`** —
  10 `invented_texture`, 3 `minor_invention`, 7 `clean`, every metric identical before/after
  (e.g. `brick14 e=0.113(0.113) c=0.111(0.111) f=0.366(0.366)`).
- Floor-only fallbacks stay floor-only (`repair.py:367-393`).

## Tests

- Gate scope: `python3 -m pytest -q tests/test_invention.py tests/test_repair.py` → **18 passed**.
  New tests: `test_kind_mapping_and_validation`, `test_tiny_sprite_needs_absolute_evidence`,
  `test_sprite_band_never_blinds_a_sprite_with_a_core`,
  `test_banded_sprite_without_core_still_fails_invented_detail`,
  `test_emissive_lift_is_not_dark_structure`, and
  `tests/test_repair.py::test_sprite_direction_gates_by_kind_and_falls_back_to_original_art`
  (covers the `original_game_art` fallback end to end).
- Whole suite: `python3 -m pytest -q` → **101 passed, 0 failed** (rerun at 2026-10-29 13:17,
  after the separate temporal task landed its own fix).
- Earlier in this session the same command reported `93 passed, 8 failed`, all 8 in
  `tests/test_temporal_stage.py` (`test_static_noise_converges_and_stays_deterministic`,
  `test_colour_transfer_equalises_material_without_blurring_texture`,
  `test_static_copy_takes_the_keyframe_where_the_original_agrees`,
  `test_moving_poses_keep_their_own_geometry`, `test_keyframe_is_the_pose_closest_to_all_the_others`,
  `test_direction_over_the_cap_falls_back_to_original_art`,
  `test_rejects_bad_alpha_and_dangerous_destinations`,
  `test_cli_writes_the_before_after_report`).
  `wasteland_hd/temporal_stage.py` is untracked work for a **different** task
  (`docs/FREEBUFF-TASK-CRITTER-TEMPORAL.md`) and imports only `PIL`, `numpy`, `scipy.ndimage`,
  `gamefiles` etc. — no `invention`, no `repair` — so the gate change was never implicated; its
  9 tests were fixed in that session, not here.

## Still open

- The 5 conservative `clean → minor_invention` disagreements above; leave them as retries unless
  Yan prefers them shipping.
- Nothing was committed by me; `a095e52` (reviewing session, 13:14) already holds
  `invention.py`, `repair.py`, `tests/test_invention.py`, `tests/test_repair.py` and the first
  snapshot of this report. Still unstaged: these report corrections plus the unrelated temporal
  task's files (`wasteland_hd/temporal_stage.py`, `tests/test_temporal_stage.py`,
  `wasteland_hd/__main__.py`, `.gitignore`) and the new `docs/GATE-LIVE-CHECK.md`.
