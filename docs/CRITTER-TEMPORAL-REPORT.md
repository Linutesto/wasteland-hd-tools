# Critter temporal stage: keyframe-anchored identity transfer (2026-10-29)

CPU only. No GPU render, no ComfyUI request, no service touched: **0 of the 60 permitted sheet
renders were used**. `/ai/wasteland-hd/critter-test` and `~/fallout2` were read, never written;
every artefact went under `tmp-temporal/` (git-ignored) except the module, the CLI subcommand, the
tests and this report. Files added: `wasteland_hd/temporal_stage.py`, the `temporal` subcommand in
`wasteland_hd/__main__.py`, `tests/test_temporal_stage.py` (9 new tests; `python3 -m pytest -q` is
**101 passed**).

```sh
python3 -m wasteland_hd temporal --game ~/fallout2/gamedata \
  --source /ai/wasteland-hd/critter-test/rows4full/hd \
  --out tmp-temporal/stage-rows4full/hd --json tmp-temporal/stage-rows4full.json
python3 -m wasteland_hd flicker --game ~/fallout2/gamedata \
  --source tmp-temporal/stage-rows4full/hd --json tmp-temporal/after-rows4full.json \
  --contact tmp-temporal/contact-after-rows4full.png --top 4
PYTHONPATH=. python3 tmp-temporal/evaluate.py     # the method/ablation tables below
PYTHONPATH=. python3 tmp-temporal/playback.py    # before/after GIFs and strips
```

## Verdict up front

