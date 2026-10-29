# Wall-junction seam gate and repair report (2026-10-29)

## Result

Walls now use the map's real object placement to detect neighbouring pieces, measure added HD discontinuity at each junction, reject `unmatched_wall`, and correct the junction band before falling back to original game art. The final reconciliation pass rechecks the actual pair that will ship, including a mixed AI/fallback pair.

The CPU correction projection reduced current failures as follows:

| Source set | Scored junctions | Before `>18` | After `>18` | Klamath before | Klamath after |
|---|---:|---:|---:|---:|---:|
| world-seam pack currently represented on the phone | 68,745 | 1,216 | 18 | 135 | 0 |
| Lightning `walls-0000` through `walls-0005` | 69,996 | 2,120 | 6 | 129 | 0 |

This is a CPU-only projection of `junction_lock`, not a write into `/ai`. It uses the same junction mask and blend as the repair implementation, with corrected frames overlaid ahead of the source trees and all junctions measured again. The live repair pass may first find a better model rung; its final reconciliation has the same source-exact correction and original-art fallback available.

## Map geometry and pair index

`wall_pairs(game)` parses the tile number, screen offsets, frame, rotation, elevation, and FID from each wall object. It places every FRM using Fallout 2's hex-to-screen transform plus the FRM direction offsets and bottom-centre object anchor. Two placed frames form a junction when their opaque masks touch or overlap after a one-pixel dilation.

The full index contains 75,490 placements across 120 maps: 46,815 on elevation 0, 18,474 on elevation 1, and 10,201 on elevation 2. It includes 74,092 different-frame junctions and 1,398 repeated-frame junctions.

Klamath contributes these indexed placements:

| Map | Junctions |
|---|---:|
| `kladwtwn.map` | 747 |
| `klagraz.map` | 12 |
| `klamall.map` | 947 |
| `klaratcv.map` | 1,569 |
| `klatoxcv.map` | 987 |
| `klatrap.map` | 13 |

## Measurement and calibration

`wall_seam_delta` composites both HD pieces at their real relative offset, samples the six-pixel 2x band on both sides of the alpha junction, and subtracts the same colour-step measurement from nearest-neighbour 2x source art. Posts, door frames, and deliberate source seams therefore provide their own baseline.

The visual calibration set contains 36 labelled junctions from `tmp-wallseam/sheets/cal-*.png`: 21 unmatched, 14 matched, and one unclear. The lowest labelled unmatched wall junction was 14.0. Ordinary matched walls reached 13.9. Three matched fence contacts reached 22.0, while the 21.8-30 wall contacts in that range showed invented pillars, lighting, or changed surface patterns. A limit of 18.0 catches the broken wall family without rejecting those continuing fence contacts. Labels and reasons are recorded in `tmp-wallseam/labels.json`.

Before correction, the current source distributions were:

| Source set | p50 | p90 | p95 | p99 | max |
|---|---:|---:|---:|---:|---:|
| world-seam | 0.00 | 7.01 | 11.02 | 21.39 | 57.41 |
| Lightning walls | 1.24 | 8.43 | 12.29 | 27.69 | 141.84 |

After correction they were:

| Source set | p50 | p90 | p95 | p99 | max |
|---|---:|---:|---:|---:|---:|
| world-seam | 0.00 | 3.31 | 5.11 | 9.53 | 29.46 |
| Lightning walls | 0.00 | 3.08 | 4.59 | 8.03 | 27.80 |

## Klamath findings

Every Klamath map/elevation containing walls was rendered as original, world-seam, and current Lightning art under `tmp-wallseam/interiors/`, with review JPEGs under `tmp-wallseam/interiors/preview/`. The 11 triplets are `klacanyn-e0`, `kladwtwn-e0`, `klagraz-e0`, `klamall-e0`, `klaratcv-e0/e1/e2`, `klatoxcv-e0/e1/e2`, and `klatrap-e0`.

The world-seam pack has 135 flagged Klamath placements: 73 in `klamall`, 29 in `klaratcv`, 26 in `kladwtwn`, and 7 in `klatoxcv`. Frequent broken families include `nec121|nec102`, `ca058|ca088`, `nec103|nec095`, and `ca017|ca055`.

The current Lightning walls have 129 flagged Klamath placements: 89 in `klatoxcv`, 23 in `klamall`, 13 in `kladwtwn`, 3 in `klaratcv`, and 1 in `klagraz`. The toxic-caves concentration is dominated by `dv1019` against `dv1004`, `ca060`, and `ca062`; those failures are visible in the marked Lightning interior renders. The correction projection leaves no Klamath junction over the calibrated limit in either source set.

## Repair behaviour

The repair gate now indexes wall neighbours separately from floor adjacency. A wall direction is measured only against neighbours already accepted during the ladder. If a candidate's remaining issue is `unmatched_wall`, `junction_lock` blends its own junction bands toward exact nearest-neighbour 2x source colours and gates it again. Invented interiors do not qualify for this correction.

After all jobs resolve, `reconcile_walls` measures every final shipping pair. This catches neighbours accepted later and pairs changed by fallback. It corrects sides still carrying AI pixels, rechecks for up to eight rounds, and can replace a stubborn direction with original game art. Source-art fallbacks are not repeatedly modified.

The existing floor path remains separate. The complete suite passes: `121 passed in 10.30s`; focused wall/invention/repair coverage passes `26 passed in 0.72s`.

## Reproduction

The read-only analysis scripts and artifacts are under `tmp-wallseam/`:

```bash
python3 tmp-wallseam/measure.py worldseam /ai/wasteland-hd/world-seam/hd
python3 tmp-wallseam/measure.py prodwalls \
  /ai/wasteland-hd/production-lightning4/jobs/walls-{0000,0001,0002,0003,0004,0005}/hd
python3 tmp-wallseam/simulate.py worldseam /ai/wasteland-hd/world-seam/hd
python3 tmp-wallseam/simulate.py prodwalls \
  /ai/wasteland-hd/production-lightning4/jobs/walls-{0000,0001,0002,0003,0004,0005}/hd
python3 tmp-wallseam/measure.py after-worldseam \
  tmp-wallseam/after-worldseam /ai/wasteland-hd/world-seam/hd
python3 tmp-wallseam/measure.py after-prodwalls \
  tmp-wallseam/after-prodwalls \
  /ai/wasteland-hd/production-lightning4/jobs/walls-{0000,0001,0002,0003,0004,0005}/hd
```
