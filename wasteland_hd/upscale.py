"""Upscaling sheets 2x. `qwen`: Qwen-Image-Edit 2511 through a running ComfyUI
(the model Yan picked in the Arroyo comparison); `lanczos`: no model, for
trying the pipeline on any computer."""
import json
import os
import time
import urllib.request
import uuid
import hashlib

from PIL import Image

PROMPT = (
    "This is a sheet of separate sprites from the isometric 1990s game Fallout 2 on a flat grey background. "
    "Upscale it to high resolution, like remastered HD game art. Keep every sprite exactly where it is, with the "
    "same outline, shape, colours, lighting and perspective. Add crisp fine detail to sand, rock, wood, thatch, "
    "cloth, metal and skin. Keep the background flat grey. Do not add, remove or merge objects."
)
# Critter sheets (sheets.pack by_animation): one row per animation direction.
# Identity lock (docs/CRITTER-PROMPT-REPORT.md): naming "face, hair, beard, skin"
# here made the model *draw* them on helmeted critters, and at denoise 1.0 it also
# redrew the suit's bulk.  The wording never names facial features and only allows
# detail inside shapes the input already has; production renders animation sheets
# at the reduced denoise in production.SETTINGS["animation_denoise"].
ANIMATION_PROMPT = (
    "This is a sheet of animation frames from the isometric 1990s game Fallout 2 on a flat grey background. "
    "Each row shows consecutive frames of the same character in one movement. Upscale it to high resolution, "
    "like remastered HD game art. Redraw every sprite as exactly the character the input shows: the same "
    "costume, headgear, gear, body proportions, patterns, colours and lighting in every frame of a row; only "
    "the pose changes. Add finer detail only inside the shapes the input already has; never redraw, add, "
    "remove or reshape any part of the character or what it wears. Keep every sprite exactly where it is, "
    "with the same outline, shape and perspective. Keep the background flat grey. "
    "Do not add, remove or merge objects."
)
NEGATIVE = "blurry, smeared, oversmoothed, watercolor, new objects, text, watermark, changed layout, merged sprites"

LIGHTNING = {
    4: "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors",
    8: "Qwen-Image-Edit-2511-Lightning-8steps-V1.0-bf16.safetensors",
}

QWEN_MODELS = {
    "unet": "qwen_image_edit_2511_fp8mixed.safetensors",
    "clip": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
    "vae": "qwen_image_vae.safetensors",
}


def lanczos(src, dst, scale=2):
    img = Image.open(src)
    img.resize((img.width * scale, img.height * scale), Image.LANCZOS).save(dst)


