# Critter animation flicker: measurement and calibration (2026-10-29)

CPU only. No GPU, no model call, no service touched; `/ai/wasteland-hd` and
`~/fallout2` were read, never written. Files added: `wasteland_hd/flicker.py`,
the `flicker` subcommand in `wasteland_hd/__main__.py`, `tests/test_flicker.py`
(8 tests; the suite is 92 passed). Contact sheets and JSON reports mentioned
here live in `tmp-flicker/` (git-ignored).

```sh
python -m wasteland_hd flicker --game ~/fallout2/gamedata \
  --source /ai/wasteland-hd/critter-test/rows4full/hd \
  --json tmp-flicker/rows4full.json --contact tmp-flicker/contact-rows4full.png --top 4
python -m wasteland_hd flicker --game ~/fallout2/gamedata \
  --source /ai/wasteland-hd/critter-test/rows4/hd --control nearest \
  --json tmp-flicker/control-nearest.json
```

Contact sheets for spot-checking (4x, `HD t | HD t+1 | 4x diff | static mask`,
worst pair of each row labelled): `tmp-flicker/contact-rows4.png`,
`tmp-flicker/contact-rows4full.png`, `tmp-flicker/contact-fast4-hmwarrab.png`
(flat grey frame the model returned), `tmp-flicker/contact-full4-hmwarrab.png`.
Per-set JSON: `tmp-flicker/{rows4,rows4full,fast,full,fast4,full4,fast4s,full4s,
control-nearest,control-lanczos}.json`.

`--source` is a HD tree (`art/critters/<name>/<direction>_<frame>.png`),
`--anim` restricts the animations, `--control nearest|lanczos` scores a
deterministic upscale of the originals instead (no HD frame is read),
`--limit` exits nonzero when an animation is above it, and `--json`/`--contact`
are refused inside `--source`. Frames must be exactly 2x with nearest-neighbour
alpha or the command fails with the offending path.

## What flicker is, and why this definition

A walk cycle is *supposed* to change. A metric that compares neighbouring
frames directly punishes real motion, and a per-pixel colour drift cannot say
which changes are invention. The original FRM is the only ground truth, and it
makes a very specific claim: over most of a critter (torso, helmet, boots,
bandolier, shoulder straps) two adjacent poses are byte identical. Every colour
difference the model puts there is generation noise -- at 8-12 FPS, boiling.

For one animation (frames x directions) the headline score is

```
flicker = mean over adjacent pairs (i, i+1 mod n) over the cycle
          mean over static pixels p of  |HD_i(p) - HD_{i+1}(p)|   (0-255 RGB)
```

where `p` is **static** when the original FRM frames agree there: same RGBA,
opaque, and the mean RGB change of the original's 3x3 neighbourhood is at most
6/255 (`LOCAL_LIMIT`), which keeps a limb that moved one pixel next to the
sample out of it. The 2x HD frames are packed top-left on a shared canvas and
compared pixel for pixel: nothing is warped, because the metric only ever
looks at material that is in the same place with the same content in both
frames (any consistent placement gives the same answer; the FRM's per-frame
x/y offsets are not usable for this, they do not align content in this art).
Direction scores are pooled by static pixel count into the animation score.

Units are 8-bit intensity. Two independent per-frame noise fields of standard
deviation sigma give `E|dA - dB| = 2 sigma / sqrt(pi) = 1.128 sigma`, so the
headline is about 1.1x an "equivalent independent per-pixel noise amplitude".
A deterministic upscale of the originals scores **exactly 0**: real motion
costs nothing, because content that moved is not compared at all.

Reported next to it, same mask unless stated:

| field | definition | what it isolates |
| --- | --- | --- |
| `broad` | mean of \|low-pass of the **difference field**\*\| | coherent colour/shading drift: clothing or skin changing hue between frames |
| `fine` | mean of \|difference - its low-pass\| | detail boiling independent of broad drift: texture, stubble, straps, pattern |
| `rim` | same as `flicker` on the one-native-pixel band touching the silhouette | outline/edge treatment jitter |
| `interior` | same as `flicker` off that band | interior material |
| `material` | per-palette-class mean HD colour drift between frames, weighted by class area | costume identity, **translation invariant**, so it also covers moving material |
| `registered` | `temporal.score_group` on the same canvases | the existing stabiliser's objective, kept only as a diagnostic |

\* Normalised convolution of the masked difference (`LP(delta*m)/LP(m)`), so the
blur cannot pull a moving neighbour into a still pixel. Differencing blurred
*frames* instead leaks them in: it gave a deterministic upscale a nonzero score.

Candidates evaluated and rejected:

* **change outside the moving silhouette** -- `sheets.cut` copies the original
  alpha at 2x nearest and `qa.badness` rejects any other alpha, so this is
  identically zero for any pipeline output. It measures nothing.
* **raw per-pixel colour drift between neighbours** -- dominated by genuine
  motion; the original walk alone scores as badly as a model output.
* **warp everything into correspondence** (what `temporal.py` does) -- coarse
  1998 art matches only to within a pixel or two, and that misregistration
  error is larger than the noise it is meant to reveal. The controls below
  quantify it: on the noise-free Lanczos upscale the `registered` column is
  already 1.76-2.48, a third of what a real model output scores there.

## Calibration

All results below come from the frozen evidence in
`/ai/wasteland-hd/critter-test/` and `/home/yan/fallout2/gamedata`.
`rows4` = one-animation-per-row sheets (`pack(by_animation=True)`), 256 px
sheets, 4x render, `--prompt animation`, Lightning 8 steps; `rows4full` = the
same sheets at 30 steps / cfg 4. `fast`/`full` are the older shelf-packed 512
sheets (constant `PROMPT`), `fast4`/`full4` and `fast4s`/`full4s` their 4x
variants. Every PNG graph on disk was checked to confirm the settings.

Controls (a deterministic upscale of the originals, so generation noise is zero
by construction) and the model sets:

| set | animation | dirs | static px | **flicker** | broad | fine | rim | material | registered |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| original 2x nearest | hfprimab | 6 | 2472 | **0.00** | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| original 2x nearest | hmwarraa | 6 | 63124 | **0.00** | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| original 2x nearest | hmwarrab | 6 | 2680 | **0.00** | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| original 2x nearest | nmpeasab | 6 | 3888 | **0.00** | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| original 2x lanczos | hfprimab | 6 | 2472 | **1.75** | 0.42 | 1.66 | 2.76 | 0.74 | 1.76 |
| original 2x lanczos | hmwarraa | 6 | 63124 | **1.70** | 0.29 | 1.65 | 1.80 | 0.54 | 1.77 |
| original 2x lanczos | hmwarrab | 6 | 2680 | **1.87** | 0.38 | 1.79 | 2.59 | 0.69 | 1.92 |
| original 2x lanczos | nmpeasab | 6 | 3888 | **2.51** | 0.57 | 2.34 | 2.43 | 0.55 | 2.48 |
| rows4 (fast, rows) | hfprimab | 6 | 2472 | **4.37** | 2.69 | 3.14 | 5.58 | 3.83 | 5.95 |
| rows4 (fast, rows) | hmwarraa | 6 | 63124 | **4.75** | 2.67 | 3.27 | 5.36 | 2.54 | 4.35 |
| rows4 (fast, rows) | hmwarrab | 6 | 2680 | **2.43** | 1.53 | 1.82 | 5.83 | 2.42 | 2.23 |
| rows4 (fast, rows) | nmpeasab | 6 | 3888 | **5.16** | 2.75 | 4.00 | 5.42 | 1.98 | 5.11 |
| rows4full (30 steps, rows) | hfprimab | 6 | 2472 | **8.93** | 4.03 | 7.70 | 8.03 | 4.22 | 6.86 |
| rows4full (30 steps, rows) | hmwarraa | 6 | 63124 | **4.84** | 1.96 | 4.20 | 5.67 | 3.02 | 4.86 |
| rows4full (30 steps, rows) | hmwarrab | 6 | 2680 | **3.89** | 2.56 | 2.48 | 6.65 | 3.35 | 2.97 |
| rows4full (30 steps, rows) | nmpeasab | 6 | 3888 | **6.08** | 2.59 | 5.12 | 5.86 | 2.24 | 5.71 |
| fast (shelf) | hmwarraa | 6 | 63124 | **4.23** | 2.12 | 3.25 | 5.44 | 2.07 | 4.34 |
| fast (shelf) | hmwarrab | 6 | 2680 | **2.96** | 2.09 | 1.82 | 7.83 | 5.23 | 3.07 |
| full (shelf) | hmwarraa | 6 | 63124 | **4.38** | 1.69 | 3.79 | 5.04 | 1.92 | 4.45 |
| full (shelf) | hmwarrab | 6 | 2680 | **3.81** | 2.90 | 2.04 | 8.09 | 7.41 | 4.29 |
| fast4 (shelf 4x) | hmwarraa | 6 | 63124 | **24.23** | 15.58 | 16.81 | 21.05 | 9.72 | 24.32 |
| fast4 (shelf 4x) | hmwarrab | 6 | 2680 | **42.70** | 34.36 | 21.08 | 38.10 | 16.01 | 46.22 |
| full4 (shelf 4x) | hmwarraa | 6 | 63124 | **5.02** | 3.66 | 3.07 | 5.50 | 2.57 | 4.04 |
| full4 (shelf 4x) | hmwarrab | 6 | 2680 | **7.99** | 7.52 | 1.59 | 9.48 | 11.31 | 13.24 |
| fast4s (shelf 4x small) | hmwarraa | 6 | 63124 | **4.61** | 2.02 | 3.73 | 5.56 | 2.60 | 4.86 |
| fast4s (shelf 4x small) | hmwarrab | 6 | 2680 | **3.52** | 2.69 | 1.80 | 7.18 | 3.11 | 3.37 |
| full4s (shelf 4x small) | hmwarraa | 6 | 63124 | **4.61** | 1.83 | 3.98 | 5.32 | 2.56 | 4.63 |
| full4s (shelf 4x small) | hmwarrab | 6 | 2680 | **2.80** | 2.13 | 1.64 | 5.87 | 3.76 | 2.75 |