**GO for critter production behind this stage**, with the gate enforced per animation in
`production.py` (see [Production integration](#production-integration-proposed)) and the existing
per-frame QA left in place.

* Before: 1 of 8 animation measurements passed (rows4/hmwarrab). After: **8 of 8 pass**
  (rows4 4/4, rows4full 4/4).
* Cost: one deterministic CPU pass, 10.5 s and 110 MB peak for the 4-animation, 216-frame
  calibration tree (49 ms/frame). 224,365 critter frames is therefore about **3 CPU-hours total**,
  against the ~5 GPU-days the generation itself costs. No GPU budget is consumed.
* The stage writes nothing that changes engine geometry: RGB only, the source alpha at 2x
  nearest-neighbour is copied through, so `production.evaluate`'s dimension/alpha digests and
  `qa.badness` still apply unchanged. On this tree the per-frame QA rejects 111 of 216 frames after
  the stage against 124 before, i.e. it does not make QA worse.
* Two honest caveats, both expanded below: the flicker gate now largely *measures the stage's own
  consistency rule* on the pixels the original declares unchanged, so it cannot be read as a
  quality score; and one calibration animation (`hfprimab`) is a **semantic no-go** whatever its
  temporal score, because Qwen drew a bearded man for a female critter.

## What the stage does

Per direction (frames x one direction), `temporal_stage.stage_directory`:

1. **Keyframe.** `choose_keyframe` scores every pose by its area-weighted mean per-palette-class HD
   colour drift (`material`) to every other pose of that direction, ties broken by the largest
   single drift and then by frame index. The winner is the pose whose *material identity* is most
   representative; anchoring to one frame cannot drift along the cycle the way a
   neighbour-to-neighbour correction can.
2. **`colour`** — per-original-palette-class transfer: each frame's mean HD colour per original
   palette class is moved onto the keyframe's mean for that class, clipped at 48/255 and applied
   *exactly* per class (no spatial blur), so the frame's texture and the original's palette
   boundaries survive. This is the `material` signal, and it is translation invariant, so it also
   covers moving material.
3. **`residual`** — the keyframe's low-frequency AI residual (HD minus nearest-neighbour original,
   blurred at sigma 2 HD px) is carried onto native pixels where `temporal._match` finds the same
   original material in this pose, confidence weighted, capped at 24/255. This is the `broad`
   lighting/shading signal; texture is deliberately not transferred here.
4. **`texture`** — where the strict original patch match is confident, the keyframe's actual texels
   are drawn (feathered confidence, interior only, never the frame's own outline band), so one
   invented pattern (the leopard trousers) is not re-invented per frame.
5. **`static`** — where the original says the keyframe and this pose are byte-identical (same RGBA
   pixel and a 3x3 patch the original leaves unchanged, the evidence the flicker metric samples),
   the keyframe's HD texels are copied exactly. The weight is the mask itself: its boundary is an
   original palette/motion edge, so feathering it would only leave half the invented texture in the
   measured pixels.
6. **Per-direction selection.** The rewritten direction is kept only if its gate margin
   (`max(broad/2.5, material/3.0, flicker/6.0)`) improves; otherwise the input direction is written
   verbatim.
7. **Per-direction fallback.** After staging, a direction whose flicker is still above 2x its
   animation's mean direction flicker is replaced by the original art at 2x nearest (0.000 by
   construction) — the stand-in for production's `original_game_art_entire_direction`.

## Results

`flicker / broad / material`, gate = broad ≤ 2.5, material ≤ 3.0, flicker ≤ 6.0. Columns are the
four calibration animations; `rows4` is the Lightning-8-step row layout, `rows4full` the 30-step
one. Everything is measured from the written PNGs with `python -m wasteland_hd flicker`
(authoritative); the ablation rows run with the per-direction selection **off** so each component
shows its own effect, except the two `all` rows which are production behaviour.

### rows4

| method | hfprimab | hmwarraa | hmwarrab | nmpeasab |
| --- | --- | --- | --- | --- |
| before | 4.369 / 2.695 / 3.829 FAIL | 4.747 / 2.671 / 2.54 FAIL | 2.432 / 1.526 / 2.424 pass | 5.163 / 2.745 / 1.983 FAIL |
| colour | 5.326 / 3.627 / 0.409 FAIL | 4.674 / 2.344 / 0.396 pass | 2.632 / 1.739 / 0.402 pass | 5.296 / 2.851 / 0.373 FAIL |
| residual | 5.017 / 2.857 / 3.717 FAIL | 4.288 / 1.958 / 2.246 pass | 2.717 / 1.608 / 2.419 pass | 5.297 / 2.676 / 2.1 FAIL |
| texture | 4.702 / 2.935 / 3.597 FAIL | 2.611 / 1.335 / 2.17 pass | 2.255 / 1.454 / 2.372 pass | 4.252 / 2.204 / 1.969 pass |
| static | 3.302 / 1.99 / 3.69 FAIL | 2.117 / 1.069 / 2.278 pass | 1.455 / 0.928 / 2.412 pass | 3.266 / 1.808 / 1.961 pass |
| colour+residual | 5.346 / 3.158 / 1.712 FAIL | 4.238 / 1.629 / 0.919 pass | 2.724 / 1.638 / 1.1 pass | 5.361 / 2.688 / 1.071 FAIL |
| all, no selection | 3.823 / 2.348 / 1.773 pass | 1.635 / 0.736 / 1.125 pass | 1.615 / 1.057 / 1.117 pass | 3.202 / 1.75 / 1.178 pass |
| **all (selection)** | **3.531 / 2.057 / 2.073 pass** | **1.635 / 0.736 / 1.125 pass** | **1.615 / 1.057 / 1.117 pass** | **3.202 / 1.75 / 1.178 pass** |
| all + fallback | 3.531 / 2.057 / 2.073 pass | 1.635 / 0.736 / 1.125 pass | 1.615 / 1.057 / 1.117 pass | 3.202 / 1.75 / 1.178 pass |

### rows4full

| method | hfprimab | hmwarraa | hmwarrab | nmpeasab |
| --- | --- | --- | --- | --- |
| before | 8.934 / 4.028 / 4.22 FAIL | 4.836 / 1.955 / 3.023 FAIL | 3.891 / 2.563 / 3.354 FAIL | 6.084 / 2.594 / 2.244 FAIL |
| colour | 9.451 / 4.495 / 0.556 FAIL | 5.1 / 2.045 / 0.473 pass | 3.692 / 2.349 / 0.441 pass | 6.181 / 2.716 / 0.422 FAIL |
| residual | 9.191 / 3.813 / 4.24 FAIL | 4.718 / 1.537 / 2.673 pass | 3.609 / 1.994 / 3.055 FAIL | 6.263 / 2.82 / 2.268 FAIL |
| texture | 8.104 / 3.768 / 4.154 FAIL | 2.999 / 1.267 / 2.755 pass | 2.532 / 1.582 / 3.226 FAIL | 5.114 / 2.279 / 2.164 pass |
| static | 5.613 / 2.528 / 4.225 FAIL | 2.781 / 1.158 / 2.943 pass | 3.082 / 2.042 / 3.343 FAIL | 4.676 / 1.941 / 2.242 pass |
| colour+residual | 9.242 / 3.913 / 1.804 FAIL | 4.932 / 1.596 / 1.049 pass | 3.573 / 1.991 / 1.219 pass | 6.278 / 2.842 / 1.144 FAIL |
| all, no selection | 1.434 / 0.946 / 1.421 pass | 2.136 / 0.829 / 1.421 pass | 1.809 / 0.93 / 1.27 pass | 4.413 / 2.208 / 1.231 pass |
| all (selection) | 5.848 / 2.864 / 1.942 FAIL | 2.136 / 0.829 / 1.421 pass | 1.809 / 0.93 / 1.27 pass | 4.701 / 2.173 / 1.519 pass |
| **all + fallback** | **1.434 / 0.946 / 1.421 pass** | **2.136 / 0.829 / 1.421 pass** | **1.809 / 0.93 / 1.27 pass** | **4.701 / 2.173 / 1.519 pass** |

Reading the ablation:

* `colour` alone does exactly what it is designed for: `material` 3.8-4.2 -> 0.37-0.56 on every
  animation and both sets. It does not lower `flicker` (it can raise it: aligning class *means*
  does not move each individual pixel closer), which is why it cannot pass alone.
* `static` alone is the strongest single component on `flicker` (`hmwarraa` 4.747 -> 2.117,
  `nmpeasab` 5.163 -> 3.266) because it acts on exactly the pixels the metric samples.
* `texture` alone is what removes the per-frame pattern invention (`hmwarraa` rows4full
  4.836 -> 2.999) but it needs the other two for colour and for the poses whose match fails.
* all four together is the only combination that passes everywhere. Selection costs a little
  pooled flicker where it protects a direction's own margin: on rows4full/nmpeasab 4.413 (no
  selection) against 4.701 (selection), because it reverts two directions rather than let their
  gate margin regress. Both pass the animation gate.

