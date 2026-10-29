"""Repair isolation, whole-direction ladder, fallback, and restart behavior."""
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

from PIL import Image, ImageDraw
import pytest

from wasteland_hd import repair
from wasteland_hd.production import digest, read_json


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    root, out = tmp_path/'production', tmp_path/'repair'
    job = root/'jobs'/'tiles-0000'
    rel = 'art/tiles/toy/0_0.png'
    original = Image.new('RGBA',(32,32),(100,90,80,255))
    bad = original.resize((64,64),Image.Resampling.NEAREST)
    for x in range(8,56,7):
        ImageDraw.Draw(bad).rectangle((x,8,x+3,55),fill=(5,5,5,255))
    source = job/'hd'/rel
    source.parent.mkdir(parents=True)
    bad.save(source)
    (root/'staging').mkdir(parents=True)
    with zipfile.ZipFile(root/'staging/tiles-0000.zip','w') as z:
        z.write(source,'hd/'+rel)
    expected = {'rel':rel,'width':64,'height':64,'alpha':digest(bad.getchannel('A').tobytes())}
    group = {'art':'art/tiles/toy.frm','direction':0,'frames':[expected],'animated':False}
    (job/'inventory.json').write_text(json.dumps({'groups':{'art/tiles/toy/0':group}}))
    (job/'state.json').write_text(json.dumps({'status':'needs_review','groups':{'art/tiles/toy/0':{'complete':True}}}))
    (job/'coverage.json').write_text('{"status":"needs_review"}')
    (root/'plan.json').write_text(json.dumps({'game':'fixture'}))
    monkeypatch.setattr(repair,'Game',lambda game: SimpleNamespace())
    monkeypatch.setattr(repair.qa,'original_frame',lambda game,cache,r:original)
    monkeypatch.setattr(repair.invention,'cached_adjacency',lambda *a,**kw:[])
    monkeypatch.setattr(repair.invention,'cached_wall_pairs',lambda *a,**kw:[])
    return root,out,job,rel,original,bad


