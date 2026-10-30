"""Source-ground novelty and border classification on synthetic opaque tiles."""
import pytest
from PIL import Image, ImageDraw

from wasteland_hd import invention


def test_verdicts_and_scale_measurements():
    original = Image.new('RGBA',(32,32),(100,90,80,255))
    base = original.resize((64,64),Image.Resampling.NEAREST)
    assert invention.score(base,original)['verdict'] == 'clean'
    minor = base.copy()
    ImageDraw.Draw(minor).rectangle((25,25,34,34),fill=(160,40,40,255))
    result = invention.score(minor,original)
    assert result['verdict'] == 'minor_invention'
    assert result['native']['novel_fraction'] < result['scale2']['novel_fraction']
    texture = base.copy()
    for x in range(8,56,7):
        ImageDraw.Draw(texture).rectangle((x,8,x+3,55),fill=(5,5,5,255))
    result = invention.score(texture,original)
    assert result['verdict'] == 'invented_texture'
    assert result['scale2']['largest_novel_region'] > invention.TEXTURE_CLUSTER_LIMIT
    assert result['scale2']['flat_damage'] > invention.FLAT_DAMAGE_LIMIT
    border = base.copy()
    ImageDraw.Draw(border).rectangle((0,0,63,5),fill=(255,0,0,255))
    assert invention.score(border,original)['verdict'] == 'unmatched_floor'
    assert invention.score(base,original,seam_delta=20)['verdict'] == 'unmatched_floor'
    assert invention.score(base,original,seam_delta=20,floor=False)['verdict'] == 'clean'


def test_kind_mapping_and_validation():
    assert invention.kind_of('art/tiles/brick14/0_0.png') == 'tiles'
    assert invention.kind_of('art/walls/nec002/0_0.png') == 'walls'
    assert invention.kind_of('art/scenery/door01/0_0.png') == 'scenery'
    assert invention.kind_of('art/items/club/0_0.png') == 'items'
    assert invention.kind_of('art/misc/knife/2_5.png') == 'misc'
    assert invention.kind_of('art/interface/iface/0_0.png') == 'tiles'
    original = Image.new('RGBA',(16,16),(100,90,80,255))
    base = original.resize((32,32),Image.Resampling.NEAREST)
    with pytest.raises(ValueError):
        invention.score(base,original,kind='critters')


def test_tiny_sprite_needs_absolute_evidence():
    # 8x10 opaque: below the area where fractions mean anything.
    original = Image.new('RGBA',(16,16),(0,0,0,0))
    ImageDraw.Draw(original).rectangle((4,4,11,13),fill=(200,190,180,255))
    base = original.resize((32,32),Image.Resampling.NEAREST)
    clean = invention.score(base,original,kind='items')
    assert clean['verdict'] == 'clean'
    assert clean['scale2']['mask_pixels'] < invention.SPRITE_MIN_AREA
    small = base.copy()
    ImageDraw.Draw(small).rectangle((14,14,15,15),fill=(5,5,5,255))
    minor = invention.score(small,original,kind='items')
    assert 0 < minor['scale2']['novel_pixels'] < invention.SPRITE_MIN_PIXELS_TEXTURE
    assert minor['verdict'] == 'minor_invention'
    big = base.copy()
    ImageDraw.Draw(big).rectangle((12,12,19,23),fill=(5,5,5,255))
    assert invention.score(big,original,kind='items')['verdict'] == 'invented_texture'
    # The floor rule would have failed the same small change on ratios alone.
    assert invention.score(small,original,kind='tiles',floor=False)['verdict'] == 'invented_texture'


def test_sprite_band_never_blinds_a_sprite_with_a_core():
    # Ellipse: wide enough that most of the mask lies beyond the alpha band.
    original = Image.new('RGBA',(40,40),(0,0,0,0))
    ImageDraw.Draw(original).ellipse((0,0,39,39),fill=(200,190,180,255))
    base = original.resize((80,80),Image.Resampling.NEAREST)
    assert invention.score(base,original,kind='walls')['verdict'] == 'clean'
    patch = base.copy()
    ImageDraw.Draw(patch).rectangle((24,24,55,55),fill=(5,5,5,255))
    assert invention.score(patch,original,kind='walls')['verdict'] == 'invented_texture'


