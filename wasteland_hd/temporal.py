"""Deterministic, original-guided temporal consistency for HD critter sprites.

The reference sprite is a source of *correspondence*, not a target texture:
Qwen's colour and fine detail are pooled across frames whose native 3x3/5x5
patches actually agree. Native-pixel block matching compensates for changing
poses, and the game's accumulated frame shifts are deliberately factored out
for this body-relative comparison (but restored when the game draws the art).

Only NumPy, SciPy and Pillow are used; the input tree is never modified.
"""

from collections import defaultdict
from pathlib import Path
import re
import shutil

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter, median_filter, uniform_filter

from .gamefiles import frame_rgba, read_frm


FRAME_NAME = re.compile(r"^(\d+)_(\d+)\.png$")


def _group_frames(game, name, direction):
    art = f"art/critters/{name}"
    try:
        frm = read_frm(game.read(art + ".frm"))
        frames = frm.directions[direction]
    except FileNotFoundError:
        frm = read_frm(game.read(art + f".fr{direction}"))
        frames = frm.directions[0]
    return frames


def _canvases(game, name, direction, paths):
    """Load complete groups onto a native, pose-centred canvas.

    Cumulative FRM shifts move the entire character in world coordinates.
    Subtracting those translations before matching lets torso texture agree
    even during walks; the individual image extents still use bottom-centre.
    """
    frames = _group_frames(game, name, direction)
    originals = []
    for i, path in paths:
        frame = frames[i]
        orig = Image.frombytes("RGBA", (frame.width, frame.height),
                               frame_rgba(frame, game.palette()))
        hd = Image.open(path).convert("RGBA")
        if hd.size != (frame.width * 2, frame.height * 2):
            raise ValueError(f"{path}: expected {frame.width * 2}x{frame.height * 2}, got {hd.size}")
        originals.append((i, orig, hd))

    width = max(im.width for _i, im, _hd in originals) + 24
    height = max(im.height for _i, im, _hd in originals) + 24
    native, detail, alpha, boxes = [], [], [], []
    for i, orig, hd in originals:
        x, y = (width - orig.width) // 2, height - orig.height - 12
        box = (2*x, 2*y, 2*(x+orig.width), 2*(y+orig.height))
        ref = np.zeros((height, width, 4), dtype=np.uint8)
        ref[y:y + orig.height, x:x + orig.width] = np.asarray(orig)
        native.append(ref)
        rgba = np.zeros((height * 2, width * 2, 4), dtype=np.uint8)
        rgba[box[1]:box[3], box[0]:box[2]] = np.asarray(hd)
        expected_alpha = np.repeat(np.repeat(ref[..., 3], 2, axis=0), 2, axis=1)
        if not np.array_equal(rgba[..., 3], expected_alpha):
            raise ValueError(f"{name}/{direction}_{i}: HD alpha differs from original 2x nearest")
        detail.append(rgba[..., :3].astype(np.float32))
        alpha.append(expected_alpha)
        boxes.append(box)
    return np.stack(native), np.stack(detail), np.stack(alpha), boxes


def _offset_slice(length, delta):
    """Slice of target and source for source pixel at target + delta."""
    start = max(0, -delta)
    end = min(length, length - delta)
    return slice(start, end), slice(start + delta, end + delta)


