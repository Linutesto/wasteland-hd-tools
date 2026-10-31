# Critter prompt must keep heads/helmets exactly as drawn (2026-10-30)

Branch `agent/critter-prompt`, worktree `~/agent-wt/critter-prompt`. Nothing under
`/ai/wasteland-hd/production-lightning4` was written by this task (read-only); no process was
killed; `hd-production` was not started; budget was 30 renders, 27 spent.

## Problem (Yan: NOT acceptable)

First critter sheets of `critters-0000` (job attempt-00) drew power-armour characters
(`hanpwr*`, `hapowr*`) whose original head is a dark helmet as a **bare head with a beard**,
and `anim-00040` got a slimmer suit. Identity/costume must stay as drawn: helmets stay
helmets, hoods stay hoods, no added faces/beards/hair, same body bulk.

Root cause, in two parts:

1. **The prompt named the failure.** Today's `ANIMATION_PROMPT` literally asked for
   "the same face, hair, beard, skin" in every frame - priming the model to *draw* those
   things on helmeted critters.
2. **Denoise 1.0 regenerates.** At `denoise 1.0` the sampler discards the Lanczos input's
   structure, so no wording alone could hold the silhouette: the strongest wording we tried
   (v2) was *worse* than the current prompt on the identity metric.

## What was rendered

Six sheets from `critters-0000/attempt-00/sheets/` (all heads clearly visible):

| sheet | art | frames | why |
| --- | --- | --- | --- |
| `anim-00040` | `hanpwraq` | 11 | reported failure (dark armour + helmet) |
| `anim-00060` | `hanpwras` | 9 | reported failure |
| `anim-00045` | `hanpwrar` | 17 | dark armour, standing/prone poses |
| `anim-00684` | `hapowraa` | 10 | second armour family (lighter suit, rifle) |
| `anim-01345` | `harobeaa` | 21 | robes/hood |
| `anim-01349` | `harobeab` | 14 | robes/hood |

Job `critters-0000` is the alphabetically first 300 critter FRMs: it contains only
`hanpwr*` (114), `hapowr*` (115) and `harobe*` (14) - **there are no women's or children's
sheets in this job to test** (they live in later `critters-000x` chunks, none of which has
rendered anything yet).

Four variants = 2 prompts x 2 denoise (a clean 2x2), all through the production path
`python3 -m wasteland_hd upscale --backend qwen --quality fast --steps 8 --seed 42
--render-scale 4 --prompt animation --prompt-file <text> --denoise <d>`:

| variant | prompt | denoise | renders |
| --- | --- | --- | --- |
| `v0-current` | today's prompt, byte-exact (baseline) | 1.0 | 3 (the sheets production had not reached) |
| `v1-neutral-dn100` | neutral: no face/hair/beard/skin words | 1.0 | 6 |
| `v2-lock-dn100` | explicit "helmet stays a helmet ... never draw a face" | 1.0 | 6 |
| `v3-neutral-dn055` | same neutral text as v1 | 0.55 | 6 |
| `v4-lock-dn055` | same lock text as v2 | 0.55 | 6 |

