"""production.py ``--temporal``: stage wiring, flicker gate, fallback, resume, plan compatibility.

No GPU and no service: the Comfy call is the same nearest-neighbour fake the
production tests use, the temporal stage runs as the real in-process stage, and
frames are synthetic critters.  ``plan.json`` is only ever read here; the live
root is never written.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image, PngImagePlugin
import pytest

from wasteland_hd import flicker, production as p, temporal_stage
from wasteland_hd.gamefiles import Frame, Frm

W, H = 12, 10
PALETTE = [(0, 0, 0), (120, 80, 50), (60, 45, 35), (30, 25, 22)]
LIVE_PLAN = Path("/ai/wasteland-hd/production-lightning4/plan.json")
NOISE = 20.0  # 4x-render sigma, ~10 after the 2x cut: above the gate, below QA


class FakeGame:
    """Discovery/palette stand-in; FRM decoding is patched on the module."""

    def __init__(self, names):
        self.names = set(names)
        self.data_dir = None
        self.dats = [SimpleNamespace(names=lambda prefix: list(self.names))]

    def read(self, art):
        if art not in self.names:
            raise FileNotFoundError(art)
        return art.encode()

    def art_list(self, index):
        return []

    def palette(self):
        return PALETTE


def _pose(body, trousers, head):
    pixels = np.zeros((H, W), np.uint8)
    pixels[2:8, 2:10] = body      # torso
    pixels[6:8, 2:10] = trousers  # legs
    pixels[2:4, 3:5] = head       # head
    pixels[0, 0] = 1              # the one pixel every pose shares
    return Frame(W, H, 0, 0, bytes(pixels.ravel()))


CALM, OTHER = _pose(1, 2, 3), _pose(2, 3, 2)


def _drifting():
    """Frames 1 and 2 agree and the keyframe (pose 0/3) draws other material there.

    So the flicker metric samples their whole body, while neither the static copy
    nor the patch-matched texture transfer can reach those samples: the same
    shape as ``hfprimab/0`` in docs/CRITTER-TEMPORAL-REPORT.md, which is what the
    direction fallback exists for.
    """
    return [CALM, OTHER, OTHER, CALM]


def _frm(directions, count=4):
    return Frm(10, 0, count, [0] * 6, [0] * 6, directions)


def _items_frm():
    frame = Frame(4, 3, 0, 0, bytes([1] * 11 + [0]))
    return Frm(10, 0, 1, [0] * 6, [0] * 6, [[frame]] * 6)


def _dot(frame, value):
    """The same pose with the one shared pixel changed, so no pair agrees there."""
    pixels = np.frombuffer(frame.pixels, np.uint8).copy().reshape(H, W)
    pixels[0, 0] = value
    return Frame(W, H, 0, 0, bytes(pixels.ravel()))


THIRD = _pose(3, 1, 1)
# Three poses that differ at every pixel, shared dot included: the original
# declares no static pixel, so flicker is unmeasurable and no seed can pass it.
UNMEASURED = [_dot(CALM, 1), _dot(OTHER, 2), _dot(THIRD, 3)]


SOURCES = {"art/critters/tester.frm": _frm([[CALM] * 4 for _ in range(3)] + [_drifting()]),
           "art/critters/unmeasured.frm": _frm([list(UNMEASURED) for _ in range(3)], count=3),
           "art/items/toy.frm": _items_frm()}


def _rendered(native, meta, src, seed, noisy):
    """Deterministic 4x render; ``noisy`` adds independent noise per frame.

    The sheet is RGB with a separate ``.mask.png`` (the cut reads only the mask
    for alpha), so the fake render clears RGB outside the mask: otherwise the
    sheet background would read as real art at the sprite boundary and the
    critter QA structure gate would reject a perfect nearest-neighbour render.
    """
    rendered = native.convert("RGBA").resize((native.width * 4, native.height * 4),
                                             Image.Resampling.NEAREST)
    pixels = np.asarray(rendered, np.float32).copy()
    with Image.open(meta / (src.name + ".mask.png")) as mask:
        inside = np.asarray(mask.resize((rendered.width, rendered.height),
                                        Image.Resampling.NEAREST)) > 0
    if noisy:
        placements = json.loads((meta / (src.name + ".json")).read_text())
        for item in placements:
            rng = np.random.default_rng([seed, item["direction"], item["frame"]])
            x, y = item["x"] * 4, item["y"] * 4
            h, w = item["height"] * 4, item["width"] * 4
            patch = pixels[y:y + h, x:x + w]
            keep = inside[y:y + h, x:x + w]
            patch[..., :3][keep] += rng.normal(0, NOISE, (int(keep.sum()), 3))
    pixels[..., :3][~inside] = 0
    return Image.fromarray(np.clip(np.rint(pixels), 0, 255).astype(np.uint8))


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setattr(p, "read_frm", lambda raw: SOURCES[raw.decode()])
    monkeypatch.setattr(p, "source_stamp", lambda game: "fixture")
    monkeypatch.setattr(p.Controller, "wait_ready", lambda self: None)
    # The stage measures originals through flicker._frames; resolve them by direction.
    monkeypatch.setattr(flicker, "_frames",
                        lambda game, name, direction: SOURCES[f"art/critters/{name}.frm"].directions[direction])
    calls, noisy = [], set()

    def fake_run(command, **kwargs):
        assert command[:3] == [p.sys.executable, "-m", "wasteland_hd"]
        calls.append(command)
        op = command[3]
        if op == "upscale":
            work = Path(command[command.index("--work") + 1])
            seed = int(command[command.index("--seed") + 1])
            prompt = command[command.index("--prompt") + 1]
            for src in sorted((work / "sheets").glob("*.png")):
                meta = work.parents[1] / "sheets"  # the attempt's sheet, mask and placements
                with Image.open(src) as native:
                    rendered = _rendered(native, meta, src, seed, seed in noisy)
                info = PngImagePlugin.PngInfo()
                graph = p.production_model(seed, prompt == "animation").graph(
                    "fixture", rendered.width, rendered.height)
                info.add_text("prompt", json.dumps(graph))
                target = work / "upscaled" / src.name
                target.parent.mkdir(exist_ok=True)
                rendered.save(target, pnginfo=info)
        elif op == "seams":
            source = Path(command[command.index("--source") + 1])
            out = Path(command[command.index("--out") + 1])
            for src in source.rglob("*.png"):
                target = out / src.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                p.atomic_copy(src, target)
        elif op == "temporal":
            # The real stage, driven by the arguments production.py passes.
            values = dict(zip(command[4::2], command[5::2]))
            result = temporal_stage.stage_directory(
                FakeGame(()), Path(values["--source"]), Path(values["--out"]),
                components=tuple(values["--components"].split(",")),
                strength=float(values["--strength"]), radius=int(values["--radius"]),
                cap=float(values["--fallback-cap"]))
            flicker.write_json(result, values["--json"])
        else:
            raise AssertionError(op)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(p.subprocess, "run", fake_run)

    def make(names=None, extra=()):
        names = names or {"art/critters/tester.frm"}
        args = p.parser().parse_args(["--root", str(tmp_path / "production"),
                                     "--game", str(tmp_path / "game"), "--no-seams", *extra])
        return p.Controller(args, FakeGame(names))

    return make, calls, noisy


def _state(root, job="critters-0000"):
    return p.read_json(root / "jobs" / job / "state.json")


def test_temporal_runs_only_for_critters_and_records_metrics(harness):
    make, calls, _noisy = harness
    c = make(extra=["--temporal", "--seeds", "42"])
    assert c.run() == p.NEEDS_REVIEW
    assert [call[3] for call in calls].count("temporal") == 1
    state = _state(c.root)
    group = state["groups"]["art/critters/tester/3"]
    assert group["complete"] and group["provenance"]["kind"] == "generated+temporal"
    assert group["provenance"]["temporal"]["components"] == list(temporal_stage.COMPONENTS)
    assert group["flicker"]["pass"] is True
    assert group["flicker"]["limits"] == {"broad": 2.5, "material": 3.0, "flicker": 6.0}
    assert group["flicker"]["direction"]["flicker"] <= 6.0
    coverage = p.read_json(c.root / "jobs/critters-0000/coverage.json")
    assert coverage["temporal"]["settings"]["rule"] == "temporal-stage/1"
    assert coverage["groups"]["art/critters/tester/3"]["flicker"]["pass"] is True
    # The option lives outside the immutable plan, per job type.
    assert "temporal" not in p.read_json(c.root / "plan.json")
    assert p.read_json(c.root / "options.json")["critters"]["temporal"]["enabled"] is True


def test_temporal_skips_non_critter_jobs(harness):
    make, calls, _noisy = harness
    c = make({"art/items/toy.frm"}, extra=["--temporal", "--seeds", "42"])
    c.run()
    assert not any(call[3] == "temporal" for call in calls)
    group = _state(c.root, "items-0000")["groups"]["art/items/toy/0"]
    assert group["provenance"]["kind"] == "generated"
    assert "flicker" not in group


def test_stage_off_calls_no_temporal_command(harness):
    make, calls, _noisy = harness
    c = make(extra=["--seeds", "42"])
    c.run()
    assert not any(call[3] == "temporal" for call in calls)
    assert not (c.root / "options.json").exists()
    group = _state(c.root)["groups"]["art/critters/tester/3"]
    assert group["provenance"]["kind"] == "generated"


def test_gate_rejection_moves_to_the_next_seed(harness):
    make, calls, noisy = harness
    noisy.add(42)
    c = make(extra=["--temporal", "--seeds", "42,7"])
    c.run()
    gates = sorted((c.root / "jobs/critters-0000").glob("attempt-*/post-*/temporal/gate.json"))
    assert len(gates) == 2
    first = p.read_json(gates[0])
    rejected = first["verdicts"]["art/critters/tester/3"]
    assert rejected["pass"] is False
    assert rejected["reason"] in {"direction_cap", "flicker_gate"}
    assert first["verdicts"]["art/critters/tester/0"]["pass"] is True
    # The failing direction's frames are deleted: the 2x-nearest stand-in is never shipped.
    out = gates[0].parent / "hd"
    assert not list((out / "art/critters/tester").glob("3_*.png"))
    assert list((out / "art/critters/tester").glob("0_*.png"))
    # Only the next seed's frames are accepted.
    state = _state(c.root)
    group = state["groups"]["art/critters/tester/3"]
    assert group["complete"] and group["provenance"]["seed"] == 7
    assert group["flicker"]["pass"] is True


def test_exhausted_gate_falls_back_to_original_art(harness):
    make, calls, noisy = harness
    c = make({"art/critters/unmeasured.frm"}, extra=["--temporal", "--seeds", "42,7,99"])
    assert c.run() == p.NEEDS_REVIEW
    state = _state(c.root)
    key = "art/critters/unmeasured/0"
    group = state["groups"][key]
    assert not group["complete"]
    assert group["fallback"] == "original_game_art_entire_direction"
    assert group["flicker"]["pass"] is False
    assert group["flicker"]["reason"] == "flicker_gate"
    assert group["flicker"]["direction"]["flicker"] is None  # nothing measurable
    assert not list((c.root / "jobs/critters-0000/hd/art/critters/unmeasured").glob("*.png"))
    assert not state["coverage_complete"]
    assert [call[3] for call in calls].count("temporal") == 3
    for gate in (c.root / "jobs/critters-0000").glob("attempt-*/post-*/temporal/gate.json"):
        assert p.read_json(gate)["verdicts"][key]["pass"] is False


def test_resume_after_interruption_reuses_the_gate_without_restaging(harness, monkeypatch):
    make, calls, _noisy = harness
    c = make(extra=["--temporal", "--seeds", "42"])
    c.run()
    work = c.root / "jobs/critters-0000"
    for png in (work / "hd").rglob("*.png"):
        png.unlink()
    state = p.read_json(work / "state.json")
    state["attempts"]["0"]["status"] = "running"  # crash after the stage, before accept
    p.atomic_json(work / "state.json", state)
    before = len(calls)
    monkeypatch.setattr(p.subprocess, "run", lambda *a, **k: pytest.fail("re-ran a stage on resume"))
    make(extra=["--temporal", "--seeds", "42"]).run()
    assert len(calls) == before
    group = _state(c.root)["groups"]["art/critters/tester/3"]
    assert group["complete"] and group["provenance"]["seed"] == 42
    assert group["flicker"]["pass"] is True  # restored from the durable gate file
    assert list((work / "hd/art/critters/tester").glob("3_*.png"))


def test_temporal_option_is_frozen_once_critters_started(harness):
    make, calls, _noisy = harness
    make(extra=["--temporal", "--seeds", "42"]).run()
    count = len(calls)
    make(extra=["--temporal", "--seeds", "42"]).run()  # same option: ordinary resume
    assert len(calls) == count
    with pytest.raises(ValueError, match="options changed after critters-0000"):
        make(extra=["--seeds", "42"]).run()


def test_temporal_option_document_on_the_live_roots_started_state(tmp_path):
    if not LIVE_PLAN.is_file():
        pytest.skip("live production root not available")
    root = tmp_path / "live"
    (root / "jobs" / "scenery-0000").mkdir(parents=True)
    (root / "plan.json").write_text(LIVE_PLAN.read_text())
    p.atomic_json(root / "jobs" / "scenery-0000" / "state.json", {"attempts": {"0": {"status": "done"}}})
    stub = SimpleNamespace(root=root, args=SimpleNamespace(temporal=True),
                           jobs=[("scenery-0000", "scenery", []), ("critters-0000", "critters", [])])
    stub.started = lambda name: p.Controller.started(stub, name)
    options = p.Controller.job_options(stub)
    # The live root has started scenery/tiles/walls only: nothing blocks critters.
    assert options["critters"]["temporal"]["enabled"] is True
    assert not (root / "options.json").exists()  # job_options only computes
    # Once a critter job has started, the option cannot appear or disappear.
    (root / "jobs" / "critters-0000" / "attempt-00").mkdir(parents=True)
    with pytest.raises(ValueError, match="options changed after critters-0000"):
        p.Controller.job_options(stub)


def test_live_plan_differs_only_by_the_critter_recipe_change(tmp_path, monkeypatch):
    """The live root's plan must drift by the critter recipe *only*.

    docs/CRITTER-PROMPT-REPORT.md moves plan.json aside so the running root
    restarts with the new animation prompt/denoise; every other field (sources,
    stamp, seeds, chunking, adoption) has to still match byte for byte, and
    --temporal must keep leaving the document unchanged.
    """
    if not LIVE_PLAN.is_file():
        pytest.skip("live production root not available")
    live = json.loads(LIVE_PLAN.read_text())
    raw = LIVE_PLAN.read_text()
    monkeypatch.setattr(p, "source_stamp", lambda game: live["source_stamp"])
    argv = ["--root", str(tmp_path / "root"), "--game", live["game"],
            "--seeds", ",".join(str(seed) for seed in live["seeds"]),
            "--chunk-size", str(live["chunk_size"]), "--adopt-world", live["adopt_world"]]
    document = None
    for extra in ([], ["--temporal"]):
        args = p.parser().parse_args(argv + extra)
        candidate = p.plan_document(args, FakeGame(()), live["sources"], live["unsupported_visuals"])
        encoded = json.dumps(candidate, indent=2, sort_keys=True, allow_nan=False) + "\n"
        if document is None:
            document, encoded_first = candidate, encoded
        else:
            assert encoded == encoded_first  # --temporal never changes plan bytes
    recipe = {"animation_prompt_sha256", "animation_denoise"}
    drifted = {key for key in set(live["settings"]) | set(document["settings"])
               if live["settings"].get(key) != document["settings"].get(key)}
    assert drifted == recipe, f"unexpected plan drift: {sorted(drifted - recipe)}"
    for key, value in live.items():
        if key != "settings":
            assert document[key] == value, f"non-recipe plan field changed: {key}"
    # With the live recipe restored, the document is the live file byte for byte.
    settings = dict(live["settings"])
    for key, value in document["settings"].items():
        if key not in recipe:
            assert settings.get(key) == value
            settings[key] = value
    restored = {**document, "settings": settings}
    assert json.dumps(restored, indent=2, sort_keys=True, allow_nan=False) + "\n" == raw
    # The naive alternative (a temporal key in the plan) would refuse this root.
    assert json.dumps({**document, "temporal": True}, indent=2, sort_keys=True) + "\n" != raw
