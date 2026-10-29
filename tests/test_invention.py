"""Source-ground novelty and border classification on synthetic opaque tiles."""
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
