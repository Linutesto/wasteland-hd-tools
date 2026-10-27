"""End-to-end sheet cut/pack and offline pack distribution tests."""
import hashlib
import io
import json
import pathlib
import struct
import types
import zipfile

import pytest
from PIL import Image

from wasteland_hd import __main__ as cli
from wasteland_hd.gamefiles import Frame


def test_lanczos_sheet_cut_pack_roundtrip(tmp_path, monkeypatch):
    frame = Frame(3, 2, 0, 0, bytes((1, 0, 2, 3, 0, 1)))

    class ToyGame:
        def __init__(self, _):
            pass

        def art_list(self, _):
            return ["toy.frm"]

        def read(self, _):
            return b"unused"

        def palette(self):
            return [(0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255)]

    monkeypatch.setattr(cli, "Game", ToyGame)
    monkeypatch.setattr(cli.sh, "frames_of", lambda _g, _arts: [("art/tiles/toy.frm", 0, 0, frame)])
    work = tmp_path / "work"
    output = tmp_path / "pack.zip"
    args = types.SimpleNamespace(game="dummy", map=[], type=["tiles"], work=str(work),
                                 backend="lanczos", quality="auto", steps=30, comfy="", out=str(output))
    cli.cmd_sheets(args)
    cli.cmd_upscale(args)
    cli.cmd_cut(args)
    cli.cmd_pack(args)
    with zipfile.ZipFile(output) as z:
        assert z.namelist() == ["hd/art/tiles/toy/0_0.png"]
        img = Image.open(io.BytesIO(z.read(z.namelist()[0])))
    assert img.size == (6, 4)
    mask = Image.frombytes("L", (3, 2), bytes((255, 0, 255, 255, 0, 255)))
    assert img.getchannel("A").tobytes() == mask.resize((6, 4), Image.Resampling.NEAREST).tobytes()


def manifest_for(tmp_path, name, contents):
    archive = tmp_path / name
    with zipfile.ZipFile(archive, "w") as z:
        for path, content in contents.items():
            z.writestr(path, content)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    packs_path = tmp_path / "packs.json"
    packs_path.write_text(json.dumps({"packs": [{"name": "mini", "file": name, "size": archive.stat().st_size,
                                                  "url": archive.as_uri(), "sha256": digest}]}))
    return packs_path


def validated_game(tmp_path):
    """Point at the player's real DATs without copying GBs into the fixture."""
    source = pathlib.Path.home() / "fallout2/gamedata"
    if not (source / "MASTER.DAT").is_file() or not (source / "CRITTER.DAT").is_file():
        pytest.skip("player's Fallout 2 not available")
    game = tmp_path / "game"
    game.mkdir()
    (game / "master.dat").symlink_to(source / "MASTER.DAT")
    (game / "critter.dat").symlink_to(source / "CRITTER.DAT")
    return game


def install(packs, game):
    cli.cmd_install(types.SimpleNamespace(packs=packs.as_uri(), game=str(game), only=None))


def test_install_file_url_checksum_and_reinstall(tmp_path):
    game = validated_game(tmp_path)
    packs = manifest_for(tmp_path, "mini.zip", {"hd/art/tiles/toy/0_0.png": b"image"})
    install(packs, game)
    assert (game / "data/hd/art/tiles/toy/0_0.png").read_bytes() == b"image"
    assert (game / "data/hd/.mini.sha256").read_text() == json.loads(packs.read_text())["packs"][0]["sha256"]
    install(packs, game)


def test_install_rejects_game_without_fallout2(tmp_path):
    game = tmp_path / "empty"
    game.mkdir()
    packs = manifest_for(tmp_path, "mini.zip", {"hd/ok": b"ok"})
    with pytest.raises(SystemExit, match="master.dat"):
        install(packs, game)


