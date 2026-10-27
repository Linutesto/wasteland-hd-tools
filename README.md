# Wasteland HD tools

Make your own HD art for **Wasteland Reborn HD**, the Fallout 2 phone port
([Wasteland Reborn](https://github.com/Linutesto/wasteland-reborn-installer)),
from **your own copy of Fallout 2**. The tools read the game's art, redraw it
at twice the resolution with an AI image model on your own graphics card, and
pack it as `hd.zip` for the game.

No Fallout 2 file is included here, and nothing here sends your files
anywhere: the model runs on your computer.

## Play it (you need Fallout 2)

You need your own Fallout 2 from Steam, GOG or Epic. Without it, nothing
here does anything.

1. Install **Wasteland Reborn** with its Windows installer
   ([download](https://github.com/Linutesto/wasteland-reborn-installer/releases/latest)).
   It finds your Fallout 2 and can put the game on your phone.
2. Get the ready-made HD packs (no graphics card needed):
   ```sh
   pip install pillow
   python -m wasteland_hd install --game "%LOCALAPPDATA%\WastelandReborn\game"
   ```
   It checks that the folder holds a real Fallout 2 install, then downloads
   the packs from [wasteland-hd-assets](https://github.com/Linutesto/wasteland-hd-assets),
   verifies their checksums and unpacks them into the game's `data/` folder.
   Run it again later for new packs.

The HD engine that draws these packs (Wasteland Reborn HD, Android app and
Windows) is still being built; until it ships, the packs are for testing.
Want your own look instead? Make your own packs below.

## What you need

- Fallout 2 (Steam, GOG or Epic): the folder with `master.dat` and `critter.dat`.
- Python 3.10+ with Pillow: `pip install pillow`.
- To upscale with AI: an NVIDIA graphics card with **24 GB** of memory (RTX 3090/4090/5090) and [ComfyUI](https://github.com/comfyanonymous/ComfyUI) with these files in its `models/` folder:

| ComfyUI folder | File | From (in `split_files/`) |
| --- | --- | --- |
| `diffusion_models/` | `qwen_image_edit_2511_fp8mixed.safetensors` | [Comfy-Org/Qwen-Image-Edit_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI) |
| `text_encoders/` | `qwen_2.5_vl_7b_fp8_scaled.safetensors` | [Comfy-Org/Qwen-Image_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI) |
| `vae/` | `qwen_image_vae.safetensors` | [Comfy-Org/Qwen-Image_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI) |
| `loras/` (optional, 5× faster) | `Qwen-Image-Edit-2511-Lightning-8steps-V1.0-bf16.safetensors` | [lightx2v/Qwen-Image-Edit-2511-Lightning](https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning) (top folder) |

No such card? `--backend lanczos` runs the whole pipeline with a plain
resize, so you can try everything, and the art packs published separately
need no card at all.

## Make a pack

```sh
# Arroyo, the first village, start to finish (ComfyUI running on :8188):
python -m wasteland_hd all --game "C:/Games/Fallout 2" --map arvillag.map --work work --out arroyo-hd.zip

# --quality auto (the default): the 8-step Lightning LoRA for still art, full
# 30 steps for animations (Lightning drops tiny details like flies unevenly
# between frames, which flickers). --quality fast / full force one mode.
python -m wasteland_hd all --game "C:/Games/Fallout 2" --map arvillag.map --work work --quality full

# Every floor tile in the game:
python -m wasteland_hd all --game "C:/Games/Fallout 2" --type tiles --work tiles --out tiles-hd.zip
```

The steps also run one at a time (`list`, `sheets`, `upscale`, `cut`,
`pack`), and every step can be re-run: finished sheets are skipped, so
an interrupted run continues where it stopped.

### Fix floor tile seams after AI upscaling

Diffusion can redraw neighbouring isometric floor tiles with incompatible
lighting, textures and details. Run a deterministic seam pass on the **cut**
frames before packing them (extra dependencies: `pip install numpy scipy`).
It blends invented large-scale colour/shape changes back toward Fallout 2's
own tile art and retains Qwen's high-frequency detail inside the tile.
The original colour and alpha are matched exactly at tile edges. It writes
to a **separate directory** so the original AI output remains available:

```sh
python -m wasteland_hd seams --game "C:/Games/Fallout 2" \
  --source work/hd --out work/seamfix/hd
```

Use the resulting `work/seamfix/hd/art/tiles/` in place of `work/hd/art/tiles/`
when assembling the final pack; the other art categories stay unchanged.
Defaults: 16 HD-pixel edge feather, 8-pixel texture radius, 100% microdetail
and 25% AI macro drift. Reusable floor tiles share one original reference;
perfectly custom **map-wide** redraws would need position-specific tile art
and an engine lookup keyed by map coordinates.

**How it works.** Small sprites are packed onto 512×512 sheets on a flat
background (one model call per sheet instead of per sprite). Each sheet is
redrawn at 1024×1024 by Qwen-Image-Edit 2511, cut back into frames, and the
original transparency (scaled 2×) is put back, so anything the model drew in
the background is dropped. Output: `hd/art/<type>/<name>/<direction>_<frame>.png`,
exactly 2× each original frame, which the HD engine loads in place of the
original frame when present.

On an RTX 4090, one sheet takes about 82 s (30 steps) or 15 s (8-step
Lightning). Arroyo is 497 frames on 10 sheets.

## Install a pack

Phone: copy the `.zip` into `Android/data/io.github.imbaner.fallout2ce.hd/files/profiles/<profile>/data/` and
unzip it there (or let the Wasteland HD app's updater do it). PC: unzip
into the game folder's `data/`.

## Legal

Wasteland Reborn and these tools are free fan projects, not affiliated with
or endorsed by Bethesda Softworks, ZeniMax or Interplay. Fallout is a
trademark of Bethesda Softworks LLC. You need your own legally bought copy
of Fallout 2. The HD art these tools make is derived from that copy: share
it only for free, credit the original game, and never sell it.

The tools' own code is MIT-licensed (see `LICENSE`).
