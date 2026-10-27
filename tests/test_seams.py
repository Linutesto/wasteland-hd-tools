"""Regression tests for deterministic floor-art edge reconstruction."""
import numpy as np
import pytest
from PIL import Image, ImageDraw
from scipy.ndimage import distance_transform_edt

from wasteland_hd.seams import reconcile_tile


def test_seams_lock_alpha_edges_and_preserve_interior_detail():
    source = Image.new("RGBA", (54, 42), (110, 91, 68, 0))
    ImageDraw.Draw(source).ellipse((3, 3, 50, 38), fill=(110, 91, 68, 255))
    arr = np.asarray(source).copy()
    hd_arr = arr.copy()
    yy, xx = np.indices(arr.shape[:2])
    # Wrong global tint + independent checker texture in the AI upscale.
    hd_arr[..., :3] = np.clip(arr[..., :3].astype(int) + 65 + (((xx + yy) % 2) * 30)[..., None], 0, 255)
    hd = Image.fromarray(hd_arr)
    fixed = reconcile_tile(hd, source, feather=9, sigma=4)
    out = np.asarray(fixed)
    alpha = arr[..., 3] > 0
    edge = (distance_transform_edt(np.pad(alpha, 1))[1:-1, 1:-1] - 1) == 0
    assert np.array_equal(out[..., 3], arr[..., 3])
    assert np.array_equal(out[edge, :3], arr[edge, :3])
    assert np.any(out[19:23, 25:29, :3] != arr[19:23, 25:29, :3])
    assert fixed.size == hd.size


def test_seams_reject_mismatched_dimensions_and_masks():
    source = Image.new("RGBA", (32, 32), (20, 30, 40, 255))
    with pytest.raises(ValueError, match="tile size"):
        reconcile_tile(Image.new("RGBA", (64, 64)), source)
    with pytest.raises(ValueError, match="alpha"):
        reconcile_tile(Image.new("RGBA", (32, 32)), source)
    with pytest.raises(ValueError, match="positive"):
        reconcile_tile(source, source, feather=0)
