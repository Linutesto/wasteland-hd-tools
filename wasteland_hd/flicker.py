"""Measure per-frame *generation* flicker in upscaled critter animations.

A walk cycle is supposed to change: limbs move, the body bobs, the sprite
shifts.  A metric that compares neighbouring frames directly therefore
punishes the original animation as much as the model's output, and a plain
colour drift cannot say which changes are invention.  The original FRM is the
only ground truth here, and it says something very specific: on most of the
sprite (torso, helmet, boots, bandolier) two adjacent poses are *byte
identical*.  Every colour difference the model introduces there is noise, at
8-12 FPS visible as boiling.  The metric is therefore

    mean |HD_t - HD_(t+1)| over the pixels where original_t == original_(t+1)
                                  and their 3x3 neighbourhoods agree (6/255)

averaged over every adjacent pair of the cycle (last -> first included) and
pooled over the directions of the animation, in 0-255 RGB units.  It is zero
for an exact nearest-neighbour copy of the originals -- real motion costs
nothing, because it is excluded from the comparison -- and grows only with
changes the original does not justify.

Components reported next to the headline number (same masks, lower is better):

* ``broad``     the locally coherent part of the *difference field* (masked
  low pass, sigma = 2 HD pixels), i.e. wide shading/colour drift such as
  clothing changing hue between frames;
* ``fine``      what is left of the difference after that low pass, i.e.
  detail boiling (texture, stubble, straps) independent of the broad drift;
  ``flicker <= broad + fine`` by the triangle inequality, they are not a
  partition of it;
* ``rim``       the difference inside the one-native-pixel band touching the
  silhouette: the drawn outline/edge treatment changing;
* ``interior``  the difference away from that band;
* ``material``  per-palette-class mean HD colour drift between frames.  It is
  translation invariant, so it also covers moving material the static mask
  cannot see, and it is the shape-independent half of "the same trousers are
  grey in frame 2 and brown in frame 3";
* ``registered`` the existing original-guided residual score from
  :mod:`wasteland_hd.temporal`, kept as a diagnostic.  It compares Qwen minus
  original on *motion-matched* pixels, so it also carries the registration
  error of that match: the noise-free Lanczos control already scores 1.8-2.5
  there, a third of a typical model output, which is why percentage gains on it
  understate a visual change.

Candidates rejected while designing the metric, and why:

* change outside the moving silhouette -- the pipeline copies the original's
  alpha at 2x nearest-neighbour (``sheets.cut``) and QA rejects anything else,
  so this is identically zero and measures nothing;
* raw per-pixel colour drift -- dominated by genuine motion;
* warping every pixel into correspondence -- coarse 1998 art only matches to
  within a pixel or two, and that misregistration error is larger than the
  noise it is supposed to reveal (see ``registered``).

Only NumPy, SciPy and Pillow are used; the input tree is never modified.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import binary_erosion, gaussian_filter, uniform_filter

from .gamefiles import frame_rgba, read_frm, unique_directions

FRAME_NAME = re.compile(r"^(\d+)_(\d+)\.png$")
PAD = 4            # native pixels of margin around a direction's canvas
ERODE = 3          # 3x3: the outline band and the "patch unchanged" window
LOCAL_LIMIT = 6.0  # 0-255 mean original change allowed in that 3x3 window
BROAD_SIGMA = 2.0  # HD pixels; separates broad drift from fine boil
MIN_CLASS = 6      # native pixels a palette class needs in both frames
METRICS = ("flicker", "broad", "fine", "rim", "interior", "material")


def _frames(game, name, direction):
    """The original FRM frames of one direction (one file per direction supported)."""
    art = f"art/critters/{name}"
    try:
        return read_frm(game.read(art + ".frm")).directions[direction]
    except FileNotFoundError:
        return read_frm(game.read(f"{art}.fr{direction}")).directions[0]


def _nearest2(a):
    y, x = (-3, -2) if a.ndim >= 3 and a.shape[-1] in (3, 4) else (-2, -1)
    return np.repeat(np.repeat(a, 2, axis=y), 2, axis=x)


def _paths(root, name, direction):
    """[(frame index, HD png)] of one direction in a HD tree."""
    folder = Path(root) / "art" / "critters" / name
    found = []
    for png in folder.glob("*.png"):
        match = FRAME_NAME.fullmatch(png.name)
        if match and int(match.group(1)) == direction:
            found.append((int(match.group(2)), png))
    return sorted(found, key=lambda item: item[0])


def _canvases(game, name, direction, paths, control=None):
    """Native originals and 2x details on one canvas each, top-left packed.

    Only content that is identical in both frames is ever compared, so no
    anatomical anchor or world translation is needed; any consistent placement
    gives the same answer, and top-left is the one that keeps the sampled
    pixels in the same canvas position in both frames.  ``control`` replaces
    the HD frames with a deterministic upscale of the originals.
    """
    frames = _frames(game, name, direction)
    palette = game.palette()
    originals, details = [], []
    for index, path in paths:
        frame = frames[index]
        orig = np.frombuffer(frame_rgba(frame, palette), np.uint8).reshape(frame.height, frame.width, 4)
        originals.append(orig)
        expected = (frame.height * 2, frame.width * 2)
        if control is None:
            hd = np.asarray(Image.open(path).convert("RGBA"))
            if hd.shape[:2] != expected:
                raise ValueError(f"{path}: expected {expected[1]}x{expected[0]}, got {hd.shape[1]}x{hd.shape[0]}")
            details.append(hd)
        elif control == "lanczos":
            # RGB only: the pipeline replaces alpha with nearest-neighbour anyway.
            rgb = Image.fromarray(np.ascontiguousarray(orig[..., :3]))
            big = np.asarray(rgb.resize((frame.width * 2, frame.height * 2), Image.LANCZOS))
            details.append(np.dstack((big, _nearest2(orig[..., 3]))))
        elif control == "nearest":
            details.append(_nearest2(orig))
        else:
            raise ValueError(f"unknown control: {control}")

    width = max(o.shape[1] for o in originals) + 2 * PAD
    height = max(o.shape[0] for o in originals) + 2 * PAD
    native = np.zeros((len(originals), height, width, 4), dtype=np.uint8)
    detail = np.zeros((len(details), height * 2, width * 2, 4), dtype=np.uint8)
    for i, (orig, hd) in enumerate(zip(originals, details)):
        h, w = orig.shape[:2]
        native[i, PAD:PAD + h, PAD:PAD + w] = orig
        detail[i, 2 * PAD:2 * PAD + 2 * h, 2 * PAD:2 * PAD + 2 * w] = hd
        if not np.array_equal(detail[i, ..., 3], _nearest2(native[i, ..., 3])):
            raise ValueError(f"{name}/{direction}_{i}: HD alpha differs from original 2x nearest")
    return native, detail


def _block_mean(rgb):
    """One HD colour per native pixel, so classes can be compared per original texel."""
    h, w = rgb.shape[0] // 2, rgb.shape[1] // 2
    return rgb[:2 * h, :2 * w].reshape(h, 2, w, 2, 3).mean(axis=(1, 3))


def _class_means(native, rgb):
    """{packed original RGB: (opaque pixel count, mean HD RGB)} for one frame."""
    opaque = native[..., 3] > 0
    keys = ((native[..., 0].astype(np.int32) << 16)
            | (native[..., 1].astype(np.int32) << 8) | native[..., 2].astype(np.int32))[opaque]
    if not keys.size:
        return {}
    unique, inverse = np.unique(keys, return_inverse=True)
    counts = np.bincount(inverse).astype(np.float32)
    block = _block_mean(rgb)[opaque]
    means = np.stack([np.bincount(inverse, weights=block[:, c]) / counts for c in range(3)], axis=1)
    return {int(k): (float(n), m) for k, n, m in zip(unique, counts, means)}


def _unchanged(a, b):
    """Pixels whose original content and 3x3 surroundings neither moved nor changed.

    A hard all-neighbours-equal test erases every sample on thin sprites
    (plants, rats) whose poses share only scattered pixels; the mean original
    change of the patch is the same safeguard with a measured tolerance.  A
    moved limb next to the sample still shows, it is just no longer enough on
    its own to discard the pixel.
    """
    change = (np.abs(a[..., :3].astype(np.float32) - b[..., :3].astype(np.float32)).mean(axis=-1)
              + 255.0 * (a[..., 3] != b[..., 3]))
    return uniform_filter(change, ERODE) <= LOCAL_LIMIT


def _static(a, b):
    """The pixels the metric compares for one pair: identical and locally unchanged."""
    return np.all(a == b, axis=-1) & (a[..., 3] > 0) & _unchanged(a, b)


def _measure(native, detail):
    """Cycle sums: (per-metric sum, pixel or class-area weight)."""
    n = len(detail)
    rgb = detail[..., :3].astype(np.float32)
    sums = dict.fromkeys(METRICS, 0.0)
    weights = dict.fromkeys(METRICS, 0)
    for i in range(n):
        j = (i + 1) % n
        safe = _static(native[i], native[j])
        rim = safe & ~binary_erosion(native[i, ..., 3] > 0, structure=np.ones((ERODE, ERODE)))
        mask = _nearest2(safe)
        rim2, interior2 = _nearest2(rim), _nearest2(safe & ~rim)
        if mask.any():
            delta = rgb[i] - rgb[j]
            diff = np.abs(delta).mean(axis=-1)
            # Decompose the difference itself: its locally coherent part is
            # broad drift, what is left is detail boil.  Masked normalised
            # convolution keeps moving neighbours out of a still pixel (an
            # unmasked blur leaks them in and gives a nonzero score to a
            # deterministic upscale).
            weight = mask.astype(np.float32)
            support = np.maximum(gaussian_filter(weight, BROAD_SIGMA), 1e-4)
            low = np.stack([gaussian_filter(delta[..., c] * weight, BROAD_SIGMA) for c in range(3)], axis=-1)
            low /= support[..., None]
            sums["flicker"] += float(diff[mask].sum())
            sums["broad"] += float(np.abs(low).mean(axis=-1)[mask].sum())
            sums["fine"] += float(np.abs(delta - low).mean(axis=-1)[mask].sum())
            sums["rim"] += float(diff[rim2].sum())
            sums["interior"] += float(diff[interior2].sum())
            weights["flicker"] += int(mask.sum())
            weights["broad"] += int(mask.sum())
            weights["fine"] += int(mask.sum())
            weights["rim"] += int(rim2.sum())
            weights["interior"] += int(interior2.sum())
        # Material identity: the same original palette class, moving or not.
        a = _class_means(native[i], rgb[i])
        b = _class_means(native[j], rgb[j])
        for key in a.keys() & b.keys():
            na, ma = a[key]
            nb, mb = b[key]
            if na >= MIN_CLASS and nb >= MIN_CLASS:
                sums["material"] += float(np.abs(ma - mb).mean()) * na
                weights["material"] += int(na)
    return sums, weights


def _ratio(sums, weights, key):
    return round(sums[key] / weights[key], 3) if weights[key] else None


def _registered(native, detail):
    """The temporal.py diagnostic (its own score, not part of this metric)."""
    from .temporal import score_group

    return round(score_group(native, detail[..., :3].astype(np.float32)), 3)


def direction_score(game, name, direction, paths, control=None):
    """Flicker record for one direction (an animation is frames x directions)."""
    native, detail = _canvases(game, name, direction, paths, control=control)
    sums, weights = _measure(native, detail)
    record = {"animation": name, "direction": direction, "frames": len(paths),
              "pixels": weights["flicker"], "material_pixels": weights["material"]}
    record.update({key: _ratio(sums, weights, key) for key in METRICS})
    record["registered"] = _registered(native, detail)
    return record


def _hd_animations(root):
    """{animation: [direction, ...]} in a HD tree's art/critters/."""
    out = {}
    base = Path(root) / "art" / "critters"
    if not base.is_dir():
        raise FileNotFoundError(f"{root}: no art/critters/ in the source tree")
    for folder in sorted(p for p in base.iterdir() if p.is_dir()):
        directions = {int(m.group(1)) for png in folder.glob("*.png")
                      for m in [FRAME_NAME.fullmatch(png.name)] if m}
        if directions:
            out[folder.name] = sorted(directions)
    return out


