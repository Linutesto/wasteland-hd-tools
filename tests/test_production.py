"""production.py: durable source coverage and local staging; no GPU/network calls."""

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import zipfile

from PIL import Image, PngImagePlugin
import pytest

from wasteland_hd import production as p
from wasteland_hd.gamefiles import Frame, Frm
from wasteland_hd.upscale import ANIMATION_PROMPT, LIGHTNING, PROMPT

REAL_WAIT_READY = p.Controller.wait_ready


class ToyGame:
    def __init__(self, names, aliases=None):
        self.names = names
        self.aliases = aliases or {}
        self.data_dir = None
        self.dats = [SimpleNamespace(names=lambda prefix: list(names))]

    def read(self, art):
        if art not in self.names:
            raise FileNotFoundError(art)
        return art.encode()

    def art_list(self, index):
        return self.aliases.get(p.ART_TYPES[index], [])

    def palette(self):
        return [(0, 0, 0), (180, 30, 30), (30, 180, 30)]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    sources = {}

    def decode(raw):
        frames = sources[raw.decode()]
        return Frm(10, 0, len(frames), [0] * 6, [0] * 6, [frames] * 6)

    monkeypatch.setattr(p, "read_frm", decode)
    monkeypatch.setattr(p, "source_stamp", lambda game: "fixture")
    monkeypatch.setattr(p.Controller, "wait_ready", lambda self: None)
    calls = []
    perturb = {"seeds": set(), "missing_cut": False}

    def fake_run(command, **kwargs):
        assert command[:3] == [p.sys.executable, "-m", "wasteland_hd"]
        calls.append(command)
        op = command[3]
        if op == "upscale":
            work = Path(command[command.index("--work") + 1])
            seed = int(command[command.index("--seed") + 1])
            prompt = command[command.index("--prompt") + 1]
            for src in (work / "sheets").glob("*.png"):
                with Image.open(src) as native:
                    rendered = native.resize((native.width * 4, native.height * 4), Image.Resampling.NEAREST)
                metadata = work.parents[1] / "sheets" / (src.name + ".json")
                placements = json.loads(metadata.read_text())
                if seed in perturb["seeds"]:
                    f = placements[-1]
                    rendered.paste((0, 240, 0), (f["x"] * 4, f["y"] * 4,
                                               (f["x"] + f["width"]) * 4, (f["y"] + f["height"]) * 4))
                info = PngImagePlugin.PngInfo()
                model = p.Comfy(seed=seed, prompt=ANIMATION_PROMPT if prompt == "animation" else PROMPT)
                graph = model.graph("fixture", rendered.width, rendered.height)
                del graph["lora"]
                info.add_text("prompt", json.dumps(graph))
                target = work / "upscaled" / src.name
                target.parent.mkdir(exist_ok=True)
                rendered.save(target, pnginfo=info)
        elif op == "seams":
            # A malicious colour fixer must never get an initially rejected direction.
            source = Path(command[command.index("--source") + 1])
            out = Path(command[command.index("--out") + 1])
            for src in source.rglob("*.png"):
                target = out / src.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                p.atomic_copy(src, target)
        else:
            raise AssertionError(op)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(p.subprocess, "run", fake_run)

    def make(names=None, aliases=None, extra=()):
        names = names or {"art/items/toy.frm": 1}
        for art, count in names.items():
            sources[art] = [Frame(4, 3, 0, 0, bytes([1] * 11 + [0])) for _ in range(count)]
        args = p.parser().parse_args(["--root", str(tmp_path / "production"), "--game", str(tmp_path / "game"),
                                     "--no-seams", *extra])
        return p.Controller(args, ToyGame(names, aliases))

    return make, calls, perturb, sources


def state(controller, name="items-0000"):
    return p.read_json(controller.root / "jobs" / name / "state.json")


def test_full_settings_priority_coverage_and_local_zip(setup):
    make, calls, _, _ = setup
    names = {f"art/{kind}/toy.frm": 1 for kind in p.ART_TYPES}
    c = make(names)
    assert [kind for _, kind, _ in c.jobs] == p.PRIORITY
    assert c.run() == p.NEEDS_REVIEW
    report = p.read_json(c.root / "report.json")
    assert report["coverage_complete"]
    assert set(report["types"]) == set(p.ART_TYPES)
    assert report["status"] == "needs_review"
    for call in calls:
        assert call[call.index("--quality") + 1] == "full"
        assert call[call.index("--steps") + 1] == "30"
        assert call[call.index("--render-scale") + 1] == "4"
    for archive in (c.root / "staging").glob("*.zip"):
        with zipfile.ZipFile(archive) as pack:
            assert pack.namelist()
            assert all(name.startswith("hd/") for name in pack.namelist())
            assert pack.testzip() is None
        receipt = p.read_json(archive.with_suffix(".json"))
        assert receipt["sha256"] == p.file_digest(archive)
        assert receipt["visual_review"] == "pending"


