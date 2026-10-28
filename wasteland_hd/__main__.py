"""wasteland-hd: make your own HD art pack for Wasteland Reborn HD from your own Fallout 2.

  python -m wasteland_hd list    --game DIR [--map arvillag.map | --type tiles ...]
  python -m wasteland_hd sheets  --game DIR --work WORK [--map ... | --type ...]
  python -m wasteland_hd upscale --work WORK [--backend qwen|lanczos] [--comfy URL]
  python -m wasteland_hd cut     --work WORK
  python -m wasteland_hd seams   --game DIR --source WORK/hd --out FIXED/hd
  python -m wasteland_hd qa      --game DIR --work WORK [--also RETRY]  (drop recoloured frames)
  python -m wasteland_hd retry   --game DIR --work WORK   (sheets of rejected frames, in WORK/retry)
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

from .gamefiles import ART_TYPES, Game, map_art


def __getattr__(name):
    # Existing tooling can access cli.sh while installer-only Python stays Pillow-free.
    if name == 'sh':
        from . import sheets
        return sheets
    raise AttributeError(name)


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
    from . import sheets as sh
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
    from .upscale import LIGHTNING, Comfy, lanczos
    os.makedirs(os.path.join(args.work, "upscaled"), exist_ok=True)
    fast = Comfy(args.comfy, steps=8, cfg=1.0, lora=LIGHTNING[8], seed=args.seed)
    full = Comfy(args.comfy, steps=args.steps, seed=args.seed)

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
    from . import sheets as sh
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


def cmd_qa(args):
    from . import qa
    qa.check(Game(args.game), args.work, also=args.also or [], limit=args.limit)


def cmd_retry(args):
    """Sheets of the frames `qa` rejected, in <work>/retry, for another upscale run (full steps, other seed)."""
    from . import qa
    from . import sheets as sh
    game = Game(args.game)
    wanted = {}
    for art, d, i in qa.rejected_frames(args.work):
        wanted.setdefault(art, set()).add((d, i))
    frames = [f for f in sh.frames_of(game, sorted(wanted)) if (f[1], f[2]) in wanted[f[0]]]
    out = os.path.join(args.work, "retry", "sheets")
    os.makedirs(out, exist_ok=True)
    # "anim-" so `upscale --quality auto` gives them full steps too.
    for i, items in enumerate(sh.pack(frames) if frames else []):
        path = os.path.join(out, f"anim-{i:05d}.png")
        sh.render_sheet(items, game.palette(), path)
    print(f"{len(frames)} rejected frames on {len(glob.glob(os.path.join(out, '*.png.json')))} sheets in {out}; next:\n"
          f"  upscale --work {os.path.join(args.work, 'retry')} --quality full --seed 7\n"
          f"  cut --work {os.path.join(args.work, 'retry')}\n"
          f"  qa --work {args.work} --also {os.path.join(args.work, 'retry')}")


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


def _download_pack(url, partial, expected_size):
    """Resume HTTP downloads when the server honours Range; restart otherwise."""
    import urllib.request

    offset = os.path.getsize(partial) if os.path.isfile(partial) else 0
    if offset > expected_size:
        os.unlink(partial)
        offset = 0
    if offset == expected_size:
        return
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=120) as response:
        content_range = response.headers.get("Content-Range", "")
        resumed = (offset > 0 and getattr(response, "status", None) == 206
                   and content_range.startswith(f"bytes {offset}-"))
        with open(partial, "ab" if resumed else "wb") as output:
            while chunk := response.read(1 << 20):
                output.write(chunk)
    if os.path.getsize(partial) != expected_size:
        raise ValueError(f"download size mismatch for {url}; partial download kept for retry")


def cmd_install(args):
    """Downloads the published HD packs into a Wasteland Reborn game folder, after checking it holds Fallout 2."""
    import hashlib
    import json
    import re
    import shutil
    import stat
    import tempfile
    import urllib.request
    from pathlib import Path

    from .gamefiles import check_fallout2

    problem = check_fallout2(args.game)
    if problem:
        sys.exit(f"{problem}\nWasteland HD needs your own Fallout 2 (Steam, GOG or Epic) installed with Wasteland Reborn.")
    data_dir = os.path.join(args.game, "data")
    with urllib.request.urlopen(args.packs, timeout=60) as r:
        packs = json.load(r)["packs"]
    for pack in packs:
        if args.only and pack["name"] not in args.only:
            continue
        name, filename, digest_expected = pack["name"], pack["file"], pack["sha256"]
        expected_size = pack["size"]
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name)
                or name in (".", "..") or not isinstance(filename, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.zip", filename)
                or not isinstance(digest_expected, str)
                or not re.fullmatch(r"[0-9a-fA-F]{64}", digest_expected)
                or type(expected_size) is not int or not 0 < expected_size < 16 * 1024**3):
            raise ValueError("invalid HD pack manifest entry")
        stamp = os.path.join(data_dir, "hd", f".{name}.sha256")
        if os.path.exists(stamp) and open(stamp).read().strip() == pack["sha256"]:
            print(f"{name}: up to date")
            continue
        tmp = os.path.join(args.game, filename + ".part")
        print(f"{name}: downloading {expected_size / 1e6:.0f} MB")
        _download_pack(pack["url"], tmp, expected_size)
        digest = hashlib.sha256()
        with open(tmp, "rb") as payload:
            while chunk := payload.read(1 << 20):
                digest.update(chunk)
        if digest.hexdigest().lower() != digest_expected.lower():
            os.unlink(tmp)
            sys.exit(f"{name}: download damaged (checksum mismatch), try again")
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(tmp) as z:
            entries = z.infolist()
            if sum(info.file_size for info in entries) > max(128 * 1024**2, expected_size * 100):
                sys.exit(f"{name}: archive expands beyond its size limit")
            for info in entries:
                parts = info.filename.rstrip("/").split("/")
                kind = (info.external_attr >> 16) & 0o170000
                if (len(parts) < 2 or parts[0] != "hd" or any(p in ("", ".", "..") for p in parts)
                        or "\\" in info.filename or kind not in (0, stat.S_IFREG, stat.S_IFDIR)):
                    sys.exit(f"{name}: unexpected file {info.filename} in pack")
            # Validate the entire archive before publishing any of its files.
            with tempfile.TemporaryDirectory(prefix=".hd-stage-", dir=data_dir) as temporary:
                root = Path(temporary)
                for info in entries:
                    dest = root.joinpath(*info.filename.rstrip("/").split("/"))
                    if info.is_dir():
                        dest.mkdir(parents=True, exist_ok=True)
                        continue
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as source, dest.open("wb") as output:
                        shutil.copyfileobj(source, output)
                for info in entries:
                    if info.is_dir():
                        continue
                    path = Path(data_dir).joinpath(*info.filename.split("/"))
                    if not path.resolve().is_relative_to(Path(data_dir).resolve()):
                        sys.exit(f"{name}: invalid destination {info.filename}")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(root.joinpath(*info.filename.split("/")), path)
        os.unlink(tmp)
        os.makedirs(os.path.dirname(stamp), exist_ok=True)
        with open(stamp, "w") as f:
            f.write(digest_expected)
        print(f"{name}: installed")


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
    for name in ("qa", "retry"):
        p = sub.add_parser(name, help="drop frames the model recoloured; --also adds another run's frames as candidates"
                           if name == "qa" else "sheets of the frames qa rejected, for another upscale run")
        p.add_argument("--game", required=True, help="your own Fallout 2 game folder")
        p.add_argument("--work", required=True, help="work folder (with hd/)")
        if name == "qa":
            p.add_argument("--also", action="append", help="another work folder whose hd/ frames are candidates too")
            p.add_argument("--limit", type=float, default=12.0, help="largest average colour change kept (0-255)")
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
            p.add_argument("--seed", type=int, default=42)
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
