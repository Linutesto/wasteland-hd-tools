# The selector never trades passing HD art for original art — 2026-10-30

Task `docs/FREEBUFF-TASK-SELECT-RULE.md`, worktree `~/agent-wt/select`, branch `agent/select`.
Notes kept in `tmp-select-rule/PROGRESS.md` (gitignored). `/ai` was only read; no GPU, no
service, no push.

## Result

The rule is now in `wasteland_hd/select.py`, so every consumer that runs
`python3 -m wasteland_hd select` (the phone pack builder, critter packs, the HD installer,
`wasteland_hd install`) gets it: **original art is chosen only when no candidate passes the
gate; the fitted score ranks passing HD candidates among themselves.**

Both runs on the real roots (`/ai/wasteland-hd/{production,repair}-lightning4`, 32 jobs,
**7,965 directions**), same flags, `--no-judge` so only the rule differs:

| Category | directions | fallback **before** | fallback **after** | back to HD |
| --- | ---: | ---: | ---: | ---: |
| backgrnd | 21 | 12 | 0 | 12 |
| intrface | 459 | 127 | 0 | 127 |
| inven | 366 | 3 | 0 | 3 |
| items | 202 | 16 | 0 | 16 |
| misc | 126 | 33 | 0 | 33 |
| scenery | 1,863 | 149 | 0 | 149 |
| skilldex | 173 | 5 | 0 | 5 |
| tiles | 3,082 | 859 | 0 | 859 |
| walls | 1,673 | 199 | 20 | 179 |
| **Total** | **7,965** | **1,403** | **20** | **1,383** |

* **1,383 directions came back to HD** — 1,212 now take a repair root, 171 a production root.
  Those are *all* the `chosen_source` changes between the two runs: nothing moved the other
  way, and no passing HD candidate was replaced by another one.
* Of the before-run's 1,403 fallbacks, **1,383 had at least one gate-passing candidate
  listed** — the score alone sent them to original art, exactly the failure the pack builder
  had to override (`~/fallout2/docs/PACK-SELECT-REPORT.md`, 1,137 overridden directions).
  With the rule in the selector, the override is no longer needed: the selector itself now
  says the same thing.
* The **20** that still fall back are the only directions in the run where *no* root passes
  the gate (see below); their `reason` is `no candidate passed the gate`.

Machine-readable: `tmp-select-rule/fallback-counts.json`, produced by
`tmp-select-rule/compare.py` from both runs' `selection.json` files.

## What changed in `wasteland_hd/select.py`

1. **The rule** — `rank(found, weights)` scores only candidates whose frames all cleared the
   gate (integrity → `qa.badness` → seam deltas → invention verdict); the original-art
   fallback gets score `0.0` and never joins the ranking. `choose(found, weights)` returns
   `(chosen, reason, candidates)`:
   * something passed → best passing candidate, `reason: "gate then fitted detail score"`,
     candidates ordered best-passing first **with the fallback last**;
   * nothing passed → the fallback, `reason: "no candidate passed the gate"`.
   The vision-judge tie-break (gap < 0.025) now only ever picks between two *passing*
   candidates. `selection.json` keeps the score of the chosen entry, the score of every
   passing candidate, the reason, and `baseline`, as before.
2. **Critter jobs** (`critters-NNNN`; animations):
   * *A direction is one candidate.* `candidates()` accepts a root only when **every** frame
     of the group is in that single root, and the selection loop copies all frames from the
     chosen root — frames of two candidates are never mixed inside a direction.
   * *Temporal stage outputs are the gate inputs.* For critter jobs each production attempt
     root resolves through `attempt/post.json` → `hd` (the post-process/temporal-stage tree),
     not the raw `attempt/hd` render; an attempt with no usable stage checkpoint is not a
     root at all. The stage deletes the frames of every direction its flicker gate refused,
     so those frames cannot be selected.
   * *The flicker verdict in production `state.json` is a gate input.* `flicker_verdict()`
     reads `groups[<direction>].flicker`, else `temporal.directions[<direction>]`;
     `production_refuser()` refuses every **production** root of a direction whose verdict
     failed. Repair candidates stay eligible — the verdict describes production's staged
     frames — so a repaired animation that passes the art gate wins instead of falling back,
     and a failing verdict can never ship pre-temporal production frames. Chosen rows carry
     the verdict under `row["flicker"]`.
