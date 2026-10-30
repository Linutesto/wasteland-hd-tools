"""Chroma-cast measurement, correction, and its gate wiring (synthetic art).

A cast is a connected region whose Lab a*/b* moved the same way; resampling
noise never reaches area + a consistent mean vector, and a frame with no
flagged region must come back from the correction byte-identical.
"""
import numpy as np
from PIL import Image, ImageDraw

from wasteland_hd import chroma, invention


def _flat(size, rgb):
    return Image.new('RGBA', size, rgb + (255,))


def _cast_hd(original):
    """The original redrawn 2x with one big block shifted cool (purple-ish)."""
    hd = original.resize((original.width * 2, original.height * 2),
                         Image.Resampling.NEAREST)
    block = Image.new('RGBA', (64, 64), (120, 44, 78, 255))
    hd.paste(block, (32, 32))
    return hd


def test_cast_measure_flags_a_connected_cool_block():
    original = _flat((64, 64), (150, 40, 40))
    hd = _cast_hd(original)
    measured = chroma.cast_measure(hd, original)
    assert measured['opaque_px'] == 64 * 64
    assert measured['cast_regions'] >= 1
    assert measured['cast_area'] > chroma.CAST_AREA_LIMIT
    assert measured['cast_max'] >= chroma.CAST_REGION_DELTA
    assert chroma.cast_flagged(hd, original)
    # The regional-mean gates see far less than the region does.
    assert measured['mean_delta'] < measured['cast_max']


def test_scattered_noise_is_not_a_cast():
    original = _flat((64, 64), (150, 40, 40))
    hd = original.resize((128, 128), Image.Resampling.NEAREST).copy()
    draw = ImageDraw.Draw(hd)
    for i in range(40):                     # isolated ±10 px, no cluster
        x, y = (i * 37) % 120 + 4, (i * 53) % 120 + 4
        draw.point((x, y), fill=(160, 40, 40, 255))
        draw.point((x + 1, y + 1), fill=(140, 40, 40, 255))
    measured = chroma.cast_measure(hd, original)
    assert measured['cast_area'] == 0.0
    assert not chroma.cast_flagged(hd, original)


def test_cast_correct_pulls_chroma_back_and_keeps_alpha_and_luminance():
    original = _flat((64, 64), (150, 40, 40))
    hd = _cast_hd(original)
    fixed = chroma.cast_correct(hd, original)
    assert np.array_equal(np.asarray(fixed)[..., 3], np.asarray(hd)[..., 3])
    mask = chroma.cast_mask(hd, original)
    src = np.asarray(original.convert('RGB'), np.float32)[mask]
    def chroma_err(image):
        native = np.asarray(image.convert('RGB')
                            .resize(original.size, Image.Resampling.BOX),
                            np.float32)[mask]
        _ls, sa, sb = chroma._srgb_to_lab(src.reshape(1, -1, 3))
        _ld, da, db = chroma._srgb_to_lab(native.reshape(1, -1, 3))
        return float(np.hypot((da - sa).mean(), (db - sb).mean()))
    assert chroma_err(fixed) < chroma_err(hd) / 2
    # HD luminance is kept: L over the mask barely moves.
    native_hd = np.asarray(hd.convert('RGB').resize(original.size, Image.Resampling.BOX), np.float32)[mask]
    native_fx = np.asarray(fixed.convert('RGB').resize(original.size, Image.Resampling.BOX), np.float32)[mask]
    l_hd = chroma._srgb_to_lab(native_hd.reshape(1, -1, 3))[0].mean()
    l_fx = chroma._srgb_to_lab(native_fx.reshape(1, -1, 3))[0].mean()
    assert abs(float(l_fx - l_hd)) < 2.0


def test_cast_correct_is_byte_identical_when_nothing_is_flagged():
    original = _flat((64, 64), (150, 40, 40))
    hd = original.resize((128, 128), Image.Resampling.NEAREST)
    fixed = chroma.cast_correct(hd, original)
    assert np.array_equal(np.asarray(hd), np.asarray(fixed))


def test_score_reports_cast_detail_for_sprites_only():
    original = _flat((64, 64), (150, 40, 40))
    hd = _cast_hd(original)
    walls = invention.score(hd, original, kind='walls')
    assert 'cast' in walls
    assert walls['cast']['cast_area'] > chroma.CAST_AREA_LIMIT
    # Detail only: the verdict path is unchanged by the cast measurement.
    assert walls['verdict'] in ('clean', 'minor_invention', 'invented_texture')
    # Tiles/floors keep their exact behaviour: no new key, same verdict.
    base = original.resize((128, 128), Image.Resampling.NEAREST)
    tiles = invention.score(base, original, kind='tiles')
    assert 'cast' not in tiles
    assert tiles['verdict'] == 'clean'
    # The flag measures them without changing the verdict either.
    with_cast = invention.score(base, original, kind='tiles', cast_tiles=True)
    assert 'cast' in with_cast
    assert with_cast['verdict'] == tiles['verdict']
    assert with_cast['native'] == tiles['native']
    assert with_cast['scale2'] == tiles['scale2']
