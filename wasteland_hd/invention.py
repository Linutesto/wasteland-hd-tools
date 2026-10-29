"""CPU-only source-ground texture and border measurements for HD frames."""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import (binary_dilation, distance_transform_edt, gaussian_filter,
                           label, maximum_filter, sobel, uniform_filter)

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


def score(hd: Image.Image, original: Image.Image, *, seam_delta=0.0, floor=True, kind='tiles'):
    """Return measurements at native and 2x sizes and a four-way verdict."""
    if kind not in KINDS:
        raise ValueError(f'unknown art kind: {kind!r}')
    if hd.size != (original.width*2, original.height*2):
        raise ValueError('HD frame must be exact 2x')
    sprite = kind != 'tiles'
    # Sprite border error is measured at the alpha silhouette, where AA and
    # erosion are normal; only floor tiles can be unmatched against neighbours.
    floor = floor and not sprite
    native = _single_scale(hd.resize(original.size, Image.Resampling.BOX), original, sprite=sprite)
    high = _single_scale(hd, original, sprite=sprite)
    # At native scale, source pixels are themselves strongly stair-stepped;
    # use the 2x evidence for inventions and retain native evidence for review.
    area = high['novel_fraction']; cluster = high['largest_novel_region']
    flat = high['flat_damage']
    border = high['border_error']
    if floor and (border > BORDER_ERROR_LIMIT or seam_delta > SEAM_DELTA_LIMIT):
        verdict = 'unmatched_floor'
    elif sprite:
        verdict = _sprite_verdict(high)
    elif area > TEXTURE_AREA_LIMIT or cluster > TEXTURE_CLUSTER_LIMIT or (flat > FLAT_DAMAGE_LIMIT and cluster > 0.025) or high['dark_novel'] > 0.025:
        verdict = 'invented_texture'
    elif area > MINOR_AREA_LIMIT or cluster > 0.015:
        verdict = 'minor_invention'
    else:
        verdict = 'clean'
    return {'verdict': verdict, 'native': native, 'scale2': high,
            'seam_delta': round(float(seam_delta), 3), 'kind': kind}


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