def test_install_rejects_bad_checksum(tmp_path):
    game = validated_game(tmp_path)
    packs = manifest_for(tmp_path, "mini.zip", {"hd/ok": b"ok"})
    doc = json.loads(packs.read_text())
    doc["packs"][0]["sha256"] = "0" * 64
    packs.write_text(json.dumps(doc))
    with pytest.raises(SystemExit, match="checksum mismatch"):
        install(packs, game)
    assert not (game / "data/hd/ok").exists()


def test_install_resumes_http_range(tmp_path, monkeypatch):
    game = validated_game(tmp_path)
    packs = manifest_for(tmp_path, "mini.zip", {"hd/ok": b"resume works"})
    archive = (tmp_path / "mini.zip").read_bytes()
    partial = len(archive) // 2
    (game / "mini.zip.part").write_bytes(archive[:partial])
    manifest = json.loads(packs.read_text())
    manifest["packs"][0]["url"] = "https://example.invalid/mini.zip"
    packs.write_text(json.dumps(manifest))
    import urllib.request
    original_urlopen = urllib.request.urlopen
    seen = []

    class RangeReply(io.BytesIO):
        status = 206

        def __init__(self):
            super().__init__(archive[partial:])
            self.headers = {"Content-Range": f"bytes {partial}-{len(archive)-1}/{len(archive)}"}

    def fake_urlopen(request, **kwargs):
        if isinstance(request, urllib.request.Request):
            seen.append(request.get_header("Range"))
            return RangeReply()
        return original_urlopen(request, **kwargs)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    install(packs, game)
    assert seen == [f"bytes={partial}-"]
    assert (game / "data/hd/ok").read_bytes() == b"resume works"
    assert not (game / "mini.zip.part").exists()


@pytest.mark.parametrize("unsafe", ["../outside.zip", "nested/evil.zip", "bad\\\\name.zip"])
def test_install_rejects_unsafe_manifest_filename(tmp_path, unsafe):
    game = validated_game(tmp_path)
    packs = manifest_for(tmp_path, "mini.zip", {"hd/ok": b"ok"})
    doc = json.loads(packs.read_text())
    doc["packs"][0]["file"] = unsafe
    packs.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="invalid HD pack manifest"):
        install(packs, game)
    assert not (game / "data/hd/ok").exists()


@pytest.mark.parametrize("entry", ["evil.txt", "../outside.txt", "hd/../../outside.txt"])
def test_install_rejects_zip_outside_hd(tmp_path, entry):
    game = validated_game(tmp_path)
    packs = manifest_for(tmp_path, "mini.zip", {"hd/good.png": b"good", entry: b"bad"})
    with pytest.raises(SystemExit, match="unexpected file"):
        install(packs, game)
    assert not (game / "data/hd/good.png").exists()


def test_install_rejects_backslash_zip_path(tmp_path):
    game = validated_game(tmp_path)
    packs = manifest_for(tmp_path, "mini.zip", {"hd/good.png": b"good", "hd/..\\outside.txt": b"bad"})
    with pytest.raises(SystemExit, match="unexpected file"):
        install(packs, game)
    assert not (game / "data/hd/good.png").exists()


def test_manifest_add_update(tmp_path):
    pack_path = tmp_path / "arroyo.zip"
    with zipfile.ZipFile(pack_path, "w") as z:
        z.writestr("hd/art/tiles/a/0_0.png", b"a")
        z.writestr("hd/art/tiles/b/0_0.png", b"b")
    manifest = tmp_path / "packs.json"
    args = types.SimpleNamespace(zip=str(pack_path), name="arroyo-hd", covers="Arroyo village",
                                 tag="arroyo-v1", packs_json=str(manifest))
    cli.cmd_manifest(args)
    cli.cmd_manifest(args)
    data = json.loads(manifest.read_text())
    assert len(data["packs"]) == 1
    assert data["packs"][0]["frames"] == 2
    assert data["packs"][0]["sha256"] == hashlib.sha256(pack_path.read_bytes()).hexdigest()
    assert data["packs"][0]["url"].endswith("/arroyo-v1/arroyo.zip")