Verdict on the two calibration requirements:

* **The originals are the low end.** Exactly 0.000 on every animation and
  direction, and the deterministic Lanczos re-draw -- which has zero generation
  noise by construction but does resample every pixel -- scores 1.70-2.51. All
  model output sits above that floor, so the metric ranks generation noise, not
  resampling. `flicker` is zero for real motion by construction, and the test
  suite pins both facts down (`test_original_nearest_has_zero_flicker`,
  `test_real_motion_is_not_flicker`).
* **The known verdict (full 30-step rows better than fast rows) is reproduced
  only for the warrior walk, and only in `broad`.** On `hmwarraa`,
  `rows4full` has `broad` 1.96 against `rows4` 2.67 (27% less coherent colour
  drift) in the largest static sample (24% of the sprite, 63 124 pixel pairs).
  The same relationship holds for every other full-vs-fast pair on that walk:
  `full` 1.69 vs `fast` 2.12, `full4s` 1.83 vs `fast4s` 2.02, `full4` 3.66 vs
  `fast4` 15.58. But the headline `flicker` does not: 4.84 vs 4.75 (rows),
  4.38 vs 4.23 (shelf). The reason is precise and visible in the numbers: the
  headline is dominated by `fine`, and 30-step/cfg-4 output invents more
  high-frequency detail per frame, which then lands differently on each frame.
  For `hmwarraa` `fine` is 4.20 (full) vs 3.27 (fast) while `broad` moves the
  other way; the `rim` column (outline treatment) is essentially unchanged
  (5.67 vs 5.36). Where the model is more conservative (Lightning 8 steps), its
  detail is closer to the coarse source and changes less, so it wins on raw
  pixel difference without being more stable in appearance.

  So the eyeball call and the metric agree about *appearance identity* -- the
  thing the call was about -- and disagree about *detail-level pixel change*,
  because 30 steps buys sharper invented texture at the cost of sub-pixel
  placement stability. The metric should not be read as "less detail is
  better": `flicker` is a distance between frames, not a sharpness reward, and
  the `broad`/`fine` split is what separates the two effects. On the other
  three animations full steps are worse on every component (`hfprimab` 8.93 vs
  4.37, `nmpeasab` 6.08 vs 5.16, `hmwarrab` 3.89 vs 2.43), so any claim that
  "30 steps is better" must be about the warrior, not a general rule.

## Does `pack(by_animation=True)` + `--prompt animation` suffice?

It is necessary and it is a large win, but on this evidence it is **not
sufficient**.

* Same model, same 4x render, same seeds, only the sheet layout and prompt
  change: `rows4` vs `fast4` on `hmwarraa` is **4.75 vs 24.23** and on
  `hmwarrab` **2.43 vs 42.70** -- a 5x and a 17x reduction. `fast4` is also the
  set with the largest broken-frame population (`hmwarrab/4` scores 58.26; the
  contact sheet shows a frame the model returned as a flat grey silhouette),
  and the row layout plus the "draw the character identically in every frame of
  a row" prompt is what removes them.
* It does not remove per-frame independent divergence. With animation sheets
  and full 30 steps, every animation still fails at least one limit below (the
  warrior walk only by 0.02), each for a different reason: `hfprimab`
  re-invents the leopard trouser pattern per frame (`fine` 7.70), `hmwarrab`
  changes costume colours (`material` 3.35, and the worst pair 6->7 redraws the
  character from behind), `nmpeasab` swaps its speckled surface (`fine` 5.12).
