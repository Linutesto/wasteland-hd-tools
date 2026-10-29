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
    def fake_render(self,job_name,key,group,step,denoise):
        calls.append(denoise)
        dest=out/f'candidate-{step}'
        path=dest/rel
        path.parent.mkdir(parents=True,exist_ok=True)
        (bad if step==0 else good).save(path)
        return dest,{'kind':'reduced_denoise','denoise':denoise}
    monkeypatch.setattr(repair.Controller,'render',fake_render)
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
    def fake_render(self,job_name,key,group,step,denoise):
        calls.append(denoise)
        dest=out/f'candidate-{step}'
        path=dest/rel
        path.parent.mkdir(parents=True,exist_ok=True)
        bad.save(path)
        return dest,{'kind':'reduced_denoise','denoise':denoise}
    monkeypatch.setattr(repair.Controller,'render',fake_render)
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
    def fake_render(self,job_name,key,group,step,denoise):
        dest=out/f'candidate-{step}'
        path=dest/rel
        path.parent.mkdir(parents=True,exist_ok=True)
        border.save(path)
        return dest,{'kind':'reduced_denoise','denoise':denoise}
    monkeypatch.setattr(repair.Controller,'render',fake_render)
    repair.Controller(root,out).run()
    row=read_json(out/'jobs/tiles-0000/coverage.json')['groups']['art/tiles/toy/0']
    assert row['fallback'] == 'edge_lock'
    assert row['accepted']