Headline, measured from the written trees (`tmp-temporal/after-*.json`):

| set | animation | before flicker/broad/material | after flicker/broad/material | gate |
| --- | --- | --- | --- | --- |
| rows4 | hfprimab | 4.369 / 2.695 / 3.829 | 3.531 / 2.057 / 2.073 | pass |
| rows4 | hmwarraa | 4.747 / 2.671 / 2.540 | 1.635 / 0.736 / 1.125 | pass |
| rows4 | hmwarrab | 2.432 / 1.526 / 2.424 | 1.615 / 1.057 / 1.117 | pass |
| rows4 | nmpeasab | 5.163 / 2.745 / 1.983 | 3.202 / 1.750 / 1.178 | pass |
| rows4full | hfprimab | 8.934 / 4.028 / 4.220 | 1.434 / 0.946 / 1.421 | pass (1/6 directions on original art) |
| rows4full | hmwarraa | 4.836 / 1.955 / 3.023 | 2.136 / 0.829 / 1.421 | pass |
| rows4full | hmwarrab | 3.891 / 2.563 / 3.354 | 1.809 / 0.930 / 1.270 | pass |
| rows4full | nmpeasab | 6.084 / 2.594 / 2.244 | 4.701 / 2.173 / 1.519 | pass |

Direction-level decisions in the headline runs (24 directions per set):

