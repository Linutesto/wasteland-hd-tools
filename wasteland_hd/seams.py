"""Reconcile AI-redrawn floor tiles with the original, seam-compatible art.

Diffusion generates every tile independently and invents new colours and large
shapes along the shared borders. A hard paste or ordinary blur cannot repair
those mismatches. Retain AI microtexture, but use the game's own pixels as the
shared low-frequency reference and lock every tile boundary to that reference.

This is a conservative per-FRM fix. Truly joint reconstruction of the entire
map requires position-specific tiles and engine support: the same FRM can be
placed next to different neighbours on different maps.
"""

from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import distance_transform_edt, gaussian_filter

from .gamefiles import frame_rgba, read_frm


def _masked_blur(rgb, mask, sigma):
    """Blur only opaque pixels; transparent sheet gutters must not tint edges."""
    weights = gaussian_filter(mask.astype(np.float32), sigma=sigma)
    numerator = gaussian_filter(rgb * mask[..., None], sigma=(sigma, sigma, 0))
    return numerator / np.maximum(weights[..., None], 1e-5)


def reconcile_tile(hd, source, *, feather=16, sigma=8, detail=1.0, macro=0.25):
    """Make HD and source agree exactly on every opaque/transparent boundary.

    hd and source are 2x RGBA PIL images with identical original-game alpha.
    This preserves their dimensions and alpha. `feather` is measured in HD px.
    `sigma` separates high-frequency AI texture from invented large features.
    """
    if hd.size != source.size:
        raise ValueError(f"tile size mismatch: {hd.size} != {source.size}")
    hd = hd.convert("RGBA")
    source = source.convert("RGBA")
    a = np.asarray(hd.getchannel("A"))
    if not np.array_equal(a, np.asarray(source.getchannel("A"))):
        raise ValueError("HD alpha must exactly match source alpha")
    if not feather > 0 or not sigma > 0:
        raise ValueError("feather and sigma must be positive")
    mask = a > 0
    if not mask.any():
        return source.copy()
    # Padding makes image-edge pixels count as actual boundaries as well.
    dist = distance_transform_edt(np.pad(mask, 1))[1:-1, 1:-1] - 1
    t = np.clip(dist / feather, 0, 1)
    strength = (t * t * (3 - 2 * t))[..., None].astype(np.float32)
    src_rgb = np.asarray(source, dtype=np.float32)[..., :3]
    hd_rgb = np.asarray(hd, dtype=np.float32)[..., :3]
    hd_low = _masked_blur(hd_rgb, mask, sigma)
    src_low = _masked_blur(src_rgb, mask, sigma)
    # Macro drift causes visible tile outlines. Keep a little of it inside
    # each tile, plus sharp AI surface texture. Across edges use the native art.
    ai_contribution = detail * (hd_rgb - hd_low) + macro * (hd_low - src_low)
    rgb = np.clip(np.rint(src_rgb + strength * ai_contribution), 0, 255).astype("uint8")
    rgba = np.dstack((rgb, a))
    return Image.fromarray(rgba)


def reconcile_directory(game, source_root, dest_root, **kwargs):
    """Read HD tile PNGs and genuine Fallout 2 FRMs; write to a new tree."""
    source_root, dest_root = Path(source_root), Path(dest_root)
    hd_tiles = source_root / "art" / "tiles"
    if not hd_tiles.is_dir():
        raise FileNotFoundError(f"no HD tiles in {hd_tiles}")
    count = 0
    for png in sorted(hd_tiles.glob("*/*.png")):
        stem = png.parent.name
        d, i = map(int, png.stem.split("_"))
        frm = read_frm(game.read(f"art/tiles/{stem}.frm"))
        frame = frm.directions[d][i]
        orig = Image.frombytes("RGBA", (frame.width, frame.height),
                               frame_rgba(frame, game.palette()))
        orig = orig.resize((frame.width * 2, frame.height * 2), Image.Resampling.NEAREST)
        fixed = reconcile_tile(Image.open(png), orig, **kwargs)
        out = dest_root / "art" / "tiles" / stem / png.name
        out.parent.mkdir(parents=True, exist_ok=True)
        fixed.save(out)
        count += 1
    return count
