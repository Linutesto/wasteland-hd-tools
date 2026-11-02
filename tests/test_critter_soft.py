"""CPU critter fallback, resume migration, packaging, and selection."""
import json
import zipfile
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw

from wasteland_hd import repair, select
from wasteland_hd.production import digest, file_digest, read_json


def test_soft_alpha_colour_halo_and_determinism():
    original = Image.new('RGBA', (8, 8), (0, 255, 0, 0))
    ImageDraw.Draw(original).rectangle((2, 2, 5, 5), fill=(200, 50, 20, 255))
    original.putpixel((3, 3), (100, 70, 30, 128))
    soft = repair.source_soft_2x(original)
    near = original.resize(soft.size, Image.Resampling.NEAREST)
    assert soft.getchannel('A').tobytes() == near.getchannel('A').tobytes()
    assert soft.tobytes() == repair.source_soft_2x(original).tobytes()
    pixels = np.asarray(soft)
    assert pixels[..., 1].max() < 100  # invisible green cannot bleed into the sprite
    assert soft.tobytes() != near.tobytes()
    empty = Image.new('RGBA', (2, 2), (10, 20, 30, 0))
    assert repair.source_soft_2x(empty).tobytes() == empty.resize((4, 4), Image.Resampling.NEAREST).tobytes()


def test_upgrade_no_render_and_select(tmp_path, monkeypatch):
    root, out = tmp_path/'production', tmp_path/'repair'
    job = root/'jobs'/'critters-0000'
    job.mkdir(parents=True)
    (root/'plan.json').write_text(json.dumps({'game': 'fixture'}))
    rels = [f'art/critters/toy/0_{i}.png' for i in range(2)]
    images = []
    for i in range(2):
        im = Image.new('RGBA', (24, 24), (0, 0, 0, 0))
        draw = ImageDraw.Draw(im)
        draw.rectangle((3, 3, 20, 20), fill=(110, 95, 80, 255))
        draw.rectangle((8+i, 8, 15+i, 15), fill=(70, 65, 60, 255))
        images.append(im)
    group = {'art': 'art/critters/toy.frm', 'direction': 0, 'animated': True, 'frames': []}
    for rel, im in zip(rels, images):
        nearest = im.resize((48, 48), Image.Resampling.NEAREST)
        group['frames'].append({'rel':rel, 'width':48, 'height':48, 'alpha':digest(nearest.getchannel('A').tobytes())})
        path = out/'jobs'/job.name/'hd'/rel
        path.parent.mkdir(parents=True, exist_ok=True)
        nearest.save(path)
    key = 'art/critters/toy/0'
    (job/'inventory.json').write_text(json.dumps({'groups': {key: group}}))
    (job/'state.json').write_text(json.dumps({'status':'needs_review', 'groups':{key:{'complete':False, 'fallback':'original_game_art_entire_direction'}}}))
    (job/'coverage.json').write_text('{"status":"needs_review"}')
    old = {'accepted':True, 'fallback':'original_game_art', 'provenance':{'kind':'original_game_art'},
           'source':str(out/'jobs'/job.name/'original-art'),
           'hashes':{rel:file_digest(out/'jobs'/job.name/'hd'/rel) for rel in rels}}
    state_path = out/'jobs'/job.name/'state.json'
    state_path.write_text(json.dumps({'groups':{key:old}}))
    monkeypatch.setattr(repair, 'Game', lambda _: SimpleNamespace())
    originals = dict(zip(rels, images))
    monkeypatch.setattr(repair.qa, 'original_frame', lambda game, cache, rel: originals[rel])
    monkeypatch.setattr(repair.invention, 'cached_adjacency', lambda *a, **kw: [])
    monkeypatch.setattr(repair.invention, 'cached_wall_pairs', lambda *a, **kw: [])
    monkeypatch.setattr(repair.Controller, 'render_batch', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('render called')))
    controller = repair.Controller(root, out)
    out.mkdir(exist_ok=True)
    repair.atomic_json(out/'plan.json', controller.plan)
    before = file_digest(out/'plan.json')
    result = controller.run()['jobs'][job.name]['groups'][key]
    assert result['fallback'] == result['provenance']['kind'] == 'source_soft_2x'
    assert result['gate']['complete']
    assert file_digest(out/'plan.json') == before
    with zipfile.ZipFile(out/'staging'/f'{job.name}.zip') as z:
        for rel, im in zip(rels, images):
            with Image.open(z.open('hd/'+rel)) as saved:
                assert saved.tobytes() == repair.source_soft_2x(im).tobytes()
    roots = select.source_roots(root, out, job.name, critter=True)
    found = select.candidates(group, roots, originals)
    found.append(select.fallback(group, originals, tmp_path/'fallback'))
    chosen, _, _ = select.choose(found, (1, 1, .05))
    assert chosen['source'] == 'repair_accepted'
    assert select.choose(found, (1, 1, .05))[0]['frames']
    again = repair.Controller(root, out).run()['jobs'][job.name]['groups'][key]
    assert again == result
    assert read_json(state_path)['groups'][key] == result


def test_new_exhausted_direction_uses_soft(tmp_path, monkeypatch):
    root, out = tmp_path/'production', tmp_path/'repair'
    root.mkdir()
    (root/'plan.json').write_text(json.dumps({'game':'fixture'}))
    original = Image.new('RGBA', (20, 20), (80, 75, 70, 255))
    ImageDraw.Draw(original).rectangle((5, 5, 14, 14), fill=(120, 110, 100, 255))
    near = original.resize((40, 40), Image.Resampling.NEAREST)
    rel = 'art/critters/toy/1_0.png'
    group = {'art':'art/critters/toy.frm', 'direction':1, 'animated':True,
             'frames':[{'rel':rel, 'width':40, 'height':40, 'alpha':digest(near.getchannel('A').tobytes())}]}
    monkeypatch.setattr(repair, 'Game', lambda _: SimpleNamespace())
    monkeypatch.setattr(repair.qa, 'original_frame', lambda *args: original)
    controller = repair.Controller(root, out)
    result = controller.process_group(tmp_path/'critters-0000', 'art/critters/toy/1', group,
                                      False, prepared=[RuntimeError('failed'), RuntimeError('failed')])
    assert result['accepted'] and result['fallback'] == 'source_soft_2x'
    with Image.open(out/'jobs'/'critters-0000'/'hd'/rel) as saved:
        assert saved.tobytes() == repair.source_soft_2x(original).tobytes()
