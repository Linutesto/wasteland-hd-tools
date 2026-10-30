# Chroma-cast report (task K, 2026-10-30)

Branch `agent/chroma`, worktree `~/agent-wt/chroma`. Closes the open item of
`docs/WALL-RESIDUAL-REPORT.md` ("the purple/blue tint on the stained wall"):
a *regional* colour cast that `qa.colour_shift` / `qa.badness` structurally
cannot see, because both compare regional **means** and a cast covering 2% of a
wall moves the mean by less than a unit.

Everything below is CPU-only, read-only against `/ai/wasteland-hd/*` and both
shipped HD roots; nothing was pushed, no service was used.

## 1. The metric — `wasteland_hd/chroma.py`

`cast_measure(hd, original)` works at native resolution — the HD frame
BOX-downscaled to the original's size, exactly how `qa.colour_shift` compares
colours:

1. **Exposure match first.** `_exposure_match(src, dst)` re-exposes the
   original to the HD frame's luminance (a gain taken in gamma space,
   clamped to `[0.25, 4]`, identity verified exact: max error 0). A redraw that
   only renders the same colours brighter/darker also moves Lab a*/b* (a lighter
   red is a more saturated red); without this step, brightness-only differences
   read as casts.
2. **Residual chroma per pixel**: CIELAB a*/b* of the HD frame minus the
   exposure-matched original's.
3. **Connected regions** of pixels whose residual exceeds `CAST_PIXEL_DELTA = 4.0`
   (`scipy.ndimage.label`), inside the opaque mask minus a 1-px silhouette band
   (silhouette AA is normal, interior-only when the sprite has ≥ 64 interior px).
4. Each region is scored by the magnitude of its **mean** vector: ≥ `CAST_MIN_AREA = 24`
   native px **and** mean ≥ `CAST_REGION_DELTA = 8.0`. A frame is flagged when the
   flagged regions cover ≥ `CAST_AREA_LIMIT = 0.015` of the opaque mask.

Single noisy pixels never reach a verdict: they have neither area nor a
consistent direction, and the *mean* vector is what a real cast preserves while
noise cancels. The returned dict (`opaque_px, cast_px, cast_area, cast_regions,
cast_max, cast_dir, mean_delta`) is JSON-friendly; `mean_delta` is the quantity
the existing mean gates see, kept for comparison.

## 2. Measurement of all shipped frames

`tmp-chroma/measure.py` → `tmp-chroma/cast.json` (~14 s, both roots:
`~/fallout2/tests/hd-pack-lightning/hd` and `~/agent-wt/pack/tmp-pack/out/hd`;
16,700 rows, 9,244 unique by (rel, sha)). Per category, frames **over
CAST_AREA_LIMIT**:

| kind   | frames/root | exact source | over limit (lightning / pack) | area p50 / p90 / p99 / max (lightning) |
|--------|------------:|------------:|------------------------------:|----------------------------------------|
| tiles  | 3,082 | 13 / 12 | **167 / 180** | 0 / 0 / 0.073 / 0.428 |
| walls  | 1,673 | 11 / 0  | **103 / 107** | 0 / 0 / 0.807 / 0.901 |
| scenery| 2,782 | 50 / 50 | **227 / 227** | 0 / 0.001 / 0.812 / 0.999 |
| items  |   299 |  0 / 0  | **16 / 16**   | 0 / 0 / 0.862 / 0.953 |
| misc   |   514 | 35 / 35 | **54 / 54**   | 0 / 0.287 / 0.956 / 0.978 |

607 of 9,244 unique frames are flagged (6.6%). The two roots agree everywhere
except tiles/walls, where the builds differ frame-for-frame; that is why those
two rows have two over-limit counts.

## 3. Labels and calibration

Contact sheets of the **worst 40** by `cast_area`
(`tmp-chroma/sheets/NN-<frame>.png`, original 2x | HD shipped | cast mask, with
the metrics and region swatches in the caption) were looked at individually and
labelled in `tmp-chroma/labels.json` (57 labels total: 55 `cast`, 2 `fine`):

- **Worst 40 — all `cast`.** Every one is a real, whole-region, visible chroma
  shift on an `exact=False` redraw, not AA, not brightness, not noise: the
  placeholder-red fields (`shuttl*`, `pooltbl*`, `minect1*`, `mstr6*`, `tower*`
  "UNUSED ART") lose ~(-13, +11, +8) RGB of red saturation; `lazrpis` laser
  pistols shift the same cool way; `spear`/`goop02` shift warm; `vertibrd`
  moves 16 in B−G on its purple field (the exact quantity the wall report
  measured); `jas1000` (wall) and `block2` (wall) shift warm.
