import hashlib
import json
import zipfile

import numpy as np
import pytest
from PIL import Image
from wasteland_hd import select


# ---------- synthetic trees ----------

def native_image(seed=0, size=(8, 8)):
    """A native-resolution RGBA original with opaque alpha."""
    rng = np.random.default_rng(seed)
    pixels = rng.integers(0, 255, (size[1], size[0], 4), dtype=np.uint8)
    pixels[..., 3] = 255
    return Image.fromarray(pixels)


def spec_for(rel, native):
    """The inventory record production writes for one frame of a direction."""
    hd = native.resize((native.width * 2, native.height * 2), Image.Resampling.NEAREST)
    return {'rel': rel, 'width': hd.width, 'height': hd.height,
            'alpha': hashlib.sha256(hd.getchannel('A').tobytes()).hexdigest()}


def write_hd(root, rel, native, corrupt_alpha=False):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    hd = native.resize((native.width * 2, native.height * 2), Image.Resampling.NEAREST)
    if corrupt_alpha:
        pixels = np.array(hd)
        pixels[..., 3] = 0
        hd = Image.fromarray(pixels)
    hd.save(path)
    return path


def passing(source, detail, novelty=0.0, drift=0.0):
    """A gate-passing candidate: its frames all cleared the whole gate."""
    return {'source': source, 'root': '/root/' + source,
            'frames': {'f': {'features': {'detail': detail, 'novelty': novelty, 'drift': drift}}}}


def fallback_item(root='/out/fallback'):
    """What fallback() appends: original art, no gate measurements."""
    return {'source': 'original_game_art', 'root': root, 'frames': {}}


# ---------- the rule ----------

def test_features_and_score():
    original = Image.new('RGBA', (8, 8), (60, 60, 60, 255))
    for x in range(4):
        for y in range(8): original.putpixel((x,y),(20,20,20,255))
    hd = original.resize((16,16), Image.Resampling.NEAREST)
    f = select.features(hd, original, {'scale2': {'novel_fraction': 0}}, 0)
    assert f['novelty'] == 0
    assert f['detail'] == 0
    assert select.score({'detail': .5, 'novelty': .1, 'drift': .1}, (1,1,1)) == pytest.approx(.3)


def test_judge_cache_and_swap(tmp_path):
    orig = Image.new('RGBA', (4,4), 'red')
    a = orig.resize((8,8)); b = Image.new('RGBA',(8,8),'blue')
    budget = {'used': 0, 'limit': 2}; calls=[]
    def judge(png,key,model):
        calls.append(1); return 'A'
    assert select.judge_pair(orig,a,b,tmp_path,budget,'mock',judge) == 'A'
    assert select.judge_pair(orig,a,b,tmp_path,budget,'mock',judge) == 'A'
    assert len(calls) == 1
    assert select.judge_pair(orig,b,a,tmp_path,budget,'mock',judge) == 'A'
    assert budget['used'] == 2


def test_fit_weights():
    a={'detail': .8,'novelty':0.,'drift':0.}
    b={'detail': .1,'novelty':.4,'drift':.5}
    weights, agreement = select.fit_weights([(a,b,'A')])
    assert select.score(a,weights) > select.score(b,weights)
    assert agreement == 1


def test_passing_hd_never_loses_to_fallback():
    weights = (1., 1., .05)
    hd = passing('production', detail=0.02, novelty=0.10, drift=0.5)
    fb = fallback_item()
    # The HD candidate's fitted score is below the fallback's 0.0: under a plain
    # argmax over every entry the original art would win this direction.
    assert select.score(hd['frames']['f']['features'], weights) < 0
    chosen, reason, ordered = select.choose([hd, fb], weights)
    assert chosen is hd
    assert reason == 'gate then fitted detail score'
    assert ordered == [hd, fb]
    # Candidate discovery order must not matter either.
    chosen, reason, ordered = select.choose([fb, hd], weights)
    assert chosen is hd and ordered == [hd, fb]
    # With several passing candidates the score only ranks those.
    other = passing('repair_accepted', detail=0.4, novelty=0.0)
    chosen, reason, ordered = select.choose([hd, other, fb], weights)
    assert chosen is other
    assert ordered == [other, hd, fb]


def test_fallback_only_when_no_candidate_passed():
    fb = fallback_item()
    chosen, reason, ordered = select.choose([fb], (1., 1., .05))
    assert chosen is fb
    assert reason == 'no candidate passed the gate'
    assert ordered == [fb]