* rows4: 23 kept, 1 reverted (`hfprimab/3`, whose margin did not improve), 0 fallback.
* rows4full: 21 kept, 2 reverted (`nmpeasab/1`, `nmpeasab/5`), 1 fallback (`hfprimab/0`,
  flicker 18.083 -> 0.000 as original art; it was 3.2x its animation's mean direction score of
  4.99, over the 2x cap).
* The keyframe is not always frame 0 and not the same frame in the two sets (e.g. rows4full
  hmwarraa picks 2/0/8/4/4/6 for directions 0-5), which is the point: it is the pose that shares
  the most material with its cycle.
* Three directions were kept although a component rose (rows4 `hmwarrab/4` flicker 1.49 -> 1.79
  with material 2.33 -> 1.07, rows4 `hmwarrab/5` 1.29 -> 2.00 with material 2.13 -> 1.20,
  rows4full `hfprimab/1` broad 1.36 -> 1.68 with material 4.51 -> 2.37). All stay far inside the
  limits; the rule maximises the gate margin, not each component.

## Visual evidence (`tmp-temporal/`, git-ignored)

* Contact sheets, worst pair of each of the four worst directions, labelled
  (`HD t | HD t+1 | 4x diff | static mask`): `contact-before-{rows4,rows4full}.png`,
  `contact-after-{rows4,rows4full}.png`. Before, e.g. `hmwarrab/0` pair 6->7 switches costume and
  turns the character around, and `hfprimab/0` 6->7 changes the whole figure; after, those panels
  are the same character with different edges.
* Playback GIFs, three panels (`original 2x | before | after`) placed with the FRM's accumulated
  x/y shifts and a bottom-centre anchor, plus a strip PNG of every frame:
  `rows4full-hmwarraa-d2.{gif,-strip.png}`, `rows4full-hfprimab-d0.{gif,-strip.png}`,
  `rows4full-nmpeasab-d0.{gif,-strip.png}`, `rows4-hmwarrab-d0.{gif,-strip.png}`.
* 4x zoom before/after of single frames: `zoom-before-after.png`
  (`hmwarraa/2_8`, `nmpeasab/0_0`, `hfprimab/2_5`).

What the eye adds to the numbers: the trousers pattern and costume colours are one material across
the cycle instead of one per frame, no mosaic/speckle appeared at 4x (the earlier concern about
raw block copying), and detail is not blurred into mush — the 4x zoom keeps the invented HD
texture, it is simply the same texture in every frame.

## What the gate now measures (read this before trusting the pass)

Of the pixel-pairs the metric samples, the keyframe covers 45.6% (`hfprimab`), 54.8% (`hmwarraa`),
53.4% (`hmwarrab`) and 21.0% (`nmpeasab`) of rows4full; `static` copies the keyframe exactly there,
so on that share of the samples `flicker` and `broad` are 0 *by construction*. The stage is not a
pure metric hack — it is the fix the original's evidence asks for on those pixels, and the rest of
the sample is handled by `texture`/`colour`/`residual` with partial coverage — but the gate can no
longer be read as "the model is stable"; it reads "the pipeline's consistency rule is applied".
A production gate should therefore also record the fallback budget and keep per-frame QA, and no
animation should ship on the flicker number alone.

Candidates tested and rejected while building it, with their numbers:

* **Per-pixel material consensus** (each native pixel takes the medoid of the poses whose original
  pixel is identical there, `tmp-temporal/proto_consensus.py`): `flicker` **and** `broad` become
  exactly 0.000 on all 24 rows4full directions, because every non-material sample in the metric is
  such a pixel. That is saturation, not stabilisation: the metric stops being able to distinguish
  anything, and the output is a per-pixel patchwork of other poses' texels. Rejected; the
  keyframe-restricted `static` component is the honest version of the same idea.
