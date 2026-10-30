"""CPU-only source-ground texture and border measurements for HD frames."""
from __future__ import annotations

from collections import defaultdict
import struct
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import (binary_dilation, distance_transform_edt, gaussian_filter,
                           label, maximum_filter, sobel, uniform_filter)

from .chroma import cast_measure
from .gamefiles import read_frm
from .production import atomic_json, read_json

# Calibrated on the named October 29 Lightning tile review. Values use opaque area.
EDGE_EXCESS = 20.0
EDGE_ORIGINAL_FACTOR = 1.5
EDGE_DILATION = 7
FLAT_SOURCE_STD = 7.0
FLAT_EXCESS_STD = 9.0
BORDER_ERROR_LIMIT = 12.0
SEAM_DELTA_LIMIT = 18.0
TEXTURE_AREA_LIMIT = 0.08
TEXTURE_CLUSTER_LIMIT = 0.055
FLAT_DAMAGE_LIMIT = 0.045
MINOR_AREA_LIMIT = 0.04

# Wall junction calibration (October 29 2026 world-seam junction review).
# Walls are placed by the engine's hex screen point, so two wall sprites that
# meet on the map touch pixel-for-pixel; HD redraws of each piece can disagree
# across that contact in colour or brick phase where the source art does not.
WALL_SEAM_BAND = 6          # 2x px sampled on each side of a junction contact
WALL_SEAM_MIN_SAMPLES = 16  # fewer junction px than this cannot support a verdict
WALL_SEAM_DELTA_LIMIT = 18.0  # calibrated on labelled world-seam junctions
# Measured quantity version: the seam cache is keyed on it, so changing what a
# junction delta *is* (the line term below, added October 30) re-measures every
# junction instead of trusting deltas the previous metric produced.
WALL_SEAM_METRIC = 2
# A redraw can leave a bright 1-px rim on a frame's alpha boundary.  Where two
# wall pieces overlap that rim reads as a light vertical line running the whole
# wall height, which the 6-px colour band averages away (it compares each band
# pixel with the *other* sprite, not with its own neighbour).  The junction
# therefore also measures the sharpest 1-2 px column step along the contact and
# subtracts the same step the original pair has, so the source's own joints
# (posts, door frames) stay the baseline.  Shares WALL_SEAM_DELTA_LIMIT: both
# terms are an extra brightness break, in the same 0-255 units.
WALL_LINE_WINDOW = 2        # 2x px of context kept around each row's contact
WALL_LINE_MIN_ROWS = 8      # fewer junction rows than this cannot hold a line

# Non-floor sprite calibration (walls/scenery/items/misc, October 29 2026
# world-seam review). Sprites are alpha silhouettes, often thin strips or tiny
# frames, so floor ratios alone misread them.
KINDS = ('tiles', 'walls', 'scenery', 'items', 'misc')
SPRITE_BAND = 4          # 2x px beside the alpha boundary: silhouette AA, not invention
SPRITE_CORE_SHARE = 0.25 # band is only trusted when this much of the mask stays beyond it
SPRITE_CORE_MIN = 64     # ...and at least this many 2x px do
SPRITE_MIN_AREA = 400    # 2x opaque-px below which fractions need absolute evidence
SPRITE_MIN_PIXELS = 12   # absolute novel px for minor_invention on tiny/thin sprites
SPRITE_MIN_PIXELS_TEXTURE = 40   # absolute novel/flat px for invented_texture there
SPRITE_DARK_LIMIT = 0.05 # dark-region novelty; sprites use a looser bar than tiles
SPRITE_FLAT_CLUSTER = 0.03   # connected novelty needed before flat damage counts on sprites
EMISSIVE_LIFT = 25.0     # HD brighter than source by this much is added light, not dark structure


def kind_of(rel: str) -> str:
    """Art kind for a relative art path; anything non-sprite counts as tiles."""
    parts = rel.split('/')
    if len(parts) >= 3 and parts[0] == 'art' and parts[1] in KINDS:
        return parts[1]
    return 'tiles'


def _edge(rgb):
    grey = gaussian_filter(rgb.mean(axis=2), 1.2)
    return np.hypot(sobel(grey, axis=0), sobel(grey, axis=1))