**27/30 renders.** Production had already rendered sheets 00000-00091, so its own renders of
00040/00060/00045 serve as the "current production" column; the three unrendered sheets got
the `v0-current` baseline (graph-identical to production's recipe). Every render is verified
against its recipe with `render_matches` (embedded ComfyUI graph: prompts, denoise, seed 42,
8 Lightning steps, cfg 1, LoRA, shift, loaders): **27 verified, 0 mismatches**.

Runner: `tmp-critter-prompt/run_variants.py` - one work dir per sheet, waits for an **empty
ComfyUI queue** before each CLI call (the server is shared with `hd-repair-loop.sh`), counts
renders in `tmp-critter-prompt/budget.json`, and is resumable (a completed render is skipped
by graph match, never re-used blindly). ComfyUI was down 18:38-18:47 (Claude's restart); all
27 renders happened 19:41-20:11, after it was back, so **no render fell in that window and
nothing had to be re-run**.

## Verdicts

Artifacts (all under the gitignored `tmp-critter-prompt/`):

- `contact/anim-*.png` - original | current production | v1 | v2 | v3 | v4, whole frames at
  the shipped 2x (26 files, chunked).
- `zoom/h-*.png` - tight head crops (mask-detected topmost blob) for the judgement calls.
- `metric.json` - the identity metric below, full precision.
- `prompts/*.txt` - the exact prompt texts.

### Identity metric (`~/agent-wt/critter/tools/critter_live_qa.py`, read-only)

`identity_check` over the cut 2x frames of all 6 sheets = 10 directions per source
(IoU, structure, and skin/hair/clothes region colour vs the original FRM; flag limits are
the calibrated ones from `docs/CRITTER-LIVE-QA-REPORT.md`, deliberately zero-margin on the
good side):

| source | flagged directions | structure median / max |
| --- | --- | --- |
| current production | **5/10**: `hanpwraq/1 hanpwrar/0 hanpwras/5 hapowraa/0 hapowraa/1` | 10.72 / 33.61 |
| v1 neutral dn1.0 | 4/10: `hanpwraq/1 hanpwras/5 hapowraa/0 hapowraa/1` | 10.59 / 24.40 |
| v2 lock dn1.0 | **7/10** (worst): incl. `harobeaa/0` (skin dominant colour 62!) | 12.06 / 24.68 |
| **v3 neutral dn0.55** | **0/10** | **9.39 / 11.84** |
| v4 lock dn0.55 | 3/10: `hapowraa/1 harobeaa/0 harobeab/1` | 10.08 / 14.97 |

(The lock wording *named* faces/beards and made things worse at both denoises; dropping the
words entirely is what fixes it. Reduced denoise dominates everything else.)

### Looking at the sheets (contact + head zooms)

- **current production**: `anim-00040` and `anim-00684` unmistakably show a **bare-headed,
  bearded man** where the original has a dark helmet; `anim-00040/1_3` also reads slimmer
  than the original's armour bulk. This reproduces Yan's complaint exactly.
- **v3 (winner)**: every head stays what the input draws - the helmeted dome, the hood; no
  eyes/beard/skin over headgear on any of the six sheets; silhouette, armour bulk and colours
  track the original frame for frame. Robes keep their hoods and palette.
- **v1/v2 at dn 1.0**: the bearded head goes away in the zooms, but the metric still flags
  body/clothes colour and structure drift on the armour sheets (the "slimmer suit" mode), and
  v2/v4 add skin-colour drift on the robe sheet.
- **detail trade-off** (mean |gradient| inside the sprite, sheet 00040, shipped 2x frames):

  | original (nearest 2x) | lanczos 2x | current | v1 | v2 | v3 | v4 |
  | --- | --- | --- | --- | --- | --- | --- |
  | 5.5-7.4 | 6.8-8.8 | 10.2-11.1 | 8.2-10.8 | 8.2-10.4 | 6.3-8.4 | 6.4-8.5 |

  v3 sits just above plain Lanczos: it is a smooth, clean redraw that adds modest crispness
  rather than the aggressive (and identity-destroying) detail of denoise 1.0. Structure vs
  the original confirms it: lanczos 7.89, v3 8.39, v1 10.65, current 12.27 (flag limit 13.5).
  Small surface detail is exactly what Yan said may change; silhouette/head/costume may not.

**Winner: v3 - the neutral prompt at denoise 0.55.** It is the only recipe with zero
identity flags, and the only one where every head zoom matches the original's headgear.

## Chosen recipe, as implemented

1. `wasteland_hd/upscale.py` - `ANIMATION_PROMPT` replaced with the tested neutral text
   (byte-identical to `tmp-critter-prompt/prompts/v3-neutral-dn055.txt`,
   sha256 `b8e0820e5bdea05c15cbb12e9884288619f7fc609eb3ecaebc7db2b33c2f1d0b`; the old text
   hashed `56bc5489fa413a09...`). The text never names face/hair/beard/skin and only allows
   detail *inside shapes the input already has*. `Comfy` gained a `denoise` parameter
   (default 1.0, so every existing graph is unchanged).
2. `wasteland_hd/production.py` - `SETTINGS["animation_denoise"] = 0.55`;
   `production_model()` selects it for animation sheets (static stays at `denoise 1.0`);
   the `upscale` child command now passes `--denoise` explicitly per sheet type. Both prompt
   hash and denoise are pinned in `plan.json` and verified against every PNG's graph.
3. `wasteland_hd/__main__.py` - `upscale` gained `--prompt-file` and `--denoise`
   (experiment overrides; defaults reproduce today's behaviour), used by the A/B runner.
4. `docs/AUTONOMOUS-PRODUCTION.md` - records static denoise 1 / animation denoise 0.55.

Tests: `python3 -m pytest -q` -> **137 passed**.
`tests/test_production_temporal.py::test_live_plan_resumes_byte_for_byte_with_the_option`
was renamed to `..._differs_only_by_the_critter_recipe_change` and now asserts that the plan
this code writes differs from the live root's plan **only** in `animation_prompt_sha256` and
`animation_denoise` (everything else byte-identical, `--temporal` still changes nothing) -
which is precisely the premise of the switch below.

## How production switches (the options hash changes)

`plan.json` embeds `settings.animation_prompt_sha256`
(`56bc5489...` -> `b8e0820e...`) and the new `settings.animation_denoise`, so
`Controller.run()` would refuse the live root with exit 64 ("production plan/source
changed"). `options.json` (the temporal option, guarded by the "options changed after
critters-0000 started" check) is **not** touched, so that guard never fires.

Because every *other* plan field is proven identical by the test above, the switch is: move
the two pinned plan files aside (never delete), then start production with the exact same
arguments as before. **Exact commands for Claude** (run them yourself; nothing here has been
executed by this task):

```sh
# 0. Preconditions
pgrep -f '^python3 -u -m wasteland_hd.production'    # must print NOTHING (no live controller)
tail -1 /ai/wasteland-hd/repair-lightning4.log        # should be a "repair exit ..." line:
                                                      # the hd-repair loop sleeps 30 min between windows;
                                                      # do the two moves below inside that window
python3 -c "import json;print(json.load(open('/ai/wasteland-hd/production-lightning4/jobs/critters-0000/state.json'))['attempts'])"
# must show attempt "0": status "running" (it does today). If it ever says "done",
# ALSO move jobs/critters-0000/state.json aside with the same stamp - otherwise the
# finished attempt would be skipped and keep the old prompt.

# 1. Move both plan files aside, back-to-back, so no repair pass starts on a mixed pair
STAMP=$(date +%Y%m%d-%H%M%S)
mv /ai/wasteland-hd/production-lightning4/plan.json \
   /ai/wasteland-hd/production-lightning4/plan.json.pre-critter-prompt-$STAMP
mv /ai/wasteland-hd/repair-lightning4/plan.json \
   /ai/wasteland-hd/repair-lightning4/plan.json.pre-critter-prompt-$STAMP
# the repair root must move too: it pins production plan.json's sha256
# (repair.plan.production_plan_sha256) and would otherwise exit 64, which stops hd-repair-loop.sh.

# 2. Start production exactly as before (service or manual - same args, same root)
systemctl --user start hd-production        # or the manual command from docs/AUTONOMOUS-PRODUCTION.md:
# python3 -u -m wasteland_hd.production \
#   --game /home/yan/fallout2/gamedata \
#   --root /ai/wasteland-hd/production-lightning4 \
#   --adopt-world /ai/wasteland-hd/batch4/world --temporal

# 3. Verify
python3 -c "import json;p=json.load(open('/ai/wasteland-hd/production-lightning4/plan.json'));\
print(p['settings']['animation_prompt_sha256'], p['settings']['animation_denoise'])"
# expect: b8e0820e5bdea05c15cbb12e9884288619f7fc609eb3ecaebc7db2b33c2f1d0b 0.55
grep -c 'animation_denoise' /ai/wasteland-hd/production-lightning4/plan.json   # expect 1
ls /ai/wasteland-hd/production-lightning4/jobs/critters-0000/attempt-00/renders/anim-00040/stale/
# expect a new TIMESTAMP dir: the 92 old-prompt renders are archived there by production
# itself (moved, never deleted) before being re-rendered with the new recipe.
```

What happens on that restart, and why nothing else moves:

- **critters-0000 restarts with the new recipe.** Its attempt-0 is `status: running` and no
  direction is accepted yet (`hd/` is empty), so the attempt re-enters; `sheets.json` and all
  native sheets are reused; every existing render fails `render_matches` against the new
  graph, gets moved into `renders/<sheet>/stale/<ts>/` with its old `command.json`/`cut.json`
  (fresh retry budget), and is re-rendered at prompt `b8e0820e...`, denoise 0.55.
- **All non-critter jobs stay as they are.** Every other job is `status: needs_review` with
  all attempts `done`, so `run_job` only re-verifies accepted files (integrity + QA, CPU) and
  never enters a render path; their `state.json`, `hd/` trees and staging zips are untouched.
  They use `PROMPT` (unchanged hash, denoise 1.0) anyway.
- **`options.json` is untouched**, so the temporal option and its "options changed after
  critters-0000 started" guard are unaffected.
- **Repair keeps working**: its plan re-pins the new production plan sha256 on the next
  window; already-accepted repair directions are skipped by their state hashes. If the loop
  did ever exit 64 (the millisecond window between the two `mv`s), restart it in tmux
  session `hd-repair` with `bash /home/yan/fallout2/tools/hd-repair-loop.sh`.
- `hd-production` is still STOPPED until step 2 is deliberately executed; do not start it
  just because this report exists.

## Limits and next steps

- Only job `critters-0000` exists yet: verdicts cover power armour and robes. Women's,
  children's and other critter chunks (later `critters-000x`) were not testable; re-check
  `hfprimab`-style identities there with `tools/critter_live_qa.py` once they render.
- Denoise 1.0 with any wording cannot guarantee identity (v2 was the worst recipe of the
  four), and denoise 0.55 adds only modest detail over Lanczos. If Yan wants more "remaster
  punch", the untested middle ground is the neutral prompt at denoise 0.7-0.8 (budget was
  capped at 30 renders; 3 remain unspent) - to be A/B'd on the same six sheets.
- The identity metric is a flag, not a gate: shipped art still goes through production's
  flicker gate, per-frame QA and visual review.
