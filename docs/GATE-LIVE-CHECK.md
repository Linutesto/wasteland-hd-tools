# Live check: the calibrated gate in the real repair pass (2026-10-29)

Brief: `docs/FREEBUFF-TASK-GATE-LIVE-CHECK.md`, follow-up to `docs/GATE-NONFLOOR-REPORT.md`.
Everything outside this repo was read-only; nothing was restarted, and I made no commits (the
gate code + a snapshot of the other report went in as `a095e52` from the reviewing session,
not from here).

## 1. Wiring, with file:line

- `wasteland_hd/repair.py:125` — `Controller.gate()`, the only place a frame is scored.
- `wasteland_hd/repair.py:142-145` — the single `invention.score(...)` call, and it passes **both**
  `floor=rel.startswith('art/tiles/')` **and** `kind=invention.kind_of(rel)`:
  ```python
  score = invention.score(image, original,
                          seam_delta=self.seam_for(rel, image, overrides),
                          floor=rel.startswith('art/tiles/'),
                          kind=invention.kind_of(rel))
  ```
  Because `gate()` walks every frame of every group, every direction it scores — walls,
  scenery, items, misc *and* tiles — is scored with its own kind. There is no other
  `invention.score` call in `repair.py`.
- `wasteland_hd/invention.py:42-47` — `kind_of()` maps exactly the paths that were calibrated:
  `art/walls/... → 'walls'`, `art/scenery/... → 'scenery'`, `art/items/... → 'items'`,
  `art/misc/... → 'misc'`; anything else (`art/tiles/...`, `art/interface/...`,
  `art/critters/...`, malformed paths) returns `'tiles'` and therefore keeps the floor gate.
  Asserted by `tests/test_invention.py:31-41`.
- `wasteland_hd/invention.py:142` (`score` signature), `:148` (`sprite = kind != 'tiles'`),
  `:151` (`floor = floor and not sprite`, so a sprite can never be `unmatched_floor`),
  `:161-162` (`elif sprite: verdict = _sprite_verdict(high)`).
- Non-floor fallback: `wasteland_hd/repair.py:367-393` keeps `edge_lock` and `source_floor_2x`
  behind `group['art'].startswith('art/tiles/')`; `repair.py:394-405` is the new
  `elif source is None:` branch that resizes the untouched game frames 2x (nearest) and records
  `{'kind':'original_game_art'}`; `repair.py:415` reports it under `fallback`. An invented
  texture is never a fallback source.
- The live loop picks the code up from disk: `~/fallout2/tools/hd-repair-loop.sh` does
  `cd /home/yan/wasteland-hd-tools` and runs `python3 -m wasteland_hd.repair --root
  /ai/wasteland-hd/production-lightning4 --out /ai/wasteland-hd/repair-lightning4 >> .../repair-lightning4.log`,
  then `sleep 600`. Each pass is a fresh interpreter, so no restart was needed to get the new
  gate into the loop.

## 2. A live pass that started after the code landed

Code on disk: `wasteland_hd/repair.py` mtime **10:55**, `wasteland_hd/invention.py` mtime
**11:38** (2026-10-29). Loop passes end at 10:33, 10:51, 11:06, 11:26, 11:39, 11:54, 12:05,
12:17, 12:32, 12:47, 12:57, 13:08 — all `repair exit 0`.

- First pass that started *after* both files landed: **11:39:20 → 11:54:26** (11 tiles jobs).
- Most recent complete pass, used below: **started 12:57:56, finished 13:08:26, exit 0**
  (`/ai/wasteland-hd/repair-lightning4.log`).

Pass at 13:08:26 — 17 jobs (`tiles-0000`..`tiles-0010`, `walls-0000`..`walls-0005`),
4,755 frames gated:

| kind | jobs | frames | clean | minor_invention | invented_texture / unmatched_floor |
|---|---|---|---|---|---|
| tiles | 11 | 3,082 | 2,698 | 384 | **0** |
| walls | 6 | 1,673 | 1,623 | 50 | **0** |
| **total** | **17** | **4,755** | **4,321** | **434** | **0** |

Per-job verdict counts in that pass:

```
tiles-0000 clean 269 minor 31      walls-0000 clean 297 minor 3
tiles-0001 clean 262 minor 38      walls-0001 clean 293 minor 7
tiles-0002 clean 244 minor 56      walls-0002 clean 287 minor 13
tiles-0003 clean 266 minor 34      walls-0003 clean 285 minor 15
tiles-0004 clean 249 minor 51      walls-0004 clean 291 minor 9
tiles-0005 clean 267 minor 33      walls-0005 clean 170 minor 3
tiles-0006 clean 212 minor 88
tiles-0007 clean 281 minor 19      summary: clean 4321, minor_invention 434
tiles-0008 clean 296 minor 4       invented_texture / unmatched_floor: none
tiles-0009 clean 270 minor 30
tiles-0010 clean 82
```

Walls were repaired progressively *after* the code landed (state.json mtimes):
`walls-0000/0001` 12:05, `walls-0002` 12:17, `walls-0003` 12:32, `walls-0004` 12:45,
`walls-0005` 12:47 — so all 1,673 wall frames were gated by the new per-kind rules.

Accepted / fallback, summed over `jobs/*/state.json` (all 17 jobs):

| | groups | accepted | fallback none | edge_lock | source_floor_2x | original_game_art |
|---|---|---|---|---|---|---|
| tiles | 1,341 | 1,341 | 1,241 | 5 | 95 | 0 |
| walls | 130 | 130 | 130 | 0 | 0 | 0 |
| **total** | **1,471** | **1,471 (100%)** | **1,371** | **5** | **95** | **0** |

Provenance kinds across all groups: `reduced_denoise` 1,371, `edge_lock` 5,
`source_floor_2x` 95, `original_game_art` 0. Both floor fallbacks still only ever appear on
`tiles-*` jobs; no non-floor group has needed the original-art fallback yet because every wall
group's render passed the gate on the first accepted attempt.

Verdict-count growth per pass (shows the new gate running on each newly finished job):

```
11:39:20  10 jobs   tiles c2616 m384 f0
11:54:26  11 jobs   tiles c2698 m384 f0
12:05:50  13 jobs   +walls c590  m10 f0
12:17:12  14 jobs   +walls c877  m23 f0
12:32:03  15 jobs   +walls c1162 m38 f0
12:47:26  17 jobs   +walls c1623 m50 f0
12:57:56  17 jobs   (identical, resume)
13:08:26  17 jobs   (identical, resume)
```

## 3. Regression guard

- `python3 -m pytest -q` → **101 passed** (run 2026-10-29 13:17). An earlier run in the same
  session showed `93 passed, 8 failed`, all 8 in `tests/test_temporal_stage.py` from the
  separate `docs/FREEBUFF-TASK-CRITTER-TEMPORAL.md` task (a module that imports no
  `invention`/`repair` code); that session has since fixed them. Gate scope
  (`tests/test_invention.py tests/test_repair.py`) is 18/18 at every run.
- The 20 tile calibration labels from `docs/REPAIR-GATE-REPORT.md`, rescored with the new code
  (`tmp-audit/nonfloor/tile-labels.txt`), every one HOLD with identical before/after numbers:

```
brick14   HOLD gate=invented_texture  required=invented_texture  e=0.113(0.113) c=0.111(0.111) f=0.366(0.366) b=2.4(2.4)
brick20   HOLD gate=invented_texture  required=invented_texture  e=0.086(0.086) c=0.068(0.068) f=0.097(0.097) b=4.8(4.8)
chur071   HOLD gate=invented_texture  required=invented_texture  e=0.037(0.037) c=0.013(0.013) f=0.030(0.030) b=2.8(2.8)
brdb045   HOLD gate=invented_texture  required=invented_texture  e=0.226(0.226) c=0.214(0.214) f=0.230(0.230) b=4.1(4.1)
brdb004   HOLD gate=invented_texture  required=invented_texture  e=0.063(0.063) c=0.040(0.040) f=0.096(0.096) b=4.5(4.5)
brda057   HOLD gate=invented_texture  required=invented_texture  e=0.124(0.124) c=0.121(0.121) f=0.221(0.221) b=3.4(3.4)
brda058   HOLD gate=invented_texture  required=invented_texture  e=0.051(0.051) c=0.030(0.030) f=0.089(0.089) b=4.3(4.3)
curb29    HOLD gate=invented_texture  required=invented_texture  e=0.056(0.056) c=0.034(0.034) f=0.052(0.052) b=4.4(4.4)
curb32    HOLD gate=minor_invention   required=minor_invention   e=0.056(0.056) c=0.028(0.028) f=0.002(0.002) b=5.4(5.4)
enfl032   HOLD gate=invented_texture  required=invented_texture  e=0.088(0.088) c=0.050(0.050) f=0.093(0.093) b=3.1(3.1)
carpet24  HOLD gate=invented_texture  required=invented_texture  e=0.062(0.062) c=0.028(0.028) f=0.054(0.054) b=3.4(3.4)
crta006   HOLD gate=minor_invention   required=minor_invention   e=0.053(0.053) c=0.010(0.010) f=0.030(0.030) b=4.8(4.8)
edg7002   HOLD gate=minor_invention   required=minor_invention   e=0.064(0.064) c=0.016(0.016) f=0.044(0.044) b=4.6(4.6)
bld1043   HOLD gate=clean             required=clean             e=0.004(0.004) c=0.002(0.002) f=0.000(0.000) b=4.0(4.0)
bld1034   HOLD gate=clean             required=clean             e=0.011(0.011) c=0.003(0.003) f=0.002(0.002) b=4.8(4.8)
bld1054   HOLD gate=clean             required=clean             e=0.012(0.012) c=0.005(0.005) f=0.004(0.004) b=3.2(3.2)
enfl012   HOLD gate=clean             required=clean             e=0.037(0.037) c=0.015(0.015) f=0.023(0.023) b=3.6(3.6)
bld1021   HOLD gate=clean             required=clean             e=0.028(0.028) c=0.009(0.009) f=0.010(0.010) b=3.9(3.9)
bld1069   HOLD gate=clean             required=clean             e=0.021(0.021) c=0.013(0.013) f=0.016(0.016) b=3.7(3.7)
brdb016   HOLD gate=clean             required=clean             e=0.036(0.036) c=0.008(0.008) f=0.000(0.000) b=6.1(6.1)
ALL 20 CALIBRATION LABELS HOLD
```

## 4. No surprises

- **No Traceback** in `/ai/wasteland-hd/repair-lightning4.log`, and no `invented_texture`,
  `unmatched_floor` or `original_game_art` string anywhere in it either (grep count 0); all 12
  passes in the log ended `repair exit 0`.
- **Tiles behaviour unchanged, live**: the 9 tiles jobs present in the last pass to end before
  the change (11:26:19, before `invention.py` landed at 11:38) have **identical verdict dicts**
  in every later pass through 13:08:26 (diff of the log's per-pass `jobs` counts = `[]`); the
  only changes are newly finished jobs being added. That matches the code path: for `kind='tiles'`,
  `sprite` is `False`, the new alpha band is `np.zeros_like(mask)` (a no-op) and every floor
  threshold is untouched (`invention.py:148-168`).
- **Non-floor fallback goes to original art**: `repair.py:394-405` resizes the untouched game
  frames and records `original_game_art`, with `source_floor_2x` still behind the
  `art/tiles/` check at `repair.py:388-393`; `repair.py:415` surfaces it as a fallback.
  Covered end-to-end by `tests/test_repair.py::test_sprite_direction_gates_by_kind_and_falls_back_to_original_art`.
  In this run the branch was never reached (all 130 wall groups accepted a render), so the
  live evidence for it is "no `source_floor_2x` and no invented texture ever appeared on a
  non-floor job", plus the test.

## Still worth watching

- Only `tiles-*` and `walls-*` jobs exist so far — `hd-production` has not emitted
  scenery/items/misc jobs yet, so the scenery/items/misc half of the calibration is still only
  proven offline (world-seam + tests), not against a live pass.
- Walls failed **0 / 1,673** frames live (0%), against 3.56% invented on the world-seam
  calibration set. World-seam is older, harsher full-Qwen material than current production
  output, so a lower rate is expected, but 0% means the live walls have not yet exercised the
  `invented_texture` path — worth re-reading this report once scenery jobs appear.
- I did not commit. The gate code (`invention.py`, `repair.py`, both test files) and the first
  snapshot of `docs/GATE-NONFLOOR-REPORT.md` were committed by the reviewing session as
  `a095e52` at 13:14 — the loop has been running that exact code since. Still unstaged: my
  report corrections and this file, plus the separate temporal task's files.