def _single_scale(hd, original, *, sprite=False):
    # Original is resized with bicubic interpolation, avoiding nearest-pixel edges.
    src = np.asarray(original.convert('RGBA').resize(hd.size, Image.Resampling.BICUBIC), np.float32)
    dst = np.asarray(hd.convert('RGBA'), np.float32)
    opaque = np.asarray(original.getchannel('A').resize(hd.size, Image.Resampling.NEAREST)) > 0
    distance = distance_transform_edt(opaque)
    interior = distance > 3
    mask = interior if interior.sum() >= 16 else opaque
    keys = ('novel_fraction', 'largest_novel_region', 'novel_window', 'flat_damage',
            'dark_novel', 'border_error')
    if not mask.any():
        extra = ('novel_pixels', 'flat_pixels', 'mask_pixels') if sprite else ()
        return {k: 0.0 for k in keys + extra}
    # Luminance finds absent edges; chroma catches the magenta stripes.
    se = maximum_filter(_edge(src[..., :3]), size=EDGE_DILATION)
    he = _edge(dst[..., :3])
    novel = (he > EDGE_ORIGINAL_FACTOR * se + EDGE_EXCESS) & mask
    # Silhouette anti-aliasing is not invention: ignore the band hugging the
    # alpha boundary, but only when enough of the sprite lies beyond it. Stroke
    # art (fences, grids, blades) has no core at all, and a band that swallows
    # the whole silhouette would blind the gate to wholly invented textures.
    if sprite:
        band = mask & (distance <= SPRITE_BAND)
        core = mask & ~band
        trusted = core.sum() >= SPRITE_CORE_MIN and core.sum() >= SPRITE_CORE_SHARE * mask.sum()
        band = band if trusted else np.zeros_like(mask)
    else:
        band = np.zeros_like(mask)
    novel = novel & ~band
    nearby = uniform_filter(novel.astype(np.float32), size=12)
    # Link edges in a motif before measuring connected area, then restrict to
    # truly novel edge pixels so thin, separate strokes do not become a full tile.
    connected, count = label(binary_dilation(novel, iterations=2))
    largest = max((int((novel & (connected == n)).sum()) for n in range(1, count + 1)), default=0)
    source_luma = src[..., :3].mean(axis=2)
    hd_luma = dst[..., :3].mean(axis=2)
    def std(a):
        return np.sqrt(np.maximum(uniform_filter(a*a, size=9) - uniform_filter(a, size=9)**2, 0))
    flat = (std(source_luma) < FLAT_SOURCE_STD) & mask
    damage = flat & (std(hd_luma) > std(source_luma) + FLAT_EXCESS_STD)
    if sprite:
        damage = damage & ~band
    dark = novel & (source_luma < 55)
    if sprite:
        # Lamps, screens and other lit detail may glow more after upscaling;
        # added light is not invented dark structure. The lift is taken over a
        # neighbourhood so the anti-aliased ring of a glow counts as light too.
        lift = maximum_filter(hd_luma - source_luma, size=EDGE_DILATION)
        dark = dark & ~(lift > EMISSIVE_LIFT)
    boundary = opaque & (distance <= 4)
    # Include image perimeter; a fully opaque rectangle has no alpha boundary.
    boundary[:4] |= opaque[:4]; boundary[-4:] |= opaque[-4:]
    boundary[:, :4] |= opaque[:, :4]; boundary[:, -4:] |= opaque[:, -4:]
    err = np.abs(src[..., :3] - dst[..., :3]).mean(axis=2)
    result = {'novel_fraction': round(float(novel.sum()/mask.sum()), 5),
              'largest_novel_region': round(float(largest/mask.sum()), 5),
              'novel_window': round(float(nearby.max()), 5),
              'flat_damage': round(float(damage.sum()/mask.sum()), 5),
              'dark_novel': round(float(dark.sum()/mask.sum()), 5),
              'border_error': round(float(err[boundary].mean()) if boundary.any() else 0.0, 3)}
    if sprite:
        result.update({'novel_pixels': int(novel.sum()), 'flat_pixels': int(damage.sum()),
                       'mask_pixels': int(mask.sum())})
    return result