def test_audit_only_writes_one_report_and_leaves_production_untouched(fixture):
    root,out,job,rel,original,bad = fixture
    before = {p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    report = repair.Controller(root,out,audit_only=True).run()
    assert report['jobs']['tiles-0000']['verdict_counts']['invented_texture'] == 1
    assert sorted(p.name for p in out.iterdir()) == ['report.json']
    assert before == {p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_ladder_order_accepts_second_retry_and_resume_skips_render(fixture,monkeypatch):
    root,out,job,rel,original,bad = fixture
    calls=[]
    good = original.resize((64,64),Image.Resampling.NEAREST)
    def fake_render(self,job_name,groups,pending,step,denoise):
        calls.append(denoise)
        dest=out/f'candidate-{step}'
        path=dest/rel
        path.parent.mkdir(parents=True,exist_ok=True)
        (bad if step==0 else good).save(path)
        return {key:(dest,{'kind':'reduced_denoise','denoise':denoise}) for key in pending}
    monkeypatch.setattr(repair.Controller,'render_batch',fake_render)
    report=repair.Controller(root,out).run()
    assert calls == [0.55,0.4]
    assert report['jobs']['tiles-0000']['accepted'] == 1
    with zipfile.ZipFile(out/'staging/tiles-0000.zip') as z:
        with Image.open(z.open('hd/'+rel)) as im:
            assert list(im.getdata()) == list(good.getdata())
    assert read_json(out/'jobs/tiles-0000/coverage.json')['groups']['art/tiles/toy/0']['fallback'] is None
    repair.Controller(root,out).run()
    assert calls == [0.55,0.4]


def test_failed_retries_use_source_floor_fallback(fixture,monkeypatch):
    root,out,job,rel,original,bad = fixture
    calls=[]
    def fake_render(self,job_name,groups,pending,step,denoise):
        calls.append(denoise)
        dest=out/f'candidate-{step}'
        path=dest/rel
        path.parent.mkdir(parents=True,exist_ok=True)
        bad.save(path)
        return {key:(dest,{'kind':'reduced_denoise','denoise':denoise}) for key in pending}
    monkeypatch.setattr(repair.Controller,'render_batch',fake_render)
    report=repair.Controller(root,out).run()
    assert calls == [0.55,0.4]
    row=read_json(out/'jobs/tiles-0000/coverage.json')['groups']['art/tiles/toy/0']
    assert row['fallback'] == 'source_floor_2x'
    assert row['accepted']
    assert report['jobs']['tiles-0000']['receipt']['sha256']


def test_render_recovers_valid_graph_after_interruption(fixture,monkeypatch):
    from PIL import PngImagePlugin
    from wasteland_hd.gamefiles import Frame, Frm
    root,out,job,rel,original,bad = fixture
    controller = repair.Controller(root,out,command_retries=2)
    frame = Frame(32,32,0,0,bytes([1])*1024)
    class ToyGame:
        def read(self,name): return b'frm'
        def palette(self): return [(0,0,0),(100,90,80)]
    controller.game = ToyGame()
    monkeypatch.setattr(repair,'read_frm',lambda raw: Frm(10,0,1,[0]*6,[0]*6,[[frame]]*6),raising=False)
    # repair.render imports read_frm inside the method, so patch its source.
    from wasteland_hd import gamefiles
    monkeypatch.setattr(gamefiles,'read_frm',lambda raw: Frm(10,0,1,[0]*6,[0]*6,[[frame]]*6))
    waits=[]
    monkeypatch.setattr(repair.Controller,'wait_ready',lambda self: waits.append(True))
    calls=[]
    def interrupted_upscale(self,src,dst,scale=4):
        calls.append(self.denoise)
        with Image.open(src) as im:
            up=im.resize((im.width*scale,im.height*scale))
        info=PngImagePlugin.PngInfo()
        info.add_text('prompt',json.dumps(self.graph('fixture',up.width,up.height)))
        up.save(dst,pnginfo=info)
        raise KeyboardInterrupt('after output, before checkpoint')
    monkeypatch.setattr(repair.Comfy,'upscale',interrupted_upscale)
    group=read_json(job/'inventory.json')['groups']['art/tiles/toy/0']
    with pytest.raises(KeyboardInterrupt):
        controller.render('tiles-0000','art/tiles/toy/0',group,0,0.55)
    def no_gpu(*args,**kwargs):
        pytest.fail('valid graph should resume without GPU')
    monkeypatch.setattr(repair.Comfy,'upscale',no_gpu)
    candidate, provenance=controller.render('tiles-0000','art/tiles/toy/0',group,0,0.55)
    assert (candidate/rel).is_file()
    assert provenance['graph_verified']
    assert waits == [True]
    assert calls == [0.55]


def test_rejected_direction_is_a_repair_candidate(fixture,monkeypatch):
    root,out,job,rel,original,bad = fixture
    rejected = job/'attempt-02'/'hd'/rel
    rejected.parent.mkdir(parents=True)
    bad.save(rejected)
    (job/'hd'/rel).unlink()
    (job/'state.json').write_text(json.dumps({'status':'needs_review',
        'groups':{'art/tiles/toy/0':{'complete':False}}}))
    report = repair.Controller(root,out,audit_only=True).run()
    group = report['jobs']['tiles-0000']['groups']['art/tiles/toy/0']
    assert group['source'] == 'rejected_candidate'
    assert group['gate']['frames'][rel]['verdict'] == 'invented_texture'


def test_unmatched_floor_uses_edge_lock_before_source_fallback(fixture,monkeypatch):
    root,out,job,rel,original,bad = fixture
    border=original.resize((64,64),Image.Resampling.NEAREST)
    ImageDraw.Draw(border).rectangle((0,0,63,5),fill=(255,0,0,255))
    border.save(job/'hd'/rel)
    def fake_render(self,job_name,groups,pending,step,denoise):
        dest=out/f'candidate-{step}'
        path=dest/rel
        path.parent.mkdir(parents=True,exist_ok=True)
        border.save(path)
        return {key:(dest,{'kind':'reduced_denoise','denoise':denoise}) for key in pending}
    monkeypatch.setattr(repair.Controller,'render_batch',fake_render)
    repair.Controller(root,out).run()
    row=read_json(out/'jobs/tiles-0000/coverage.json')['groups']['art/tiles/toy/0']
    assert row['fallback'] == 'edge_lock'
    assert row['accepted']


def _batch_fixture(fixture, monkeypatch, directions=3):
    """Add several directions backed by one synthetic FRM and a mocked game."""
    from wasteland_hd.gamefiles import Frame, Frm
    root, out, job, rel, original, bad = fixture
    frame = Frame(32, 32, 0, 0, bytes([1]) * 1024)
    class ToyGame:
        def read(self, name): return b'frm'
        def palette(self): return [(0, 0, 0), (100, 90, 80)]
    monkeypatch.setattr(repair, 'Game', lambda game: ToyGame())
    from wasteland_hd import gamefiles
    monkeypatch.setattr(gamefiles, 'read_frm',
                        lambda raw: Frm(10, 0, 1, [0]*6, [0]*6, [[frame]]*6))
    inventory = read_json(job/'inventory.json')
    state = read_json(job/'state.json')
    for direction in range(1, directions):
        key = f'art/tiles/toy/{direction}'
        relative = f'art/tiles/toy/{direction}_0.png'
        item = dict(inventory['groups']['art/tiles/toy/0'])
        item['direction'] = direction
        item['frames'] = [dict(item['frames'][0], rel=relative)]
        inventory['groups'][key] = item
        state['groups'][key] = {'complete': True}
        target = job/'hd'/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        bad.save(target)
    (job/'inventory.json').write_text(json.dumps(inventory))
    (job/'state.json').write_text(json.dumps(state))
    return root, out, job, original, bad


def _mock_comfy(monkeypatch, calls, interrupt=False):
    from PIL import PngImagePlugin
    monkeypatch.setattr(repair.Controller, 'wait_ready', lambda self: None)
    def upscale(self, src, dst, scale=4):
        calls.append((self.denoise, Path(src).name))
        with Image.open(src) as native:
            up = native.resize((native.width*scale, native.height*scale))
        info = PngImagePlugin.PngInfo()
        info.add_text('prompt', json.dumps(self.graph('fixture', up.width, up.height)))
        up.save(dst, pnginfo=info)
        if interrupt and len(calls) == 1:
            raise KeyboardInterrupt('after sheet output')
    monkeypatch.setattr(repair.Comfy, 'upscale', upscale)


def test_batch_shared_sheet_and_old_state_resume(fixture, monkeypatch):
    root, out, job, original, bad = _batch_fixture(fixture, monkeypatch)
    old_key = 'art/tiles/toy/0'
    old_rel = 'art/tiles/toy/0_0.png'
    good = original.resize((64, 64), Image.Resampling.NEAREST)
    old_path = out/'jobs'/job.name/'hd'/old_rel
    old_path.parent.mkdir(parents=True, exist_ok=True)
    good.save(old_path)
    old_gate = repair.Controller(root, out).gate(job, read_json(job/'inventory.json')['groups'][old_key], old_path.parents[3])
    legacy = {'source':str(out/'jobs'/job.name/'attempts'/old_key/'denoise-0'/'candidate'/'hd'),
              'gate':old_gate, 'hashes':{old_rel:repair.file_digest(old_path)},
              'fallback':None, 'provenance':{'kind':'reduced_denoise','denoise':0.55},
              'accepted':True}
    state_path = out/'jobs'/job.name/'state.json'
    state_path.write_text(json.dumps({'groups':{old_key:legacy}}))
    calls = []
    _mock_comfy(monkeypatch, calls, interrupt=True)
    with pytest.raises(KeyboardInterrupt):
        repair.Controller(root, out).run()
    assert calls == [(0.55, 'static-00000.png')]
    _mock_comfy(monkeypatch, calls)
    report = repair.Controller(root, out).run()
    assert calls == [(0.55, 'static-00000.png')]
    assert report['jobs'][job.name]['accepted'] == 3
    assert report['jobs'][job.name]['groups'][old_key] == legacy
    marker = read_json(out/'jobs'/job.name/'attempts'/'batch'/'denoise-0'/'sheets.json')
    assert marker['keys'] == ['art/tiles/toy/1', 'art/tiles/toy/2']
    assert len(list((out/'jobs'/job.name/'attempts'/'batch'/'denoise-0').glob('*-render.png'))) == 1
    repair.Controller(root, out).run()
    assert calls == [(0.55, 'static-00000.png')]


def test_batch_ladder_only_retries_failed_direction(fixture, monkeypatch):
    root, out, job, original, bad = _batch_fixture(fixture, monkeypatch)
    calls = []
    _mock_comfy(monkeypatch, calls)
    real_gate = repair.Controller.gate
    def gate(self, work, group, source, *, overrides=None):
        result = real_gate(self, work, group, source, overrides=overrides)
        if (source and 'denoise-0' in str(source) and group['direction'] == 1):
            result['complete'] = False
            result['issues']['forced'] = 'first step rejected'
        return result
    monkeypatch.setattr(repair.Controller, 'gate', gate)
    report = repair.Controller(root, out).run()
    assert calls == [(0.55, 'static-00000.png'), (0.4, 'static-00000.png')]
    assert report['jobs'][job.name]['accepted'] == 3
    assert read_json(out/'jobs'/job.name/'attempts'/'batch'/'denoise-1'/'sheets.json')['keys'] == ['art/tiles/toy/1']
    assert report['jobs'][job.name]['groups']['art/tiles/toy/1']['provenance']['seed'] == 1013


def test_step_a_visits_next_job_before_previous_fallback(fixture, monkeypatch):
    root, out, job, rel, original, bad = fixture
    second = root/'jobs'/'tiles-0001'
    second.mkdir()
    for name in ('inventory.json', 'state.json', 'coverage.json'):
        (second/name).write_bytes((job/name).read_bytes())
    target = second/'hd'/'art/tiles/toy/0_0.png'
    target.parent.mkdir(parents=True)
    bad.save(target)
    calls = []
    def batch(self, job_name, groups, pending, step, denoise):
        calls.append((job_name, step))
        return {key:RuntimeError('mock failure') for key in pending}
    monkeypatch.setattr(repair.Controller, 'render_batch', batch)
    repair.Controller(root, out).run()
    assert calls == [('tiles-0000', 0), ('tiles-0001', 0),
                     ('tiles-0000', 1), ('tiles-0001', 1)]
    for name in ('tiles-0000', 'tiles-0001'):
        assert read_json(out/'jobs'/name/'coverage.json')['groups']['art/tiles/toy/0']['fallback'] == 'source_floor_2x'


def test_batch_separates_animation_prompt_and_recovers_completed_download(fixture, monkeypatch):
    root, out, job, original, bad = _batch_fixture(fixture, monkeypatch, directions=2)
    inventory = read_json(job/'inventory.json')
    inventory['groups']['art/tiles/toy/1']['animated'] = True
    (job/'inventory.json').write_text(json.dumps(inventory))
    from PIL import PngImagePlugin
    monkeypatch.setattr(repair.Controller, 'wait_ready', lambda self: None)
    calls = []
    def upscale(self, src, dst, scale=4):
        calls.append((self.denoise, Path(src).name, self.prompt))
        with Image.open(src) as native:
            up = native.resize((native.width*scale, native.height*scale))
        info = PngImagePlugin.PngInfo()
        info.add_text('prompt', json.dumps(self.graph('fixture', up.width, up.height)))
        up.save(dst, pnginfo=info)
        raise RuntimeError('download completed, then connection closed')
    monkeypatch.setattr(repair.Comfy, 'upscale', upscale)
    report = repair.Controller(root, out, command_retries=2).run()
    assert report['jobs'][job.name]['accepted'] == 2
    assert [(c[0], c[1]) for c in calls] == [(0.55, 'static-00000.png'),
                                             (0.55, 'anim-00000.png')]
    assert calls[0][2] != calls[1][2]
    repair.Controller(root, out).run()
    assert len(calls) == 2


def test_batch_multiple_sheets_resume_after_later_sheet(fixture, monkeypatch):
    root, out, job, original, bad = _batch_fixture(fixture, monkeypatch)
    from wasteland_hd.gamefiles import Frame, Frm
    from wasteland_hd import gamefiles
    large = Frame(120, 120, 0, 0, bytes([1]) * (120*120))
    monkeypatch.setattr(gamefiles, 'read_frm',
                        lambda raw: Frm(10, 0, 1, [0]*6, [0]*6, [[large]]*6))
    groups = read_json(job/'inventory.json')['groups']
    calls = []
    from PIL import PngImagePlugin
    monkeypatch.setattr(repair.Controller, 'wait_ready', lambda self: None)
    def upscale(self, src, dst, scale=4):
        calls.append(Path(src).name)
        with Image.open(src) as native:
            up = native.resize((native.width*scale, native.height*scale))
        info = PngImagePlugin.PngInfo()
        info.add_text('prompt', json.dumps(self.graph('fixture', up.width, up.height)))
        up.save(dst, pnginfo=info)
        if len(calls) == 3:
            raise KeyboardInterrupt('after third sheet output')
    monkeypatch.setattr(repair.Comfy, 'upscale', upscale)
    controller = repair.Controller(root, out)
    with pytest.raises(KeyboardInterrupt):
        controller.render_batch(job.name, groups, list(groups), 0, 0.55)
    result = controller.render_batch(job.name, groups, list(groups), 0, 0.55)
    assert calls == ['static-00000.png', 'static-00001.png', 'static-00002.png']
    assert all(not isinstance(item, RuntimeError) for item in result.values())
    assert len(list((out/'jobs'/job.name/'attempts'/'batch'/'denoise-0').glob('*-render.png'))) == 3


def test_sprite_direction_gates_by_kind_and_falls_back_to_original_art(tmp_path, monkeypatch):
    root, out = tmp_path/'production', tmp_path/'repair'
    root.mkdir()
    (root/'plan.json').write_text(json.dumps({'game':'fixture'}))
    original = Image.new('RGBA',(16,20),(110,100,90,255))
    good = original.resize((32,40),Image.Resampling.NEAREST)
    rel = 'art/scenery/toy/0_0.png'
    expected = {'rel':rel,'width':32,'height':40,'alpha':digest(good.getchannel('A').tobytes())}
    group = {'art':'art/scenery/toy.frm','direction':0,'frames':[expected],'animated':False}
    monkeypatch.setattr(repair,'Game',lambda game: SimpleNamespace())
    monkeypatch.setattr(repair.qa,'original_frame',lambda game,cache,r: original)
    monkeypatch.setattr(repair.invention,'cached_adjacency',lambda *a,**kw: [])
    controller = repair.Controller(root,out)
    controller.seams = {}
    def no_render(*args,**kwargs):
        raise RuntimeError('no comfyui in tests')
    monkeypatch.setattr(repair.Controller,'render',no_render)
    work = tmp_path/'scenery-0000'
    result = controller.process_group(work,'art/scenery/toy/0',group,False)
    assert result['gate']['frames'][rel]['kind'] == 'scenery'
    assert result['accepted'] is True
    assert result['fallback'] == 'original_game_art'
    with Image.open(out/'jobs'/'scenery-0000'/'hd'/rel) as saved:
        assert list(saved.getdata()) == list(good.getdata())


def _wall_fixture(tmp_path, monkeypatch):
    """Two touching wall directions; wa's art breaks the junction the source does not."""
    from wasteland_hd import repair as r

    def brick():
        image = Image.new('RGBA', (40, 60), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 39, 59), fill=(150, 130, 110, 255))
        for y in range(0, 60, 6):
            draw.rectangle((0, y, 39, y + 1), fill=(110, 90, 75, 255))
        return image

    wa, wb = 'art/walls/wa/0_0.png', 'art/walls/wb/0_0.png'
    originals = {wa: brick(), wb: brick()}
    good = {rel: im.resize((80, 120), Image.Resampling.NEAREST) for rel, im in originals.items()}
    # The break: wa's contact edge is much brighter than anything the source shows.
    bad = good[wa].copy()
    pixels = bad.load()
    for x in range(72, 80):
        for y in range(120):
            red, green, blue, alpha = pixels[x, y]
            if alpha:
                pixels[x, y] = (min(255, red + 75), min(255, green + 75), min(255, blue + 75), alpha)
    root, out = tmp_path / 'production', tmp_path / 'repair'
    root.mkdir()
    (root / 'plan.json').write_text(json.dumps({'game': 'fixture'}))
    groups, works = {}, {}
    for job, rel, image in (('walls-0000', wa, bad), ('walls-0001', wb, good[wb])):
        work = tmp_path / job
        target = work / 'hd' / rel
        target.parent.mkdir(parents=True)
        image.save(target)
        expected = {'rel': rel, 'width': 80, 'height': 120,
                    'alpha': digest(image.getchannel('A').tobytes())}
        key = rel.rsplit('/', 1)[0] + '/0'
        group = {'art': rel.rsplit('/', 1)[0] + '.frm', 'direction': 0,
                 'frames': [expected], 'animated': False}
        groups[key] = group
        works[key] = work
        (work / 'inventory.json').write_text(json.dumps({'groups': {key: group}}))
        (work / 'state.json').write_text(json.dumps(
            {'status': 'needs_review', 'groups': {key: {'complete': False}}}))
        (work / 'coverage.json').write_text('{"status":"needs_review"}')
    junction = {'map': 'walls.map', 'elevation': 0,
                'a': {'rel': wa, 'x': 0, 'y': 0, 'w': 40, 'h': 60},
                'b': {'rel': wb, 'x': 40, 'y': 0, 'w': 40, 'h': 60},
                'dx': 40, 'dy': 0, 'same': False, 'contact': 60}
    monkeypatch.setattr(r, 'Game', lambda game: SimpleNamespace())
    monkeypatch.setattr(r.qa, 'original_frame', lambda game, cache, rel: originals[rel])
    monkeypatch.setattr(r.invention, 'cached_adjacency', lambda *a, **kw: [])
    monkeypatch.setattr(r.invention, 'cached_wall_pairs', lambda *a, **kw: [junction])
    calls = []

    def fake_render(self, job_name, key, group, step, denoise):
        calls.append(denoise)
        dest = out / f'candidate-{job_name}-{step}'
        rel = group['frames'][0]['rel']
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        (bad if rel == wa else good[rel]).save(path)
        return dest, {'kind': 'reduced_denoise', 'denoise': denoise}

    monkeypatch.setattr(r.Controller, 'render', fake_render)
    return SimpleNamespace(root=root, out=out, wa=wa, wb=wb, good=good, bad=bad,
                           originals=originals, groups=groups, works=works, calls=calls)


def _columns(image, x0, x1=None):
    """Pixels of an 80x120 2x frame between columns x0 and x1."""
    data = list(image.convert('RGBA').getdata())
    return [data[y * 80 + x] for y in range(120) for x in range(x0, x1 or 80)]


def test_wall_junction_enters_ladder_and_locks_before_fallback(tmp_path, monkeypatch):
    fx = _wall_fixture(tmp_path, monkeypatch)
    controller = repair.Controller(fx.root, fx.out)
    controller.seam_index()
    # The neighbour shipped earlier, so the gate can see the junction now.
    controller.hd_index[fx.wb] = fx.works['art/walls/wb/0'] / 'hd' / fx.wb
    key = 'art/walls/wa/0'
    result = controller.process_group(fx.works[key], key, fx.groups[key], False)
    assert fx.calls == [0.55, 0.4], 'an unmatched junction re-enters the denoise ladder'
    assert result['accepted']
    assert result['fallback'] == 'junction_lock'
    assert result['gate']['frames'][fx.wa]['verdict'] != 'unmatched_wall'
    with Image.open(fx.out / 'jobs' / 'walls-0000' / 'hd' / fx.wa) as saved:
        # The junction band is exactly the source art again; the rest is HD,
        # with a feather only in the 10 px between them.
        assert _columns(saved, 74) == _columns(fx.good[fx.wa], 74)
        assert _columns(saved, 0, 60) == _columns(fx.bad, 0, 60)


def test_reconciliation_checks_a_junction_the_ladder_never_saw(tmp_path, monkeypatch):
    from wasteland_hd import invention
    fx = _wall_fixture(tmp_path, monkeypatch)
    controller = repair.Controller(fx.root, fx.out)
    controller.seam_index()
    key_a, key_b = 'art/walls/wa/0', 'art/walls/wb/0'
    # Neither neighbour is accepted yet when either direction is processed, so
    # the ladder cannot see the junction; only reconciliation can.
    result_a = controller.process_group(fx.works[key_a], key_a, fx.groups[key_a], False)
    result_b = controller.process_group(fx.works[key_b], key_b, fx.groups[key_b], False)
    assert result_a['accepted'] and result_b['accepted']
    assert result_a['fallback'] is None
    manifest_a = {'groups': {key_a: fx.groups[key_a]}}
    manifest_b = {'groups': {key_b: fx.groups[key_b]}}
    jobs = [(fx.works[key_a], {'groups': {}}, manifest_a),
            (fx.works[key_b], {'groups': {}}, manifest_b)]
    results = {(fx.works[key_a].name, key_a): result_a,
               (fx.works[key_b].name, key_b): result_b}
    controller.reconcile_walls(jobs, results)
    updated = results[(fx.works[key_a].name, key_a)]
    assert updated['fallback'] == 'junction_lock', 'the shipped junction is corrected'
    with Image.open(fx.out / 'jobs' / 'walls-0000' / 'hd' / fx.wa) as saved_a:
        assert _columns(saved_a, 74) == _columns(fx.good[fx.wa], 74)
        with Image.open(controller.hd_index[fx.wb]) as saved_b:
            delta = invention.wall_seam_delta(saved_a, saved_b, fx.originals[fx.wa],
                                              fx.originals[fx.wb], {'dx': 40, 'dy': 0})
    assert delta <= invention.WALL_SEAM_DELTA_LIMIT


def test_junction_lock_leaves_a_clean_frame_untouched(tmp_path):
    original = Image.new('RGBA', (20, 20), (100, 90, 80, 255))
    hd = original.resize((40, 40), Image.Resampling.NEAREST)
    band = None
    assert list(repair.junction_lock(hd, original, band).getdata()) == list(hd.getdata())
    import numpy as np
    mask = np.zeros((40, 40), bool)
    mask[:, 38:] = True
    locked = repair.junction_lock(hd, original, mask)
    assert list(locked.getdata())[:38 * 40] == list(hd.getdata())[:38 * 40]
    assert list(locked.getdata())[38 * 40:] == list(original.resize((40, 40),
    Image.Resampling.NEAREST).getdata())[38 * 40:]