3. **`--pack-out DIR`** writes two groupings of the selected trees, all entries `hd/art/…`:
   * **per-category** `<category>.zip` (e.g. `tiles.zip`);
   * **per-job-range** `<job>.zip` — a job *is* one range of the art list, so critters ship
     as `critters-0000.zip`, `critters-0001.zip`, … as production finishes each job.
   Both are classic ZIPs: `write_zip()` refuses any set that would need ZIP64 (≥ 65,535
   entries) and `assert_standard_zip()` re-checks the finished archive (no ZIP64 EOCD
   locator, no 4 GiB sizes/offsets, no ZIP64 extra fields) — the same guard
   `tools/hd-pack-build.py` uses. Every frame is re-checked before it is written with the
   pack builder's integrity test (`check_frame()`: RGBA PNG, exact 2× the original, alpha
   byte-exact nearest against the original FRM frame). A category too big for one classic
   ZIP (critters scale) is **skipped with a message** and carried by its job-range packs
   instead of ever emitting ZIP64.
4. `summary.json` now records `fallback` (per category) and `fallback_total` next to the
   existing switches/judge fields.

## The two runs

```bash
# BEFORE — old selector: git archive HEAD extracted to tmp-select-rule/before-src,
# verified byte-identical to HEAD with diff, run with that directory as cwd
python3 -m wasteland_hd select --production /ai/wasteland-hd/production-lightning4 \
  --repair /ai/wasteland-hd/repair-lightning4 \
  --out …/tmp-select-rule/before --no-judge                     # 15:27 → 15:49 (~22 min)

# AFTER — the same roots and flags through this branch, plus the new pack output
python3 -m wasteland_hd select --production /ai/wasteland-hd/production-lightning4 \
  --repair /ai/wasteland-hd/repair-lightning4 \
  --out …/tmp-select-rule/after --pack-out …/tmp-select-rule/packs --no-judge   # 15:59 → 16:22 (~23 min)
```

`--no-judge` makes both runs deterministic: `weights.json` is `(1.0, 1.0, 0.05)` with
`judge_trusted: false` in both — the default-weight configuration the task's *Why* section
describes (78% swap consistency, judge untrusted). A judged run re-rolls its judge sample
every time (see the pack report), which is why the before/after pair is judge-free: the only
difference between the two runs is the rule. `after/summary.json`:
`{"fallback": {"walls": 20}, "fallback_total": 20}`.

Example of the rule acting (`art/tiles/adb001/0`):

```
before: chosen source_floor_2x           score 0.0     reason "gate then fitted detail score"
after : chosen repair_attempts/…/denoise-0/candidate/hd   score -0.007208  (passing, so it wins)
        candidates: [repair … -0.007208, source_floor_2x 0.0]
art/tiles/adb036/0: [repair … 0.038335, production_attempt-00 0.019266,
                    production_attempt-02 -0.008577, source_floor_2x 0.0]  → first wins
```

## The 20 directions that still fall back

Each has `candidates: [original_game_art]` only — no root cleared the gate, so the fallback
is the correct answer, not a score accident:

* `walls-0002` (19): `fence00/0`, `fence02/0`, `fence03/0`, `fence04/0`, `fence05/0`,
  `fence06/0`, `fence09/0`, `fence10/0`, `fence11/0`, `fence15/0` … `fence23/0`,
  `jbs1002/0`
* `walls-0003` (1): `nec059/0`

## `--pack-out` output (41 archives)

`tmp-select-rule/packs/` — 9 category packs (**9,422 frames, 439,986,341 bytes**) plus 32
job-range packs carrying the same 9,422 frames (439,986,847 bytes); every job pack fits its
category pack exactly (union check in `tmp-select-rule/verify_packs.py`).

| pack | frames | bytes | | pack | frames | bytes |
| --- | ---: | ---: | --- | --- | ---: | ---: |
| backgrnd.zip | 21 | 9,971,599 | | scenery.zip | 2,782 | 109,421,284 |
| intrface.zip | 510 | 196,607,354 | | skilldex.zip | 173 | 23,314,816 |
| inven.zip | 368 | 10,022,473 | | tiles.zip | 3,082 | 47,256,368 |
| items.zip | 299 | 5,807,472 | | walls.zip | 1,673 | 35,284,879 |
| misc.zip | 514 | 2,300,096 | | **total** | **9,422** | **439,986,341** |

