"""Checking HD frames against the originals and keeping the best version of each.

The model sometimes redraws a sprite in other colours or as another thing:
a red laser pistol turned brown, a dark floor turned into tan planks, grey
thatch into yellow sand. Measured on Arroyo and the whole world (8,337
frames), almost every such frame moves the average colour of the sprite: the
mean of |original - HD shrunk back to the original size| per channel, over
the original's opaque pixels, above COLOUR_LIMIT. Frames below it looked
right in every sample, including ones a vision model wrongly flagged.

Re-running a drifted frame at full steps fixes about 60% of them (Arroyo: 29
of 49), so `retry` makes sheets of the rejected frames for another run and
`qa --also` keeps, per frame, the candidate with the least drift. A frame
none of whose candidates pass goes to <work>/rejected/ (kept, never deleted)
and is left out of the pack, so the game draws the original art there.
"""
import json
import os
import shutil

from PIL import Image, ImageStat

from .gamefiles import frame_rgba, read_frm

COLOUR_LIMIT = 12.0


def original_frame(game, cache, rel):
    """The original RGBA frame of art/<type>/<name>/<direction>_<frame>.png."""
    art, name = rel.rsplit("/", 1)
    direction, frame = map(int, name[:-4].split("_"))
    if art not in cache:
        cache[art] = read_frm(game.read(art + ".frm"))
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


def frames_in(root):
    out = set()
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.endswith(".png"):
                out.add(os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/"))
    return out


def check(game, work, also=(), limit=COLOUR_LIMIT, log=print):
    """Picks the least drifted candidate of every frame into <work>/hd, rejected ones into <work>/rejected/hd.

    Candidates: <work>/hd, <work>/rejected/hd and <dir>/hd of each `also`.
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
            path = os.path.join(root, rel)
            if os.path.exists(path):
                shift = colour_shift(Image.open(path), orig)
                if shift is None:  # nothing opaque: nothing can be wrong
                    shift = 0.0
                if best is None or shift < best[0]:
                    best = (shift, root)
        shift, source = best
        rejected = shift > limit
        target = os.path.join(rejected_root if rejected else hd_root, rel)
        if os.path.join(source, rel) != target:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(os.path.join(source, rel), target)
        # One copy per frame: the other folder of the work dir loses it.
        stale = os.path.join(hd_root if rejected else rejected_root, rel)
        if os.path.exists(stale):
            os.unlink(stale)
        results[rel] = {"shift": round(shift, 2), "source": os.path.relpath(source, work), "rejected": rejected}
    with open(os.path.join(work, "qa.json"), "w") as f:
        json.dump(results, f, indent=0)
    bad = sum(r["rejected"] for r in results.values())
    log(f"{len(results)} frames, {bad} rejected (colour shift > {limit:g}), "
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
