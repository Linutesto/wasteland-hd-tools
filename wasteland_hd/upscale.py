"""Upscaling sheets 2x. `qwen`: Qwen-Image-Edit 2511 through a running ComfyUI
(the model Yan picked in the Arroyo comparison); `lanczos`: no model, for
trying the pipeline on any computer."""
import json
import os
import time
import urllib.request
import uuid

from PIL import Image

PROMPT = (
    "This is a sheet of separate sprites from the isometric 1990s game Fallout 2 on a flat grey background. "
    "Upscale it to high resolution, like remastered HD game art. Keep every sprite exactly where it is, with the "
    "same outline, shape, colours, lighting and perspective. Add crisp fine detail to sand, rock, wood, thatch, "
    "cloth, metal and skin. Keep the background flat grey. Do not add, remove or merge objects."
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
    def __init__(self, url="http://127.0.0.1:8188", steps=30, cfg=4.0, seed=42, models=QWEN_MODELS, lora=None):
        self.url, self.steps, self.cfg, self.seed, self.models = url.rstrip("/"), steps, cfg, seed, models
        # Lightning LoRA (lightx2v): 4 or 8 steps at cfg 1 instead of 30 at cfg 4.
        self.lora = lora

    def _post_json(self, path, body):
        req = urllib.request.Request(self.url + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req))

    def _upload(self, path):
        boundary = uuid.uuid4().hex
        name = f"wasteland_hd_{uuid.uuid4().hex[:8]}.png"
        with open(path, "rb") as f:
            payload = f.read()
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
                f"Content-Type: image/png\r\n\r\n").encode() + payload + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(self.url + "/upload/image", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
        return json.load(urllib.request.urlopen(req))["name"]

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
            "pos": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["clip", 0], "prompt": PROMPT, "vae": ["vae", 0], "image1": ["scaled", 0]}},
            "neg": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["clip", 0], "prompt": NEGATIVE, "vae": ["vae", 0], "image1": ["scaled", 0]}},
            "enc": {"class_type": "VAEEncode", "inputs": {"pixels": ["scaled", 0], "vae": ["vae", 0]}},
            "ks": {"class_type": "KSampler", "inputs": {"model": ["ms", 0], "positive": ["pos", 0], "negative": ["neg", 0], "latent_image": ["enc", 0],
                                                        "seed": self.seed, "steps": self.steps, "cfg": self.cfg, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
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
        while True:
            history = json.load(urllib.request.urlopen(f"{self.url}/history/{prompt_id}"))
            if prompt_id in history:
                entry = history[prompt_id]
                if entry["status"].get("status_str") == "error":
                    raise RuntimeError(json.dumps(entry["status"]["messages"])[-1500:])
                for out in entry["outputs"].values():
                    for image in out.get("images", []):
                        q = urllib.parse.urlencode({"filename": image["filename"], "subfolder": image.get("subfolder", ""), "type": image["type"]})
                        with urllib.request.urlopen(f"{self.url}/view?{q}") as r, open(dst, "wb") as f:
                            f.write(r.read())
                        return
                raise RuntimeError("ComfyUI returned no image")
            time.sleep(1)


import urllib.parse  # noqa: E402  (used in Comfy.upscale)
