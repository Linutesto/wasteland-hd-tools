"""Chroma-cast (colour-cast) measurement and a CPU correction for HD frames.

A redraw can leave a whole connected region of a sprite cooler (blue/purple)
than the original while its *average* colour stays inside every regional-mean
limit: `qa.colour_shift` and `qa.badness` compare means, and a cast that covers
2% of a wall moves the mean by less than a unit, so both gates read it as
clean.  What separates a real cast from resampling noise is not the size of one
pixel's difference but its *consistency*: over a real cast the chroma vector
(CIELAB a*/b* minus the original's) points the same way everywhere, so the mean
of the vector survives while noise cancels.`cast_measure` therefore works at native resolution — the HD frame BOX-downscaled to the original's size, the way
`qa.colour_shift` compares colours — subtracts from every pixel the chroma it *would* have if the original were
merely re-exposed to the HD frame's luminance (see `_exposure_match`, so a brighter or darker render of the same
colours is not a cast), and groups pixels left with a chroma difference over CAST_PIXEL_DELTA into connected
regions.
Each region is scored by the magnitude of its *mean* vector, and only regions
that are both large enough (CAST_MIN_AREA) and directionally consistent enough
(CAST_REGION_DELTA) count; a frame is flagged when such regions cover at least
CAST_AREA_LIMIT of its opaque pixels.  Single noisy pixels never reach a verdict
because they have neither area nor a consistent direction.

`cast_correct` pulls a flagged region's chroma back to the original's: the
low-frequency component of the a*/b* difference is removed inside the region and
feathered out at its border, while HD luminance and HD chroma detail are kept
and alpha is untouched.  Where nothing is flagged the frame comes back
byte-identical, so correcting a frame that passes the measurement cannot change
the art that ships.
"""
from __future__ import annotations

import numpy as np
from PIL import Image
from scipy.ndimage import distance_transform_edt, gaussian_filter, label

# Native-resolution calibration (see docs/CHROMA-REPORT.md for the labels).
CAST_PIXEL_DELTA = 4.0   # Lab a*/b* distance a native px must move to join a region
CAST_MIN_AREA = 24       # native opaque px a region needs before it can be a cast
CAST_REGION_DELTA = 8.0  # mean vector magnitude a region needs to be a cast
CAST_AREA_LIMIT = 0.015  # share of the opaque mask flagged regions may cover
CAST_MASK_BAND = 1       # native px beside the alpha boundary ignored (silhouette AA)
CAST_INTERIOR_MIN = 64   # below this many interior px the boundary band is dropped

# Correction (applied at 2x, so distances are 2x px).
CAST_SIGMA = 10.0        # low-pass that splits "low frequency" from detail
CAST_FEATHER = 8.0       # px blended outward from the corrected region


def _srgb_to_lab(rgb):
    """(...,3) float RGB in 0..255 -> (L, a, b) CIELAB, D65."""
    c = np.asarray(rgb, np.float32) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    xyz = c @ np.array([[0.4124564, 0.3575761, 0.1804375],
                        [0.2126729, 0.7151522, 0.0721750],
                        [0.0193339, 0.1191920, 0.9503041]], np.float32).T
    t = xyz / np.array([0.95047, 1.0, 1.08883], np.float32)
    delta = 6.0 / 29.0
    f = np.where(t > delta ** 3, np.cbrt(t), t / (3 * delta ** 2) + 4.0 / 29.0)
    return 116.0 * f[..., 1] - 16.0, 500.0 * (f[..., 0] - f[..., 1]), 200.0 * (f[..., 1] - f[..., 2])


def _lab_to_srgb(L, a, b):
    """CIELAB -> (...3) float RGB in 0..255 (the inverse of _srgb_to_lab)."""
    fy = (np.asarray(L, np.float32) + 16.0) / 116.0
    fx = fy + np.asarray(a, np.float32) / 500.0
    fz = fy - np.asarray(b, np.float32) / 200.0
    delta = 6.0 / 29.0
    white = np.array([0.95047, 1.0, 1.08883], np.float32)
    def inv(f):
        return np.where(f > delta, f ** 3, 3 * delta ** 2 * (f - 4.0 / 29.0))
    xyz = np.stack([inv(fx), inv(fy), inv(fz)], axis=-1) * white
    c = xyz @ np.array([[3.2404542, -1.5371385, -0.4985314],
                        [-0.9692660, 1.8760108, 0.0415560],
                        [0.0556434, -0.2040259, 1.0572252]], np.float32).T
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055) * 255.0