* **Feathered per-class colour correction** (`sigma` 1.2 instead of an exact per-class offset):
  `material` 2.65 instead of 0.32 on hfprimab/4 of rows4full, because blurring mixes classes.
  On a thin-patterned sprite the classes never land on the target. Rejected.
* **Feathered static mask** (gaussian 0.9 instead of the binary mask): left 1.34 flicker on an
  otherwise identical synthetic animation whose interior is exactly copied, i.e. it keeps invented
  texture exactly in the measured pixels. Rejected; the binary mask cuts on the original's own
  palette edge.
* **Keeping the frame's outline band out of the static copy** was tested and dropped: an identical
  original pixel is the same outline pixel, and the silhouette comes from the untouched alpha.

## GPU: step 2 was not needed and was not used

The task's step 2 (keyframe-conditioned re-render with `TextEncodeQwenImageEditPlus` image2 = the
keyframe) is the remaining lever, and the 60-render budget was left untouched because step 1 met
the gate on all eight measurements — including the one direction it could not fix, which is handled
by the existing original-art fallback. Triggering it would have spent a shared GPU on a
demonstration rather than on production. If it is ever wanted, the recipe is: build the failing
direction's row sheet as `sheets.pack(..., by_animation=True)` does today, upload the direction's
keyframe (2x, the frame `choose_keyframe` picks) as `image2` of both `TextEncodeQwenImageEditPlus`
nodes in `upscale.Comfy.graph`, keep Lightning 8/30-step settings, and render through
`python -m wasteland_hd upscale` so the run waits for an empty queue and is receipt-checked. It
would be measured with the same `flicker` command, writing under
`/ai/wasteland-hd/critter-temporal/`.

## Go/no-go

**GO** for the 224,365-frame critter generation with this stage in the pipeline, under four
conditions:

1. The gate is enforced per animation in `production.py` after the stage, with a whole-animation
   original-art fallback if any animation still fails (in this calibration nothing needed it).
2. The per-direction fallback budget is tracked. It was 0/24 directions on rows4 and 1/24 on
   rows4full, all of it `hfprimab`, the animation with the thinnest static sample (1.6-2.6% of the
   sprite). Budget ~10% of directions and treat a higher rate as a signal the sheet recipe, not the
   stage, changed.
3. Per-frame QA stays on. On this tree it rejects 111 of 216 frames after the stage (124 before) —
   the calibration sheets were never QA'd or retried, so coverage, not the gate, is the binding
   constraint, and `qa`/`retry` behaviour is unchanged by the stage.
4. Semantic review still owns identity. `hfprimab` is the counterexample that matters: Qwen drew a
   **bearded man with leopard trousers** for a female critter in all six directions, and the stage
   preserves that faithfully — a stable wrong character scores well. Only the fallback direction
   shows the original woman, and QA rejects most of its frames on structure (scores 1.2-1.4 against
   a limit of 1.0), so the pipeline catches it, but a human must. Animations whose model output has
   the wrong sex, costume class or silhouette are **no-go regardless of their flicker numbers**;
   `hfprimab` should be re-rendered or shipped as original art, not gated in.

## Production integration (proposed)

Where it plugs in: `Controller.postprocess` in `wasteland_hd/production.py`, in the same place and
shape as the existing CPU stages, so it only ever runs on complete, integrity-checked directions
and its output is what `accept`/QA then sees:

```python
if self.args.temporal and (hd / "art" / "critters").exists():
    out = stage / "temporal" / "hd"
    try:
        self.command(["temporal", "--game", self.args.game, "--source", hd, "--out", out,
                      "--json", str(stage / "temporal-report.json")], stage / "temporal.json")
    except RuntimeError as exc:
        raise TimeoutError(f"CPU temporal stage failed; GPU seed checkpoint retained: {exc}") from exc
    report = read_json(stage / "temporal-report.json", {})
    for key, value in report.get("directions", {}).items():
        if value.get("fallback"):           # do NOT ship the 2x-nearest stand-in
            for expected in manifest["groups"][key]["frames"]:
                (out / expected["rel"]).unlink(missing_ok=True)
            state["groups"][key]["fallback"] = "original_game_art_entire_direction"
    hd = out
```