def _match(target, source, radius=9, *, strict=False):
    """Find the source's matching native pixel using only original FRM art.

    A local 5x5 patch determines motion; central pixel similarity rejects
    wrong correspondences. Small-offset ties favour stable placement.
    """
    h, w = target.shape[:2]
    t = target[..., :3].astype(np.float32)
    s = source[..., :3].astype(np.float32)
    ta = target[..., 3] > 0
    sa = source[..., 3] > 0
    best = np.full((h, w), 1e6, dtype=np.float32)
    by = np.zeros((h, w), dtype=np.int16)
    bx = np.zeros((h, w), dtype=np.int16)
    for dy in range(-radius, radius + 1):
        ty, sy = _offset_slice(h, dy)
        for dx in range(-radius, radius + 1):
            tx, sx = _offset_slice(w, dx)
            if ty.stop <= ty.start or tx.stop <= tx.start:
                continue
            # One large buffer allows the patch penalty to include the
            # sprite boundary instead of accidentally wrapping at an edge.
            diff = np.full((h, w), 95., dtype=np.float32)
            rgb = np.abs(t[ty, tx] - s[sy, sx]).mean(axis=2)
            diff[ty, tx] = rgb + 80. * (ta[ty, tx] != sa[sy, sx])
            cost = uniform_filter(diff, size=5, mode="constant", cval=95.)
            cost += 0.11 * (dx * dx + dy * dy)
            improved = (cost < best) & ta
            best[improved] = cost[improved]
            bx[improved] = dx
            by[improved] = dy

    # Median displacement removes isolated wrong patch matches on flat art.
    dx = median_filter(bx, size=3)
    dy = median_filter(by, size=3)
    xx = np.clip(np.arange(w)[None, :] + dx, 0, w - 1)
    yy = np.clip(np.arange(h)[:, None] + dy, 0, h - 1)
    center = np.abs(t - s[yy, xx]).mean(axis=2)
    valid = ta & sa[yy, xx] & (best < 27) & (center < 43)
    confidence = np.clip((27 - best) / 14, 0, 1) * np.clip((43 - center) / 25, 0, 1)
    if strict:
        # Transferring HD pixels requires nearly identical ORIGINAL
        # neighbourhoods. Approximate matches are suitable for a diagnostic
        # metric but can produce visible speckle when used for reconstruction.
        patch_diff = uniform_filter(center + 80 * (ta != sa[yy, xx]), size=3)
        confidence *= np.clip((17 - patch_diff) / 12, 0, 1)
        valid &= (patch_diff < 17) & (center < 10)
    return yy, xx, np.where(valid, confidence, 0).astype(np.float32)


def _nearest2(a):
    # RGB(A) arrays have channels last; alpha/weights have no channel axis.
    y, x = (-3, -2) if a.ndim >= 3 and a.shape[-1] in (3, 4) else (-2, -1)
    return np.repeat(np.repeat(a, 2, axis=y), 2, axis=x)


def _texture_residual(rgb, orig):
    """AI difference from the *exact* original at 2x; zero for nearest art."""
    base = _nearest2(orig)[..., :3].astype(np.float32)
    return rgb - base


def _pair_metric(a, b, orig_a, orig_b, correspond):
    """Per-original-matched-pixel unexplained RGB variation (0..255)."""
    yy, xx, confidence = correspond
    # Subpixel phases remain aligned; keep all 2x child texels independently.
    aa = _texture_residual(a, orig_a).reshape(*orig_a.shape[:2], 2, 2, 3)
    bb = _texture_residual(b, orig_b).reshape(*orig_b.shape[:2], 2, 2, 3)
    warped = bb[yy, xx]
    variation = np.abs(aa - warped).mean(axis=(2, 3, 4))
    weight = confidence
    return float(np.sum(variation * weight)), float(weight.sum())


def score_group(native, detail, *, radius=9):
    """0 is exact original 2x; higher means inconsistent AI changes.

    Compare adjacent poses, plus the last->first for cyclic walks. A native
    patch match protects moving limbs from being counted as static details.
    """
    n = len(detail)
    if n < 2:
        return 0.
    total, weight = 0., 0.
    for i in range(n):
        j = (i + 1) % n
        corr = _match(native[i], native[j], radius)
        error, count = _pair_metric(detail[i], detail[j], native[i], native[j], corr)
        total += error
        weight += count
    return total / weight if weight else 0.


def _warp_blocks(a, yy, xx):
    """Move every 2x detail block by the flow of its native original pixel."""
    h, w = yy.shape
    return a.reshape(h, 2, w, 2, 3).transpose(0, 2, 1, 3, 4)[yy, xx].transpose(0, 2, 1, 3, 4).reshape(h*2, w*2, 3)