def _native(hd, original):
    """(HD at native size as float RGB, original as float RGB, opaque mask)."""
    dst = np.asarray(hd.convert('RGB').resize(original.size, Image.Resampling.BOX), np.float32)
    src = np.asarray(original.convert('RGB'), np.float32)
    return dst, src, np.asarray(original.getchannel('A')) > 0


_WHITE = np.array([0.2126, 0.7152, 0.0722], np.float32)


def _exposure_match(src, dst):
    """The original's colours re-exposed to the HD frame's luminance.

    A redraw that only renders a sprite brighter or darker moves Lab a*/b* too
    (a lighter red is a more saturated red), and that is not a colour cast.
    Scaling the original by the ratio of relative luminances gives the colour
    the source *would* have at the frame's brightness — exactly, when the frame
    is a uniform gain of the source — so what is left after the subtraction is
    chroma the brightness change cannot explain.  The gain is taken in the
    perceptual (gamma) values, where a render-level brightness change actually
    happens, and is clamped so a nearly black source cannot blow up.
    """
    src = np.asarray(src, np.float32)
    dst = np.asarray(dst, np.float32)
    y_src = src @ _WHITE
    y_dst = dst @ _WHITE
    k = np.where(y_src > 2.0, y_dst / np.maximum(y_src, 2.0), 1.0)
    k = np.clip(k, 0.25, 4.0).astype(np.float32)
    return np.clip(src * k[..., None], 0.0, 255.0)


def _regions(hd, original):
    """(va, vb, opaque, candidate mask, labels): chroma vectors and connected regions."""
    dst, src, opaque = _native(hd, original)
    _, da, db = _srgb_to_lab(dst)
    _, pa, pb = _srgb_to_lab(_exposure_match(src, dst))
    va, vb = da - pa, db - pb
    if not opaque.any():
        return va, vb, opaque, np.zeros(opaque.shape, bool), np.zeros(opaque.shape, np.int32)
    interior = distance_transform_edt(opaque) > CAST_MASK_BAND
    mask = interior if interior.sum() >= min(CAST_INTERIOR_MIN, opaque.sum()) else opaque
    candidate = (np.hypot(va, vb) > CAST_PIXEL_DELTA) & mask
    if not candidate.any():
        return va, vb, opaque, candidate, np.zeros(opaque.shape, np.int32)
    connected, _ = label(candidate)
    return va, vb, opaque, candidate, connected


def cast_measure(hd, original, *, pixel_delta=CAST_PIXEL_DELTA, min_area=CAST_MIN_AREA,
                 region_delta=CAST_REGION_DELTA, area_limit=CAST_AREA_LIMIT):
    """Chroma-cast measurement of one HD frame against its original.

    Returns a JSON-friendly dict: `cast_area` is the share of the opaque mask
    covered by flagged regions, `cast_max` the mean-vector magnitude of the
    worst region of at least `min_area` px, `cast_dir` that region's mean
    a*/b* vector, `mean_delta` the whole-mask mean vector — the quantity the
    regional-mean gates see, which is why they miss a cast.
    """
    out = {'opaque_px': 0, 'cast_px': 0, 'cast_area': 0.0, 'cast_regions': 0,
           'cast_max': 0.0, 'cast_dir': [0.0, 0.0], 'mean_delta': 0.0}
    va, vb, opaque, _candidate, connected = _regions(hd, original)
    if not opaque.any() or not connected.any():
        if opaque.any():
            out['opaque_px'] = int(opaque.sum())
            out['mean_delta'] = round(float(np.hypot(va[opaque].mean(), vb[opaque].mean())), 3)
        return out
    out['opaque_px'] = int(opaque.sum())
    out['mean_delta'] = round(float(np.hypot(va[opaque].mean(), vb[opaque].mean())), 3)
    flat = connected.ravel()
    count = int(flat.max())
    area = np.bincount(flat, minlength=count + 1)
    suma = np.bincount(flat, weights=va.ravel(), minlength=count + 1)
    sumb = np.bincount(flat, weights=vb.ravel(), minlength=count + 1)
    big = area >= min_area
    if not big.any():
        return out
    mean_a = np.divide(suma, np.maximum(area, 1))
    mean_b = np.divide(sumb, np.maximum(area, 1))
    magnitude = np.hypot(mean_a, mean_b)
    big[0] = False
    worst = int(np.argmax(np.where(big, magnitude, 0.0)))
    out['cast_max'] = round(float(magnitude[worst]), 3)
    out['cast_dir'] = [round(float(mean_a[worst]), 1), round(float(mean_b[worst]), 1)]
    cast = big & (magnitude >= region_delta)
    flagged = area[cast].sum()
    if flagged:
        out['cast_px'] = int(flagged)
        out['cast_area'] = round(float(flagged / opaque.sum()), 5)
        out['cast_regions'] = int(cast.sum())
        # Largest cast region carries the report's direction.
        largest = int(np.argmax(np.where(cast, area, 0.0)))
        out['cast_dir'] = [round(float(mean_a[largest]), 1), round(float(mean_b[largest]), 1)]
    return out