def test_all_roots_fail_falls_back(tmp_path):
    native = native_image(1)
    rel = 'art/scenery/box/0_0.png'
    group = {'frames': [spec_for(rel, native)]}
    originals = {rel: native}
    write_hd(tmp_path/'hd', rel, native, corrupt_alpha=True)
    assert select.candidates(group, [('production', tmp_path/'hd')], originals) == []
    found = select.candidates(group, [('production', tmp_path/'hd')], originals)
    found.append(select.fallback(group, originals, tmp_path/'fb'))
    chosen, reason, ordered = select.choose(found, (1., 1., .05))
    assert chosen['source'] == 'original_game_art'
    assert reason == 'no candidate passed the gate'
    assert (tmp_path/'fb'/rel).is_file()


def test_direction_is_one_candidate_end_to_end(tmp_path):
    """A root missing any frame of the direction is never a candidate."""
    first, second = native_image(2), native_image(3, (6, 10))
    rels = ['art/scenery/box/0_0.png', 'art/scenery/box/0_1.png']
    group = {'frames': [spec_for(rels[0], first), spec_for(rels[1], second)]}
    originals = {rels[0]: first, rels[1]: second}
    write_hd(tmp_path/'partial', rels[0], first)                    # only frame 0
    write_hd(tmp_path/'whole', rels[0], first)
    write_hd(tmp_path/'whole', rels[1], second)
    roots = [('production_attempt-00', tmp_path/'partial'),
             ('repair_accepted', tmp_path/'whole')]
    found = select.candidates(group, roots, originals)
    assert [c['source'] for c in found] == ['repair_accepted']
    assert set(found[0]['frames']) == set(rels)                     # all frames, one root
    found.append(select.fallback(group, originals, tmp_path/'fb'))
    chosen, reason, _ = select.choose(found, (1., 1., .05))
    assert chosen['source'] == 'repair_accepted'
    assert reason == 'gate then fitted detail score'
    # Nothing whole anywhere: the direction falls back as a whole.
    assert select.candidates(group, [('production_attempt-00', tmp_path/'partial')], originals) == []


# ---------- critter jobs ----------

def critter_tree(tmp_path):
    job = tmp_path/'production'/'jobs'/'critters-0000'
    attempt = job/'attempt-00'
    staged = attempt/'post-0'/'temporal'/'hd'
    rels = ['art/critters/hmwarra/0_0.png', 'art/critters/hmwarra/0_1.png']
    images = [native_image(4), native_image(5, (6, 12))]
    for root in (attempt/'hd', staged):
        for rel, image in zip(rels, images):
            write_hd(root, rel, image)
    (attempt/'post.json').write_text(json.dumps({'hd': str(staged)}))
    group = {'frames': [spec_for(rel, image) for rel, image in zip(rels, images)]}
    originals = dict(zip(rels, images))
    state = {'groups': {'art/critters/hmwarra/0': {}}}
    return job, attempt, staged, group, originals, state


def test_critter_attempt_roots_are_stage_outputs(tmp_path):
    job, attempt, staged, group, originals, state = critter_tree(tmp_path)
    repair = tmp_path/'repair'
    roots = dict(select.source_roots(tmp_path/'production', repair, job.name, critter=True))
    assert roots['production_attempt-00'] == staged      # temporal stage output, not the raw render
    assert attempt/'hd' not in roots.values()
    # A non-critter job keeps the plain attempt root.
    plain = dict(select.source_roots(tmp_path/'production', repair, job.name, critter=False))
    assert plain['production_attempt-00'] == attempt/'hd'
    # An attempt whose stage checkpoint is gone contributes no critter root.
    (attempt/'post.json').unlink()
    roots = dict(select.source_roots(tmp_path/'production', repair, job.name, critter=True))
    assert 'production_attempt-00' not in roots