def _frm_directions(game, name):
    """Directions of a critter's originals (shared FRM directions counted once)."""
    art = f"art/critters/{name}"
    try:
        return unique_directions(read_frm(game.read(art + ".frm")))
    except FileNotFoundError:
        found = []
        for direction in range(6):
            try:
                read_frm(game.read(f"{art}.fr{direction}"))
                found.append(direction)
            except FileNotFoundError:
                continue
        return found


def _frm_paths(game, name, direction):
    return [(i, None) for i in range(len(_frames(game, name, direction)))]


def _pool(records):
    """Weighted animation totals; ``material`` by palette-class area, the rest by pixels."""
    total = dict.fromkeys(METRICS, 0.0)
    mass = dict.fromkeys(METRICS, 0)
    for record in records:
        for key in METRICS:
            weight = record["material_pixels"] if key == "material" else record["pixels"]
            if weight and record[key] is not None:
                total[key] += record[key] * weight
                mass[key] += weight
    return {key: round(total[key] / mass[key], 3) if mass[key] else None for key in METRICS}


def analyse(game, source_root, names=None, control=None):
    """Flicker of every (or the named) animation in a HD critter tree.

    ``control`` ("nearest" or "lanczos") scores a deterministic upscale of the
    originals instead of the HD files; the animation names then come from
    ``names`` or the source tree, and no HD frame is read.
    """
    if control is not None:
        wanted = {name: _frm_directions(game, name)
                  for name in (names or sorted(_hd_animations(source_root)))}
    else:
        wanted = _hd_animations(source_root)
        if names:
            missing = sorted(set(names) - set(wanted))
            if missing:
                raise ValueError(f"{source_root}: no frames for {', '.join(missing)}")
            wanted = {name: wanted[name] for name in names}
    if not wanted:
        raise ValueError(f"no critter animations to measure in {source_root}")

    directions, animations = {}, {}
    for name, wanted_dirs in sorted(wanted.items()):
        records = []
        for direction in wanted_dirs:
            paths = _paths(source_root, name, direction) if control is None \
                else _frm_paths(game, name, direction)
            if len(paths) < 2:
                continue
            record = direction_score(game, name, direction, paths, control=control)
            directions[f"{name}/{direction}"] = record
            records.append(record)
        if not records:
            raise ValueError(f"{name}: no direction with two or more frames")
        summary = _pool(records)
        summary.update({
            "frames": max(r["frames"] for r in records),
            "directions": len(records),
            "pixels": sum(r["pixels"] for r in records),
            "mean_direction": round(sum(r["flicker"] for r in records) / len(records), 3),
        })
        animations[name] = summary
    return {"source": str(source_root), "control": control,
            "animations": animations, "directions": directions}


