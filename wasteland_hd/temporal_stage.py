"""Keyframe-anchored identity transfer for HD critter animations (CPU, deterministic).

Independent Qwen frames each invent their own costume colour and texture, so a
walk cycle boils (``docs/FLICKER-TEST-REPORT.md``).  This stage picks one
*keyframe* per direction -- the pose whose material appearance is closest to
every other pose -- and makes the other frames agree with it, without touching
their geometry or their original transparency:

* ``colour``   per-original-palette-class colour transfer.  Each frame's mean
  HD colour is moved onto the keyframe's mean for the same original palette
  class, so "the same trousers" keep one hue whether they are static or moving
  (this is the ``material`` signal);
* ``residual`` low-frequency residual transfer.  Only where the original FRM
  patch matching (:func:`wasteland_hd.temporal._match`) finds the same material
  in a different pose, the keyframe's low-frequency AI residual (lighting,
  shading) is carried over with its confidence; texture is left alone (this is
  the ``broad`` signal);
* ``texture``  keyframe texture where the original FRM says the same material
  is present in a different pose (strict native patch matching, confidence
  weighted, interior only), so one invented pattern (the leopard trousers) is
  not re-invented per frame;
* ``static``   texture/identity copy.  Where the original says two poses are
  byte-identical (same RGBA pixel and a locally unchanged 3x3 patch, the same
  evidence the flicker metric samples) the keyframe's HD texels are copied over
  exactly.  The original has already stated that the material there does not
  change between the poses, so drawing it twice is the invention.

Each frame keeps its own HD silhouette and the source alpha exactly; only RGB
changes.  A direction is only rewritten when its measured gate margin (see
:data:`GATE`) improves, otherwise the input direction is kept verbatim.

Only NumPy, SciPy and Pillow are used; the input tree is never modified.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re
import shutil

import numpy as np
from PIL import Image
from scipy.ndimage import binary_erosion, gaussian_filter

from . import flicker, temporal
from .gamefiles import frame_rgba

FRAME_NAME = re.compile(r"^(\d+)_(\d+)\.png$")
PAD = 4                    # native pixels of margin around a direction's canvas
COLOUR_LIMIT = 48.0        # largest per-class RGB shift one frame may receive
RESIDUAL_SIGMA = 2.0       # HD pixels; what is left is the frame's own detail
RESIDUAL_CAP = 24.0
TEXTURE_SIGMA = 1.0        # HD pixels of feather on the match confidence
FALLBACK_CAP = 2.0         # direction flicker above this x the animation mean falls back
COMPONENTS = ("colour", "residual", "texture", "static")

# The flicker gate of docs/FLICKER-TEST-REPORT.md, per animation.
GATE = {"broad": 2.5, "material": 3.0, "flicker": 6.0}


def margin(metrics):
    """Distance to the gate in units of the limits; < 1 passes every component."""
    return max(metrics[key] / limit for key, limit in GATE.items()
               if metrics.get(key) is not None)


def passes(metrics):
    return all(metrics.get(key) is not None and metrics[key] <= limit
               for key, limit in GATE.items())


def load(game, name, direction, paths):
    """(native RGBA, HD RGB float32, HD alpha, boxes) of one direction on a shared canvas."""
    frames = flicker._frames(game, name, direction)
    palette = game.palette()
    originals = []
    for index, path in paths:
        frame = frames[index]
        orig = np.frombuffer(frame_rgba(frame, palette), np.uint8).reshape(frame.height, frame.width, 4)
        originals.append(orig)

    width = max(o.shape[1] for o in originals) + 2 * PAD
    height = max(o.shape[0] for o in originals) + 2 * PAD
    native = np.zeros((len(originals), height, width, 4), dtype=np.uint8)
    rgb = np.zeros((len(originals), height * 2, width * 2, 3), dtype=np.float32)
    alpha = np.zeros((len(originals), height * 2, width * 2), dtype=np.uint8)
    boxes = []
    for n, (orig, (_index, path)) in enumerate(zip(originals, paths)):
        h, w = orig.shape[:2]
        native[n, PAD:PAD + h, PAD:PAD + w] = orig
        with Image.open(path) as img:
            hd = np.asarray(img.convert("RGBA"))
        if hd.shape[:2] != (h * 2, w * 2):
            raise ValueError(f"{path}: expected {w * 2}x{h * 2}, got {hd.shape[1]}x{hd.shape[0]}")
        if not np.array_equal(hd[..., 3], flicker._nearest2(orig[..., 3])):
            raise ValueError(f"{name}/{direction}_{_index}: HD alpha differs from original 2x nearest")
        y, x = 2 * PAD, 2 * PAD
        rgb[n, y:y + 2 * h, x:x + 2 * w] = hd[..., :3]
        alpha[n, y:y + 2 * h, x:x + 2 * w] = hd[..., 3]
        boxes.append((y, x, 2 * h, 2 * w))
    return native, rgb, alpha, boxes


def _keys(native):
    return ((native[..., 0].astype(np.int32) << 16)
            | (native[..., 1].astype(np.int32) << 8) | native[..., 2].astype(np.int32))


def material_drift(native_a, rgb_a, native_b, rgb_b):
    """Area-weighted mean per-palette-class HD colour drift (the metric's ``material``)."""
    a = flicker._class_means(native_a, rgb_a)
    b = flicker._class_means(native_b, rgb_b)
    total = weight = 0.0
    for key in a.keys() & b.keys():
        na, ma = a[key]
        nb, mb = b[key]
        if na >= flicker.MIN_CLASS and nb >= flicker.MIN_CLASS:
            total += float(np.abs(ma - mb).mean()) * na
            weight += na
    return total / weight if weight else 0.0


def choose_keyframe(native, rgb):
    """The pose whose material identity is closest to all the others.

    Deterministic: lowest mean ``material`` drift to every other frame of the
    direction, ties broken by lower frame index.  This is the pose a human
    would redraw the others from, and it cannot drift along the cycle the way a
    neighbour-to-neighbour correction can.
    """
    n = len(native)
    if n < 2:
        return 0
    drift = np.zeros((n, n), dtype=np.float64)
    for i in range(n):
        for j in range(i + 1, n):
            drift[i, j] = drift[j, i] = material_drift(native[i], rgb[i], native[j], rgb[j])
    totals = drift.sum(axis=1) / max(1, n - 1)
    spread = drift.max(axis=1)  # a pose that matches nobody is a bad anchor
    order = np.lexsort((np.arange(n), spread, totals))
    return int(order[0])


def _colour_transfer(native, rgb, key, *, strength, limit=COLOUR_LIMIT):
    """Move every frame's per-palette-class mean HD colour onto the keyframe's.

    The offset is applied exactly per original palette class, so both the
    frame's texture and the original's own palette boundaries survive.  A
    feathered (blurred) version of this correction was measured and rejected:
    blurring mixes classes, so the class means never land on the keyframe
    (material 2.65 against 0.32 sharp on hfprimab/4 of rows4full), and on a
    patterned sprite like ``hfprimab``'s leopard trousers the classes are thin.
    """
    reference = flicker._class_means(native[key], rgb[key])
    out = rgb.copy()
    for i in range(len(rgb)):
        if i == key:
            continue
        current = flicker._class_means(native[i], rgb[i])
        share = {k: reference[k][1] - current[k][1]
                 for k in current.keys() & reference.keys()
                 if current[k][0] >= flicker.MIN_CLASS and reference[k][0] >= flicker.MIN_CLASS}
        if not share:
            continue
        opaque = native[i, ..., 3] > 0
        unique, inverse = np.unique(_keys(native[i])[opaque], return_inverse=True)
        field = np.zeros((len(unique), 3), dtype=np.float32)
        for index, value in enumerate(unique):
            delta = share.get(int(value))
            if delta is not None:
                field[index] = np.clip(delta, -limit, limit)
        delta = np.zeros(native[i].shape[:2] + (3,), dtype=np.float32)
        delta[opaque] = field[inverse]
        out[i] += strength * flicker._nearest2(delta)
    return np.clip(out, 0, 255)


def _residual_transfer(native, rgb, key, *, strength, radius=9, sigma=RESIDUAL_SIGMA,
                       cap=RESIDUAL_CAP):
    """Carry the keyframe's low-frequency AI residual onto matched materials.

    Correspondence comes only from the original FRM art (native patch matching),
    so a moving limb is corrected only where the same material is found.  The
    residual is blurred before it is used: this is lighting/shading, not texture.
    """
    base = flicker._nearest2(native[..., :3]).astype(np.float32)
    residual = rgb - base
    anchor = np.stack([gaussian_filter(residual[key, ..., c], sigma) for c in range(3)], axis=-1)
    out = rgb.copy()
    for i in range(len(rgb)):
        if i == key:
            continue
        yy, xx, confidence = temporal._match(native[i], native[key], radius, strict=True)
        weight = flicker._nearest2(confidence)
        if weight.max() <= 0:
            continue
        carried = temporal._warp_blocks(anchor, yy, xx)
        own = np.stack([gaussian_filter(residual[i, ..., c], sigma) for c in range(3)], axis=-1)
        correction = np.clip(carried - own, -cap, cap)
        support = np.maximum(gaussian_filter(weight, 1.5)[..., None], 1e-4)
        smoothed = np.stack([gaussian_filter(correction[..., c] * weight, 1.5) for c in range(3)], axis=-1)
        keep = flicker._nearest2((native[i, ..., 3] > 0).astype(np.float32))
        out[i] += strength * smoothed / support * keep[..., None]
    return np.clip(out, 0, 255)


def _texture_transfer(native, rgb, key, *, strength, radius=9, sigma=TEXTURE_SIGMA):
    """Draw the keyframe's actual texels where its material is found in this pose.

    ``temporal._match`` is strict about the *original* patch, so a limb is only
    corrected where the 1998 art says it is the same material, and the
    confidence falls off where the correspondence is uncertain.  Kept away from
    the frame's own outline band: this transfers identity, not silhouette.
    """
    out = rgb.copy()
    texture = rgb[key]
    for i in range(len(rgb)):
        if i == key:
            continue
        yy, xx, confidence = temporal._match(native[i], native[key], radius, strict=True)
        weight = flicker._nearest2(confidence)
        if weight.max() <= 0:
            continue
        weight = np.clip(gaussian_filter(weight, sigma), 0.0, 1.0) * strength
        alpha = (native[i, ..., 3] > 0).astype(np.float32)
        weight *= flicker._nearest2(alpha)
        outline = np.clip(alpha - _erode(alpha), 0.0, 1.0)
        weight *= (1 - flicker._nearest2(outline))
        w = weight[..., None]
        out[i] = w * temporal._warp_blocks(texture, yy, xx) + (1 - w) * out[i]
    return np.clip(out, 0, 255)


def static_mask(native, i, key):
    """Native pixels the original declares identical in the keyframe and frame ``i``."""
    same = np.all(native[i] == native[key], axis=-1) & (native[i, ..., 3] > 0)
    return same & flicker._unchanged(native[i], native[key])


def _static_transfer(native, rgb, key, *, strength):
    """Draw the material the original leaves unchanged exactly as the keyframe draws it.

    The weight is the mask itself, not a blur of it: the mask boundary is where
    the original's own pixel changes, i.e. a material or motion edge the 1998
    art already has, so a hard cut there is the palette edge and a feathered one
    would leave half the invented texture in the measured pixels.
    """
    out = rgb.copy()
    for i in range(len(rgb)):
        if i == key:
            continue
        weight = static_mask(native, i, key).astype(np.float32) * strength
        if weight.max() <= 0:
            continue
        w2 = np.clip(flicker._nearest2(weight)[..., None], 0.0, 1.0)
        out[i] = w2 * rgb[key] + (1 - w2) * out[i]
    return np.clip(out, 0, 255)


def _erode(mask):
    return binary_erosion(mask > 0, structure=np.ones((3, 3))).astype(np.float32)


def stage_group(native, rgb, *, key=None, components=COMPONENTS, strength=1.0, radius=9):
    """Keyframe-anchored RGB of one direction; the input arrays are not modified."""
    key = choose_keyframe(native, rgb) if key is None else int(key)
    out = rgb.astype(np.float32).copy()
    for name in COMPONENTS:
        if name not in components:
            continue
        if name == "colour":
            out = _colour_transfer(native, out, key, strength=strength)
        elif name == "residual":
            out = _residual_transfer(native, out, key, strength=strength, radius=radius)
        elif name == "texture":
            out = _texture_transfer(native, out, key, strength=strength, radius=radius)
        elif name == "static":
            out = _static_transfer(native, out, key, strength=strength)
        else:
            raise ValueError(f"unknown component: {name}")
    return out, {"keyframe": key}


def score(native, rgb):
    """The flicker metric's per-direction record for an in-memory RGB canvas."""
    rgb = np.asarray(rgb, dtype=np.float32)
    sums, weights = flicker._measure(native, np.clip(rgb, 0, 255))
    record = {key: flicker._ratio(sums, weights, key) for key in flicker.METRICS}
    record["pixels"] = weights["flicker"]
    record["material_pixels"] = weights["material"]
    return record


def _inventory(root):
    groups = defaultdict(list)
    base = Path(root) / "art" / "critters"
    if base.is_dir():
        for png in sorted(base.glob("*/*.png")):
            match = FRAME_NAME.fullmatch(png.name)
            if match:
                groups[(png.parent.name, int(match.group(1)))].append((int(match.group(2)), png))
    return {key: sorted(paths) for key, paths in groups.items()}


def _same_tree(source, out):
    src, dst = Path(source).resolve(), Path(out).resolve()
    if src == dst or src in dst.parents or dst in src.parents:
        raise ValueError("--source and --out must be separate directories, neither nested within the other")
    return src, dst


def stage_directory(game, source_root, dest_root, *, names=None, components=COMPONENTS,
                    strength=1.0, radius=9, select=True, fallback=True, cap=FALLBACK_CAP):
    """Copy the HD tree and rewrite the critter directions the stage improves.


    Other files are copied verbatim; a direction is left untouched (byte for
    byte) when the keyframe transfer does not lower its gate margin.  With
    ``fallback`` a staged direction whose own flicker stays above ``cap`` times
    its animation's mean direction score is replaced by the original art at 2x
    nearest (0.000 by construction), the production
    ``original_game_art_entire_direction`` fallback: the pose the model cannot
    hold steady ships as the 1998 art instead of boiling.
    """
    src, dst = _same_tree(source_root, dest_root)
    if not src.is_dir():
        raise FileNotFoundError(src)
    if dst.exists() and any(dst.iterdir()):
        raise FileExistsError(f"destination is not empty: {dst}")
    shutil.copytree(src, dst, dirs_exist_ok=True, copy_function=shutil.copy2)

    staged = {}
    for (name, direction), paths in sorted(_inventory(src).items()):
        if names and name not in names:
            continue
        if len(paths) < 2:
            continue
        native, rgb, alpha, boxes = load(game, name, direction, paths)
        before = score(native, rgb)
        fixed, info = stage_group(native, rgb, components=components, strength=strength, radius=radius)
        after = score(native, fixed)
        accepted = margin(after) < margin(before) - 1e-9 if select else True
        if not accepted:
            fixed, after = rgb, before
        staged[f"{name}/{direction}"] = {
            "animation": name, "direction": direction, "frames": len(paths), "paths": paths,
            "native": native, "fixed": fixed, "alpha": alpha, "boxes": boxes,
            "keyframe": info["keyframe"], "accepted": bool(accepted),
            "before": before, "after": after, "fallback": False,
        }
    if not staged:
        raise ValueError(f"no critter animation with two or more frames in {source_root}")

    if fallback:
        for name in sorted({value["animation"] for value in staged.values()}):
            group = [value for value in staged.values() if value["animation"] == name]
            mean = sum(value["after"]["flicker"] for value in group) / len(group)
            for value in group:
                if value["after"]["flicker"] <= cap * mean:
                    continue
                original = flicker._nearest2(value["native"][..., :3]).astype(np.float32)
                value["fixed"] = original
                value["after"] = score(value["native"], original)
                value["fallback"] = True

    for value in staged.values():
        for i, (_index, png) in enumerate(value["paths"]):
            y, x, h, w = value["boxes"][i]
            pixels = np.clip(np.rint(value["fixed"][i, y:y + h, x:x + w]), 0, 255).astype(np.uint8)
            rgba = np.dstack((pixels, value["alpha"][i, y:y + h, x:x + w]))
            Image.fromarray(rgba.astype(np.uint8)).save(dst / png.relative_to(src))
    directions = {key: {k: v for k, v in value.items()
                        if k not in ("paths", "native", "fixed", "alpha", "boxes")}
                  for key, value in staged.items()}
    animations = {}
    for name in sorted({value["animation"] for value in directions.values()}):
        values = [value for value in directions.values() if value["animation"] == name]
        summary = flicker._pool([value["after"] for value in values])
        summary.update({"frames": max(v["frames"] for v in values), "directions": len(values),
                        "pixels": sum(v["after"]["pixels"] for v in values),
                        "fallbacks": sum(v["fallback"] for v in values),
                        "keyframes": {str(v["direction"]): v["keyframe"] for v in values}})
        animations[name] = summary
    return {"source": str(source_root), "out": str(dest_root), "gate": GATE,
            "components": list(components), "strength": strength, "radius": radius,
            "fallback_cap": cap if fallback else None,
            "animations": animations, "directions": directions}
