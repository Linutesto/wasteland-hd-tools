"""Keyframe-anchored temporal stage: identity converges, geometry and alpha survive."""
import json
import types
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from wasteland_hd import flicker, temporal_stage
from wasteland_hd.__main__ import cmd_temporal
from wasteland_hd.gamefiles import Frame

W, H = 18, 20
PALETTE = [(0, 0, 0), (120, 80, 50), (60, 45, 35), (30, 25, 22)]


def _frame(pixels):
    return Frame(W, H, 0, 0, bytes(np.asarray(pixels, dtype=np.uint8).ravel()))


def _poses(n=4, move=0):
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
    def palette(self):
        return PALETTE


def _nearest_rgba(pose):
    native = np.frombuffer(flicker.frame_rgba(pose, PALETTE), np.uint8).reshape(H, W, 4)
    return flicker._nearest2(native)


def _drift(hd, i):
    """A different coherent colour cast per frame: costume identity boiling."""
    out = hd.astype(np.float32)
    out[..., :3] = np.clip(out[..., :3] + 9 * i, 0, 255)
    return out


def _noise(hd, i, sigma=12):
    rng = np.random.default_rng(17 + i)
    out = hd.astype(np.float32)
    out[..., :3] = np.clip(out[..., :3] + rng.normal(0, sigma, out[..., :3].shape), 0, 255)
    return out


def _tree(root, poses, perturb):
    folder = Path(root) / "art" / "critters" / "dummy"
    folder.mkdir(parents=True, exist_ok=True)
    for i, pose in enumerate(poses):
        hd = np.rint(np.clip(perturb(_nearest_rgba(pose), i), 0, 255)).astype(np.uint8)
        Image.fromarray(hd).save(folder / f"2_{i}.png")
    return Path(root)


def _run(tmp_path, monkeypatch, poses=None, perturb=_noise, components=temporal_stage.COMPONENTS,
         select=False, fallback=False, out="staged"):
    poses = poses if poses is not None else _poses()
    monkeypatch.setattr(flicker, "_frames", lambda _game, _name, _direction: poses)
    source = _tree(tmp_path / "hd", poses, perturb)
    result = temporal_stage.stage_directory(
        FakeGame(), source, tmp_path / out, components=components, select=select, fallback=fallback)
    return result, source, tmp_path / out


def test_static_noise_converges_and_stays_deterministic(tmp_path, monkeypatch):
    poses = _poses()

    def perturb(hd, i):
        return _noise(_drift(hd, i), i)

    result, source, out = _run(tmp_path, monkeypatch, poses, perturb)
    before = result["directions"]["dummy/2"]["before"]
    after = result["directions"]["dummy/2"]["after"]
    assert before["flicker"] > 4 and before["material"] > 2
    assert after["flicker"] < 1.0, after
    assert after["material"] < 0.5, after
    assert temporal_stage.passes(after)
    assert result["animations"]["dummy"]["flicker"] < 1.0

    first = [Image.open(out / "art" / "critters" / "dummy" / f"2_{i}.png").tobytes() for i in range(4)]
    again, _source, out2 = _run(tmp_path, monkeypatch, poses, perturb, out="staged2")
    second = [Image.open(out2 / "art" / "critters" / "dummy" / f"2_{i}.png").tobytes() for i in range(4)]
    assert first == second
    assert again["directions"]["dummy/2"]["after"] == after
    # Geometry and the exact source alpha are untouched.
    for i, pose in enumerate(poses):
        got = np.asarray(Image.open(out / "art" / "critters" / "dummy" / f"2_{i}.png"))
        assert got.shape == (H * 2, W * 2, 4)
        assert np.array_equal(got[..., 3], _nearest_rgba(pose)[..., 3])