Job ranges: `backgrnd-0000`, `intrface-0000/0001`, `inven-0000/0001`, `items-0000`,
`misc-0000`, `scenery-0000…0006`, `skilldex-0000`, `tiles-0000…0010`, `walls-0000…0005`
(≤ 514 entries each — far below the 65,535 classic-ZIP limit).

Verification (`python3 tmp-select-rule/verify_packs.py`, results in
`tmp-select-rule/pack-verify.json`): **41 archives, 9,422 frames re-decoded and compared
against the original FRM frames (RGBA, exact 2×, alpha byte-exact), no ZIP64 record in any
archive, all entries `hd/art/…` and unique, no extra fields.**

Critters-scale behaviour, checked on this same real tree by lowering the entry limit to 600
(above every job range, below the big categories): `scenery.zip`, `tiles.zip` and
`walls.zip` are skipped with `… entries exceed a standard ZIP; use the job-range packs`,
and **all 32 job-range packs are still written** — no ZIP64 archive is ever produced.

## Tests

`python3 -m pytest -q` → **137 passed** (13 in `tests/test_select.py`, on synthetic trees):

* `test_passing_hd_never_loses_to_fallback` — a passing candidate whose fitted score is
  *below* the fallback's 0.0 still wins; argument order and a second passing candidate only
  reorder the passing list, the fallback stays last.
* `test_fallback_only_when_no_candidate_passed`, `test_all_roots_fail_falls_back` —
  every root refused (bad alpha) → `candidates()` returns nothing → original art, written
  for the whole direction.
* `test_direction_is_one_candidate_end_to_end` — a root holding only one frame of a
  two-frame direction is never a candidate; the chosen root supplies every frame.
* `test_critter_attempt_roots_are_stage_outputs` — critter attempts resolve to the temporal
  stage output (`post.json`), non-critter attempts keep `attempt/hd`, an attempt without a
  checkpoint contributes no root.
* `test_critter_flicker_verdict_is_a_gate_input` — verdict from `groups[…].flicker` or
  `temporal.directions`, a failed verdict refuses production roots while the repair
  candidate stays eligible and wins.
* `test_pack_zip_format`, `test_pack_refuses_a_frame_the_gate_would_refuse`,
  `test_pack_refuses_a_set_that_needs_zip64`,
  `test_pack_splits_a_category_too_big_for_one_standard_zip` — `hd/`-prefixed entries,
  classic ZIP, the pack builder's integrity check, the ZIP64 refusal and the critter-scale
  split.

## Compatibility and limits

* `selection.json` changes are additive: `reason` now distinguishes the two outcomes,
  passing candidates carry their `score`, critter rows carry `flicker`. Candidate order is
  still "passing best-first, fallback last", so `hd-pack-build.py`'s `passing_candidate()`
  (skip fallback entries, take the first other) is unchanged — with the rule in the
  selector, that override now agrees with the selector's own choice.
* Fallback directions still ship their original art scaled 2× (RGBA, integrity-checked),
  because the engine draws a missing HD file through the user's pixel filter with native
  lighting — the pack builder's rationale, unchanged here.
* **Limit:** no critter job exists in a live root yet (`production-lightning4` has none;
  critter production is paused), so the critter path is proven by synthetic-tree tests and
  the stage-root/flicker code paths, not by a live critter run. Job eligibility (a job is
  selected only once its repair state exists and covers its unresolved groups) is unchanged
  from before.
* A judged run still re-rolls weights and can disagree run to run (documented in the pack
  report); the numbers above come from judge-free runs.

## Reproduce

```bash
cd ~/agent-wt/select
python3 -m pytest -q                                   # 137 passed
python3 tmp-select-rule/compare.py                     # before/after table
python3 tmp-select-rule/verify_packs.py                # 41 archives, 9,422 frames
python3 -m wasteland_hd select --production /ai/wasteland-hd/production-lightning4 \
  --repair /ai/wasteland-hd/repair-lightning4 \
  --out tmp-select-rule/after --pack-out tmp-select-rule/packs --no-judge
```