def test_banded_sprite_without_core_still_fails_invented_detail():
    # 4px opaque / 4px transparent bands: every pixel sits inside the alpha
    # band, so the band must be distrusted and the interior still measured.
    original = Image.new('RGBA',(48,48),(0,0,0,0))
    draw = ImageDraw.Draw(original)
    for y in range(0,48,8):
        draw.rectangle((0,y,47,y+3),fill=(200,190,180,255))
    base = original.resize((96,96),Image.Resampling.NEAREST)
    assert invention.score(base,original,kind='misc')['verdict'] == 'clean'
    rungs = base.copy()
    drawn = ImageDraw.Draw(rungs)
    for y in range(0,96,16):
        drawn.rectangle((0,y+5,95,y+6),fill=(5,5,5,255))
    result = invention.score(rungs,original,kind='misc')
    assert result['verdict'] == 'invented_texture'
    assert result['scale2']['novel_pixels'] >= invention.SPRITE_MIN_PIXELS_TEXTURE


def test_emissive_lift_is_not_dark_structure():
    # Dark panel with a lamp; the HD frame glows wider than the source.
    original = Image.new('RGBA',(32,32),(25,25,30,255))
    ImageDraw.Draw(original).rectangle((14,14,17,17),fill=(255,240,160,255))
    base = original.resize((64,64),Image.Resampling.NEAREST)
    glow = base.copy()
    ImageDraw.Draw(glow).ellipse((22,22,41,41),outline=(170,158,110,255),width=3)
    # Tiles still flag invented edges in the dark region around the lamp.
    assert invention.score(glow,original,kind='tiles',floor=False)['verdict'] == 'invented_texture'
    # Sprites treat the widened glow as emissive, not as new dark structure.
    scenery = invention.score(glow,original,kind='scenery')
    assert scenery['scale2']['dark_novel'] == 0.0
    assert scenery['verdict'] != 'invented_texture'


def test_source_exact_art_is_not_invention():
    # Scanline sprites (interface art such as bomb1/pipx) have no interior at
    # all: their nearest-neighbour 2x silhouette is far sharper than any
    # bicubic resample of the source, so untouched game art used to measure as
    # invented texture and the original-art fallback could never ship.
    original = Image.new('RGBA', (65, 65), (0, 0, 0, 0))
    draw = ImageDraw.Draw(original)
    for y in range(3, 64, 2):
        draw.rectangle((0, y, 64, y), fill=(90, 220, 90, 255))
    base = original.resize((130, 130), Image.Resampling.NEAREST)
    assert invention.source_exact(base, original)
    measured = invention._single_scale(base, original, sprite=False)
    assert measured['novel_fraction'] > invention.TEXTURE_AREA_LIMIT
    # With the exemption the untouched frame is clean on both paths.
    assert invention.score(base, original, kind='tiles', floor=False)['verdict'] == 'clean'
    assert invention.score(base, original, kind='misc')['verdict'] == 'clean'
    # A junction can still fail untouched art: it is the pair that breaks.
    assert invention.score(base, original, kind='walls',
                           seam_delta=invention.WALL_SEAM_DELTA_LIMIT + 1)['verdict'] == 'unmatched_wall'
    # Floor tiles keep their own verdict on the very same frame.
    assert invention.score(base, original, kind='tiles',
                           seam_delta=invention.SEAM_DELTA_LIMIT + 1)['verdict'] == 'unmatched_floor'
    # Anything that differs from the source is measured exactly as before.
    changed = base.copy()
    ImageDraw.Draw(changed).rectangle((40, 40, 52, 52), fill=(200, 40, 40, 255))
    assert not invention.source_exact(changed, original)
    assert invention.score(changed, original, kind='tiles', floor=False)['verdict'] == 'invented_texture'


def _wall_frm(width, height):
    """A minimal single-frame wall FRM: opaque rectangle, mortar rows."""
    import struct
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            pixels.append(70 if y % 6 < 2 else 90)
    header = struct.pack('>IHHH', 1, 10, 0, 1)
    header += struct.pack('>6h', *([0] * 6)) + struct.pack('>6h', *([0] * 6))
    header += struct.pack('>6I', *([0] * 6)) + b'\x00' * 4  # frame data starts at 62
    frame = struct.pack('>HHIhh', width, height, width * height, 0, 0)
    return header + frame + bytes(pixels)