- **Boundary sample** just over and just under the limit (13 sheets in
  `tmp-chroma/boundary/`): 11 `cast`, 2 `fine`. The `cast` ones are small but
  real patches (`nec125` 47 px gray→red-brown, `nec167` 73 px purple→gray with
  max 20.4, `ibox06` blue crate losing its blue…). The 2 `fine` are
  brightness-dominated: `sfroad09` (dL +11.3, residual left where highlights
  clip) and one `inside4` variant with no region at all.
- **Tiles** (`tmp-chroma/tiles/`): the two worst (`bld2029`, `bld2032`,
  "Unused Tile" placeholders, area ≈ 0.43) are real casts too — see §6.

The `cast_max` histogram over all 9,244 unique frames shows why the constants
were kept as calibrated:

```
[0,2): 4011  [2,4): 2629  [4,6): 1442  [6,8): 459   <- resampling noise floor
[8,10): 243  [10,12): 81   [12,15): 166 [15,20): 125 [20,30): 81 [30,50): 6
```

Noise (AA, dithering, palette rounding) dies out below ~6; the real-cast
population starts at 8 and runs to ~30. `CAST_REGION_DELTA = 8.0` sits in the
gap, `CAST_MIN_AREA = 24` px removes isolated pixels, `CAST_AREA_LIMIT = 0.015`
keeps the flag at frame level (6.6% of frames — visible when reviewing, far
below anything that could fail a build blind).

**Not calibrated as a hard fail.** Labels are one-sided at the top (all 40 worst
are real) and thin at the boundary (17 boundary/tile labels), which is enough
for a *detail*, not enough to make 607 frames fail a gate. Per the brief the
verdict is untouched (§7).

## 4. The correction — `cast_correct(hd, original)`

Inside the feathered flagged region (2x space, sigma `CAST_SIGMA = 10`,
feather `CAST_FEATHER = 8` px, smooth-stepped) the **low-frequency part of the
a*/b* difference** — the cast — is subtracted; HD luminance (L), HD chroma
detail (high-frequency difference) and the alpha channel are kept. Where
nothing is flagged the frame comes back **byte-identical** (verified on a clean
frame; this is what makes the repair step safe).

Measured chroma error over the flagged mask (Lab a*/b* mean-vector distance to
the original, native resolution):

| frame | area | before | after | L before→after |
|---|---|---|---|---|
| `art/scenery/shuttl05/0_0.png` | 0.999 | 8.5 | **1.0** | 42.6 → 43.8 |
| `art/walls/ware004/0_0.png`    | 0.107 | 8.4 | **2.4** | 19.1 → 19.1 |
| `art/walls/cath24/0_0.png`     | 0.018 | 5.1 | **1.2** | 11.1 → 11.0 |
| `art/misc/lazrpis/3_1.png`     | 0.978 | 2.4 | 2.9    | 32.0 → 32.0 |

(`lazrpis` is the one case where the correction does not improve the *raw*
mean difference: its measured cast (14.8) comes from the exposure-matched
residual while the raw chroma difference was already small; the change is
sub-ΔE and the picture is unchanged. Everything else halves or better.)

Before/after artifacts (original | shipped | corrected, and original | shipped |
mask | corrected):

- `tmp-chroma/before-after/*.png` — 7 frames: `ware004`, `necrn15`, `cath24`,
  `jas1000`, `block2`, `shuttl05`, `lazrpis/3_1`.
- **Klamath mall**: `tmp-chroma/before-after/klamath-e0.png` renders
  `klamall.map` elevation 0 three ways (original | as ships today | with
  `cast_correct` on the flagged walls) using the `tmp-walls`-style compositing
  from `~/agent-wt/walls/tmp-walls/interiors.py` (read-only), crop
  `(2900, 1600, 3700, 2400)` — the region the wall report measured. 168 wall
  frames are placed on that elevation; **5 flag**:

  | wall | cast_area | px | max | dir | whole-frame mean |
  |---|---|---|---|---|---|
  | `art/walls/ware004/0_0.png` | 0.1069 | 308 | 11.4 | [3.1, 8.1] | 3.05 |
  | `art/walls/nec086/0_0.png`  | 0.0233 | 40  | 13.3 | [5.9, −11.9] | 1.15 |
  | `art/walls/nec116/0_0.png`  | 0.0203 | 74  | 9.2  | [5.0, 6.4]  | 0.76 |
  | `art/walls/cath24/0_0.png`  | 0.0175 | 64  | 24.9 | [18.1, −17.1] | 0.83 |
  | `art/walls/necrn15/0_0.png` | 0.0168 | 59  | 29.0 | [20.4, −20.6] | 0.22 |

  Note the last column: every one of these sits far **below** the regional-mean
  gates' limits — this is precisely the class of shift the old gates cannot
  see. The corrected panel of the composite is deliberately subtle (a chroma
  nudge on flat wall areas, no geometry change), which is what a fidelity fix
  should look like; `tmp-chroma/before-after/klamath-walls.json` holds the full
  per-frame measurements for all 168.