def _sprite_verdict(high):
    """Verdict for alpha sprites, using absolute evidence on tiny/thin frames."""
    area = high['novel_fraction']; cluster = high['largest_novel_region']
    flat = high['flat_damage']; dark = high['dark_novel']
    if high['mask_pixels'] < SPRITE_MIN_AREA:
        # A handful of novel pixels moves a ratio a lot on tiny frames; demand
        # a real amount of changed structure before failing one. Flat-area
        # damage says nothing here: a bicubic-resized thin stroke reads as
        # flat source, so faithful wires would look damaged everywhere.
        if high['novel_pixels'] >= SPRITE_MIN_PIXELS_TEXTURE:
            return 'invented_texture'
        if high['novel_pixels'] >= SPRITE_MIN_PIXELS:
            return 'minor_invention'
        return 'clean'
    if area > TEXTURE_AREA_LIMIT or cluster > TEXTURE_CLUSTER_LIMIT or (flat > FLAT_DAMAGE_LIMIT and cluster > SPRITE_FLAT_CLUSTER) or dark > SPRITE_DARK_LIMIT:
        return 'invented_texture'
    if area > MINOR_AREA_LIMIT or cluster > 0.015:
        return 'minor_invention'
    return 'clean'


def source_exact(hd: Image.Image, original: Image.Image) -> bool:
    """True when the frame *is* the original art resampled 2x (nearest).

    Nothing was generated there, so no measurement of novelty can be true:
    any difference a reviewer sees is the source's own pixel grid.  The
    comparison is exact, which is what makes the verdict safe to trust.
    """
    return np.array_equal(np.asarray(hd.convert('RGBA')),
                          np.asarray(original.convert('RGBA')
                                     .resize(hd.size, Image.Resampling.NEAREST)))


def score(hd: Image.Image, original: Image.Image, *, seam_delta=0.0, floor=True, kind='tiles',
          cast_tiles=False):
    """Return measurements at native and 2x sizes and a four-way verdict."""
    if kind not in KINDS:
        raise ValueError(f'unknown art kind: {kind!r}')
    if hd.size != (original.width*2, original.height*2):
        raise ValueError('HD frame must be exact 2x')
    sprite = kind != 'tiles'
    # Sprite border error is measured at the alpha silhouette, where AA and
    # erosion are normal; only floor tiles can be unmatched against neighbours.
    floor = floor and not sprite
    # Untouched game art cannot hold invented texture.  Only a junction can
    # still fail it, and floor tiles (floor=True) keep their calibrated path.
    untouched = not floor and source_exact(hd, original)
    native = _single_scale(hd.resize(original.size, Image.Resampling.BOX), original, sprite=sprite)
    high = _single_scale(hd, original, sprite=sprite)
    # At native scale, source pixels are themselves strongly stair-stepped;
    # use the 2x evidence for inventions and retain native evidence for review.
    area = high['novel_fraction']; cluster = high['largest_novel_region']
    flat = high['flat_damage']
    border = high['border_error']
    if floor and (border > BORDER_ERROR_LIMIT or seam_delta > SEAM_DELTA_LIMIT):
        verdict = 'unmatched_floor'
    elif kind == 'walls' and seam_delta > WALL_SEAM_DELTA_LIMIT:
        # A wall piece that does not continue into its placed neighbour on the
        # map (colour step or brick phase across the contact) is unmatched.
        verdict = 'unmatched_wall'
    elif untouched:
        verdict = 'clean'
    elif sprite:
        verdict = _sprite_verdict(high)
    elif area > TEXTURE_AREA_LIMIT or cluster > TEXTURE_CLUSTER_LIMIT or (flat > FLAT_DAMAGE_LIMIT and cluster > 0.025) or high['dark_novel'] > 0.025:
        verdict = 'invented_texture'
    elif area > MINOR_AREA_LIMIT or cluster > 0.015:
        verdict = 'minor_invention'
    else:
        verdict = 'clean'
    score_out = {'verdict': verdict, 'native': native, 'scale2': high,
                 'seam_delta': round(float(seam_delta), 3), 'kind': kind}
    # A chroma cast (a connected region whose a*/b* moved consistently) is a
    # fidelity detail, not a calibrated hard fail: the regional-mean gates
    # cannot see one, so it is reported here without touching the verdict.
    # Tiles/floors are not measured by default — their verdicts, keys and
    # outputs stay byte-identical — although the label evidence does show
    # placeholder-tile casts (bld20xx "Unused Tile"), so `cast_tiles=True`
    # includes them (see docs/CHROMA-REPORT.md).
    if kind != 'tiles' or cast_tiles:
        score_out['cast'] = cast_measure(hd, original)
    return score_out


