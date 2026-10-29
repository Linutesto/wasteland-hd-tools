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
