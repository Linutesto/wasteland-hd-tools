"""Offline Fallout DAT2, FRM and map reader checks."""
import pathlib
import struct
import zlib

import pytest

from wasteland_hd.gamefiles import Dat, Game, frame_rgba, map_art, read_frm, unique_directions


def make_dat(path, entries):
    """Build a minimal DAT2: content followed by entry tree and footer."""
    payload = bytearray()
    records = []
    for name, contents, compress in entries:
        blob = zlib.compress(contents) if compress else contents
        records.append((name.encode("latin-1"), int(compress), len(contents), len(blob), len(payload)))
        payload.extend(blob)
    tree = bytearray(struct.pack("<I", len(records)))
    for name, compressed, original_size, packed_size, offset in records:
        tree += struct.pack("<I", len(name)) + name
        tree += struct.pack("<BIII", compressed, original_size, packed_size, offset)
    total = len(payload) + len(tree) + 8
    path.write_bytes(payload + tree + struct.pack("<II", len(tree), total))


def test_dat2_compressed_uncompressed_and_casefold(tmp_path):
    archive = tmp_path / "MASTER.DAT"
    make_dat(archive, [("Art\\Tiles\\A.FRM", b"pix", False),
                       ("MAPS/Arvillag.map", b"long text" * 30, True)])
    dat = Dat(archive)
    assert dat.read("art/tiles/a.frm") == b"pix"
    assert dat.read("maps\\ARVILLAG.MAP") == b"long text" * 30
    assert dat.names("art/") == ["Art\\Tiles\\A.FRM"]


def test_frm_shared_directions_alpha_palette():
    # Six directions point at the same frame data block.
    header = bytearray(62)
    struct.pack_into(">IHHH", header, 0, 4, 12, 0, 1)
    pixels = bytes((0, 1, 2, 0))
    blob = struct.pack(">HHIhh", 2, 2, len(pixels), -3, 4) + pixels
    frm = read_frm(header + blob)
    assert unique_directions(frm) == [0]
    assert frm.fps == 12
    assert all(d is frm.directions[0] for d in frm.directions)
    frame = frm.directions[0][0]
    assert (frame.width, frame.height, frame.x, frame.y) == (2, 2, -3, 4)
    rgba = frame_rgba(frame, [(0, 0, 0), (12, 34, 56), (8, 9, 10)])
    assert rgba[:4] == b"\x00" * 4
    assert rgba[4:8] == bytes((12, 34, 56, 255))
    assert rgba[-4:] == b"\x00" * 4


def test_arroyo_map_scan_on_players_game():
    game_dir = pathlib.Path.home() / "fallout2/gamedata"
    if not (game_dir / "MASTER.DAT").is_file():
        pytest.skip("player's Fallout 2 not installed")
    art = map_art(Game(str(game_dir)), "arvillag.map")
    assert "art/scenery/well1.frm" in art
    assert any(path.startswith("art/tiles/") for path in art)
    assert len(art) > 60
