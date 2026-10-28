"""Checking HD frames against the originals and keeping the best version of each.

The model sometimes redraws a sprite in other colours or as another thing:
a red laser pistol turned brown, a dark floor turned into tan planks, grey
thatch into yellow sand. Measured on Arroyo and the whole world (8,337
frames), many such frames move the average colour of the sprite: the
mean of |original - HD shrunk back to the original size| per channel, over
the original's opaque pixels, above COLOUR_LIMIT. Frames below it looked
right in early samples, but the 4x Arroyo comparison exposed false accepts:
dirt became wood/planks while keeping almost the same average colour.
Floor tiles therefore also require native-scale spatial structure agreement.
All frames require exact 2x dimensions and the source's nearest-neighbour
alpha mask; colour comparisons alone cannot establish engine geometry.

Re-running a drifted frame at full steps fixes about 60% of them (Arroyo: 29
of 49), so `retry` makes sheets of the rejected frames for another run and
`qa --also` keeps, per frame, the candidate with the least drift. A frame
none of whose candidates pass goes to <work>/rejected/ (kept, never deleted)
and is left out of the pack, so the game draws the original art there.
"""
import json
import os
import shutil

from PIL import Image, ImageChops, ImageFilter, ImageStat

from .gamefiles import frame_rgba, read_frm

COLOUR_LIMIT = 12.0
# Critters also get a shape check: a walking man redrawn with grey trousers
# keeps his average colour (shift 4) but his blurred image moves (20-30 vs
# 10-13 for the right frames of the same animation). Thin scenery sprites
# score high here even when right, so only critters use it.
STRUCTURE_LIMIT = 18.0
# Conservative native-scale RGB error (0..255), Gaussian sigma=1.5 native px.
# This rejects invented broad motifs while permitting subpixel HD texture.
# It is a reference-fidelity gate, not a universal semantic-quality score.
FLOOR_STRUCTURE_LIMIT = 6.0


def original_frame(game, cache, rel):
    """The original RGBA frame of art/<type>/<name>/<direction>_<frame>.png."""
    art, name = rel.rsplit("/", 1)
    direction, frame = map(int, name[:-4].split("_"))
    if art not in cache:
        try:
            cache[art] = read_frm(game.read(art + ".frm"))
        except FileNotFoundError:  # one file per direction (sheets.frames_of)
            cache[art] = None
    if cache[art] is None:
        key = f"{art}.fr{direction}"
        if key not in cache:
            cache[key] = read_frm(game.read(key))
        fr = cache[key].directions[0][frame]
    else:
        fr = cache[art].directions[direction][frame]
    return Image.frombytes("RGBA", (fr.width, fr.height), frame_rgba(fr, game.palette()))


def colour_shift(hd, orig):
    """Mean per-channel difference (0-255) of the average colours over orig's opaque pixels, or None if it has none."""
    mask = orig.getchannel("A")
    if not mask.getbbox():
        return None
    small = hd.convert("RGB").resize(orig.size, Image.BOX)
    a = ImageStat.Stat(orig.convert("RGB"), mask).mean
    b = ImageStat.Stat(small, mask).mean
    return sum(abs(x - y) for x, y in zip(a, b)) / 3


def structure_difference(hd, orig, *, source_background=False):
    """Mean difference (0-255) of the blurred images over orig's opaque pixels, or None if it has none."""
    mask = orig.getchannel("A")
    if not mask.getbbox():
        return None
    blur = ImageFilter.GaussianBlur(1.5)
    small = hd.convert("RGB").resize(orig.size, Image.BOX)
    if source_background:
        # Invisible RGB must not leak into the comparison through the blur.
        small.paste(orig.convert("RGB"), (0, 0), ImageChops.invert(mask))
    small = small.filter(blur)
    return sum(ImageStat.Stat(ImageChops.difference(orig.convert("RGB").filter(blur), small), mask).mean) / 3


def badness(hd, orig, rel, limit):
    """Worst geometry/colour/structure ratio (<=1 passes); floors get a spatial gate."""
    # Colour comparisons resize to native resolution, so they cannot detect a
    # misplaced frame or a changed silhouette. The engine expects exact 2x
    # geometry and nearest-neighbour alpha from the original FRM.
    if hd.size != (orig.width * 2, orig.height * 2) or "A" not in hd.getbands():
        return 1_000_000.0
    expected_alpha = orig.getchannel("A").resize(hd.size, Image.Resampling.NEAREST)
    if hd.getchannel("A").tobytes() != expected_alpha.tobytes():
        return 1_000_000.0
    shift = colour_shift(hd, orig)
    if shift is None:  # nothing opaque: nothing can be wrong
        return 0.0
    score = shift / limit
    if rel.startswith("art/critters/"):
        score = max(score, structure_difference(hd, orig) / STRUCTURE_LIMIT)
    elif rel.startswith("art/tiles/"):
        score = max(score, structure_difference(hd, orig, source_background=True) / FLOOR_STRUCTURE_LIMIT)
    return score


def frames_in(root):
    out = set()
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.endswith(".png"):
                out.add(os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/"))
    return out


def check(game, work, also=(), limit=COLOUR_LIMIT, log=print, keep_passing=False):
    """Picks the least drifted candidate of every frame into <work>/hd, rejected ones into <work>/rejected/hd.

    Candidates: <work>/hd, <work>/rejected/hd and <dir>/hd of each `also`.
    keep_passing: a frame in <work>/hd that passes stays, even if another
    candidate scores lower (a passing 4x-drawn frame beats an older 2x one).
    Returns {frame: {"shift", "source", "rejected"}} and writes it to <work>/qa.json.
    """
    hd_root = os.path.join(work, "hd")
    rejected_root = os.path.join(work, "rejected", "hd")
    roots = [hd_root, rejected_root] + [os.path.join(d, "hd") for d in also]
    cache, results = {}, {}
    for rel in sorted(frames_in(hd_root) | frames_in(rejected_root)):
        orig = original_frame(game, cache, rel)
        best = None
        for root in roots:
            if keep_passing and best is not None and best[1] == hd_root and best[0] <= 1:
                break
            path = os.path.join(root, rel)
            if os.path.exists(path):
                score = badness(Image.open(path), orig, rel, limit)
                if best is None or score < best[0]:
                    best = (score, root)
        score, source = best
        rejected = score > 1
        target = os.path.join(rejected_root if rejected else hd_root, rel)
        if os.path.join(source, rel) != target:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(os.path.join(source, rel), target)
        # One copy per frame: the other folder of the work dir loses it.
        stale = os.path.join(hd_root if rejected else rejected_root, rel)
        if os.path.exists(stale):
            os.unlink(stale)
        results[rel] = {"score": round(score, 3), "source": os.path.relpath(source, work), "rejected": rejected}
    with open(os.path.join(work, "qa.json"), "w") as f:
        json.dump(results, f, indent=0)
    bad = sum(r["rejected"] for r in results.values())
    log(f"{len(results)} frames, {bad} rejected (colour shift > {limit:g}, critter shape > {STRUCTURE_LIMIT:g}), "
        f"{sum(r['source'] not in ('hd', os.path.join('rejected', 'hd')) for r in results.values())} taken from retries")
    return results


def rejected_frames(work):
    """(art .frm path, direction, frame) of every frame in <work>/rejected/hd."""
    out = []
    for rel in sorted(frames_in(os.path.join(work, "rejected", "hd"))):
        art, name = rel.rsplit("/", 1)
        direction, frame = map(int, name[:-4].split("_"))
        out.append((art + ".frm", direction, frame))
    return out