def _wall_map(records):
    """A version-20 map file (no tile grids) holding the given wall objects."""
    import struct
    data = bytearray(0xEC)
    struct.pack_into('>I', data, 0, 20)
    struct.pack_into('>i', data, 0x20, 0)          # local vars
    struct.pack_into('>i', data, 0x28, 0x2 | 0x4 | 0x8)  # no elevation grids
    struct.pack_into('>i', data, 0x30, 0)          # global vars
    for index, (tile, rotation) in enumerate(records):
        record = bytearray(0x80)
        struct.pack_into('>ii', record, 0x00, index + 1, tile)
        struct.pack_into('>i', record, 0x18, 0)    # frame
        struct.pack_into('>i', record, 0x1C, rotation)
        struct.pack_into('>I', record, 0x20, (3 << 24) | index)  # fid: wall
        struct.pack_into('>i', record, 0x28, 0)    # elevation
        struct.pack_into('>I', record, 0x2C, (3 << 24) | index)  # pid
        data += record
    return bytes(data)


class _WallGame:
    """Synthetic game: one map with two touching walls and their FRMs."""
    dats = []
    data_dir = None

    def __init__(self, data_dir):
        self.data_dir = str(data_dir)

    def art_list(self, kind):
        assert kind == 3
        return ['wa.frm', 'wb.frm']

    def read(self, name):
        lower = name.lower()
        if lower.endswith('.map'):
            from pathlib import Path
            return (Path(self.data_dir) / 'maps' / Path(name).name.lower()).read_bytes()
        if lower.endswith('wa.frm'):
            return _wall_frm(40, 60)
        if lower.endswith('wb.frm'):
            return _wall_frm(40, 60)
        raise FileNotFoundError(name)