* Within the warrior walk the residual is concentrated in a few pairs, not
  spread evenly: `rows4/hmwarraa/3` scores 6.29 while its other five
  directions are 3.67-4.75, and pairs 6->7 and 11->0 carry the excess. A
  per-direction gate would keep most of the animation and resample only the
  bad poses, which is what a joint or keyframe-anchored pass would target.

## Proposed gate

Per animation (pooled over directions, from `--json`), require all three:

| signal | limit | why |
| --- | ---: | --- |
| `broad` | **2.5** | coherent colour/shading drift on material the original says is unchanged. The noise-free Lanczos floor is 0.29-0.57, and the source palette is stored 6-bit (4 intensity steps), so 2.5 is under one quantisation step averaged over a region -- the smallest drift that cannot be blamed on the palette or on resampling. |
| `material` | **3.0** | per-material colour identity between poses, the "grey trousers become brown" failure. Floor 0.54-0.74 on the control; 3.0 is ~0.75 palette steps of mean per-class shift. |
| `flicker` | **6.0** | detail boil cap (about 5.3 units of equivalent independent per-frame noise). It is not the primary signal because it is confounded by how much detail the model invents, but it catches true offenders: `fast4` (24-43) and `full4/hmwarrab` (7.99) return whole frames as flat grey silhouettes, which is exactly what their contact sheets show. |

`--limit` on the CLI checks only `flicker`; the two-component decision is a
yes/no over the JSON. A direction-level cap of twice its animation's mean
catches the single-pair failures (`rows4full/hfprimab/0` 18.08 against an
animation mean of 8.93); a direction that fails is the unit to redraw or fall
back to original art, matching the production controller's existing
`original_game_art_entire_direction` fallback.

Applying it to the frozen evidence:

| set | hfprimab | hmwarraa | hmwarrab | nmpeasab |
| --- | --- | --- | --- | --- |
| rows4 (fast rows) | fail (broad, material) | fail (broad) | **pass** | fail (broad) |
| rows4full (30 steps rows) | fail (broad, material, flicker) | fail (material, 3.02 vs 3.0) | fail (broad, material) | fail (broad, flicker) |
| fast / full (shelf) | -- | **pass** | fail (material) / fail (broad, material) | -- |
| fast4 / full4 (shelf 4x) | -- | fail (broad, material, flicker) / fail (broad) | fail (broad, material, flicker) | -- |
| fast4s / full4s (shelf 4x small) | -- | **pass** | fail (broad, material) / fail (material) | -- |

**Go/no-go: NO-GO for the 224,365-frame critter generation** on the current
recipe. Five of twenty animation measurements pass: the 12-frame warrior walk
(`hmwarraa`) in four of the eight sets -- where the row layout works best and
the static sample is large enough to trust (24% of the sprite) -- plus
`hmwarrab` in fast rows only. The 30-step rows miss on `material` by 0.02
(3.02 against 3.0), which is a tie for practical purposes, but the same set
then fails `hfprimab`, `hmwarrab` and `nmpeasab` outright, so the verdict does
not hinge on it: every other animation fails on `broad` or `material`, i.e.
the exact boiling the generation is gated on.
The recipe is still the right base -- it removes the catastrophic-frame class
-- but it needs a temporally-aware stage (joint multi-frame rendering, a
keyframe-anchored re-render of failing directions, or the deterministic
stabiliser extended to the identity signals) before the animal and female
critters can ship. The warrior walk may be generated and shipped under this
gate while that work happens; the whole measurement is CPU-only and costs
about five seconds for the four-animation, 216-frame tree used here, so gating
adds nothing worth counting next to 5 GPU-days.

## Limitations

* Static coverage depends on the animation: 24.3% of the sprite for the warrior
  walk, but only 1.6-2.6% for `hfprimab`, `hmwarrab`, `nmpeasab` (35-110 HD
  pixels per pair). Their `flicker` is therefore a small sample of the cycle;
  `material` is the more trustworthy signal there, and thin/plant sprites whose
  poses share no 3x3 still patch get very few pixel samples at all.
* `rim` mixes the drawn outline with the one-pixel motion-bleed of the model at
  the silhouette; it is a diagnostic, not a gate.
* Nothing here judges whether the HD art is *right* -- only whether it stays
  the same. A stable but wrong beard scores 0; `qa` and human review still own
  semantic correctness.
* `registered` is `temporal.score_group` on these canvases (top-left packing),
  so its numbers are not comparable with the bottom-centre values in
  `docs/ANIMATION-STABILITY.md`; it exists to show that the old objective is
  compressed by correspondence error (Lanczos control 1.76-2.48 with zero
  generation noise) and never gates anything.
* The calibration uses four critters from one art family, chosen by what was
  already on disk. The numbers are a lower bound on production spread, not a
  population estimate.