def test_restart_skips_generation_and_missing_output_fails_complete(setup):
    make, calls, _, _ = setup
    c = make(extra=["--seeds", "42"])
    c.run()
    count = len(calls)
    make(extra=["--seeds", "42"]).run()
    assert len(calls) == count
    (c.root / "jobs/items-0000/hd/art/items/toy/0_0.png").unlink()
    make(extra=["--seeds", "42"]).run()
    assert len(calls) == count  # seed budget does not reset on restart
    s = state(c)
    assert not s["coverage_complete"]
    group = s["groups"]["art/items/toy/0"]
    assert group["accepted"] == 0
    assert group["missing"] == 1
    assert group["fallback"] == "original_game_art_entire_direction"


def test_animation_retries_entire_direction_and_never_splices_frames(setup):
    make, calls, perturb, _ = setup
    perturb["seeds"] = {42}
    c = make({"art/critters/toy.fr3": 2})
    c.run()
    assert len(calls) == 2
    for call in calls:
        assert call[call.index("--prompt") + 1] == "animation"
        job = Path(call[call.index("--work") + 1])
        metadata = next((job.parents[1] / "sheets").glob("*.png.json"))
        placements = p.read_json(metadata)
        assert [(f["direction"], f["frame"]) for f in placements] == [(3, 0), (3, 1)]
    group = state(c, "critters-0000")["groups"]["art/critters/toy/3"]
    assert group["complete"] and group["accepted"] == 2
    assert group["provenance"]["seed"] == 7


def test_bounded_rejections_advance_next_job_with_explicit_fallback(setup):
    make, calls, perturb, _ = setup
    perturb["seeds"] = {42, 7, 99}
    c = make({"art/items/a.frm": 2, "art/inven/b.frm": 1})
    c.run()
    assert len(calls) == 6
    assert not state(c)["coverage_complete"]
    assert not list((c.root / "jobs/items-0000/hd").rglob("*.png"))
    assert (c.root / "jobs/inven-0000/state.json").exists()
    count = len(calls)
    make({"art/items/a.frm": 2, "art/inven/b.frm": 1}).run()
    assert len(calls) == count


def test_integrity_precedes_any_qa_for_whole_direction(setup, monkeypatch):
    make, _, _, _ = setup
    c = make({"art/items/toy.frm": 2})
    manifest, frames = p.inventory(c.game, c.paths["items"])
    group = next(iter(manifest["groups"].values()))
    hd = c.root / "candidate"
    for expected in group["frames"]:
        target = hd / expected["rel"]
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (8, 6), (180, 30, 30, 255)).save(target)
    monkeypatch.setattr(p.qa, "badness", lambda *a: pytest.fail("QA called before integrity passed"))
    result = p.evaluate(c.game, group, frames, hd)
    assert not result["complete"]
    assert result["valid"] == 0
    expected = group["frames"][0]
    assert "alpha" in p.integrity(hd / expected["rel"], expected)
    Image.new("RGBA", (9, 6)).save(hd / expected["rel"])
    assert p.integrity(hd / expected["rel"], expected) == "wrong dimensions"


def test_raw_recolour_rejected_before_seams(setup):
    make, calls, perturb, _ = setup
    perturb["seeds"] = {42, 7, 99}
    c = make({"art/tiles/toy.frm": 1})
    c.args.seams = True
    c.plan["seams"] = True
    c.run()
    assert not any(call[3] == "seams" for call in calls)
    assert not state(c, "tiles-0000")["coverage_complete"]


def test_discovery_union_alias_missing_and_unreadable_sources(setup):
    make, _, _, sources = setup
    c = make({"art/heads/unlisted.frm": 1, "art/critters/loose.fr5": 1},
             {"heads": ["missing.frm,0,0", "unlisted.frm"]})
    sources["art/heads/unlisted.frm"] = None  # parse error must be a blocker
    result = c.dry_run()
    heads = result["jobs"]["heads-0000"]
    assert heads["source_files"] == 2
    assert len(heads["source_errors"]) == 2
    assert result["jobs"]["critters-0000"]["expected"] == 1
    assert not c.root.exists()