* **New flags/settings.** `--temporal` (like `--stabilise`) and a `TEMPORAL` settings block
  (`components`, `strength`, `radius`, `fallback_cap`, plus a rule version) folded into
  `SETTINGS`/`plan.json`, so the existing plan-equality guard refuses a restart with different
  options, and provenance in `state.json` becomes `{"kind": "generated+temporal", ...}`.
  `--stabilise` stays for A/B; the new stage supersedes it in effect (the old stabiliser's own
  report says it gains little). The default should be `--temporal` on for critters.
* **Animation gate.** After `temporal`, run `flicker` once per job over the job's critter
  animations (`--json` next to the stage report) and compare the pooled `broad`/`material`/
  `flicker` against 2.5/3.0/6.0. A failing animation: unlink all of its directions' frames, mark
  them `original_game_art_entire_direction` with `fallback: "flicker_gate"`, and record the numbers
  in `coverage.json`, so a gate failure is visible rather than silently shipped.
* **Budgets.** CPU only, one pass, ~50 ms per HD frame and 110 MB resident (10.5 s / 216 frames
  measured); about 3 CPU-hours for all 224,365 critter frames, versus the ~5 GPU-days of rendering.
  It is idempotent and deterministic: two runs of the same source produced byte-identical trees
  (`diff -r`). No extra GPU renders, so the fallback budget is not a GPU cost either.
* **Order.** Run after the per-frame candidates are accepted and after `seams`/`stabilise`, before
  the group is evaluated into `work/hd`, exactly as sketched — the stage must see the final HD
  frames whose alpha the engine will use, and QA must see the staged frames.
* **Fallback levels.** (a) direction over 2x the animation mean -> original art for that direction
  (the existing controller fallback); (b) animation failing the pooled gate -> original art for the
  whole animation. Both must delete frames, not ship the 2x-nearest stand-in this measurement used.

## Limitations

* The calibration is four animations from one art family and the same two sheet recipes as
  `docs/FLICKER-TEST-REPORT.md`, so the population spread of the real 224,365 frames is unknown; the
  numbers are a lower bound, and the static sample is thin (1.6-2.6% of the sprite for three of the
  four animations, 35-110 HD pixels per pair) for `hfprimab`, `hmwarrab` and `nmpeasab`.
* The stage changes RGB only; it judges nothing. A stable wrong beard, a stable male
  `hfprimab`, and a stable invented pattern all score well (`semantic review still owns
  correctness`).
* `material` is aligned per original palette class, so if every frame's classes are moved onto the
  keyframe the gate cannot see a *stable* costume error; and a direction whose keyframe itself is
  wrong anchors the whole direction to that error. Keyframe choice is by appearance only, not by
  semantic correctness or frontality.
* Directions whose original never repeats a pixel between the keyframe and a neighbour
  (`hfprimab/0`: 4-29 of 24-268 sampled pixels shared with the keyframe per pair) cannot be fixed
  by keyframe anchoring at all; they are what the fallback is for.
* `rim` (diagnostic, not gated) stays high-ish (1.8-6.1 after) because the outline band carries the
  model's one-pixel motion bleed; it is not part of the verdict.

## Owner review (2026-10-29)

Yan watched the before/after GIFs (`http://100.75.11.55:8844/critter-temporal/`) and approved the
"after" animations. Next: production integration behind `--temporal` (docs/FREEBUFF-TASK-CRITTER-PRODUCTION.md),
then lift `~/fallout2/tools/hd-critter-guard.sh`. `hfprimab` stays a semantic no-go (wrong identity),
which the temporal stage cannot fix.
