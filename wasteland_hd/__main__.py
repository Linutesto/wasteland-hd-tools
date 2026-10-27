"""wasteland-hd: make your own HD art pack for Wasteland Reborn HD from your own Fallout 2.

  python -m wasteland_hd list    --game DIR [--map arvillag.map | --type tiles ...]
  python -m wasteland_hd sheets  --game DIR --work WORK [--map ... | --type ...]
  python -m wasteland_hd upscale --work WORK [--backend qwen|lanczos] [--comfy URL]
  python -m wasteland_hd cut     --work WORK
  python -m wasteland_hd seams   --game DIR --source WORK/hd --out FIXED/hd
  python -m wasteland_hd pack    --work WORK --out hd.zip
  python -m wasteland_hd all     --game DIR --work WORK [...]   (sheets, upscale, cut, pack)
  python -m wasteland_hd install --game DIR       (download the published packs; needs Fallout 2)

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


def sheet_paths(work, folder):
    paths = glob.glob(os.path.join(work, folder, "*.png"))
    return sorted(p for p in paths if not p.endswith((".mask.png", ".part.png")))


def cmd_list(args):
    game = Game(args.game)
    for art in select_art(game, args):
        print(art)


def cmd_sheets(args):
    game = Game(args.game)
    frames = sh.frames_of(game, select_art(game, args))
    os.makedirs(os.path.join(args.work, "sheets"), exist_ok=True)
    # Animated art (more than one frame) goes on its own sheets: the fast
    # Lightning mode drops tiny details (flies, sparks) unevenly from frame
    # to frame, which flickers, so `--quality auto` gives those full steps.
    animated = {art for art, _d, i, _f in frames if i > 0}
    for kind in ("static", "anim"):
        group = [f for f in frames if (f[0] in animated) == (kind == "anim")]
        for i, items in enumerate(sh.pack(group) if group else []):
            path = os.path.join(args.work, "sheets", f"{kind}-{i:05d}.png")
            size = sh.render_sheet(items, game.palette(), path)
            print(f"{path}: {len(items)} frames, {size[0]}x{size[1]}")


def cmd_upscale(args):
    os.makedirs(os.path.join(args.work, "upscaled"), exist_ok=True)
    fast = Comfy(args.comfy, steps=8, cfg=1.0, lora=LIGHTNING[8])
    full = Comfy(args.comfy, steps=args.steps)

    def model_for(sheet):
        if args.backend == "lanczos":
            return lanczos
        if args.quality == "fast" or (args.quality == "auto" and not os.path.basename(sheet).startswith("anim-")):
            return fast.upscale
        return full.upscale

    todo = sheet_paths(args.work, "sheets")
    for n, src in enumerate(todo, 1):
        dst = os.path.join(args.work, "upscaled", os.path.basename(src))
        if os.path.exists(dst):
            continue
        t = time.time()
        model_for(src)(src, dst + ".part.png")
        os.replace(dst + ".part.png", dst)
        print(f"[{n}/{len(todo)}] {os.path.basename(src)} {time.time() - t:.0f}s", flush=True)


def cmd_cut(args):
    total = 0
    for src in sheet_paths(args.work, "upscaled"):
        total += len(sh.cut_sheet(os.path.join(args.work, "sheets", os.path.basename(src)), src, args.work))
    print(f"{total} HD frames in {os.path.join(args.work, 'hd')}")


def cmd_seams(args):
    from .seams import reconcile_directory

    if os.path.realpath(args.source) == os.path.realpath(args.out):
        raise SystemExit("seams: --out must differ from --source to preserve the original HD art")
    count = reconcile_directory(Game(args.game), args.source, args.out, feather=args.feather,
                                sigma=args.sigma, detail=args.detail, macro=args.macro)
    print(f"{count} edge-reconciled floor tiles in {args.out}")


def cmd_pack(args):
    root = os.path.join(args.work, "hd")
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_STORED) as z:  # PNGs are already compressed
        for path in sorted(glob.glob(os.path.join(root, "**", "*.png"), recursive=True)):
            z.write(path, os.path.relpath(path, args.work))
    print(f"{args.out}: {os.path.getsize(args.out) / 1e6:.1f} MB")


def cmd_manifest(args):
    """Adds or updates a pack in packs.json (the list `install` and the HD app read)."""
    import hashlib
    import json

    digest = hashlib.sha256()
    with open(args.zip, "rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    with zipfile.ZipFile(args.zip) as z:
        frames = sum(1 for n in z.namelist() if n.endswith(".png"))
    try:
        with open(args.packs_json) as f:
            doc = json.load(f)
    except FileNotFoundError:
        doc = {"packs": []}
    entry = {"name": args.name, "file": os.path.basename(args.zip), "covers": args.covers,
             "url": f"https://github.com/Linutesto/wasteland-hd-assets/releases/download/{args.tag}/{os.path.basename(args.zip)}",
             "size": os.path.getsize(args.zip), "frames": frames, "sha256": digest.hexdigest()}
    doc["packs"] = [p for p in doc["packs"] if p["name"] != args.name] + [entry]
    with open(args.packs_json, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    print(json.dumps(entry, indent=2))


ASSETS = "https://raw.githubusercontent.com/Linutesto/wasteland-hd-assets/main/packs.json"


def cmd_install(args):
    """Downloads the published HD packs into a Wasteland Reborn game folder, after checking it holds Fallout 2."""
    import hashlib
    import json
    import urllib.request

    from .gamefiles import check_fallout2

    problem = check_fallout2(args.game)
    if problem:
        sys.exit(f"{problem}\nWasteland HD needs your own Fallout 2 (Steam, GOG or Epic) installed with Wasteland Reborn.")
    data_dir = os.path.join(args.game, "data")
    with urllib.request.urlopen(args.packs) as r:
        packs = json.load(r)["packs"]
    for pack in packs:
        if args.only and pack["name"] not in args.only:
            continue
        stamp = os.path.join(data_dir, "hd", f".{pack['name']}.sha256")
        if os.path.exists(stamp) and open(stamp).read().strip() == pack["sha256"]:
            print(f"{pack['name']}: up to date")
            continue
        tmp = os.path.join(args.game, pack["file"] + ".part")
        print(f"{pack['name']}: downloading {pack['size'] / 1e6:.0f} MB")
        digest = hashlib.sha256()
        with urllib.request.urlopen(pack["url"]) as r, open(tmp, "wb") as f:
            while chunk := r.read(1 << 20):
                digest.update(chunk)
                f.write(chunk)
        if digest.hexdigest() != pack["sha256"]:
            os.unlink(tmp)
            sys.exit(f"{pack['name']}: download damaged (checksum mismatch), try again")
        with zipfile.ZipFile(tmp) as z:
            for name in z.namelist():
                if not name.startswith("hd/") or ".." in name.split("/"):
                    sys.exit(f"{pack['name']}: unexpected file {name} in pack")
            z.extractall(data_dir)
        os.unlink(tmp)
        with open(stamp, "w") as f:
            f.write(pack["sha256"])
        print(f"{pack['name']}: installed")


def main():
    ap = argparse.ArgumentParser(prog="wasteland-hd", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("manifest", help="add a pack to packs.json")
    p.add_argument("zip")
    p.add_argument("--name", required=True)
    p.add_argument("--covers", required=True)
    p.add_argument("--tag", required=True, help="release tag the zip is attached to")
    p.add_argument("--packs-json", default="packs.json")
    p = sub.add_parser("install", help="download the published HD packs into your Wasteland Reborn game folder")
    p.add_argument("--game", required=True, help="Wasteland Reborn game folder (with master.dat)")
    p.add_argument("--only", action="append", help="only this pack (e.g. arroyo-hd)")
    p.add_argument("--packs", default=ASSETS, help=argparse.SUPPRESS)
    p = sub.add_parser("seams", help="reconcile independently AI-redrawn floor tiles with the original shared edges")
    p.add_argument("--game", required=True, help="your own Fallout 2 game folder")
    p.add_argument("--source", required=True, help="original HD folder containing art/tiles/")
    p.add_argument("--out", required=True, help="separate output HD folder; preserves the input")
    p.add_argument("--feather", type=int, default=16, help="distance in 2x pixels used to blend tile edges")
    p.add_argument("--sigma", type=float, default=8, help="scale dividing AI microtexture from large-scale colour drift")
    p.add_argument("--detail", type=float, default=1.0, help="AI microtexture strength")
    p.add_argument("--macro", type=float, default=0.25, help="amount of AI large-scale colour drift retained inside tiles")
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
            p.add_argument("--quality", choices=("auto", "fast", "full"), default="auto",
                           help="auto: Lightning (8 steps) for still art, full steps for animations; fast: Lightning for all; full: full steps for all")
            p.add_argument("--steps", type=int, default=30, help="steps of the full mode")
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
