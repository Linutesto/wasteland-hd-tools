"""Production resume must distinguish full renders from Lightning and damaged output."""
import json

from PIL import Image, PngImagePlugin

from wasteland_hd.upscale import Comfy, render_matches, write_render_receipt


def render(tmp_path, model):
    source, output = tmp_path / "source.png", tmp_path / "output.png"
    Image.new("RGB", (16, 16), "red").save(source)
    info = PngImagePlugin.PngInfo()
    graph = model.graph("source.png", 64, 64)
    if model.lora is None:
        del graph["lora"]
    info.add_text("prompt", json.dumps(graph))
    Image.new("RGB", (64, 64), "red").save(output, pnginfo=info)
    return str(source), str(output)


def test_full_rejects_lightning_and_wrong_prompt(tmp_path):
    full = Comfy()
    src, dst = render(tmp_path, full)
    assert render_matches(dst, src, 4, full)
    assert not render_matches(dst, src, 2, full)
    assert not render_matches(dst, src, 4, Comfy(seed=7))
    assert not render_matches(dst, src, 4, Comfy(prompt="different animation prompt"))
    src, dst = render(tmp_path, Comfy(steps=8, cfg=1, lora="lightning"))
    assert not render_matches(dst, src, 4, full)


def test_receipt_detects_changed_source_and_corruption(tmp_path):
    model = Comfy()
    src, dst = render(tmp_path, model)
    write_render_receipt(dst, src, model)
    assert render_matches(dst, src, 4, model)
    Image.new("RGB", (16, 16), "blue").save(src)
    assert not render_matches(dst, src, 4, model)
    with open(dst, "wb") as f:
        f.write(b"broken PNG")
    assert not render_matches(dst, src, 4, model)


def test_lanczos_requires_matching_receipt(tmp_path):
    src, dst = render(tmp_path, Comfy())
    assert not render_matches(dst, src, 4)
    write_render_receipt(dst, src)
    assert render_matches(dst, src, 4)