def test_wall_records_and_engine_placement(tmp_path):
    import struct
    data = _wall_map([(5021, 0)])
    records = invention.wall_records(data)
    assert len(records) == 1
    tile, x, y, frame, rotation, elevation, fid = records[0]
    assert (tile, x, y, frame, rotation, elevation) == (5021, 0, 0, 0, 0, 0)
    assert fid >> 24 == 3
    # tileToScreenXY with a zero view offset, then objectGetRect's anchors.
    left, up = invention._hex_screen(5021)
    v3 = 199 - 5021 % 200
    assert left == 48 * (v3 // 2) + (32 if v3 & 1 else 0) + 16 * (5021 // 200)
    assert up == -12 * (v3 // 2) + 12 * (5021 // 200)


def test_wall_pairs_finds_touching_junctions(tmp_path):
    (tmp_path / 'maps').mkdir()
    (tmp_path / 'maps' / 'walltest.map').write_bytes(_wall_map([(5021, 0), (5022, 0)]))
    pairs = invention.wall_pairs(_WallGame(tmp_path))
    assert len(pairs) == 1
    junction = pairs[0]
    assert junction['map'] == 'walltest.map'
    assert junction['a']['rel'] == 'art/walls/wa/0_0.png'
    assert junction['b']['rel'] == 'art/walls/wb/0_0.png'
    assert junction['elevation'] == 0
    # Adjacent hexes sit one engine step apart; the sprites' alphas touch.
    dx = invention._hex_screen(5022)[0] - invention._hex_screen(5021)[0]
    dy = invention._hex_screen(5022)[1] - invention._hex_screen(5021)[1]
    assert (junction['dx'], junction['dy']) == (dx, dy), 'placement follows the engine hex step'
    assert junction['contact'] > 0
    assert junction['same'] is False
    # Hexes 60 apart put their sprites far from each other: no junction.
    (tmp_path / 'maps' / 'walltest.map').write_bytes(_wall_map([(5021, 0), (5081, 0)]))
    assert invention.wall_pairs(_WallGame(tmp_path)) == []


def _wall_pair():
    """Two 40x60 brick originals that meet at dx=40, plus their 2x versions."""
    def brick():
        image = Image.new('RGBA', (40, 60), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 39, 59), fill=(150, 130, 110, 255))
        for y in range(0, 60, 6):
            draw.rectangle((0, y, 39, y + 1), fill=(110, 90, 75, 255))
        return image
    a, b = brick(), brick()
    placement = {'dx': 40, 'dy': 0}
    return a, b, placement


def test_wall_seam_delta_measures_only_the_extra_break():
    a, b, placement = _wall_pair()
    base_a = a.resize((80, 120), Image.Resampling.NEAREST)
    base_b = b.resize((80, 120), Image.Resampling.NEAREST)
    assert invention.wall_seam_delta(base_a, base_b, a, b, placement) == 0.0
    # A colour break at the junction that the source does not have.
    broken = base_b.copy()
    px = broken.load()
    for x in range(8):
        for y in range(120):
            r, g, bl, alpha = px[x, y]
            if alpha:
                px[x, y] = (max(0, r - 80), max(0, g - 80), min(255, bl + 80), alpha)
    delta = invention.wall_seam_delta(base_a, broken, a, b, placement)
    assert delta > invention.WALL_SEAM_DELTA_LIMIT
    # The same step in the source pair is the baseline, not a failure.
    broken_src = b.copy()
    px = broken_src.load()
    for x in range(4):
        for y in range(60):
            r, g, bl, alpha = px[x, y]
            if alpha:
                px[x, y] = (max(0, r - 80), max(0, g - 80), min(255, bl + 80), alpha)
    assert invention.wall_seam_delta(base_a, broken, a, broken_src, placement) == 0.0


def _bright_columns(image, columns, amount=70):
    """The 2x frame with `columns` lifted - a redraw's bright edge band."""
    out = image.copy()
    px = out.load()
    for x in columns:
        for y in range(out.height):
            r, g, bl, alpha = px[x, y]
            if alpha:
                px[x, y] = (min(255, r + amount), min(255, g + amount),
                            min(255, bl + amount), alpha)
    return out


def test_wall_seam_delta_sees_a_rim_the_colour_band_cannot():
    """The colour band compares each side with the *other* sprite, so pieces
    that are equally bright at the contact look fine there; the pair still
    steps twice across the junction - the light line a redraw leaves on a
    frame's own edge where the source art has none."""
    a, b, placement = _wall_pair()
    base_a = a.resize((80, 120), Image.Resampling.NEAREST)
    base_b = b.resize((80, 120), Image.Resampling.NEAREST)
    assert invention.wall_seam_delta(base_a, base_b, a, b, placement) == 0.0
    lit_a = _bright_columns(base_a, range(74, 80))   # a's right edge, at the contact
    lit_b = _bright_columns(base_b, range(0, 6))     # b's left edge, over it
    colour, line = invention.wall_junction_terms(lit_a, lit_b, a, b, placement)
    assert colour <= invention.WALL_SEAM_DELTA_LIMIT, 'the colour band alone is blind to it'
    assert line > invention.WALL_SEAM_DELTA_LIMIT, 'the step along the contact is not'
    assert invention.wall_seam_delta(lit_a, lit_b, a, b, placement) > invention.WALL_SEAM_DELTA_LIMIT
    # The shape seen on the shipped walls: one frame's 1-px edge rim alone.
    rim = _bright_columns(base_b, range(0, 1), 60)
    assert invention.wall_seam_delta(base_a, rim, a, b, placement) > invention.WALL_SEAM_DELTA_LIMIT
    # The same lift in the source pair is the baseline, not a failure.
    lifted = _bright_columns(b, range(0, 6))
    assert invention.wall_seam_delta(lit_a, lit_b, a, lifted, placement) <= 1.0


def test_unmatched_wall_verdict_only_for_walls():
    a, b, placement = _wall_pair()
    base = a.resize((80, 120), Image.Resampling.NEAREST)
    wall = invention.score(base, a, seam_delta=invention.WALL_SEAM_DELTA_LIMIT + 1, kind='walls')
    assert wall['verdict'] == 'unmatched_wall'
    assert invention.score(base, a, seam_delta=invention.WALL_SEAM_DELTA_LIMIT + 1, kind='walls')['verdict'] \
        == 'unmatched_wall'
    # Within the limit walls keep their sprite verdict.
    assert invention.score(base, a, seam_delta=0.5, kind='walls')['verdict'] == 'clean'
    # No other kind can be unmatched; floors keep their own verdict.
    assert invention.score(base, a, seam_delta=99, kind='scenery')['verdict'] == 'clean'
    assert invention.score(base, a, seam_delta=99, kind='tiles')['verdict'] == 'unmatched_floor'
    assert invention.score(base, a, seam_delta=99, kind='tiles', floor=False)['verdict'] == 'clean'


def test_wall_junction_mask_marks_the_contact_band():
    a, b, placement = _wall_pair()
    mask = invention.wall_junction_mask(a, [(placement, b)])
    assert mask.shape == (120, 80)
    assert mask.any()
    # The band sits on a's right edge, where b touches it.
    assert mask[:, 70:].any()
    assert not mask[:, :30].any()
