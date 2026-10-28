"""CPU-only conservative source-ground fallback and whole-scene AI gate.

Tiles are reused in multiple map positions; this deliberately treats each
source FRM identically. Do not use its output as visual release approval.
"""

import numpy as np
from PIL import Image
from scipy.ndimage import distance_transform_edt


def source_floor_2x(original, *, softness=0.35, edge_lock=2):
    """Softly interpolate source RGB inside each tile, locking all boundaries.

    Keep nearest-neighbour alpha and boundary colours exactly aligned to the
    engine's original tiles. Transparent source RGB is filled using the nearest
    visible source colour before bicubic interpolation, preventing halo bleed.
    softness=0 is a byte-for-byte nearest-neighbour source fallback.
    """
    if not 0 <= softness <= 1:
        raise ValueError('softness must be in [0, 1]')
    if not isinstance(edge_lock, int) or edge_lock < 0:
        raise ValueError('edge_lock must be a non-negative integer')
    orig = original.convert('RGBA')
    nearest = orig.resize((orig.width * 2, orig.height * 2), Image.Resampling.NEAREST)
    if softness == 0:
        return nearest
    native = np.asarray(orig)
    native_mask = native[..., 3] > 0
    if not native_mask.any():
        return nearest
    src_rgb = native[..., :3].copy()
    if not native_mask.all():
        # Indices of nearest opaque pixel for each transparent source pixel.
        idx = distance_transform_edt(~native_mask, return_distances=False, return_indices=True)
        src_rgb[~native_mask] = src_rgb[tuple(i[~native_mask] for i in idx)]
    smooth = np.asarray(Image.fromarray(src_rgb).resize(nearest.size, Image.Resampling.BICUBIC), dtype=np.float32)
    base = np.asarray(nearest, dtype=np.float32)
    opaque = base[..., 3] > 0
    dist = distance_transform_edt(np.pad(opaque, 1))[1:-1, 1:-1] - 1
    # Every boundary pixel and edge_lock interior layers remain untouched.
    position = np.clip((dist - edge_lock) / 3.0, 0, 1)
    weight = softness * (position * position * (3 - 2 * position))
    rgb = np.clip(np.rint(base[..., :3] + weight[..., None] * (smooth - base[..., :3])), 0, 255).astype(np.uint8)
    return Image.fromarray(np.dstack((rgb, np.asarray(nearest.getchannel('A')))))


def scene_ai_policy(raw_passes, placements, *, reviewed_and_approved=False):
    """Keep mixed AI floors out of release even when some tiles pass raw QA.

    `raw_passes` maps unique FRM IDs to boolean raw QA results; `placements`
    are the possibly repeated IDs of the region's map cells.
    """
    if not placements or set(raw_passes) != set(placements):
        raise ValueError('scene must have placements and complete raw QA results')
    passing = sum(bool(v) for v in raw_passes.values())
    whole_scene_approved = bool(reviewed_and_approved and passing == len(raw_passes))
    return {
        'unique_tiles': len(raw_passes),
        'raw_accepted_unique': passing,
        'raw_rejected_unique': len(raw_passes) - passing,
        'placements': len(placements),
        'ai_promoted_unique': len(raw_passes) if whole_scene_approved else 0,
        'deterministic_fallback_placements': 0 if whole_scene_approved else len(placements),
        'source': 'ai_whole_region' if whole_scene_approved else 'deterministic_source_whole_region',
        'visual_review_approved': bool(reviewed_and_approved),
        'release_approved': whole_scene_approved,
    }
