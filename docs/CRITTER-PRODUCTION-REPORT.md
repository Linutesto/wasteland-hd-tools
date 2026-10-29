# Critter temporal stage in production, behind `--temporal` (2026-10-29)

CPU only. **No GPU render, no ComfyUI request, no service restarted, nothing written under `/ai`,
nothing committed.** `/ai/wasteland-hd/production-lightning4/plan.json` and `~/fallout2` were read,
never modified. Files changed: `wasteland_hd/production.py`, `wasteland_hd/temporal_stage.py`
(two robustness fixes), new `tests/test_production_temporal.py` (9 tests). Suite: **110 passed**
(101 before). Budget: **~2.9 CPU-hours** for all 224,365 critter frames.

```sh
python3 -m pytest -q                                   # 110 passed
python3 -u -m wasteland_hd.production --status --root /ai/wasteland-hd/production-lightning4
```

## Verdict

`--temporal` is wired in and **default OFF**. With it off the produced `plan.json` is byte-for-byte
what the current code writes, and with it on the plan is *still* byte-for-byte identical — the option
is filed per job type in `options.json` and applies only to job types that have not started. The live
root can therefore be restarted with the flag: its 20 finished scenery/tiles/walls jobs resume, and
the 24 critter jobs (7,119 source FRMs, not started) run through the stage and the gate.

## 1. Design and wiring

`production.py`:

| what | where |
| --- | --- |
| stage + gate settings (components, strength, radius, fallback cap, gate limits, rule id) | `TEMPORAL_SETTINGS`, production.py:49 |
| the immutable plan document (unchanged by the flag) | `plan_document`, production.py:205 |
| gate component checks / per-direction verdicts | `gate_failures` :224, `temporal_verdicts` :230 |
| per-job-type option store, start guard | `Controller.job_options` :365, `started` :389, `temporal_for` :394 |
| stage + gate, deletions | `Controller.run_temporal` :577 |
| placement in the CPU pipeline | `Controller.postprocess` :625 |
| gate recorded with the group result | `Controller.accept` |
| `generated+temporal` provenance, job options written | `run_job`, `run` :832 |
| CLI flag | production.py:888 |

Order inside `postprocess`, unchanged for every other art kind: accepted whole directions are copied →
`seams` (tiles only) → `--stabilise` (if given) → **`temporal`** → **flicker gate** → the staged tree is
what integrity (`evaluate`) and QA then see. The stage rewrites RGB only and copies the source alpha
at 2x nearest, so the dimension/alpha digests and `qa.badness` still apply unchanged.

`run_temporal`:

1. one child command `python -m wasteland_hd temporal --game … --source <hd> --out <stage>/temporal/hd
   --components … --strength … --radius … --fallback-cap … --json <stage>/temporal-report.json`,
   through `Controller.command` (bounded retries, checkpoint). A permanent failure raises
   `TimeoutError` → exit 75, GPU seed checkpoint retained, no seed consumed — the same contract as
   `seams`/`stabilise`.
2. it runs only when the tree has at least one *accepted, complete* critter direction with two or more
   frames (`(hd/"art"/"critters").exists()` plus that count); a tree the stage would refuse is never
   passed to it.
3. `temporal_verdicts` turns the stage report into per-direction verdicts, mapped from the stage's
   `<name>/<direction>` keys to the manifest's `art/critters/<name>/<direction>`:
   * `direction_cap` — the stage already replaced the direction with original art (over 2× the
     animation's mean direction flicker);
   * `flicker_gate` — its own after-staging metrics break broad ≤ 2.5 / material ≤ 3.0 / flicker ≤ 6.0;
   * `animation_flicker_gate` — its animation's pooled metrics break the same limits.
4. every failing direction's frames are **deleted** from the staged tree (so integrity reports them
   `missing`): production never ships the stage's 2x-nearest cap stand-in, exactly like any other
   rejection. Their keys stay incomplete → the next seed attempt re-renders and re-stages *only*
   them; with the seed budget spent, `run_job`'s existing loop sets
   `fallback: "original_game_art_entire_direction"` and removes them from `hd/`.