def test_colour_transfer_equalises_material_without_blurring_texture(tmp_path, monkeypatch):
    poses = _poses()
    result, _source, out = _run(tmp_path, monkeypatch, poses, _drift, components=("colour",))
    before = result["directions"]["dummy/2"]["before"]
    after = result["directions"]["dummy/2"]["after"]
    assert before["material"] > 3
    assert after["material"] < 0.6, after

    # The correction is one constant per original palette class: it moves the
    # class mean and resamples nothing, so every per-pixel texture residual
    # (pixel minus its class mean) is exactly the input's.
    for i, pose in enumerate(poses):
        native = np.frombuffer(flicker.frame_rgba(pose, PALETTE), np.uint8).reshape(H, W, 4)
        original = np.rint(_nearest_rgba(pose)[..., :3]).astype(np.int16)
        staged = np.asarray(Image.open(out / "art" / "critters" / "dummy" / f"2_{i}.png"), np.int16)[..., :3]
        for value in np.unique(native[..., 0])[1:]:
            mask = np.repeat(np.repeat(native[..., 0] == value, 2, 0), 2, 1)
            delta = (staged - original)[mask]
            assert delta.std(axis=0).max() <= 1, (i, value, delta.std(axis=0))


def test_static_copy_takes_the_keyframe_where_the_original_agrees(tmp_path, monkeypatch):
    poses = _poses()
    result, _source, out = _run(tmp_path, monkeypatch, poses, _noise, components=("static",))
    key = result["directions"]["dummy/2"]["keyframe"]
    anchor = np.asarray(Image.open(out / "art" / "critters" / "dummy" / f"2_{key}.png"), np.float32)[..., :3]
    # The interior of the identical torso must be drawn exactly as the keyframe.
    for i in range(4):
        got = np.asarray(Image.open(out / "art" / "critters" / "dummy" / f"2_{i}.png"), np.float32)[..., :3]
        interior = np.zeros((H * 2, W * 2), dtype=bool)
        interior[8:32, 10:24] = True
        assert np.max(np.abs(got[interior] - anchor[interior])) <= 1.0


def test_moving_poses_keep_their_own_geometry(tmp_path, monkeypatch):
    poses = _poses(move=1)
    result, _source, out = _run(tmp_path, monkeypatch, poses,
                                lambda hd, i: _noise(_drift(hd, i), i))
    for i, pose in enumerate(poses):
        got = np.asarray(Image.open(out / "art" / "critters" / "dummy" / f"2_{i}.png"))
        assert np.array_equal(got[..., 3] > 0, _nearest_rgba(pose)[..., 3] > 0)
    assert temporal_stage.passes(result["animations"]["dummy"])


def test_keyframe_is_the_pose_closest_to_all_the_others():
    poses = _poses(n=3)
    native = np.stack([np.frombuffer(flicker.frame_rgba(p, PALETTE), np.uint8).reshape(H, W, 4)
                       for p in poses])
    base = flicker._nearest2(native)[..., :3].astype(np.float32)
    # Drift 5 between neighbours and 10 across: the middle pose is the median.
    delta = np.array([0.0, 5.0, 10.0], dtype=np.float32)
    rgb = np.stack([base[i] + delta[i] for i in range(3)])
    assert temporal_stage.choose_keyframe(native, rgb) == 1
    # Identical frames tie: the lowest index wins, every time.
    assert np.array_equal(native[0], native[2])
    assert temporal_stage.choose_keyframe(native, np.stack([base[0]] * 3)) == 0


