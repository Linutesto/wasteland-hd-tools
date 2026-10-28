"""Packing art frames onto sheets for the upscaler, and cutting the upscaled sheets back into HD frames.

A model call has a fixed cost, so many small frames share one sheet. Each
frame sits on a flat background with a gutter around it; after upscaling,
its 2x rectangle is cut out and the original's transparency (index 0),
scaled 2x, is put back, so whatever the model drew in the background is
dropped.
"""
import json
import os
from dataclasses import dataclass, asdict

from PIL import Image

from .gamefiles import frame_rgba, read_frm, unique_directions

SCALE = 2
SHEET = 512  # native sheet size; the model returns SHEET * SCALE
GUTTER = 6
BACKGROUND = (96, 88, 80)


@dataclass
class Placement:
    art: str  # art/<type>/<name>.frm
    direction: int
    frame: int
    x: int  # on the native sheet
    y: int
    width: int
    height: int


def frames_of(game, art_paths):
    """(art, direction, frame index, Frame) for every distinct frame of the given art."""
    out = []
    for art in art_paths:
        try:
            frm = read_frm(game.read(art))
        except (FileNotFoundError, Exception) as e:  # broken or missing art is skipped, not fatal
            print(f"skip {art}: {e}")
            continue
        # Some critters keep each direction in its own file, <name>.fr0-.fr5:
        # one direction inside, filed under the direction of its extension.
        ext = art.rsplit(".", 1)[-1].lower()
        fixed = int(ext[2]) if len(ext) == 3 and ext[:2] == "fr" and ext[2] in "012345" else None
        for d in [0] if fixed is not None else unique_directions(frm):
            for i, frame in enumerate(frm.directions[d]):
                if frame.width and frame.height:
                    out.append((art, d if fixed is None else fixed, i, frame))
    return out


def pack(frames, sheet=SHEET, gutter=GUTTER, by_animation=False):
    """Shelf packing, tallest first. Returns a list of sheets, each a list of (Placement, Frame).

    by_animation: each animation direction's frames in order on a row of its
    own, so the model draws a walk cycle in one pass and sees every frame:
    drawn apart, frames each invent their own beard, armband or trouser
    pattern, and the animation boils.
    """
    if by_animation:
        order = sorted(frames, key=lambda f: (f[0], f[1], f[2]))
    else:
        order = sorted(frames, key=lambda f: (-f[3].height, -f[3].width))
    sheets, current, x, y, shelf = [], [], gutter, gutter, 0
    group = None
    for art, d, i, frame in order:
        w, h = frame.width, frame.height
        if w + 2 * gutter > sheet or h + 2 * gutter > sheet:
            # Larger than a sheet: alone on a sheet of its own size.
            sheets.append([(Placement(art, d, i, gutter, gutter, w, h), frame)])
            continue
        if by_animation and (art, d) != group:
            group = (art, d)
            if x > gutter:
                x, y, shelf = gutter, y + shelf + gutter, 0
        if x + w + gutter > sheet:
            x, y, shelf = gutter, y + shelf + gutter, 0
        if y + h + gutter > sheet:
            sheets.append(current)
            current, x, y, shelf = [], gutter, gutter, 0
        current.append((Placement(art, d, i, x, y, w, h), frame))
        x += w + gutter
        shelf = max(shelf, h)
    if current:
        sheets.append(current)
    return sheets


def sheet_size(items, sheet=SHEET, gutter=GUTTER):
    w = max(sheet, max(p.x + p.width + gutter for p, _ in items))
    h = max(sheet, max(p.y + p.height + gutter for p, _ in items))
    # Model latents want multiples of 16 at the output size.
    return (w + 7) // 8 * 8, (h + 7) // 8 * 8


def render_sheet(items, palette, path, sheet=SHEET):
    w, h = sheet_size(items, sheet)
    img = Image.new("RGB", (w, h), BACKGROUND)
    mask = Image.new("L", (w, h), 0)
    for p, frame in items:
        sprite = Image.frombytes("RGBA", (frame.width, frame.height), frame_rgba(frame, palette))
        img.paste(sprite, (p.x, p.y), sprite)
        mask.paste(sprite.getchannel("A"), (p.x, p.y))
    img.save(path)
    mask.save(path + ".mask.png")
    with open(path + ".json", "w") as f:
        json.dump([asdict(p) for p, _ in items], f)
    return img.size


def hd_frame_path(out_dir, p):
    stem = os.path.splitext(p["art"])[0]
    return os.path.join(out_dir, "hd", stem, f"{p['direction']}_{p['frame']}.png")


def cut_sheet(native_sheet, upscaled_path, out_dir):
    """Writes every frame of an upscaled sheet as hd/<art>/<direction>_<frame>.png."""
    with open(native_sheet + ".json") as f:
        placements = json.load(f)
    native = Image.open(native_sheet).convert("RGB")
    mask = Image.open(native_sheet + ".mask.png")
    up = Image.open(upscaled_path).convert("RGB")
    expected = (native.width * SCALE, native.height * SCALE)
    if up.size != expected:
        up = up.resize(expected, Image.LANCZOS)
    written = []
    for p in placements:
        box = (p["x"] * SCALE, p["y"] * SCALE, (p["x"] + p["width"]) * SCALE, (p["y"] + p["height"]) * SCALE)
        rgb = up.crop(box)
        # The original's transparency, 2x: background pixels the model drew are dropped.
        alpha = mask.crop((p["x"], p["y"], p["x"] + p["width"], p["y"] + p["height"])).resize(rgb.size, Image.NEAREST)
        rgba = rgb.convert("RGBA")
        rgba.putalpha(alpha)
        path = hd_frame_path(out_dir, p)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rgba.save(path)
        written.append(path)
    return written