5. the verdicts and numbers are durable: `<stage>/temporal/gate.json`, `state.json`'s
   `groups[<key>]["flicker"]` (`pass`, `reason`, direction and animation metrics, limits, keyframe),
   a job-level `state["temporal"]` block, and `coverage.json` (both per group and as
   `coverage["temporal"]`). Provenance becomes `{"kind": "generated+temporal", …, "temporal": {…}}`.

The gate reads the stage's own per-direction and pooled records: they are produced by
`flicker._measure` on the exact arrays the stage writes, so a second `flicker` pass over the tree
would cost ~0.11 CPU-hours to reproduce the same numbers. A component the metric cannot measure
(`None`, e.g. no static pixel at all) **does not pass** — the direction falls back rather than ships
unmeasured; that is stated in `gate_failures` and visible in coverage.

Two small fixes in `temporal_stage.py` were required for production use: `margin()` returns 0.0 when
nothing is measurable instead of raising on an empty `max`, and the direction-cap loop ignores
`None` flicker instead of `TypeError` on `sum(...)`. Both are reachable from real critter art
(animations whose poses share no pixel) and would otherwise turn a data property into a crash.

## 2. Compatibility: the live plan is untouched, the root still resumes

The live controller refuses to start when `old != self.plan` (exit 64). Any new key — including
`"temporal": false` — would make exactly the root we want to resume unusable. So the plan carries no
temporal information at all: `plan_document` returns the same dict, in the same keys, that the code
produced before this change. The option lives in `options.json`, keyed by job type:

```json
{"critters": {"temporal": {"enabled": true, "components": ["colour", "residual", "texture", "static"],
                           "strength": 1.0, "radius": 9, "fallback_cap": 2.0,
                           "gate": {"broad": 2.5, "material": 3.0, "flicker": 6.0},
                           "rule": "temporal-stage/1"}}}
```

`job_options` refuses a change to a type that has started (`state.json` has attempts, or an
`attempt-*` dir exists) and is silent otherwise; `started`/`temporal_for` read it back. Two details
make resume safe:

* with the flag off, `options.json` is not written at all, and the `postprocess` signature is exactly
  what the old code computed, so an in-flight attempt resumes without re-running any CPU stage;
* with the flag on, the temporal setting *is* part of the `postprocess` signature (so a changed rule
  cannot reuse a checkpointed tree), and the gate file path is stored in `post.json`, so a restart
  after the stage but before `accept` reuses the measurements instead of restaging.

Proof in `tests/test_production_temporal.py`:

* `test_live_plan_resumes_byte_for_byte_with_the_option` reads the live `plan.json` (read-only),
  builds the document the new code would write with and without `--temporal`, and asserts both are
  **byte-identical** to the file (`json.dumps(..., indent=2, sort_keys=True) + "\n"`, the exact
  `atomic_json` encoding). It also copies the plan into a fresh root and asserts `read_json(copy) ==
  document`, i.e. `run()`'s `old != self.plan` is False, so no exit 64 and no plan rewrite. Negative
  control: `{**document, "temporal": true}` does **not** equal the live file, so the naive design
  would break this root.
* `test_temporal_option_document_on_the_live_roots_started_state` reproduces the live root (plan copy
  + a started `scenery-0000`, no critter job): enabling temporal for `critters` is accepted, nothing
  is written by `job_options`, and once `critters-0000/attempt-00` exists the same call raises
  `options changed after critters-0000 started` (exit 64 in `main`).
* `test_temporal_option_is_frozen_once_critters_started`: same option = ordinary resume (zero new
  commands); restart without it after critters started = refused.
* `test_resume_after_interruption_reuses_the_gate_without_restaging`: crash after the stage, accepted
  output deleted, attempt reset to `running` → restart finishes from the checkpoint with **no**
  subprocess call at all (GPU, seams and stage all reused) and the gate verdict restored from
  `gate.json`.

Live plan digest for the restart check (read, not modified):
`c30dab4c3509456cf2b984f7a3ef967cefe0b4e9d14729945fe11e28f4b59959`.

## 3. Exact restart for Claude

