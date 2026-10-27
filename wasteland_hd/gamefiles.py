"""Reading Fallout 2's own files: DAT2 archives, the palette, FRM art and maps.

Everything here only reads the player's own game files; nothing is written
back into them.
"""
import os
import struct
import zlib
from dataclasses import dataclass, field

# Art types as the game numbers them (FID bits 24-27) and their folders.
ART_TYPES = ["items", "critters", "scenery", "walls", "tiles", "misc", "intrface", "inven", "heads", "backgrnd", "skilldex"]


class Dat:
    """A Fallout 2 DAT2 archive; names are compared case-insensitively with '/' or '\\'."""

    def __init__(self, path):
        self.path = path
        self.f = open(path, "rb")
        self.f.seek(-8, os.SEEK_END)
        tree_size, data_size = struct.unpack("<II", self.f.read(8))
        self.f.seek(data_size - tree_size - 8)
        tree = self.f.read(tree_size)
        (count,) = struct.unpack_from("<I", tree, 0)
        pos = 4
        self.entries = {}
        for _ in range(count):
            (name_size,) = struct.unpack_from("<I", tree, pos)
            pos += 4
            name = tree[pos:pos + name_size].decode("latin-1")
            pos += name_size
            compressed, real_size, packed_size, offset = struct.unpack_from("<BIII", tree, pos)
            pos += 13
            self.entries[norm(name)] = (name, compressed, real_size, packed_size, offset)

    def names(self, prefix=""):
        prefix = norm(prefix)
        return [e[0] for k, e in self.entries.items() if k.startswith(prefix)]

    def __contains__(self, name):
        return norm(name) in self.entries

    def read(self, name):
        _, compressed, real_size, packed_size, offset = self.entries[norm(name)]
        self.f.seek(offset)
        data = self.f.read(packed_size)
        return zlib.decompress(data) if compressed else data


def norm(name):
    return name.replace("\\", "/").lower()


class Game:
    """The player's Fallout 2: loose files in data/ win over critter.dat over master.dat, as in the game."""

    def __init__(self, game_dir):
        self.dir = game_dir
        self.dats = []
        for name in ("critter.dat", "master.dat"):
            path = find_ci(game_dir, name)
            if path:
                self.dats.append(Dat(path))
        if not self.dats:
            raise FileNotFoundError(f"no master.dat in {game_dir}")
        self.data_dir = find_ci(game_dir, "data")
        self._palette = None
        self._lists = {}

    def read(self, name):
        if self.data_dir:
            loose = find_ci(self.data_dir, *norm(name).split("/"))
            if loose:
                with open(loose, "rb") as f:
                    return f.read()
        for dat in self.dats:
            if name in dat:
                return dat.read(name)
        raise FileNotFoundError(name)

    def palette(self):
        """256 RGB triples (the file stores 6-bit values)."""
        if self._palette is None:
            raw = self.read("color.pal")
            self._palette = [tuple(min(255, raw[i * 3 + c] * 4) for c in range(3)) for i in range(256)]
        return self._palette

    def art_list(self, type_index):
        """art/<type>/<type>.lst entries, in order (the index in a FID)."""
        if type_index not in self._lists:
            folder = ART_TYPES[type_index]
            text = self.read(f"art/{folder}/{folder}.lst").decode("latin-1")
            names = []
            for line in text.splitlines():
                entry = line.split(";")[0].strip()
                if entry:
                    names.append(entry.split()[0].lower())
            self._lists[type_index] = names
        return self._lists[type_index]

    def art_path(self, fid):
        """Path of the art of a non-critter FID, or None."""
        type_index = (fid >> 24) & 0x0F
        if type_index == 1 or type_index >= len(ART_TYPES):
            return None
        names = self.art_list(type_index)
        index = fid & 0x0FFF
        if index >= len(names):
            return None
        return f"art/{ART_TYPES[type_index]}/{names[index]}"


def check_fallout2(game_dir):
    """None if the folder holds a real Fallout 2 (both archives, readable, with the game's maps and art), else why not."""
    for name in ("master.dat", "critter.dat"):
        path = find_ci(game_dir, name)
        if not path:
            return f"No {name} in {game_dir}."
        try:
            dat = Dat(path)
        except Exception as e:  # truncated or not a DAT2 archive
            return f"{name} could not be read: {e}"
        if name == "master.dat":
            needed = ("maps/arvillag.map", "art/tiles/tiles.lst", "color.pal", "text/english/game/proto.msg")
        else:
            needed = ("art/critters/hanpwraa.frm",)
        if len(dat.entries) < (10000 if name == "master.dat" else 5000) or not all(n in dat for n in needed):
            return f"{name} is not Fallout 2's."
    return None