def map_adjacency(game):
    """Undirected pairs of tile FRMs actually neighbouring in map floors.

    Fallout 2 maps hold three 100x100 tile grids; upper 16 bits are floor and
    lower 16 bits roof. Only floor placements enter this list. Map coordinates
    are grid coordinates; each horizontal/vertical shared edge is counted once.
    """
    names = {name for dat in game.dats for name in dat.names('maps/') if name.lower().endswith('.map')}
    if game.data_dir:
        names.update('maps/' + p.name.lower() for p in (Path(game.data_dir) / 'maps').glob('*.map'))
    tiles = game.art_list(4)
    pairs = set()
    for name in sorted(names):
        try:
            data = game.read(name)
            if struct.unpack_from('>I', data, 0)[0] != 20:
                continue
            local = struct.unpack_from('>i', data, 0x20)[0]
            flags = struct.unpack_from('>i', data, 0x28)[0]
            glob = struct.unpack_from('>i', data, 0x30)[0]
            pos = 0xEC + 4*(local+glob)
            for absent in (0x2, 0x4, 0x8):
                if flags & absent:
                    continue
                grid = [struct.unpack_from('>I', data, pos+4*i)[0] >> 16 & 0x0FFF for i in range(10000)]
                pos += 40000
                for y in range(100):
                    for x in range(100):
                        a = grid[y*100+x]
                        if a <= 1 or a >= len(tiles):
                            continue
                        for dx,dy,side in ((1,0,'right'),(0,1,'bottom')):
                            if x+dx >= 100 or y+dy >= 100:
                                continue
                            b = grid[(y+dy)*100+x+dx]
                            if 1 < b < len(tiles):
                                pairs.add((f'art/tiles/{tiles[a].rsplit(".",1)[0]}/0_0.png',
                                           f'art/tiles/{tiles[b].rsplit(".",1)[0]}/0_0.png', side))
        except (OSError, ValueError, struct.error, IndexError):
            continue
    return sorted(pairs)


def cached_adjacency(game, out_root, *, write=True):
    path = Path(out_root) / 'map-adjacency.json'
    saved = read_json(path)
    if saved is not None:
        return saved['pairs']
    pairs = map_adjacency(game)
    if write:
        atomic_json(path, {'pairs': pairs})
    return pairs


# ---------------------------------------------------------------- wall junctions

HEX_GRID_WIDTH = 200   # map hex columns (fallout2-ce map_defs.h)
WALL_TOUCH = 1         # native px: each alpha dilated this far, so <=2 px apart counts as touching
WALL_CELL = 64         # native px grid used to avoid testing every wall pair


