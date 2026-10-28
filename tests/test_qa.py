"""qa: the least drifted candidate of each frame is kept, recoloured frames are set aside, never deleted."""
import json

from PIL import Image

from wasteland_hd import qa

RED = (200, 30, 30, 255)


def save(root, rel, colour):
    path = root / "hd" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (8, 8), colour).save(path)


def test_check_keeps_best_candidate_and_rejects_drift(tmp_path, monkeypatch):
    monkeypatch.setattr(qa, "original_frame", lambda _g, _c, _rel: Image.new("RGBA", (4, 4), RED))
    work, retry = tmp_path / "work", tmp_path / "retry"
    save(work, "art/misc/good/0_0.png", (205, 35, 28, 255))  # close: kept
    save(work, "art/misc/brown/0_0.png", (120, 80, 40, 255))  # recoloured, no retry: rejected
    save(work, "art/misc/fixed/0_0.png", (120, 80, 40, 255))  # recoloured, the retry is right
    save(retry, "art/misc/fixed/0_0.png", (198, 32, 30, 255))

    results = qa.check(None, str(work), also=[str(retry)], log=lambda _m: None)

    assert not results["art/misc/good/0_0.png"]["rejected"]
    assert results["art/misc/brown/0_0.png"]["rejected"]
    assert results["art/misc/fixed/0_0.png"] == {"shift": 1.33, "source": "../retry/hd", "rejected": False}
    assert (work / "rejected/hd/art/misc/brown/0_0.png").exists()
    assert not (work / "hd/art/misc/brown/0_0.png").exists()
    assert Image.open(work / "hd/art/misc/fixed/0_0.png").getpixel((0, 0)) == (198, 32, 30, 255)
    assert qa.rejected_frames(str(work)) == [("art/misc/brown.frm", 0, 0)]
    assert json.loads((work / "qa.json").read_text()) == results

    # Run again with a retry that fixes the rejected frame: it comes back.
    save(retry, "art/misc/brown/0_0.png", (201, 31, 29, 255))
    results = qa.check(None, str(work), also=[str(retry)], log=lambda _m: None)
    assert not results["art/misc/brown/0_0.png"]["rejected"]
    assert (work / "hd/art/misc/brown/0_0.png").exists()
    assert not (work / "rejected/hd/art/misc/brown/0_0.png").exists()


def test_colour_shift_ignores_transparent_pixels():
    orig = Image.new("RGBA", (2, 1), (0, 0, 0, 0))
    orig.putpixel((0, 0), RED)
    hd = Image.new("RGBA", (4, 2), (0, 255, 0, 255))
    hd.paste(Image.new("RGBA", (2, 2), RED), (0, 0))
    assert qa.colour_shift(hd, orig) == 0
    assert qa.colour_shift(hd, Image.new("RGBA", (2, 1), (0, 0, 0, 0))) is None
