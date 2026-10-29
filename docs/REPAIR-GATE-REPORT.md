# Repair gate report — 2026-10-29

## Built

- `wasteland_hd/invention.py` measures native and 2× novelty, connected novel edge area, originally flat-region damage, source border error, and extra pairwise seam contrast from real map placements. The map adjacency index is cached under the repair output root during a live pass.
- `wasteland_hd/repair.py` rescans completed production jobs and their rejected attempts. Failed directions use denoise 0.55 then 0.4 with new seeds (1009, 1013), graph verification, bounded checkpointed calls, and all integrity/QA/invention gates. Unmatched floor candidates can use a feathered source border; remaining floors fall back to `source_floor_2x`. Repaired ZIPs, receipts and coverage remain under a separate root.
- `--audit-only` used no GPU and wrote only `tmp-audit/report.json`. The production controller and its files were not changed.

## Calibration

I inspected the original/HD PNG pairs for all named labels. Metrics below are 2× opaque-area fractions; `edge` is novel-edge fraction, `region` is the largest connected novel-edge fraction, `flat` is damage to originally near-constant areas, and `border` is mean RGB error in the boundary band (0–255). `seam` is additional mapped-pair edge contrast.

| Tile | Required | Verdict | edge | region | flat | border | seam |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| brick14 | invented_texture | invented_texture | 0.113 | 0.111 | 0.366 | 2.4 | 0.0 |
| brick20 | invented_texture | invented_texture | 0.086 | 0.068 | 0.097 | 4.8 | 0.0 |
| chur071 | invented_texture | invented_texture | 0.037 | 0.013 | 0.030 | 2.8 | 0.1 |
| brdb045 | invented_texture | invented_texture | 0.226 | 0.214 | 0.230 | 4.1 | 0.0 |
| brdb004 | invented_texture | invented_texture | 0.063 | 0.040 | 0.096 | 4.5 | 0.0 |
| brda057 | invented_texture | invented_texture | 0.124 | 0.121 | 0.221 | 3.4 | 0.0 |
| brda058 | invented_texture | invented_texture | 0.051 | 0.030 | 0.089 | 4.3 | 0.0 |
| curb29 | borderline | invented_texture | 0.056 | 0.034 | 0.052 | 4.4 | 0.0 |
| curb32 | borderline | minor_invention | 0.056 | 0.028 | 0.002 | 5.4 | 0.0 |
| enfl032 | borderline | invented_texture | 0.088 | 0.050 | 0.093 | 3.1 | 0.0 |
| carpet24 | borderline | invented_texture | 0.062 | 0.028 | 0.054 | 3.4 | 0.0 |
| crta006 | borderline | minor_invention | 0.053 | 0.010 | 0.030 | 4.8 | 0.0 |
| edg7002 | minor_invention | minor_invention | 0.064 | 0.016 | 0.044 | 4.6 | 0.0 |
| bld1043 | clean | clean | 0.004 | 0.002 | 0.000 | 4.0 | 0.2 |
| bld1034 | clean | clean | 0.011 | 0.003 | 0.002 | 4.8 | 0.3 |
| bld1054 | clean | clean | 0.012 | 0.005 | 0.004 | 3.2 | 0.2 |
| enfl012 | clean | clean | 0.037 | 0.015 | 0.023 | 3.6 | 0.0 |
| bld1021 | clean | clean | 0.028 | 0.009 | 0.010 | 3.9 | 0.2 |
| bld1069 | clean | clean | 0.021 | 0.013 | 0.016 | 3.7 | 0.2 |
| brdb016 | clean | clean | 0.036 | 0.008 | 0.000 | 6.1 | 0.0 |

The borderline set measured as follows: `curb29`, `enfl032`, and `carpet24` have broad connected novelty and flat-area damage, so they fail as invented texture. `curb32` and `crta006` have localized additions without broad flat-area replacement, so they remain accepted with `minor_invention`. `edg7002` also remains accepted with `minor_invention`; its new objects are localized and its flat-area damage stays below the texture-failure threshold. The seven faithful sharpening examples remain `clean` despite raw colour differences.

## CPU audit of completed jobs

The production run advanced while this work was in progress. This snapshot includes four completed tile jobs, tiles-0000 through tiles-0003. Counts below include both previously accepted directions and previously rejected candidate directions; rejected candidates still need the old QA gate before acceptance. Each job here contains one frame per direction.

| Job | clean | minor_invention | invented_texture | unmatched_floor | Previously accepted | Previously rejected |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| tiles-0000 | 194 | 30 | 6 | 70 | 222 | 78 |
| tiles-0001 | 108 | 38 | 26 | 128 | 168 | 132 |
| tiles-0002 | 158 | 56 | 22 | 64 | 232 | 68 |
| tiles-0003 | 175 | 34 | 23 | 68 | 230 | 70 |
| **Total** | **635** | **158** | **77** | **330** | **852** | **348** |

Named audit examples: `clean` — bld1043, bld1034, enfl012; `minor_invention` — edg7002, curb32, crta006; `invented_texture` — brick14, brdb045, chur071; `unmatched_floor` — adb001, brda047, chur086 (these three were already rejected by production QA). Among the 852 previously accepted directions, 72 are newly flagged as `invented_texture`; none are `unmatched_floor` in this snapshot.

## Start the real repair pass

Run from this checkout when ready to use ComfyUI:

```sh
python3 -u -m wasteland_hd.repair \
  --root /ai/wasteland-hd/production-lightning4 \
  --out /ai/wasteland-hd/repair-lightning4
```

The command is resumable. Re-run it to include jobs that finish after the first pass. It uses the existing empty-queue readiness wait and does not stop `hd-production`.

## Verification and limits

`python3 -m pytest -q`: 73 passed. The audit command exited 0. Tests use synthetic frames and mocked ComfyUI; no GPU call was made here. As an additional CPU check, `source_floor_2x` passed both the existing QA gate and the new invention gate on all 1,200 tile frames in this completed-job snapshot.

The invented-texture thresholds are calibrated on the named tiles, not an exhaustive visual ground truth. Map adjacency comes from actual Fallout 2 map tile grids, but the simple diamond-edge comparison may miss seams caused by map overlap or tile reuse in other orientations. Neither metric proves semantic fidelity. Review the generated contact sheets and repaired packs before release.

The requested commit could not be created in this session: `git add` failed because `.git/index.lock` is on a read-only filesystem. The implementation, tests, and docs remain in the working tree. The pre-existing `.gitignore` edit and untracked task note were left untouched.