## 5. What did *not* change

- **Verdicts.** `cast_measure` is reported, never gated: no frame changes its
  verdict because of a cast (see §7 for the one flag).
- **Floors/tiles by default.** `invention.score` does not measure tiles unless
  `cast_tiles=True`, so tile/floor outputs and returned keys are byte-identical
  to before. `repair.py`'s floor paths (`source_floor_2x`, `edge_lock`) are
  untouched.
- **`/ai`**, the two HD roots and the game files: read-only, never written.

## 6. Floors do have casts — gated behind a flag

Label evidence says the flagged tiles are real (worst two are visible
placeholder-red desaturation, §3), and 167–180 tile frames flag per root. Per
the brief this is **reported here and gated behind a flag**, not enabled:
`invention.score(..., kind='tiles', cast_tiles=True)` measures them
(default `False` keeps tiles byte-identical), and `repair.py` gains **no**
floor correction step. If floor casts should ship a fix, the next step is a
labelled floor sample around the limit plus a repair step for
`art/tiles/*` — both deliberately out of scope here.

## 7. Wiring

**`wasteland_hd/invention.py` — verdict detail, not a hard fail:**
`score()` now returns a `cast` measurement dict alongside `verdict` for every
non-tile kind (`if kind != 'tiles' or cast_tiles`). The verdict chain itself is
untouched, so no frame can fail because of it; consumers (repair gate, reports)
see the detail in `measured['frames'][rel]['cast']`.

**`wasteland_hd/repair.py` — CPU step before falling back, like `edge_restore`:**
`_repair_wall_group`'s attempt chain is now
`edge-restore → junction-lock → chroma-cast → original art`. The new
`('chroma-cast', 'chroma_cast')` attempt runs `cast_correct` over the group's
frames, is guarded against re-running (`candidate.name != 'chroma-cast'`, same
pattern as `junction-lock`), is a byte-identical no-op when nothing is flagged,
and is re-gated exactly like the other attempts. `'chroma_cast'` was added to
the `fallback` provenance whitelist. As with `edge_restore`, this is wall-group
scope; the floor ladder (`edge_lock`, `source_floor_2x`) is unchanged.

## 8. Tests

```
python3 -m pytest -q          ->  132 passed in 5.47s
```

New `tests/test_chroma.py` (5 tests, synthetic art): a connected cool block is
flagged with `cast_area > limit` while the frame mean stays below the region;
scattered ±10 noise is not a cast; `cast_correct` halves the chroma error while
keeping alpha and L (within 2.0); a clean frame comes back byte-identical;
`score()` reports `cast` for walls, adds no key for tiles, and the
`cast_tiles=True` flag adds the detail without touching the verdict, native or
scale-2 measurements. The existing 127 tests (invention, repair, seams, qa…)
pass unchanged.

## 9. Artifacts and limits

Artifacts (working tree, `tmp-chroma/` — untracked by repo convention, like
`tmp-walls/`): `measure.py`, `sheets.py`, `klamath.py`, `labels.json`,
`PROGRESS.md`, `cast.json` (4.9 MB, all measurements), `sheets/` (worst 40),
`boundary/` (13), `tiles/` (2), `before-after/` (8 frame sheets +
`klamath-e0.png` + `klamath-walls.json`).

Limits, stated plainly:

1. **No hard fail and no production write.** Calibration supports a detail only;
   `/ai/wasteland-hd/{repair,production}-lightning4` were read, never modified,
   and no pack was rebuilt.
2. **Label coverage is thin at the boundary** (17 sheets): recall below
   `CAST_AREA_LIMIT = 0.015` is unmeasured — some real sub-limit casts
   (`nec033`, `mach02`, `ibox06` are already labelled `cast` under the limit)
   are knowingly traded away for precision.
3. **The worst 40 are dominated by placeholder art** ("UNUSED ART"/"Unused
   Tile" fields), which are real casts but low-traffic frames; the wall-scenery
   cases (`jas1000`, `block2`, the five Klamath walls) are the ones that appear
   in shipped maps.
4. `cast_correct` currently runs only where the repair ladder reaches it
   (wall groups); scenery/items/misc casts are measured and reported but not
   auto-corrected anywhere — that needs a decision on whether a sprite-level
   correction may ship without re-review.