def test_direction_over_the_cap_falls_back_to_original_art(tmp_path, monkeypatch):
    poses = _poses()
    monkeypatch.setattr(flicker, "_frames", lambda _game, _name, _direction: poses)
    source = _tree(tmp_path / "hd", poses, lambda hd, i: _noise(hd, i, sigma=2))
    # One animation, four directions: three calm ones and one that cannot hold
    # its appearance.  The cap is relative to its own animation's mean.
    for direction in (0, 1, 3):
        for png in sorted((source / "art" / "critters" / "dummy").glob("*.png")):
            target = source / "art" / "critters" / "jumpy" / png.name.replace("2_", f"{direction}_")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(png.read_bytes())
    outlier = _tree(tmp_path / "hd-outlier", poses, lambda hd, i: _noise(hd, i, sigma=90))
    for png in (outlier / "art" / "critters" / "dummy").glob("*.png"):
        target = source / "art" / "critters" / "jumpy" / png.name.replace("2_", "4_")
        target.parent.mkdir(parents=True, exist_ok=True)
        png.replace(target)
    # The stage does nothing (empty component list) so the outlier keeps its noise.
    result = temporal_stage.stage_directory(FakeGame(), source, tmp_path / "out",
                                            components=(), fallback=True)
    assert result["directions"]["jumpy/4"]["fallback"] is True
    assert all(not value["fallback"] for key, value in result["directions"].items()
               if key != "jumpy/4")
    got = np.asarray(Image.open(tmp_path / "out" / "art" / "critters" / "jumpy" / "4_0.png"))
    assert np.array_equal(got[..., :3], _nearest_rgba(poses[0])[..., :3])
    assert result["directions"]["jumpy/4"]["after"]["flicker"] == 0


def test_rejects_bad_alpha_and_dangerous_destinations(tmp_path, monkeypatch):
    poses = _poses()
    monkeypatch.setattr(flicker, "_frames", lambda _game, _name, _direction: poses)
    source = _tree(tmp_path / "hd", poses, lambda hd, i: hd)
    with pytest.raises(ValueError, match="separate"):
        temporal_stage.stage_directory(FakeGame(), source, source / "nested")
    good = (source / "art" / "critters" / "dummy" / "2_1.png").read_bytes()
    Image.new("RGBA", (W * 2, H * 2), (90, 50, 30, 0)).save(
        source / "art" / "critters" / "dummy" / "2_1.png")
    with pytest.raises(ValueError, match="alpha"):
        temporal_stage.stage_directory(FakeGame(), source, tmp_path / "out")
    Image.fromarray(_nearest_rgba(poses[1])).save(source / "art" / "critters" / "dummy" / "2_1.png")
    temporal_stage.stage_directory(FakeGame(), source, tmp_path / "empty")


def test_non_critter_files_are_copied_verbatim_and_destination_must_be_empty(tmp_path, monkeypatch):
    poses = _poses()
    monkeypatch.setattr(flicker, "_frames", lambda _game, _name, _direction: poses)
    source = _tree(tmp_path / "hd", poses, lambda hd, i: hd)
    other = source / "art" / "tiles" / "keep" / "0_0.png"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"not decoded, copied through")
    temporal_stage.stage_directory(FakeGame(), source, tmp_path / "out")
    assert (tmp_path / "out" / other.relative_to(source)).read_bytes() == other.read_bytes()
    with pytest.raises(FileExistsError):
        temporal_stage.stage_directory(FakeGame(), source, tmp_path / "out")


def test_cli_writes_the_before_after_report(tmp_path, monkeypatch):
    poses = _poses()
    monkeypatch.setattr(flicker, "_frames", lambda _game, _name, _direction: poses)
    source = _tree(tmp_path / "hd", poses, _noise)
    monkeypatch.setattr("wasteland_hd.__main__.Game", lambda _path: FakeGame())
    report = tmp_path / "stage.json"
    cmd_temporal(types.SimpleNamespace(
        game=str(tmp_path), source=str(source), out=str(tmp_path / "out"), anim=None,
        components="colour,residual,texture,static", strength=1.0, radius=9,
        keep_all=False, no_fallback=False, fallback_cap=2.0, json=str(report)))
    saved = json.loads(report.read_text())
    assert saved["gate"] == {"broad": 2.5, "material": 3.0, "flicker": 6.0}
    assert saved["animations"]["dummy"]["flicker"] < 1.0
    assert 0 <= saved["directions"]["dummy/2"]["keyframe"] < 4