def _balance_colours(native, detail):
    """Remove per-frame palette-class colour drift without losing texture.

    Each original palette colour is a semantic hint (skin, trousers, shadow).
    Quantise it into RGB buckets, compare mean HD-over-original colour for
    that bucket across poses, and correct only the broad RGB offset. The
    correction is blurred spatially, so Qwen's microdetail remains intact.
    """
    n, h, w, _ = native.shape
    keys = (native[..., 0].astype(np.int32) // 24 * 121
            + native[..., 1].astype(np.int32) // 24 * 11
            + native[..., 2].astype(np.int32) // 24)
    nkeys = 1331
    block = detail.reshape(n, h, 2, w, 2, 3).mean(axis=(2, 4))
    residual = block - native[..., :3].astype(np.float32)
    valid = native[..., 3] > 0
    per_frame = np.full((n, nkeys, 3), np.nan, dtype=np.float32)
    counts = np.zeros((n, nkeys), dtype=np.int32)
    for i in range(n):
        ids = keys[i, valid[i]]
        counts[i] = np.bincount(ids, minlength=nkeys)
        for c in range(3):
            sums = np.bincount(ids, weights=residual[i, valid[i], c], minlength=nkeys)
            np.divide(sums, np.maximum(counts[i], 1), out=per_frame[i, :, c], casting="unsafe")
        per_frame[i, counts[i] < 8] = np.nan
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        reference = np.nanmedian(per_frame, axis=0)
    reference = np.nan_to_num(reference)
    out = detail.copy()
    for i in range(n):
        delta = per_frame[i] - reference
        delta[~np.isfinite(delta)] = 0.
        # Suppress anomalous colour shifts without turning a naturally
        # shaded frame into a flat palette-coloured cut-out.
        delta = np.clip(delta[keys[i]], -45, 45)
        mask = valid[i].astype(np.float32)
        smooth = gaussian_filter(delta * mask[..., None], sigma=(1.5, 1.5, 0))
        smooth /= np.maximum(gaussian_filter(mask, 1.5)[..., None], 1e-4)
        out[i] -= _nearest2(smooth) * 0.72
    return np.clip(out, 0, 255)


def _median_atlas_group(native, detail, alpha, *, radius=9):
    """Canonical median appearance warped from frame 0 onto matching poses.

    A median of matched AI *residuals* avoids copying hallucinations that
    appear on only one frame. Unmatched limbs retain their current drawing.
    """
    n = len(detail)
    if n < 2:
        return detail.copy()
    if np.all(native == native[0]):
        # Truly static originals (e.g. breathing frames reused verbatim):
        # any HD changes are invention. One common median frame is exact.
        single = np.clip(np.rint(np.median(detail, axis=0)), 0, 255).astype(np.uint8)
        return np.broadcast_to(single, detail.shape).copy()
    balanced = _balance_colours(native, detail)
    orig0 = native[0]
    candidate = []
    support = []
    for i in range(n):
        if i == 0:
            residual = _texture_residual(balanced[0], orig0)
            confidence = (orig0[..., 3] > 0).astype(np.float32)
        else:
            yy, xx, confidence = _match(orig0, native[i], radius, strict=True)
            residual = _warp_blocks(_texture_residual(balanced[i], native[i]), yy, xx)
        confidence = _nearest2(confidence)
        candidate.append(residual)
        support.append(confidence)
    stack = np.stack(candidate)
    weights = np.stack(support)
    # Moderate confidence floor before pooling to avoid selecting wrong
    # texture from a quite different body pose.
    shared = np.where(weights[..., None] > 0.2, stack, np.nan)
    with np.errstate(all="ignore"):
        # NumPy's warning for an entirely missing patch is expected.
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            atlas = np.nanmedian(shared, axis=0)
    reference = _texture_residual(balanced[0], orig0)
    atlas = np.where(np.isfinite(atlas), atlas, reference)
    seen = (weights > 0.2).sum(axis=0)
    # A recurring feature should have at least two supporting poses, unless
    # this is a two-frame cycle where the reference is the only safe anchor.
    reliable = (seen >= min(2, n)).astype(np.float32)

    result = []
    for i in range(n):
        if i == 0:
            yy, xx = np.indices(native[0].shape[:2])
            confidence = (native[0, ..., 3] > 0).astype(np.float32)
        else:
            yy, xx, confidence = _match(native[i], orig0, radius, strict=True)
        transferred = _warp_blocks(atlas, yy, xx)
        quality = _nearest2(confidence) * _warp_blocks(reliable[..., None].repeat(3, axis=2), yy, xx)[..., 0]
        # Feather motion boundaries. Preserve original silhouette and the
        # AI's independently drawn moving regions outside shared anatomy.
        blend = gaussian_filter(quality, sigma=1.4)
        blend *= _nearest2((native[i, ..., 3] > 0).astype(np.float32))
        blend = np.clip(blend, 0, 1)[..., None]
        original_residual = _texture_residual(balanced[i], native[i])
        fixed = _nearest2(native[i])[..., :3] + blend * transferred + (1 - blend) * original_residual
        result.append(np.clip(np.rint(fixed), 0, 255).astype(np.uint8))
    return np.stack(result)


def _smooth_neighbours(native, detail, *, radius=9):
    """Blend broad colour drift from motion-matched neighbours.

    Blur the *correction field*, rather than the HD image: fine texture is
    left intact, and independently matched 2x blocks cannot create a noisy
    mosaic at motion boundaries. A hard cap avoids painting a different limb
    or a different garment over the current pose.
    """
    count = len(detail)
    orig = _nearest2(native)[..., :3].astype(np.float32)
    residual = detail - orig
    output = []
    for i in range(count):
        delta_sum = np.zeros_like(detail[i])
        support = np.zeros(detail.shape[1:3], dtype=np.float32)
        for j in {(i - 1) % count, (i + 1) % count}:
            yy, xx, confidence = _match(native[i], native[j], radius)
            weight = _nearest2(confidence)
            neighbour = _warp_blocks(residual[j], yy, xx)
            delta = np.clip(neighbour - residual[i], -50, 50)
            delta_sum += gaussian_filter(delta * weight[..., None], sigma=(2.5, 2.5, 0))
            support += gaussian_filter(weight, 2.5)
        correction = np.clip(delta_sum / np.maximum(support[..., None], .01), -22, 22)
        correction *= (.8 * np.clip(support[..., None] / 1.6, 0, 1))
        output.append(np.clip(np.rint(detail[i] + correction), 0, 255).astype(np.uint8))
    return np.stack(output)


def stabilise_group(native, detail, alpha, *, radius=9):
    """Pick the least-flickering original-guided candidate for each cycle.

    Median atlas works well on repeated idle poses; local smooth correction
    is safer for walking limbs. Selecting by the same objective used in the
    report prevents the more aggressive technique from worsening a cycle.
    """
    if len(detail) < 2 or np.array_equal(native, np.broadcast_to(native[:1], native.shape)):
        return _median_atlas_group(native, detail, alpha, radius=radius)
    atlas = _median_atlas_group(native, detail, alpha, radius=radius)
    neighbours = _smooth_neighbours(native, detail, radius=radius)
    baseline = score_group(native, detail, radius=radius)
    options = ((score_group(native, atlas, radius=radius), atlas),
               (score_group(native, neighbours, radius=radius), neighbours))
    best_score, best = min(options, key=lambda entry: entry[0])
    # A candidate that creates more unexplained variation is not an upgrade.
    return best if best_score < baseline else np.clip(np.rint(detail), 0, 255).astype(np.uint8)


def _inventory(source_root):
    """Return only valid critter PNG names, grouped by animation/direction."""
    groups = defaultdict(list)
    base = source_root / "art" / "critters"
    if base.is_dir():
        for png in sorted(base.glob("*/*.png")):
            match = FRAME_NAME.fullmatch(png.name)
            if match:
                direction, index = map(int, match.groups())
                groups[(png.parent.name, direction)].append((index, png))
    return groups


def analyse_directory(game, source_root, *, radius=9):
    """{animation/direction: flicker} for all complete or partial sequences."""
    scores = {}
    for (name, d), paths in sorted(_inventory(Path(source_root)).items()):
        native, rgb, _alpha, _boxes = _canvases(game, name, d, paths)
        scores[f"{name}/{d}"] = round(score_group(native, rgb, radius=radius), 3)
    return scores


def stabilise_directory(game, source_root, dest_root, *, radius=9):
    """Copy whole HD tree, and replace only critter animation frames.

    Both paths must differ and destination must not live inside source.
    Originals are never modified. Source's other files are copied verbatim.
    """
    src, dst = Path(source_root).resolve(), Path(dest_root).resolve()
    if src == dst or src in dst.parents or dst in src.parents:
        raise ValueError("--source and --out must be separate directories, neither nested within the other")
    if not src.is_dir():
        raise FileNotFoundError(src)
    if dst.exists() and any(dst.iterdir()):
        raise FileExistsError(f"destination is not empty: {dst}")
    shutil.copytree(src, dst, dirs_exist_ok=True, copy_function=shutil.copy2)
    outcomes = {}
    for (name, d), paths in sorted(_inventory(src).items()):
        native, rgb, alpha, boxes = _canvases(game, name, d, paths)
        before = score_group(native, rgb, radius=radius)
        fixed = stabilise_group(native, rgb, alpha, radius=radius)
        after = score_group(native, fixed, radius=radius)
        for (index, png), img, mask, box in zip(paths, fixed, alpha, boxes):
            rgba = np.dstack((img[box[1]:box[3], box[0]:box[2]], mask[box[1]:box[3], box[0]:box[2]]))
            Image.fromarray(rgba.astype(np.uint8)).save(dst / png.relative_to(src))
        outcomes[f"{name}/{d}"] = {"before": round(before, 3), "after": round(after, 3)}
    return outcomes