def _hex_screen(tile):
    """tileToScreenXY (tile.cc) with a zero view offset: the hex's absolute screen point."""
    v3 = HEX_GRID_WIDTH - 1 - tile % HEX_GRID_WIDTH
    v4 = tile // HEX_GRID_WIDTH
    x = 48 * (v3 // 2)
    y = -12 * (v3 // 2)
    if v3 & 1:
        if v3 <= 0:
            x -= 16
            y += 12
        else:
            x += 32
    return x + 16 * v4, y + 12 * v4


def wall_records(data):
    """(tile, x, y, frame, rotation, elevation, fid) of every wall object in a map file.

    Object records (object.cc objectRead) start with id, tile, x, y, sx, sy,
    frame, rotation, then fid at +0x20, elevation at +0x28 and pid at +0x2C.
    Records have variable-length tails, so like map_art the list is found by
    scanning for that id pattern rather than walking it.
    """
    if struct.unpack_from('>I', data, 0)[0] != 20:
        raise ValueError('not a Fallout 2 map')
    local, = struct.unpack_from('>i', data, 0x20)
    flags, = struct.unpack_from('>i', data, 0x28)
    glob, = struct.unpack_from('>i', data, 0x30)
    pos = 0xEC + 4 * (glob + local)
    for absent in (0x2, 0x4, 0x8):
        if not flags & absent:
            pos += 40000
    out = []
    for p in range(pos, len(data) - 0x30, 4):
        fid, _obj_flags, elevation, pid = struct.unpack_from('>IIII', data, p + 0x20)
        if fid >> 24 != 3 or pid >> 24 != 3 or elevation > 2 or fid & 0x00FFF000:
            continue  # not a wall object (3 = OBJ_TYPE_WALL in both fid and pid)
        tile, x, y, _sx, _sy, frame, rotation = struct.unpack_from('>iiiiiii', data, p + 0x04)
        if not (0 <= tile < HEX_GRID_WIDTH * 200 and 0 <= rotation <= 5
                and 0 <= frame < 4096 and abs(x) <= 4096 and abs(y) <= 4096):
            continue  # bytes that only look like a record
        out.append((tile, x, y, frame, rotation, elevation, fid))
    return out


def _wall_sprite(game, cache, art, rotation, frame):
    """(rel, width, height, shift_x, shift_y, mask, dilated) of one wall frame, or None."""
    key = (art, rotation, frame)
    if key in cache:
        return cache[key]
    sprite = None
    try:
        frm = read_frm(game.read(art))
        frames = frm.directions[rotation]
        if frame < len(frames) and frames[frame].width and frames[frame].height:
            fr = frames[frame]
            pixels = np.frombuffer(fr.pixels, np.uint8).reshape(fr.height, fr.width)
            mask = pixels != 0
            # The PNG is filed under the first direction sharing these frames.
            shared = id(frames)
            direction = next((d for d, f in enumerate(frm.directions) if id(f) == shared), rotation)
            stem = art[:-4] if art.lower().endswith('.frm') else art
            sprite = (f'{stem}/{direction}_{frame}.png', fr.width, fr.height,
                      frm.shift_x[rotation], frm.shift_y[rotation],
                      mask, binary_dilation(mask, iterations=WALL_TOUCH))
    except (OSError, ValueError, struct.error, IndexError):
        sprite = None
    cache[key] = sprite
    return sprite


def _place_mask(box, mask, ix0, iy0, ix1, iy1):
    """mask on the overlap grid, False where the sprite does not reach."""
    out = np.zeros((iy1 - iy0, ix1 - ix0), bool)
    ox, oy = box
    x0, x1 = max(ox, ix0), ix1
    y0, y1 = max(oy, iy0), iy1
    x1 = min(x1, ox + mask.shape[1])
    y1 = min(y1, oy + mask.shape[0])
    if x1 > x0 and y1 > y0:
        out[y0 - iy0:y1 - iy0, x0 - ix0:x1 - ix0] = mask[y0 - oy:y1 - oy, x0 - ox:x1 - ox]
    return out


def _map_wall_junctions(game, name, sprites, walls_lst):
    """Junctions between touching wall sprites of one map elevation."""
    records = wall_records(game.read(name))
    label = name.replace(chr(92), '/').rsplit('/', 1)[-1].lower()
    out = []
    for elevation in sorted({r[5] for r in records}):
        placed = []
        for tile, x, y, frame, rotation, elev, fid in records:
            if elev != elevation:
                continue
            index = fid & 0xFFF
            if index >= len(walls_lst):
                continue
            sprite = _wall_sprite(game, sprites, f'art/walls/{walls_lst[index]}', rotation, frame)
            if sprite is None:
                continue
            rel, w, h, shift_x, shift_y, _mask, _dil = sprite
            sx, sy = _hex_screen(tile)
            left = sx + 16 + shift_x + x - w // 2   # object.cc objectGetRect
            top = sy + 8 + shift_y + y - h + 1
            placed.append((sprite, left, top))
        grid = defaultdict(list)
        for i, (sprite, left, top) in enumerate(placed):
            w, h = sprite[1], sprite[2]
            for gx in range((left - 1) // WALL_CELL, (left + w + 1) // WALL_CELL + 1):
                for gy in range((top - 1) // WALL_CELL, (top + h + 1) // WALL_CELL + 1):
                    grid[(gx, gy)].append(i)
        seen = set()
        for indexes in grid.values():
            for n, i in enumerate(indexes):
                for j in indexes[n + 1:]:
                    if (i, j) in seen:
                        continue
                    seen.add((i, j))
                    (sa, ax, ay), (sb, bx, by) = placed[i], placed[j]
                    ix0 = max(ax - WALL_TOUCH, bx - WALL_TOUCH)
                    iy0 = max(ay - WALL_TOUCH, by - WALL_TOUCH)
                    ix1 = min(ax + sa[1], bx + sb[1]) + WALL_TOUCH
                    iy1 = min(ay + sa[2], by + sb[2]) + WALL_TOUCH
                    if ix1 <= ix0 or iy1 <= iy0:
                        continue
                    ia = _place_mask((ax, ay), sa[6], ix0, iy0, ix1, iy1)
                    ib = _place_mask((bx, by), sb[6], ix0, iy0, ix1, iy1)
                    contact = int((ia & ib).sum())
                    if not contact:
                        continue
                    out.append({'map': label, 'elevation': elevation,
                                'a': {'rel': sa[0], 'x': ax, 'y': ay, 'w': sa[1], 'h': sa[2]},
                                'b': {'rel': sb[0], 'x': bx, 'y': by, 'w': sb[1], 'h': sb[2]},
                                'dx': bx - ax, 'dy': by - ay,
                                'same': sa[0] == sb[0], 'contact': contact})
    return out


def map_names(game):
    names = {name for dat in game.dats for name in dat.names('maps/') if name.lower().endswith('.map')}
    if game.data_dir:
        names.update('maps/' + p.name.lower() for p in (Path(game.data_dir) / 'maps').glob('*.map'))
    return sorted(names)


def wall_pairs(game):
    """Every pair of wall sprites whose opaque areas touch or overlap on screen.

    Wall objects are placed exactly as the engine places them: the hex's
    screen point plus the FRM's direction offsets, the sprite's bottom-centre
    on that point (object.cc objectGetRect). Each junction records both art
    frames and their relative offset, so the pair can be composited at 2x and
    compared with the original pair.
    """
    walls_lst = game.art_list(3)
    sprites = {}
    pairs = []
    for name in map_names(game):
        try:
            pairs.extend(_map_wall_junctions(game, name, sprites, walls_lst))
        except (OSError, ValueError, struct.error, IndexError, KeyError):
            continue
    return pairs


def cached_wall_pairs(game, out_root, *, write=True):
    path = Path(out_root) / 'wall-pairs.json'
    saved = read_json(path)
    if saved is not None:
        return saved['pairs']
    pairs = wall_pairs(game)
    if write:
        atomic_json(path, {'pairs': pairs})
    return pairs


def _junction_geometry(placement):
    """Canvas origin of a placement: how far b sits left/above a, in 2x px."""
    return min(0, int(placement['dx']) * 2), min(0, int(placement['dy']) * 2)


def _pair_2x(a, b, placement, *, nearest):
    """Both sprites as (rgb, mask) on one 2x canvas, plus their canvas origins."""
    if nearest:
        a = a.resize((a.width * 2, a.height * 2), Image.Resampling.NEAREST)
        b = b.resize((b.width * 2, b.height * 2), Image.Resampling.NEAREST)
    dx, dy = int(placement['dx']) * 2, int(placement['dy']) * 2
    ox, oy = min(0, dx), min(0, dy)
    width = max(a.width, dx + b.width) - ox
    height = max(a.height, dy + b.height) - oy
    canvases = []
    for image, (ix, iy) in ((a, (-ox, -oy)), (b, (dx - ox, dy - oy))):
        rgba = np.asarray(image.convert('RGBA'), np.float32)
        rgb = np.zeros((height, width, 3), np.float32)
        mask = np.zeros((height, width), bool)
        rgb[iy:iy + image.height, ix:ix + image.width] = rgba[..., :3]
        mask[iy:iy + image.height, ix:ix + image.width] = rgba[..., 3] > 0
        canvases.append((rgb, mask))
    return canvases, (-ox, -oy), (dx - ox, dy - oy)


def junction_band(ma, mb, band=WALL_SEAM_BAND):
    """Boolean masks of the pixels of a and b that form their junction.

    A junction pixel is on its own sprite's alpha boundary (within `band` 2x
    px of the outside) and within `band` px of the other sprite, so deeply
    overlapping interiors are excluded while both the touching edge and an
    alpha boundary lying over the neighbour are measured.
    """
    # edt(x) is the distance to the nearest 0 of x, so depth inside a is
    # edt(ma) while the distance from a pixel to b is edt(~mb).
    sel_a = ma & (distance_transform_edt(ma) <= band) & (distance_transform_edt(~mb) <= band)
    sel_b = mb & (distance_transform_edt(mb) <= band) & (distance_transform_edt(~ma) <= band)
    return sel_a, sel_b


def _band_gap(rgb_a, rgb_b, mask_a, mask_b, sel_a, sel_b):
    """Mean colour step across the junction: each band pixel vs the nearest pixel of the other sprite."""
    samples = []
    for sel, (src, dst_rgb, dst_mask) in ((sel_a, (rgb_a, rgb_b, mask_b)),
                                          (sel_b, (rgb_b, rgb_a, mask_a))):
        if not sel.any():
            continue
        _dist, index = distance_transform_edt(~dst_mask, return_indices=True)
        ys, xs = np.where(sel)
        ny, nx = index[0][ys, xs], index[1][ys, xs]
        samples.append(np.abs(src[ys, xs] - dst_rgb[ny, nx]).mean(axis=1))
    if not samples:
        return 0.0, 0
    values = np.concatenate(samples)
    if len(values) < WALL_SEAM_MIN_SAMPLES:
        return 0.0, len(values)
    return float(values.mean()), len(values)


def _line_step(rgb, valid, sel, rows):
    """Sharpest 1-2 px step in each junction row, at that row's own contact.

    Only columns within WALL_LINE_WINDOW of the row's own contact count, so a
    diagonal junction is measured where its two pieces actually meet instead
    of over the whole bounding box of the contact.  Each row contributes its
    largest step and the median over rows is returned: a line running the
    whole contact (what a reader sees as a light vertical line) lifts nearly
    every row at the same column, while texture that changes column from row
    to row cancels out.  `valid` keeps columns that do not carry the contact
    from stepping against transparent black.
    """
    if len(rows) < WALL_LINE_MIN_ROWS:
        return 0.0
    near = binary_dilation(sel, structure=np.ones((1, 3), bool),
                           iterations=WALL_LINE_WINDOW)
    step = np.abs(np.diff(rgb.mean(axis=2), axis=1))
    pair = valid[:, :-1] & valid[:, 1:] & near[:, :-1] & near[:, 1:]
    row_step = np.where(pair, step, -np.inf).max(axis=1)
    row_step = row_step[np.isfinite(row_step)]
    if len(row_step) < WALL_LINE_MIN_ROWS:
        return 0.0
    return float(np.median(row_step))


def wall_junction_terms(hd_a, hd_b, orig_a, orig_b, placement):
    """(colour excess, line excess) of one junction - what wall_seam_delta takes the worse of.

    Both HD pieces are composited at their real relative offset at 2x, the
    junction band is measured on each side against the nearest pixel of the
    other piece, and the same measurement on the original pair at 2x
    (nearest) is subtracted: intentional source joints (posts, door frames)
    are the baseline, not a failure.

    The second term is the sharpest 1-2 px column step along the same contact,
    likewise less the original pair's step, taken in both draw orders since
    which piece covers which is the engine's, not ours.  The colour band
    compares each side with the *other* sprite, so a bright rim a redraw
    leaves on a frame's own edge can agree across the contact and still read
    as a light vertical line where the source art has none.
    """
    try:
        canvases, _a0, _b0 = _pair_2x(hd_a, hd_b, placement, nearest=False)
        (ha, ma), (hb, mb) = canvases
        canvases, _a1, _b1 = _pair_2x(orig_a, orig_b, placement, nearest=True)
        (oa, oma), (ob, omb) = canvases
    except (ValueError, OSError):
        return 0.0, 0.0
    sel_a, sel_b = junction_band(ma, mb)
    if not (sel_a.any() or sel_b.any()):
        return 0.0, 0.0
    hd_gap, n = _band_gap(ha, hb, ma, mb, sel_a, sel_b)
    if n < WALL_SEAM_MIN_SAMPLES:
        return 0.0, 0.0
    src_sel_a, src_sel_b = junction_band(oma, omb)
    src_gap, _ = _band_gap(oa, ob, oma, omb, src_sel_a, src_sel_b)
    colour = max(0.0, hd_gap - src_gap)
    sel = sel_a | sel_b
    rows = np.flatnonzero(sel.any(axis=1))
    if len(rows) < WALL_LINE_MIN_ROWS:
        return colour, 0.0
    # Alpha is the source's own, so one validity mask fits both pairs; only the
    # pixels that carry the contact may set the profile.
    valid = ma | mb
    hd_line = max(_line_step(np.where(mb[..., None], hb, ha), valid, sel, rows),
                  _line_step(np.where(ma[..., None], ha, hb), valid, sel, rows))
    src_line = max(_line_step(np.where(omb[..., None], ob, oa), valid, sel, rows),
                   _line_step(np.where(oma[..., None], oa, ob), valid, sel, rows))
    return colour, max(0.0, hd_line - src_line)


def wall_seam_delta(hd_a, hd_b, orig_a, orig_b, placement):
    """Extra colour/pattern break an HD pair shows across a junction.

    The worse of the two measurements wall_junction_terms makes: the mean
    colour gap across the contact, and the sharpest column step along it.
    Both are brightness breaks in the same 0-255 units, so both are judged
    against WALL_SEAM_DELTA_LIMIT.
    """
    colour, line = wall_junction_terms(hd_a, hd_b, orig_a, orig_b, placement)
    return max(colour, line)


def wall_junction_mask(original, junctions):
    """2x mask of this frame's own junction bands: [(placement, other_original), ...]."""
    if not junctions:
        return None
    width, height = original.width * 2, original.height * 2
    canvas = np.zeros((height, width), bool)
    for placement, other in junctions:
        canvases, (ax, ay), _ = _pair_2x(original, other, placement, nearest=True)
        (_rgb_a, mask_a), (_rgb_b, mask_b) = canvases
        sel_a, _sel_b = junction_band(mask_a, mask_b)
        # sel_a lies inside this frame's own footprint, so it crops to 2x size.
        canvas |= sel_a[ay:ay + height, ax:ax + width]
    return canvas


def seam_delta(a_hd, b_hd, a_original, b_original, side):
    """Increase in colour mismatch along adjacent isometric diamond edges.

    Each row (right adjacency) or column (bottom adjacency) contributes its
    outermost opaque sample plus two inward pixels. The original pair provides
    the baseline, so intentional source seams do not become HD failures.
    """
    def edge(image, side, first):
        rgba = np.asarray(image.convert('RGBA'), np.float32)
        rgb, mask = rgba[..., :3], rgba[..., 3] > 0
        if side == 'bottom':
            rgb, mask = rgb.transpose(1,0,2), mask.T
        # Reverse the first image to measure its right/bottom boundary; the
        # second image uses its left/top boundary.
        if first:
            rgb, mask = rgb[:, ::-1], mask[:, ::-1]
        valid = mask.any(axis=1)
        boundary = np.argmax(mask, axis=1)
        samples = []
        for depth in range(3):
            idx = np.minimum(boundary+depth, mask.shape[1]-1)
            samples.append(rgb[np.arange(len(idx)),idx])
        return np.stack(samples,axis=1), valid
    ao = a_original.resize(a_hd.size, Image.Resampling.NEAREST)
    bo = b_original.resize(b_hd.size, Image.Resampling.NEAREST)
    ah, am = edge(a_hd,side,True); bh,bm = edge(b_hd,side,False)
    ax, axm = edge(ao,side,True); bx,bxm = edge(bo,side,False)
    if ah.shape != bh.shape or ax.shape != bx.shape:
        return 0.0
    valid = am & bm & axm & bxm
    if valid.sum() < 4:
        return 0.0
    hd_gap = np.abs(ah[valid]-bh[valid]).mean()
    src_gap = np.abs(ax[valid]-bx[valid]).mean()
    return max(0.0,float(hd_gap-src_gap))