@pytest.mark.parametrize("fast", [False, True])
def test_adoption_preserves_legacy_full_skips_but_lightning_regenerates(setup, tmp_path, fast):
    make, calls, _, _ = setup
    world = tmp_path / "world"
    c = make({"art/tiles/toy.frm": 1}, extra=["--adopt-world", str(world)])
    manifest, frames = p.inventory(c.game, c.paths["tiles"])
    items = p.sheets.pack(list(frames.values()), sheet=256)[0]
    native = world / "sheets/static-00000.png"
    native.parent.mkdir(parents=True)
    p.sheets.render_sheet(items, c.game.palette(), str(native), sheet=256)
    up = world / "upscaled/static-00000.png"
    up.parent.mkdir()
    info = PngImagePlugin.PngInfo()
    model = p.Comfy(steps=8, cfg=1, lora=LIGHTNING[8]) if fast else p.Comfy()
    graph = model.graph("fixture", 1024, 1024)
    if not fast:
        del graph["lora"]
    info.add_text("prompt", json.dumps(graph))
    with Image.open(native) as im:
        im.resize((1024, 1024), Image.Resampling.NEAREST).save(up, pnginfo=info)
    before = {str(f): f.read_bytes() for f in world.rglob("*") if f.is_file()}
    c.run()
    assert len(calls) == int(fast)
    assert {str(f): f.read_bytes() for f in world.rglob("*") if f.is_file()} == before
    receipt = p.read_json(c.root / "jobs/tiles-0000/adopted/provenance.json")
    assert receipt[up.name]["full_compatible"] == (not fast)
    group = state(c, "tiles-0000")["groups"]["art/tiles/toy/0"]
    assert group["complete"]
    if not fast:
        assert group["provenance"]["kind"] == "adopted"
        assert group["provenance"]["all_full"] is True


def test_single_controller_lock_released_on_error(tmp_path):
    with p.controller_lock(tmp_path):
        with pytest.raises(RuntimeError, match="another production"):
            with p.controller_lock(tmp_path):
                pass
    with p.controller_lock(tmp_path):
        pass


def test_corrupt_checkpoint_and_readonly_status(tmp_path, capsys):
    root = tmp_path / "not-created"
    assert p.main(["--root", str(root), "--status"]) == 0
    assert not root.exists()
    root.mkdir()
    (root / "report.json").write_text('{"status":"needs_review"}')
    before = (root / "report.json").stat().st_mtime_ns
    with p.controller_lock(root):
        assert p.main(["--root", str(root), "--status"]) == 2
    assert (root / "report.json").stat().st_mtime_ns == before
    (root / "report.json").write_text("{broken")
    assert p.main(["--root", str(root), "--status"]) == 64
    assert "corrupted checkpoint" in capsys.readouterr().err


def test_command_timeout_budget_survives_restart(setup, monkeypatch):
    make, _, _, _ = setup
    c = make()
    seen = []

    def fail(command, **kwargs):
        seen.append(command)
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(p.subprocess, "run", fail)
    checkpoint = c.root / "command.json"
    with pytest.raises(RuntimeError, match="bounded command failures"):
        c.command(["upscale"], checkpoint, gpu=True)
    assert len(seen) == 2
    with pytest.raises(RuntimeError):
        c.command(["upscale"], checkpoint, gpu=True)
    assert len(seen) == 2


def test_readiness_waits_for_queue_and_times_out_without_mutations(setup, monkeypatch):
    make, calls, _, _ = setup
    c = make()
    def reply(url, **kwargs):
        from io import BytesIO
        return BytesIO(json.dumps({"queue_running": [["existing-job"]], "queue_pending": []}
                                 if url.endswith("/queue") else {}).encode())
    monkeypatch.setattr(p.urllib.request, "urlopen", reply)
    c.args.ready_timeout = 0
    with pytest.raises(TimeoutError, match="preserving current GPU job"):
        REAL_WAIT_READY(c)
    assert not calls


def test_animation_packing_is_explicit(setup, monkeypatch):
    make, _, _, _ = setup
    c = make({"art/heads/talk.frm": 3})
    original = p.sheets.pack
    flags = []

    def pack(frames, **kwargs):
        flags.append(kwargs)
        return original(frames, **kwargs)

    monkeypatch.setattr(p.sheets, "pack", pack)
    c.run()
    assert flags and all(f == {"sheet": 256, "by_animation": True} for f in flags)