def cast_flagged(hd, original, **limits) -> bool:
    """True when the frame carries a cast over the calibrated limit."""
    measured = cast_measure(hd, original, **limits)
    return measured['cast_area'] >= (limits.get('area_limit', CAST_AREA_LIMIT))


def cast_mask(hd, original, *, pixel_delta=CAST_PIXEL_DELTA, min_area=CAST_MIN_AREA,
              region_delta=CAST_REGION_DELTA):
    """Flagged regions as a bool array at native size (for contact sheets)."""
    _va, _vb, opaque, _candidate, connected = _regions(hd, original)
    if not connected.any():
        return np.zeros(opaque.shape, bool)
    flat = connected.ravel()
    count = int(flat.max())
    area = np.bincount(flat, minlength=count + 1)
    suma = np.bincount(flat, weights=_va.ravel(), minlength=count + 1)
    sumb = np.bincount(flat, weights=_vb.ravel(), minlength=count + 1)
    mean_a = np.divide(suma, np.maximum(area, 1))
    mean_b = np.divide(sumb, np.maximum(area, 1))
    cast = (area >= min_area) & (np.hypot(mean_a, mean_b) >= region_delta)
    cast[0] = False
    if not cast.any():
        return np.zeros(opaque.shape, bool)
    return cast[connected]


def cast_correct(hd, original, *, sigma=CAST_SIGMA, feather=CAST_FEATHER,
                 **limits):
    """Pull the flagged cast regions' chroma back to the original's.

    The chroma difference from the *exposure-matched* original — the colour the
    source would have at this frame's brightness, the same quantity the
    measurement flags on — is split into a low-frequency part (the cast) and
    the rest; only the low-frequency part is removed, and only inside the
    feathered region, so HD luminance, HD chroma detail and the alpha channel
    all survive.  A frame with no flagged region is returned unchanged.
    """
    native_mask = cast_mask(hd, original, **limits)
    if not native_mask.any():
        return hd.copy()
    a = np.asarray(hd.convert('RGBA'), np.float32)
    b = np.asarray(original.convert('RGBA').resize(hd.size, Image.Resampling.NEAREST), np.float32)
    if a.shape != b.shape:
        raise ValueError('chroma cast correction needs matching sizes')
    lab_l, lab_a, lab_b = _srgb_to_lab(a[..., :3])
    _src_l, src_a, src_b = _srgb_to_lab(_exposure_match(b[..., :3], a[..., :3]))
    chroma = np.stack([lab_a - src_a, lab_b - src_b], axis=-1)
    low = gaussian_filter(chroma, (sigma, sigma, 0.0))
    region = np.asarray(Image.fromarray((native_mask * 255).astype('uint8'))
                        .resize(hd.size, Image.Resampling.NEAREST)) > 0
    distance = distance_transform_edt(~region)
    weight = np.clip(1.0 - distance / max(feather, 1.0), 0.0, 1.0)
    weight = weight * weight * (3 - 2 * weight)
    weight[region] = 1.0
    # Keep HD luminance and HD chroma detail; drop only the low-frequency part
    # of the difference, feathered to zero outside the region.
    out_a = lab_a - weight * low[..., 0]
    out_b = lab_b - weight * low[..., 1]
    rgb = _lab_to_srgb(lab_l, out_a, out_b)
    out = np.dstack([np.clip(np.rint(rgb), 0, 255).astype('uint8'),
                     a[..., 3].astype('uint8')])
    return Image.fromarray(out)