def _panel(rgb, alpha, background=(32, 30, 28)):
    """A sprite on a flat background: alpha-shaped, no black halo."""
    canvas = np.empty((*rgb.shape[:2], 3), dtype=np.uint8)
    canvas[:] = background
    keep = alpha > 0
    canvas[keep] = rgb[keep]
    return canvas


def contact_sheet(game, source_root, result, path, top=4, control=None):
    """Strip of the worst directions: HD t | HD t+1 | 4x diff | static mask.

    Rows are ranked by the same score the CLI prints and every panel is
    labelled, so a human can confirm that the offending pixels are material the
    original leaves identical.  Nothing here feeds back into the score.
    """
    ranked = sorted((r for r in result["directions"].values() if r["pixels"]),
                    key=lambda r: -r["flicker"])[:top]
    rows = []
    for record in ranked:
        name, direction = record["animation"], record["direction"]
        paths = _paths(source_root, name, direction) if control is None \
            else _frm_paths(game, name, direction)
        native, detail = _canvases(game, name, direction, paths, control=control)
        best = (float("-inf"), 0, 1)
        for i in range(len(detail)):
            j = (i + 1) % len(detail)
            mask = _nearest2(_static(native[i], native[j]))
            if mask.any():
                value = float(np.abs(detail[i, ..., :3].astype(np.float32)
                                     - detail[j, ..., :3].astype(np.float32)).mean(axis=-1)[mask].mean())
                if value > best[0]:
                    best = (value, i, j)
        _value, i, j = best
        diff = np.abs(detail[i, ..., :3].astype(np.float32)
                      - detail[j, ..., :3].astype(np.float32)).mean(axis=-1)
        static = np.where(_nearest2(_static(native[i], native[j])), 255, 0).astype(np.uint8)
        alpha = detail[i, ..., 3]
        panels = [
            _panel(detail[i, ..., :3], alpha),
            _panel(detail[j, ..., :3], detail[j, ..., 3]),
            _panel(np.dstack((np.clip(diff * 4, 0, 255),) * 3).astype(np.uint8), alpha),
            _panel(np.dstack((static,) * 3), alpha),
        ]
        rows.append((f"{name}/{direction} flicker {record['flicker']:.2f} "
                     f"worst pair {i}->{j} diff {best[0]:.2f}",
                     np.concatenate(panels, axis=1)))

    if not rows:
        return []
    widths = max(row.shape[1] for _label, row in rows) + 6
    label_height, scale = 12, 3
    strip = np.full((len(rows) * (max(r.shape[0] for _l, r in rows) + label_height), widths, 3),
                    (22, 22, 22), dtype=np.uint8)
    y = 0
    for label, row in rows:
        strip[y:y + row.shape[0], :row.shape[1]] = row
        y += row.shape[0] + label_height
    sheet = Image.fromarray(np.repeat(np.repeat(strip, scale, axis=0), scale, axis=1))
    draw = ImageDraw.Draw(sheet)
    y = 0
    for label, row in rows:
        y += row.shape[0] * scale
        draw.text((3, y + 1), label, fill=(230, 230, 230))
        y += label_height * scale
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)
    return [f"{r['animation']}/{r['direction']}" for r in ranked]


def safe_output(source, path):
    """Refuse to write a report or sheet inside the measured tree."""
    src, dst = Path(source).resolve(), Path(path).resolve()
    if dst == src or src in dst.parents:
        raise ValueError(f"{path}: refusing to write inside the measured tree {source}")
    return dst


def write_json(result, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(result, f, indent=2, sort_keys=True)
        f.write("\n")
    return path