def test_critter_flicker_verdict_is_a_gate_input(tmp_path):
    job, attempt, staged, group, originals, state = critter_tree(tmp_path)
    key = 'art/critters/hmwarra/0'
    # The verdict may live on the group or in the job's temporal table.
    state['groups'][key]['flicker'] = {'pass': False, 'reason': 'flicker_gate'}
    assert select.flicker_verdict(state, key)['pass'] is False
    state['temporal'] = {'directions': {key: {'pass': False, 'reason': 'flicker_gate'}}}
    del state['groups'][key]['flicker']
    assert select.flicker_verdict(state, key)['pass'] is False
    assert select.flicker_verdict({'groups': {}}, key) is None
    assert select.flicker_verdict({'groups': {key: {'fallback': 'x'}}}, key) is None

    roots = select.source_roots(tmp_path/'production', tmp_path/'repair', job.name, critter=True)
    # Failing verdict: production's staged frames are refused, so nothing passes...
    assert select.candidates(group, roots, originals,
                             refuse=select.production_refuser(select.flicker_verdict(state, key))) == []
    # ...a passing verdict keeps them eligible...
    state['groups'][key]['flicker'] = {'pass': True, 'reason': None}
    del state['temporal']
    refuse = select.production_refuser(select.flicker_verdict(state, key))
    assert refuse is None
    assert len(select.candidates(group, roots, originals, refuse=refuse)) == 1
    # ...and repair candidates stay eligible under a failed verdict: HD art never
    # loses to original art just because production's attempt flickered.
    write_hd(tmp_path/'repair'/'jobs'/job.name/'hd', group['frames'][0]['rel'], originals[group['frames'][0]['rel']])
    write_hd(tmp_path/'repair'/'jobs'/job.name/'hd', group['frames'][1]['rel'], originals[group['frames'][1]['rel']])
    state['groups'][key]['flicker'] = {'pass': False, 'reason': 'flicker_gate'}
    roots = select.source_roots(tmp_path/'production', tmp_path/'repair', job.name, critter=True)
    found = select.candidates(group, roots, originals,
                              refuse=select.production_refuser(select.flicker_verdict(state, key)))
    assert [c['source'] for c in found] == ['repair_accepted']
    found.append(select.fallback(group, originals, tmp_path/'fb'))
    chosen, reason, _ = select.choose(found, (1., 1., .05))
    assert chosen['source'] == 'repair_accepted'
    assert reason == 'gate then fitted detail score'


# ---------- --pack-out ----------

def build_selection_tree(tmp_path):
    jobs = tmp_path/'jobs'
    originals = {}
    frames = [('scenery-0000', 'art/scenery/box/0_0.png', 10),
              ('scenery-0000', 'art/scenery/box/0_1.png', 11),
              ('scenery-0001', 'art/scenery/door/0_0.png', 12)]
    for job, rel, seed in frames:
        native = native_image(seed)
        originals[rel] = native
        write_hd(jobs/job/'hd', rel, native)
    return jobs, originals


def test_pack_zip_format(tmp_path):
    jobs, originals = build_selection_tree(tmp_path)
    packs = tmp_path/'packs'
    written = select.write_packs(packs, jobs, originals)
    assert set(written) == {'scenery-0000', 'scenery-0001', 'scenery'}
    for name, info in written.items():
        archive = packs/(name + '.zip')
        assert archive.is_file() and info['bytes'] == archive.stat().st_size
        # The Android updater refuses ZIP64; assert_standard_zip is the pack builder's guard.
        assert select.assert_standard_zip(archive) == info['frames']
        with zipfile.ZipFile(archive) as z:
            names = z.namelist()
            assert names and all(n.startswith('hd/art/') for n in names)
            assert len(names) < 0xFFFF
            for entry in z.infolist():
                assert max(entry.file_size, entry.compress_size, entry.header_offset) < 0xFFFFFFFE
    # Per-job packs split the job ranges; the per-category pack holds them all.
    with zipfile.ZipFile(packs/'scenery.zip') as z:
        assert sorted(z.namelist()) == sorted(
            ['hd/art/scenery/box/0_0.png', 'hd/art/scenery/box/0_1.png', 'hd/art/scenery/door/0_0.png'])
    with zipfile.ZipFile(packs/'scenery-0000.zip') as z:
        assert len(z.namelist()) == 2


def test_pack_refuses_a_frame_the_gate_would_refuse(tmp_path):
    jobs, originals = build_selection_tree(tmp_path)
    rel = 'art/scenery/box/0_0.png'
    write_hd(jobs/'scenery-0000'/'hd', rel, originals[rel], corrupt_alpha=True)
    with pytest.raises(ValueError, match='alpha mismatch'):
        select.write_packs(tmp_path/'packs', jobs, originals)


def test_pack_refuses_a_set_that_needs_zip64(tmp_path, monkeypatch):
    monkeypatch.setattr(select, 'MAX_ZIP_ENTRIES', 2)
    entries = [('hd/art/a.png', b'a'), ('hd/art/b.png', b'b'), ('hd/art/c.png', b'c')]
    with pytest.raises(ValueError, match='standard ZIP'):
        select.write_zip(tmp_path/'too-big.zip', entries)


def test_pack_splits_a_category_too_big_for_one_standard_zip(tmp_path, monkeypatch, capsys):
    """Critters-scale categories ship as job-range packs, never as ZIP64."""
    jobs, originals = build_selection_tree(tmp_path)   # one category, two job ranges, 3 frames
    monkeypatch.setattr(select, 'MAX_ZIP_ENTRIES', 3)
    written = select.write_packs(tmp_path/'packs', jobs, originals)
    assert 'scenery' not in written                              # 3 entries >= the classic limit
    assert {'scenery-0000', 'scenery-0001'} <= set(written)       # the job ranges still ship
    assert not (tmp_path/'packs'/'scenery.zip').exists()
    assert 'scenery.zip skipped' in capsys.readouterr().out