class Comfy:
    def __init__(self, url="http://127.0.0.1:8188", steps=30, cfg=4.0, seed=42, models=QWEN_MODELS, lora=None,
                 prompt=PROMPT, denoise=1.0):
        self.url, self.steps, self.cfg, self.seed, self.models = url.rstrip("/"), steps, cfg, seed, models
        self.prompt = prompt
        # KSampler denoise: 1.0 regenerates, lower keeps the lanczos input's
        # structure (the repair ladder redraws failed directions at 0.55/0.4).
        self.denoise = denoise
        # Lightning LoRA (lightx2v): 4 or 8 steps at cfg 1 instead of 30 at cfg 4.
        self.lora = lora
        self.timeout = 1800

    def _post_json(self, path, body):
        req = urllib.request.Request(self.url + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=60))

    def _upload(self, path):
        boundary = uuid.uuid4().hex
        name = f"wasteland_hd_{uuid.uuid4().hex[:8]}.png"
        with open(path, "rb") as f:
            payload = f.read()
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
                f"Content-Type: image/png\r\n\r\n").encode() + payload + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(self.url + "/upload/image", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
        return json.load(urllib.request.urlopen(req, timeout=60))["name"]

    def graph(self, image_name, width, height):
        m = self.models
        return {
            "src": {"class_type": "LoadImage", "inputs": {"image": image_name}},
            "scaled": {"class_type": "ImageScale", "inputs": {"image": ["src", 0], "upscale_method": "lanczos", "width": width, "height": height, "crop": "disabled"}},
            "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": m["unet"], "weight_dtype": "default"}},
            "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": m["clip"], "type": "qwen_image"}},
            "vae": {"class_type": "VAELoader", "inputs": {"vae_name": m["vae"]}},
            "lora": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["unet", 0], "lora_name": self.lora or "", "strength_model": 1.0}},
            "ms": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["lora" if self.lora else "unet", 0], "shift": 3.0}},
            "pos": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["clip", 0], "prompt": self.prompt, "vae": ["vae", 0], "image1": ["scaled", 0]}},
            "neg": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["clip", 0], "prompt": NEGATIVE, "vae": ["vae", 0], "image1": ["scaled", 0]}},
            "enc": {"class_type": "VAEEncode", "inputs": {"pixels": ["scaled", 0], "vae": ["vae", 0]}},
            "ks": {"class_type": "KSampler", "inputs": {"model": ["ms", 0], "positive": ["pos", 0], "negative": ["neg", 0], "latent_image": ["enc", 0],
                                                        "seed": self.seed, "steps": self.steps, "cfg": self.cfg, "sampler_name": "euler", "scheduler": "simple", "denoise": self.denoise}},
            "dec": {"class_type": "VAEDecode", "inputs": {"samples": ["ks", 0], "vae": ["vae", 0]}},
            "save": {"class_type": "SaveImage", "inputs": {"images": ["dec", 0], "filename_prefix": "wasteland_hd"}},
        }

    def upscale(self, src, dst, scale=2):
        img = Image.open(src)
        name = self._upload(src)
        graph = self.graph(name, img.width * scale, img.height * scale)
        if not self.lora:
            del graph["lora"]
        prompt_id = self._post_json("/prompt", {"prompt": graph})["prompt_id"]
        deadline = time.monotonic() + self.timeout
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"ComfyUI prompt {prompt_id} exceeded {self.timeout}s")
            history = json.load(urllib.request.urlopen(f"{self.url}/history/{prompt_id}", timeout=60))
            if prompt_id in history:
                entry = history[prompt_id]
                if entry["status"].get("status_str") == "error":
                    raise RuntimeError(json.dumps(entry["status"]["messages"])[-1500:])
                for out in entry["outputs"].values():
                    for image in out.get("images", []):
                        q = urllib.parse.urlencode({"filename": image["filename"], "subfolder": image.get("subfolder", ""), "type": image["type"]})
                        with urllib.request.urlopen(f"{self.url}/view?{q}", timeout=120) as r, open(dst, "wb") as f:
                            f.write(r.read())
                        return
                raise RuntimeError("ComfyUI returned no image")
            time.sleep(1)


def render_matches(path, source, scale, model=None):
    """Resume only intact output produced with the requested model settings.

    ComfyUI embeds its graph in PNGs, including older renders without our
    source-hash receipt. Lanczos receipts identify the source bytes too.
    """
    try:
        with Image.open(source) as native, Image.open(path) as rendered:
            if rendered.size != (native.width * scale, native.height * scale):
                return False
            graph = json.loads(rendered.info.get("prompt", "{}"))
            rendered.verify()
        receipt = path + ".render.json"
        if os.path.exists(receipt):
            with open(receipt) as f:
                saved = json.load(f)
            if saved["source_sha256"] != hashlib.sha256(open(source, "rb").read()).hexdigest():
                return False
            if saved["backend"] != ("qwen" if model else "lanczos"):
                return False
        elif model is None:
            return False
        if model is None:
            return True
        # Compare every inference node, including sampler/scheduler/denoise,
        # AuraFlow shift, CLIP/VAE, both prompts and LoRA strength. A matching
        # seed + step count alone can accidentally certify a different style.
        expected = model.graph("__source__", native.width * scale, native.height * scale)
        if not model.lora:
            del expected["lora"]
        return (set(graph) == set(expected)
                and all(graph[key] == node for key, node in expected.items()
                        if key not in ("src", "save"))
                and graph["src"].get("class_type") == "LoadImage"
                and graph["save"].get("class_type") == "SaveImage")
    except (OSError, ValueError, KeyError, TypeError, SyntaxError):
        return False


def write_render_receipt(path, source, model=None):
    with open(source, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    temporary = path + ".render.json.part"
    with open(temporary, "w") as f:
        json.dump({"source_sha256": digest, "backend": "qwen" if model else "lanczos"}, f)
    os.replace(temporary, path + ".render.json")


import urllib.parse  # noqa: E402  (used in Comfy.upscale)
