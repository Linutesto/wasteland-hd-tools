"""wasteland-hd: make your own HD art pack for Wasteland Reborn HD from your own Fallout 2.

  python -m wasteland_hd list    --game DIR [--map arvillag.map | --type tiles ...]
  python -m wasteland_hd sheets  --game DIR --work WORK [--map ... | --type ...]
  python -m wasteland_hd upscale --work WORK [--backend qwen|lanczos] [--comfy URL]
  python -m wasteland_hd cut     --work WORK
  python -m wasteland_hd pack    --work WORK --out hd.zip
  python -m wasteland_hd all     --game DIR --work WORK [...]   (sheets, upscale, cut, pack)

Every step can be re-run: finished sheets are skipped.
"""
import argparse
import glob
import os
import sys
import time
import zipfile

from . import sheets as sh
from .gamefiles import ART_TYPES, Game, map_art
from .upscale import LIGHTNING, Comfy, lanczos


def select_art(game, args):
    arts = set()
    for m in args.map or []:
        arts.update(map_art(game, m))
    for t in args.type or []:
        index = ART_TYPES.index(t)
        arts.update(f"art/{t}/{name}" for name in game.art_list(index))
    if not arts:
        sys.exit("choose art with --map and/or --type")
    return sorted(arts)


def cmd_list(args):
    game = Game(args.game)
    for art in select_art(game, args):
        print(art)


def cmd_sheets(args):
    game = Game(args.game)
    frames = sh.frames_of(game, select_art(game, args))
    os.makedirs(os.path.join(args.work, "sheets"), exist_ok=True)
    for i, items in enumerate(sh.pack(frames)):
        path = os.path.join(args.work, "sheets", f"sheet{i:05d}.png")
        size = sh.render_sheet(items, game.palette(), path)
        print(f"{path}: {len(items)} frames, {size[0]}x{size[1]}")


def cmd_upscale(args):
    os.makedirs(os.path.join(args.work, "upscaled"), exist_ok=True)
    if args.lightning:
        comfy = Comfy(args.comfy, steps=args.lightning, cfg=1.0, lora=LIGHTNING[args.lightning])
    else:
        comfy = Comfy(args.comfy, steps=args.steps)
    comfy = comfy if args.backend == "qwen" else None
    todo = sorted(glob.glob(os.path.join(args.work, "sheets", "sheet*.png")))
    todo = [p for p in todo if not p.endswith(".mask.png")]
    for n, src in enumerate(todo, 1):
        dst = os.path.join(args.work, "upscaled", os.path.basename(src))
        if os.path.exists(dst):
            continue
        t = time.time()
        (comfy.upscale if comfy else lanczos)(src, dst + ".part.png")
        os.replace(dst + ".part.png", dst)
        print(f"[{n}/{len(todo)}] {os.path.basename(src)} {time.time() - t:.0f}s", flush=True)


def cmd_cut(args):
    total = 0
    for src in sorted(glob.glob(os.path.join(args.work, "upscaled", "sheet*.png"))):
        total += len(sh.cut_sheet(os.path.join(args.work, "sheets", os.path.basename(src)), src, args.work))
    print(f"{total} HD frames in {os.path.join(args.work, 'hd')}")


def cmd_pack(args):
    root = os.path.join(args.work, "hd")
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_STORED) as z:  # PNGs are already compressed
        for path in sorted(glob.glob(os.path.join(root, "**", "*.png"), recursive=True)):
            z.write(path, os.path.relpath(path, args.work))
    print(f"{args.out}: {os.path.getsize(args.out) / 1e6:.1f} MB")


def main():
    ap = argparse.ArgumentParser(prog="wasteland-hd", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("list", "sheets", "upscale", "cut", "pack", "all"):
        p = sub.add_parser(name)
        if name in ("list", "sheets", "all"):
            p.add_argument("--game", required=True, help="Fallout 2 folder (with master.dat)")
            p.add_argument("--map", action="append", help="art used by this map, e.g. arvillag.map")
            p.add_argument("--type", action="append", choices=ART_TYPES, help="all art of a type")
        if name != "list":
            p.add_argument("--work", required=True, help="work folder")
        if name in ("upscale", "all"):
            p.add_argument("--backend", choices=("qwen", "lanczos"), default="qwen")
            p.add_argument("--comfy", default="http://127.0.0.1:8188")
            p.add_argument("--steps", type=int, default=30)
            p.add_argument("--lightning", type=int, choices=(4, 8), help="Qwen Lightning LoRA: 4 or 8 steps (much faster)")
        if name in ("pack", "all"):
            p.add_argument("--out", default="hd.zip")
    args = ap.parse_args()
    if args.cmd == "all":
        for step in (cmd_sheets, cmd_upscale, cmd_cut, cmd_pack):
            step(args)
    else:
        globals()["cmd_" + args.cmd](args)


if __name__ == "__main__":
    main()
