import pytest
from PIL import Image
from wasteland_hd import select


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
