"""Synthetic animation tests: temporal detail, exact alpha and safe file copy."""
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from wasteland_hd import temporal
from wasteland_hd.gamefiles import Frame


def _static_animation(n=8, w=24, h=28):
    native = np.zeros((n, h, w, 4), dtype=np.uint8)
    native[:, 3:-3, 3:-3, :3] = [114, 71, 45]
    native[:, 3:-3, 3:-3, 3] = 255
    base = temporal._nearest2(native)[..., :3].astype(np.float32)
    rng = np.random.default_rng(11)
    checker = (np.indices((h * 2, w * 2)).sum(axis=0) % 2).astype(np.float32) * 14
    details = base + checker[None, ..., None]
    noisy = np.clip(details + rng.normal(0, 16, (n, h * 2, w * 2, 3)), 0, 255)
    return native, noisy.astype(np.float32), temporal._nearest2(native[..., 3])


def test_nearest_original_has_zero_flicker():
    native, _hd, _alpha = _static_animation()
    original_nearest = temporal._nearest2(native)[..., :3].astype(np.float32)
    assert temporal.score_group(native, original_nearest) == 0


def test_static_noisy_sprite_converges_and_is_deterministic():
    native, noisy, alpha = _static_animation()
    before = temporal.score_group(native, noisy)
    after = temporal.stabilise_group(native, noisy, alpha)
    after_score = temporal.score_group(native, after)
    assert before > 5
    assert after_score < before * 0.1, (before, after_score)
    assert np.array_equal(after, temporal.stabilise_group(native, noisy, alpha))
    assert after.shape == noisy.shape


def test_separate_tree_preserves_noncritters_and_exact_original_alpha(tmp_path, monkeypatch):
    source = tmp_path / "hd"
    destination = tmp_path / "stable" / "hd"
    sprite_dir = source / "art" / "critters" / "dummy"
    sprite_dir.mkdir(parents=True)
    noncritter = source / "art" / "tiles" / "keep" / "0_0.png"
    noncritter.parent.mkdir(parents=True)
    noncritter.write_bytes(b"original bytes, preserved without image decoding")
    native, hd, alpha = _static_animation(n=4, w=24, h=28)
    mask = alpha[0]
    frames = [Frame(24, 28, 0, 0, bytes([1 if v else 0 for v in native[i, ..., 3].ravel()]))
              for i in range(4)]

    class FakeGame:
        def palette(self):
            return [(0, 0, 0), (114, 71, 45)]

    monkeypatch.setattr(temporal, "_group_frames", lambda _game, _name, _direction: frames)
    for i in range(4):
        rgb = np.clip(hd[i], 0, 255).astype(np.uint8)
        Image.fromarray(np.dstack((rgb, mask))).save(sprite_dir / f"2_{i}.png")
    results = temporal.stabilise_directory(FakeGame(), source, destination)
    assert results["dummy/2"]["after"] < results["dummy/2"]["before"]
    assert noncritter.read_bytes() == (destination / noncritter.relative_to(source)).read_bytes()
    for i in range(4):
        restored = Image.open(destination / "art" / "critters" / "dummy" / f"2_{i}.png")
        assert restored.size == (48, 56)
        assert np.array_equal(np.asarray(restored.getchannel("A")), mask)
    with pytest.raises(ValueError, match="separate"):
        temporal.stabilise_directory(FakeGame(), source, source)
    with pytest.raises(FileExistsError):
        temporal.stabilise_directory(FakeGame(), source, destination)


def test_rejects_changed_alpha_and_dimensions(tmp_path, monkeypatch):
    source = tmp_path / "src"
    d = source / "art" / "critters" / "dummy"
    d.mkdir(parents=True)
    frame = Frame(3, 2, 0, 0, bytes((1,) * 6))
    monkeypatch.setattr(temporal, "_group_frames", lambda *_args: [frame])

    class FakeGame:
        def palette(self):
            return [(0, 0, 0), (120, 80, 40)]

    Image.new("RGBA", (5, 4), (90, 50, 30, 255)).save(d / "0_0.png")
    with pytest.raises(ValueError, match="expected"):
        temporal.analyse_directory(FakeGame(), source)
    Image.new("RGBA", (6, 4), (90, 50, 30, 0)).save(d / "0_0.png")
    with pytest.raises(ValueError, match="alpha"):
        temporal.analyse_directory(FakeGame(), source)


def test_moving_original_nearest_has_no_detail_flicker():
    # Different poses are inherent game motion and must not count as AI boiling.
    native = np.zeros((4, 18, 20, 4), dtype=np.uint8)
    for i in range(4):
        native[i, 2:15, 4+i:13+i, :3] = [110, 75, 48]
        native[i, 2:15, 4+i:13+i, 3] = 255
    original = temporal._nearest2(native)[..., :3].astype(np.float32)
    alpha = temporal._nearest2(native[..., 3])
    assert temporal.score_group(native, original, radius=3) == 0
    fixed = temporal.stabilise_group(native, original, alpha, radius=3)
    assert np.array_equal(fixed, original.astype(np.uint8))