The live controller is pid 2323799, started ~5.5 h before this report, running
`python3 -u -m wasteland_hd.production --game /home/yan/fallout2/gamedata
--root /ai/wasteland-hd/production-lightning4 --adopt-world /ai/wasteland-hd/batch4/world`
under the systemd user unit `hd-production.service`
(`/bin/bash /home/yan/fallout2/tools/hd-production-watch.sh`, `WorkingDirectory=/home/yan`).
That interpreter imported the old `production.py`, so the new flag needs a restart. Outside this
repo (Claude's call, not done here):

```sh
# 1. stop the supervisor, then the controller (the watcher restarts it otherwise)
systemctl --user stop hd-production.service
#    if the controller is still alive after the stop, let its current sheet finish and
#    terminate it with SIGTERM - the attempt is checkpointed and resumes.
# 2. add --temporal to the controller invocation in
#    /home/yan/fallout2/tools/hd-production-watch.sh  (it hard-codes the command line)
# 3. start it again
systemctl --user start hd-production.service
```

Equivalent by hand (same tmux/workdir the watcher uses; this is the command the flag must reach):

```sh
cd /home/yan/wasteland-hd-tools
python3 -u -m wasteland_hd.production \
  --game /home/yan/fallout2/gamedata \
  --root /ai/wasteland-hd/production-lightning4 \
  --adopt-world /ai/wasteland-hd/batch4/world \
  --temporal
```

Checks after the restart (all read-only):

```sh
python3 -m wasteland_hd.production --status --root /ai/wasteland-hd/production-lightning4  # exit 2 at needs_review
sha256sum /ai/wasteland-hd/production-lightning4/plan.json          # still c30dab4c…
cat /ai/wasteland-hd/production-lightning4/options.json             # critters.temporal.enabled true
grep "temporal stage ON" /ai/wasteland-hd/production-lightning4/production.log
```

Expected on start: the 20 finished jobs are re-verified from their state (no GPU, no CPU stage
re-run), the report keeps their coverage, and the first *critter* job is the first thing that uses
the stage. No "production plan/source changed" message; exit 64 there would mean the plan no longer
matches and must not be forced.

Turning the flag **off** again after critter jobs have started is refused on purpose (exit 64,
"critters options changed after critters-0000 started"): a type must not mix frames produced under
two different rules. Turn it off with a new `--root`.

## 4. What happens on the first critter job

`critters-0000` is 300 of the 7,119 source FRMs (≈ 9,450 HD frames at 31.5 frames/file); there are 24
critter jobs, `critters-0000` … `critters-0023`.

1. `inventory` decodes the 300 FRMs strictly and writes the manifest (per-direction groups,
   per-frame 2x dimensions and alpha digests, source hashes). Undecodable art stays a coverage
   blocker; nothing is invented.
2. Sheets are packed **by animation**, the Comfy readiness check waits for an empty queue, Lightning
   8-step settings are enforced, every render is receipt-checked, then `cut_sheet` cuts at 2x and
   writes the source alpha at 2x nearest.
3. `postprocess`: only integrals directions that are complete *and* pass integrity + QA are copied
   forward. `seams` is skipped (no `art/tiles/` in a critter tree) and `stabilise` runs only if the
   flag is also given. Then the temporal stage: **one child command**, ~7 minutes CPU for this job
   (9,450 × 46.6 ms), writing `<attempt>/post-<id>/temporal/hd` and `temporal-report.json`. The
   console shows `production: critter temporal stage ON (temporal-stage/1)` once at startup and the
   stage's own per-direction lines in `temporal.json.log`.
4. The gate runs on that report. A direction over 2× its animation's mean flicker, over any of
   broad 2.5 / material 3.0 / flicker 6.0, or in an animation over those limits, is **rejected**:
   its frames are deleted from the staged tree (`state` shows `missing`, `flicker.reason` and the
   numbers), and the direction goes to the next seed attempt (42 → 7 → 99), where only the still
   incomplete directions are re-sheeted and re-rendered.
5. With all three seeds spent the existing fallback applies:
   `fallback: "original_game_art_entire_direction"`, frames absent from `hd/` and from the ZIP,
   `coverage_complete` false for that job, and the reason/metrics readable in `state.json` and
   `coverage.json` for the human review that is still mandatory.
6. Integrity (exact 2x size, exact source alpha) and QA (`qa.badness` ≤ 1, per critter frame) still
   run **on the staged frames**; the stage changes RGB only, so those gates are unchanged. On the
   calibration tree QA rejected 111/216 frames after staging against 124/216 before, i.e. the stage
   does not make the existing gate stricter.

## 5. CPU budget for the 7,119 critter files

Measured on this machine (read-only), sampling 19 real critter animations spread across the live
plan's critter list, each direction staged at its real size with production-like per-frame noise, and
timing `temporal_stage.stage_group` plus `flicker` scores:

| quantity | measurement |
| --- | --- |
| source FRMs / frames in the plan | 7,119 / **224,365** (31.5 frames per file) |
| mean native sprite | 2,879 px (≈ 5,800 HD px) |
| `stage_group` | fit `9.8 ms + 0.0116 ms/px` → **43.1 ms/frame** population-weighted |
| one flicker score (gate measurement) | **1.7 ms/frame** |
| production per frame (stage + its two before/after scores) | **46.6 ms/frame** |
| **total stage + gate** | **2.90 CPU-hours** (≈ 1.47 s per source FRM) |
| spread | 2.6–3.1 CPU-h from the sample; 4.6 CPU-h if every sprite were as large as the sample mean (5,075 px) |
| memory | 135 MB peak RSS (calibration tree: 110 MB) |
| GPU | 0 renders, 0 ComfyUI requests |

So a critter job costs ~7 CPU-minutes of staging plus ~45 s of gate measurement; the whole run adds
about **3 CPU-hours** against the ~5 GPU-days of generation. Deterministic and idempotent (the stage
two-runs-identical result is covered by `tests/test_temporal_stage.py`).

## 6. Tests

`tests/test_production_temporal.py` (9, no GPU, no service, Comfy mocked exactly as
`tests/test_production.py` does, the real stage driven in-process, synthetic critter frames):

* stage runs only for critters, and records metrics/limits/provenance/`options.json`/coverage;
* no `temporal` command for an items job, nor when the flag is off (and no `options.json`);
* gate rejection moves to the next seed: attempt 0's `gate.json` shows `direction_cap` (or the gate)
  for the drifting direction and pass for the calm ones, its frames are gone from the staged tree,
  and only seed 7's frames are accepted;
* exhausted gate → `original_game_art_entire_direction`, no frames in `hd/`, `coverage_complete`
  false, one temporal run per seed, `flicker.pass` false with `reason: flicker_gate` in every attempt;
* resume after interruption: no subprocess call at all, gate verdict restored from disk;
* option frozen once a type started; option accepted for the live root's started state;
* live `plan.json` byte-compatibility with and without the flag, plus the naive-design negative
  control.

Whole suite: `python3 -m pytest -q` → **110 passed**.

## 7. Limits, stated plainly

* The gate numbers come from the stage's own measurements rather than a second read-back of the
  written PNGs. Same code (`flicker._measure`) and same arrays, but a read-back pass (~0.11 CPU-h)
  could move the last decimal; it was skipped deliberately so the gate adds no extra tree pass.
* The gate cannot be read as a quality score (see `docs/CRITTER-TEMPORAL-REPORT.md` §"What the gate
  now measures"): the stage's own consistency rule covers a large share of the sampled pixels.
  Semantic review still owns identity — a stable wrong costume passes, `hfprimab` being the known
  example. Production only ever ships what QA *and* human review accept.
* Measured behaviour, not assumed: a direction whose original shares no pixel between poses is
  unmeasurable and is rejected → original art; a *single* drifting direction in an otherwise empty
  tree can pool its uncopied pairs down to a pass (4.47 vs 6.0 in the test data) because half the
  cycle's pairs are keyframe-copied — the 2× direction cap catches it only while its animation is
  staged as a whole, which is the normal case (a direction is retried alone only after its siblings
  were accepted).
* `options.json` is not covered by the plan-equality guard by design; it has its own started-job
  guard and is recorded in every accepted group's provenance, so a mixed-rule tree is visible rather
  than silent.
* The budget is a sample of 19 animations scaled by area and by the confirmed 224,365 frames; the
  population's largest sprites (12k+ native px) are 2–4× slower per frame than the mean.

## Files

* `wasteland_hd/production.py` — `--temporal`, `plan_document`, `options.json` per job type,
  `run_temporal`, gate verdicts + recording, provenance, gate file in the `postprocess` checkpoint.
* `wasteland_hd/temporal_stage.py` — `margin` default and `None`-safe direction cap (production
  robustness only; no change to the measured behaviour of the stage).
* `tests/test_production_temporal.py` — the 9 tests above.
