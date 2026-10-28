"""Whole-region source fallback invariants that seam metrics cannot prove."""

import numpy as np
import pytest
from PIL import Image

from wasteland_hd.floor_fidelity import source_floor_2x, scene_ai_policy
from wasteland_hd import qa


def source_image():
    yy, xx = np.indices((36, 80))
    a = np.where((xx > 3) & (xx < 76) & (yy > 2) & (yy < 33), 255, 0)
    r = np.clip(100 + 13 * np.sin(xx / 6) + xx // 4, 0, 255)
    g = np.clip(90 + 12 * np.cos(yy / 4) + yy // 3, 0, 255)
    b = 75 + (xx // 5 + yy // 4) % 20
    out = np.stack((r, g, b, a), axis=-1).astype('uint8')
    out[a == 0] = (255, 0, 255, 0)  # hostile transparent gutters
    return Image.fromarray(out)


def test_source_floor_preserves_alpha_dimensions_and_boundary_colours():
    original = source_image()
    result = source_floor_2x(original)
    nearest = source_floor_2x(original, softness=0)
    assert result.size == (160, 72)
    assert result.getchannel('A').tobytes() == nearest.getchannel('A').tobytes()
    mask = np.asarray(nearest.getchannel('A')) > 0
    from scipy.ndimage import distance_transform_edt
    boundary = (distance_transform_edt(np.pad(mask, 1))[1:-1, 1:-1] - 1) <= 2
    assert np.array_equal(np.asarray(result)[boundary], np.asarray(nearest)[boundary])
    assert np.any(np.asarray(result)[~boundary & mask, :3] != np.asarray(nearest)[~boundary & mask, :3])
    assert result.tobytes() == source_floor_2x(original).tobytes()
    assert nearest.tobytes() == original.resize(nearest.size, Image.Resampling.NEAREST).tobytes()
    assert qa.badness(result, original, 'art/tiles/example/0_0.png', 12) <= 1


def test_source_floor_no_transparent_magenta_bleed_and_edge_invariants():
    original = Image.new('RGBA', (16, 16), (255, 0, 255, 0))
    original.paste((80, 75, 70, 255), (3, 3, 13, 13))
    result = np.asarray(source_floor_2x(original, edge_lock=0))
    mask = result[..., 3] > 0
    assert np.array_equal(result[mask, :3], np.broadcast_to([80, 75, 70], result[mask, :3].shape))


@pytest.mark.parametrize('kwargs', [{'softness': -1}, {'softness': 1.1}, {'edge_lock': -1}, {'edge_lock': 0.5}])
def test_source_floor_rejects_invalid_controls(kwargs):
    with pytest.raises(ValueError):
        source_floor_2x(source_image(), **kwargs)


def test_scene_policy_prevents_unreviewed_and_mixed_tiles():
    tiles = ['a', 'b', 'a']
    unreviewed = scene_ai_policy({'a': True, 'b': True}, tiles)
    assert unreviewed['ai_promoted_unique'] == 0
    assert unreviewed['deterministic_fallback_placements'] == 3
    mixed = scene_ai_policy({'a': True, 'b': False}, tiles, reviewed_and_approved=True)
    assert mixed['raw_rejected_unique'] == 1
    assert mixed['deterministic_fallback_placements'] == 3
    assert not mixed['release_approved']
    approved = scene_ai_policy({'a': True, 'b': True}, tiles, reviewed_and_approved=True)
    assert approved['ai_promoted_unique'] == 2 and approved['release_approved']
    with pytest.raises(ValueError):
        scene_ai_policy({'a': True}, tiles)
