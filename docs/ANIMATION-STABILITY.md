# HD critter animation stability (2026-10-28)

Independent Qwen-Image-Edit frames can look good individually while clothing, hair, face markings, outlines, and lighting change between consecutive frames. At 8–12 FPS this appears as *boiling*. This CPU-only pass attempts to reduce changes where the original Fallout 2 sprites contain evidence that two frames depict the same material.

## Run

```bash
python -m wasteland_hd stabilise --game ~/fallout2/gamedata \
  --source work/run/hd --out work/stabilised/hd
```

The output must be an empty, separate destination. The command copies the entire input HD tree, then rewrites only `art/critters/<animation>/<direction>_<frame>.png`; the other files retain identical bytes. It refuses nested source/destination paths, mismatched dimensions, or alpha that is not exactly the original sprite at 2× nearest-neighbour. Requires `numpy`, `scipy`, and `Pillow`; never calls the GPU or a model. `--radius` defaults to 9 original pixels.

## Metric

Every frame is placed on a common bottom-centred canvas. Pixel-level *world* translations from the FRM's cumulative `(frame.x, frame.y)` shifts are factored out of correspondence search; the playback GIFs restore those shifts. A 5×5 patch search on **original** palette-derived RGBA frames finds a matching source pixel in the adjacent pose within 9 original pixels. Matches with incompatible colour or silhouette receive near-zero weight.

Let `R_t = HD_t − nearest2(original_t)`, retaining each individual 2×2 child texel. For each matched original pixel the score is the mean absolute RGB difference of `R_t` and the adjacent frame's motion-warped `R_(t+1)`, averaged with confidence weights over the cycle (including last→first). Units: 0–255 pixel intensities; **smaller is better**. A pure original nearest-neighbour 2× sequence scores **exactly 0**, even with normal animation motion. This measures unexplained appearance changes on corresponded regions, not the artistic quality or semantic correctness of the model's output. It can miss flicker in unmatched limbs and cannot establish that a beard should exist.

## Deterministic stabilisation

- **Shared appearance atlas:** tightly corresponding original patches contribute Qwen-minus-native residuals to a median reference. This reference is transferred back with soft confidence and original body pose respected. An optional palette-class correction damps colour drift without directly blurring all texture.
- **Smooth neighbouring correction:** corresponding frames contribute their residual *difference*. After confidence-weighted spatial smoothing (2.5 HD px) and a ±22 RGB correction cap, it changes broad colour fluctuations while retaining the current HD frame's sharp detail. Raw block-by-block copying was rejected because it introduced obvious speckle and checker patches despite improving the numeric metric.
- **Per-direction selection:** use the median atlas or the smoothed neighbour result, whichever has the lower measured temporal score; keep the original pixels when neither helps. When all native frames are identical, assign the exact per-pixel median frame to the entire sequence.

The pipeline never edits the source. It preserves dimensions and copies native alpha at 2× nearest-neighbour verbatim. The score is an *internal acceptance gate*: improvements on that score do not guarantee all motion will look natural.

## Measurements

Eight available test sources were evaluated on 2026-10-28 using `/home/yan/fallout2/gamedata`; results below average the six direction scores of each animation (not weighted by frame count). Read-only test art: `/ai/wasteland-hd/critter-test/<set>/hd/`. Outputs: `work/stability/<set>/hd/`. Full **per-animation direction** numbers: `work/stability/metric-report.md`, machine-readable `work/stability/metrics.json` and `work/stability/metric-directions.csv` (all gitignored). A smaller number indicates fewer unexplained frame-to-frame RGB differences.

| Test set | Animation | Mean before | Mean after | Improved directions |
|---|---|---:|---:|---:|
| fast | hmwarraa | 8.318 | 8.157 | 4/6 |
| fast | hmwarrab | 10.129 | 10.027 | 6/6 |
| fast4 | hmwarraa | 12.961 | 12.000 | 5/6 |
| fast4 | hmwarrab | 13.840 | 13.330 | 6/6 |
| fast4s | hmwarraa | 8.357 | 8.194 | 5/6 |
| fast4s | hmwarrab | 9.922 | 9.910 | 5/6 |
| full | hmwarraa | 8.084 | 7.917 | 5/6 |
| full | hmwarrab | 9.971 | 9.917 | 6/6 |
| full4 | hmwarraa | 6.161 | 5.982 | 5/6 |
| full4 | hmwarrab | 8.636 | 8.066 | 6/6 |
| full4s | hmwarraa | 8.199 | 8.007 | 4/6 |
| full4s | hmwarrab | 9.498 | 9.477 | 5/6 |
| rows4 | hfprimab | 8.571 | 8.559 | 5/6 |
| rows4 | hmwarraa | 8.472 | 8.359 | 5/6 |
| rows4 | hmwarrab | 9.953 | 9.938 | 5/6 |
| rows4 | nmpeasab | 8.502 | 8.454 | 6/6 |
| rows4full | hfprimab | 8.214 | 8.206 | 6/6 |
| rows4full | hmwarraa | 8.428 | 8.289 | 4/6 |
| rows4full | hmwarrab | 9.694 | 9.659 | 6/6 |
| rows4full | nmpeasab | 8.471 | 8.437 | 6/6 |

For the Arroyo warrior walk, `full4s` starts at **9.498**, below `fast` at **10.129**, matching the visibly more stable clothing and facial detail of the 4×-then-downsampled full sequence. These source distributions differ; the metric is comparative, not a ground-truth animation score.

## Playback evidence and visual caveats

GIFs in `work/stability/` use the game's per-frame accumulated x/y shifts, a common bottom-centre anchor, and three labelled panels: **original 2× | AI before | stabilised after**. Contact sheets (`*-contact.png`) make differences visible by frame. Examples:

- `fast-hmwarrab-d2-original-before-after.gif` (8-frame warrior walk)
- `fast-hmwarraa-d2-original-before-after.gif` (12-frame warrior idle)
- `full4s-hmwarrab-d2-original-before-after.gif` and `full4s-hmwarraa-d2-original-before-after.gif`
- `rows4-hmwarrab-d2-original-before-after.gif` and `rows4-hmwarraa-d2-original-before-after.gif`
- `rows4full-hmwarrab-d2-original-before-after.gif` and `rows4full-hmwarraa-d2-original-before-after.gif`

Visual inspection found the smooth/capped correction retains outlines and detail more faithfully than aggressive neighbour transfer, which caused visible mosaic noise. The remaining beard, straps, shorts, and trouser-pattern substitutions in some walk frames are **semantic hallucinations**; a deterministic local filter cannot reliably decide which appearance is canonically correct from a coarse 1998 FRM. This is a partial improvement to temporal continuity, not a complete fix for frame-to-frame costume identity. Some directions may be left unchanged by the acceptance gate.

## Next experiment

Compare Claude's **one-animation-per-row** `rows4` sheet prompts against `full4s` and `fast` on both the score and these GIFs before adopting them. For meaningful semantic correction, extract stable labels/region masks (hair/beard, bandolier, pants, wraps) from a chosen keyframe, propagate them using native-pose correspondence, and selectively re-render inconsistent masked regions *jointly across frames* (requires Qwen time; this CPU-only pipeline does not). Also try landmarks/optical-flow alignment on the original silhouette plus backward/forward match consistency to handle limbs and self-occlusion better. Keep the native-alpha invariance and verify in-game playback before publishing art.
