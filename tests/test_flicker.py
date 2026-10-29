"""Flicker metric tests: motion is free, generation noise is not, safe writes."""
import numpy as np
from PIL import Image
import pytest

from wasteland_hd import flicker
from wasteland_hd.gamefiles import Frame

W, H = 18, 20
PALETTE = [(0, 0, 0), (120, 80, 50), (60, 45, 35), (30, 25, 22)]


def _frame(pixels):
    return Frame(W, H, 0, 0, bytes(pixels.ravel()))


def _static_frames(n=4, move=0):
    """n poses of one body; ``move`` slides the torso sideways each frame."""
    out = []
    for i in range(n):
        pixels = np.zeros((H, W), dtype=np.uint8)
        pixels[3:17, 4 + i * move:13 + i * move] = 1   # skin
        pixels[13:17, 4 + i * move:13 + i * move] = 2  # trousers
        pixels[3:6, 6 + i * move:8 + i * move] = 3     # hair
        out.append(_frame(pixels))
    return out


class FakeGame:
    def __init__(self, frames):
        self.frames = frames

    def palette(self):
        return PALETTE

    def read(self, name):  # pragma: no cover - monkeypatched _frames never calls it
        raise FileNotFoundError(name)


def _hd(native, frame_index, perturbation=None):
    """HD bytes of one frame: exact 2x nearest, optionally perturbed."""
    rgba = native.copy()
    if perturbation is not None:
        rgba[..., :3] = perturbation(rgba[..., :3], frame_index)
    return rgba


def _run(tmp_path, monkeypatch, poses, perturb=None, control=None):
    monkeypatch.setattr(flicker, "_frames", lambda _game, _name, _direction: poses)
    root = tmp_path / "hd"
    folder = root / "art" / "critters" / "dummy"
    folder.mkdir(parents=True, exist_ok=True)
    details = []
    for i, pose in enumerate(poses):
        native = np.frombuffer(flicker.frame_rgba(pose, PALETTE), np.uint8).reshape(H, W, 4)
        hd = _nearest_or_control(native, perturb, i)
        details.append(hd)
        Image.fromarray(hd).save(folder / f"2_{i}.png")
    paths = flicker._paths(root, "dummy", 2)
    return flicker.direction_score(FakeGame(poses), "dummy", 2, paths, control=control), details


def _nearest_or_control(native, perturb, i):
    hd = flicker._nearest2(native)
    if perturb is not None:
        hd = perturb(hd, i)
    return hd.astype(np.uint8)


def test_original_nearest_has_zero_flicker(tmp_path, monkeypatch):
    record, _hd = _run(tmp_path, monkeypatch, _static_frames(), control="nearest")
    assert record["flicker"] == 0
    assert record["broad"] == 0 and record["fine"] == 0 and record["material"] == 0
    assert record["pixels"] > 0


def test_real_motion_is_not_flicker(tmp_path, monkeypatch):
    # A sliding body still leaves the uniform middle of the torso identical
    # from pose to pose (same palette index); the metric samples it and finds
    # the HD copy unchanged, so genuine motion costs nothing.
    record, _hd = _run(tmp_path, monkeypatch, _static_frames(move=1))
    assert record["pixels"] > 0
    assert record["flicker"] == 0


def test_injected_noise_scores_higher_than_untouched(tmp_path, monkeypatch):
    clean, _ = _run(tmp_path / "clean", monkeypatch, _static_frames())
    assert clean["flicker"] == 0

    def noise(hd, i):
        rng = np.random.default_rng(7 + i)
        out = hd.astype(np.float32)
        out[..., :3] = np.clip(out[..., :3] + rng.normal(0, 12, out[..., :3].shape), 0, 255)
        return out

    noisy, _ = _run(tmp_path / "noisy", monkeypatch, _static_frames(), perturb=noise)
    assert noisy["flicker"] > 4
    assert noisy["pixels"] == clean["pixels"]


def test_stronger_perturbation_scores_higher(tmp_path, monkeypatch):
    scores = []
    for sigma in (4, 10, 20):
        def noise(hd, i, sigma=sigma):
            rng = np.random.default_rng(3 + i)  # independent per frame
            out = hd.astype(np.float32)
            out[..., :3] = np.clip(out[..., :3] + rng.normal(0, sigma, out[..., :3].shape), 0, 255)
            return out

        _record, _ = _run(tmp_path / f"s{sigma}", monkeypatch, _static_frames(move=0), perturb=noise)
        scores.append(_record["flicker"])
        assert _record["flicker"] > 0
    assert scores[0] < scores[1] < scores[2], scores


def test_broad_shift_is_colour_drift_and_noise_is_detail_boil(tmp_path, monkeypatch):
    # One frame recoloured as a whole: the change is coherent, so it is broad.
    def recolour(hd, i):
        out = hd.astype(np.float32)
        if i == 1:
            out[..., :3] = np.clip(out[..., :3] + 24, 0, 255)
        return out

    broad, _ = _run(tmp_path / "broad", monkeypatch, _static_frames(), perturb=recolour)
    assert broad["broad"] > 4 * broad["fine"], broad

    def speckle(hd, i):
        # White noise at HD-pixel scale: zero local mean, so no coherent drift.
        rng = np.random.default_rng(11)
        out = hd.astype(np.float32)
        if i == 1:
            out[..., :3] = np.clip(out[..., :3] + rng.normal(0, 24, out[..., :3].shape), 0, 255)
        return out

    fine, _ = _run(tmp_path / "fine", monkeypatch, _static_frames(), perturb=speckle)
    assert fine["flicker"] > 4
    assert fine["fine"] > 4 * fine["broad"], fine


def test_rejects_wrong_alpha_and_dimensions(tmp_path, monkeypatch):
    monkeypatch.setattr(flicker, "_frames", lambda _g, _n, _d: _static_frames())
    root = tmp_path / "hd"
    folder = root / "art" / "critters" / "dummy"
    folder.mkdir(parents=True)
    native = np.frombuffer(flicker.frame_rgba(_static_frames()[0], PALETTE), np.uint8).reshape(H, W, 4)
    hd = flicker._nearest2(native)
    for i in range(4):
        Image.fromarray(hd).save(folder / f"2_{i}.png")
    damaged = hd.copy()
    damaged[12, 8, 3] = 0  # inside the body: alpha no longer nearest-neighbour
    Image.fromarray(damaged).save(folder / "2_2.png")
    with pytest.raises(ValueError, match="alpha"):
        flicker.direction_score(FakeGame(None), "dummy", 2, flicker._paths(root, "dummy", 2))
    Image.fromarray(hd[:20]).save(folder / "2_2.png")  # half height
    with pytest.raises(ValueError, match="expected"):
        flicker.direction_score(FakeGame(None), "dummy", 2, flicker._paths(root, "dummy", 2))


def test_refuses_to_write_inside_the_measured_tree(tmp_path):
    source = tmp_path / "hd"
    (source / "art" / "critters").mkdir(parents=True)
    with pytest.raises(ValueError, match="refusing"):
        flicker.safe_output(source, source / "report.json")
    with pytest.raises(ValueError, match="refusing"):
        flicker.safe_output(source, source / "sub" / "report.json")
    assert flicker.safe_output(source, tmp_path / "flicker.json") == (tmp_path / "flicker.json").resolve()


def test_unknown_animation_is_an_error(tmp_path):
    source = tmp_path / "hd"
    (source / "art" / "critters" / "other").mkdir(parents=True)
    with pytest.raises(ValueError, match="no frames for dummy"):
        flicker.analyse(FakeGame(None), source, names=["dummy"])