def find_ci(directory, *parts):
    """Case-insensitive path lookup (game folders mix cases)."""
    path = directory
    for part in parts:
        try:
            match = next((e for e in os.listdir(path) if e.lower() == part.lower()), None)
        except (FileNotFoundError, NotADirectoryError):
            return None
        if match is None:
            return None
        path = os.path.join(path, match)
    return path


@dataclass
class Frame:
    width: int
    height: int
    x: int  # per-frame shift, as stored
    y: int
    pixels: bytes  # palette indices, row-major


@dataclass
class Frm:
    fps: int
    action_frame: int
    frames_per_direction: int
    shift_x: list
    shift_y: list
    # directions[d] = list of frames; directions sharing data share the list
    directions: list = field(default_factory=list)


def read_frm(data):
    fps, action, fpd = struct.unpack_from(">HHH", data, 4)
    shift_x = list(struct.unpack_from(">6h", data, 10))
    shift_y = list(struct.unpack_from(">6h", data, 22))
    offsets = struct.unpack_from(">6I", data, 34)
    frm = Frm(fps, action, fpd, shift_x, shift_y)
    by_offset = {}
    for d in range(6):
        if offsets[d] in by_offset:
            frm.directions.append(by_offset[offsets[d]])
            continue
        pos = 62 + offsets[d]
        frames = []
        for _ in range(fpd):
            w, h, size, x, y = struct.unpack_from(">HHIhh", data, pos)
            pos += 12
            frames.append(Frame(w, h, x, y, data[pos:pos + size]))
            pos += size
        by_offset[offsets[d]] = frames
        frm.directions.append(frames)
    return frm


def unique_directions(frm):
    """Indices of directions with their own frames (the rest repeat one of them)."""
    seen, out = set(), []
    for d, frames in enumerate(frm.directions):
        if id(frames) not in seen:
            seen.add(id(frames))
            out.append(d)
    return out


def frame_rgba(frame, palette):
    """RGBA bytes of a frame; palette index 0 is transparent."""
    out = bytearray(frame.width * frame.height * 4)
    for i, index in enumerate(frame.pixels):
        if index:
            r, g, b = palette[index]
            out[i * 4:i * 4 + 4] = bytes((r, g, b, 255))
    return bytes(out)


def map_art(game, map_name):
    """Art paths a map uses: its floor and roof tiles and the art of its objects (not critters)."""
    data = game.read(f"maps/{map_name}")
    version, = struct.unpack_from(">I", data, 0)
    if version != 20:
        raise ValueError(f"{map_name}: not a Fallout 2 map (version {version})")
    local_vars, = struct.unpack_from(">i", data, 0x20)
    flags, = struct.unpack_from(">i", data, 0x28)
    global_vars, = struct.unpack_from(">i", data, 0x30)
    pos = 0xEC + 4 * (global_vars + local_vars)
    paths = set()
    tiles = game.art_list(4)
    for elevation, absent in enumerate((0x2, 0x4, 0x8)):
        if flags & absent:
            continue
        for i in range(10000):
            value, = struct.unpack_from(">I", data, pos + i * 4)
            for tile in (value >> 16, value & 0xFFFF):
                index = tile & 0x0FFF
                if index > 1 and index < len(tiles):  # 0 = none, 1 = blank
                    paths.add(f"art/tiles/{tiles[index]}")
        pos += 40000
    # Objects: every record holds fid at +0x20, elevation at +0x28 and pid at
    # +0x2C; the pid's type matches the fid's. Records have variable-length
    # tails, so scan for that pattern instead of walking them.
    for p in range(pos, len(data) - 0x30, 4):
        fid, _flags, elevation, pid = struct.unpack_from(">IIII", data, p + 0x20)
        type_index = fid >> 24
        if type_index in (0, 2, 3, 5) and pid >> 24 == type_index and elevation <= 2 and (fid & 0x00FFF000) == 0:
            path = game.art_path(fid)
            if path:
                paths.add(path)
    return sorted(paths)
