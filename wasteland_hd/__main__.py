"""wasteland-hd: make your own HD art pack for Wasteland Reborn HD from your own Fallout 2.

  python -m wasteland_hd list    --game DIR [--map arvillag.map | --type tiles ...]
  python -m wasteland_hd sheets  --game DIR --work WORK [--map ... | --type ...]
  python -m wasteland_hd upscale --work WORK [--backend qwen|lanczos] [--comfy URL]
  python -m wasteland_hd cut     --work WORK
  python -m wasteland_hd seams   --game DIR --source WORK/hd --out FIXED/hd
  python -m wasteland_hd stabilise --game DIR --source WORK/hd --out FIXED/hd
  python -m wasteland_hd temporal --game DIR --source WORK/hd --out FIXED/hd  (keyframe identity transfer)
  python -m wasteland_hd flicker --game DIR --source WORK/hd --json FLICKER.json  (measure animation boiling)
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
        if t == "critters":
            # critters.lst holds base names; the art is <base><weapon><action>.frm
            # (or .fr0-.fr5, one file per direction), so list the archives.
            arts.update(n.replace("\\", "/").lower() for dat in game.dats for n in dat.names("art/critters/")
                        if n.lower().rsplit(".", 1)[-1] in ("frm", "fr0", "fr1", "fr2", "fr3", "fr4", "fr5"))
            continue
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
        for i, items in enumerate(sh.pack(group, sheet=args.sheet_size) if group else []):
            path = os.path.join(args.work, "sheets", f"{kind}-{i:05d}.png")
            size = sh.render_sheet(items, game.palette(), path, sheet=args.sheet_size)
            print(f"{path}: {len(items)} frames, {size[0]}x{size[1]}")


def cmd_upscale(args):
    from .upscale import ANIMATION_PROMPT, LIGHTNING, PROMPT, Comfy, lanczos, render_matches, write_render_receipt
    os.makedirs(os.path.join(args.work, "upscaled"), exist_ok=True)
    prompt = ANIMATION_PROMPT if args.prompt == "animation" else PROMPT
    fast = Comfy(args.comfy, steps=8, cfg=1.0, lora=LIGHTNING[8], seed=args.seed, prompt=prompt)
    full = Comfy(args.comfy, steps=args.steps, seed=args.seed, prompt=prompt)

    def model_for(sheet):
        if args.backend == "lanczos":
            return None
        if args.quality == "fast" or (args.quality == "auto" and not os.path.basename(sheet).startswith("anim-")):
            return fast
        return full

    todo = sheet_paths(args.work, "sheets")
    for n, src in enumerate(todo, 1):
        dst = os.path.join(args.work, "upscaled", os.path.basename(src))
        model = model_for(src)
        if os.path.exists(dst) and render_matches(dst, src, args.render_scale, model):
            continue
        if os.path.exists(dst):
            # Preserve mismatched historical renders instead of silently using
            # Lightning output in a requested full-quality run.
            stale = os.path.join(args.work, "upscaled-stale", str(time.time_ns()))
            os.makedirs(stale, exist_ok=True)
            os.replace(dst, os.path.join(stale, os.path.basename(dst)))
        t = time.time()
        (model.upscale if model else lanczos)(src, dst + ".part.png", scale=args.render_scale)
        os.replace(dst + ".part.png", dst)
        write_render_receipt(dst, src, model)
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


def cmd_stabilise(args):
    from .temporal import stabilise_directory

    scores = stabilise_directory(Game(args.game), args.source, args.out, radius=args.radius)
    for group, values in scores.items():
        print(f"{group}: {values['before']:.3f} -> {values['after']:.3f} flicker")
    print(f"{len(scores)} critter directions stabilised in {args.out}")


def cmd_temporal(args):
    """Keyframe-anchored identity transfer; keeps only directions whose gate margin improves."""
    from . import temporal_stage

    result = temporal_stage.stage_directory(
        Game(args.game), args.source, args.out, names=args.anim,
        components=tuple(args.components.split(",")), strength=args.strength,
        radius=args.radius, select=not args.keep_all,
        fallback=not args.no_fallback, cap=args.fallback_cap)
    directions = result["directions"]
    for group, values in sorted(directions.items()):
        before, after = values["before"], values["after"]
        state = "fallback" if values["fallback"] else ("kept" if values["accepted"] else "reverted")
        print(f"{group}: keyframe {values['keyframe']} {state}  "
              f"flicker {before['flicker']} -> {after['flicker']}  "
              f"broad {before['broad']} -> {after['broad']}  "
              f"material {before['material']} -> {after['material']}  "
              f"margin {temporal_stage.margin(before):.2f} -> {temporal_stage.margin(after):.2f}")
    for name, values in sorted(result["animations"].items()):
        print(f"{name}: flicker {values['flicker']:.3f} broad {values['broad']:.3f} "
              f"material {values['material']:.3f} -> "
              f"{'pass' if temporal_stage.passes(values) else 'FAIL'} "
              f"({values['fallbacks']}/{values['directions']} directions on original art)")
    fallbacks = sum(v["fallback"] for v in directions.values())
    kept = sum(v["accepted"] and not v["fallback"] for v in directions.values())
    print(f"{kept}/{len(directions)} directions rewritten, {fallbacks} fell back to original art, "
          f"{len(directions) - kept - fallbacks} reverted, in {args.out}")
    if args.json:
        from pathlib import Path
        from .flicker import safe_output, write_json

        safe_output(args.source, args.json)
        write_json(result, Path(args.json))
        print(f"JSON: {args.json}")


def cmd_flicker(args):
    """Measures generation noise on pixels the original animation leaves identical."""
    from .flicker import analyse, contact_sheet, safe_output, write_json

    safe_output(args.source, args.json)
    result = analyse(Game(args.game), args.source, names=args.anim, control=args.control)
    for name, values in result["animations"].items():
        print(f"{name}: flicker {values['flicker']:.3f} (broad {values['broad']:.3f}, "
              f"fine {values['fine']:.3f}, rim {values['rim']:.3f}, material {values['material']:.3f}) "
              f"{values['directions']} directions, {values['pixels']} pixels")
    for group, values in result["directions"].items():
        print(f"  {group}: {values['flicker']}")
    if args.contact:
        safe_output(args.source, args.contact)
        contact_sheet(Game(args.game), args.source, result, args.contact,
                      top=args.top, control=args.control)
        print(f"contact sheet: {args.contact}")
    write_json(result, args.json)
    print(f"JSON: {args.json}")
    if args.limit is not None:
        over = {n: v["flicker"] for n, v in result["animations"].items()
                if v["flicker"] is not None and v["flicker"] > args.limit}
        if over:
            sys.exit("over flicker limit " + f"{args.limit:g}: "
                     + ", ".join(f"{n} {v:.3f}" for n, v in sorted(over.items())))


def cmd_qa(args):
    from . import qa
    qa.check(Game(args.game), args.work, also=args.also or [], limit=args.limit, keep_passing=args.keep_passing)


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
    for i, items in enumerate(sh.pack(frames, sheet=args.sheet_size) if frames else []):
        path = os.path.join(out, f"anim-{i:05d}.png")
        sh.render_sheet(items, game.palette(), path, sheet=args.sheet_size)
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
    if len(sys.argv) > 1 and sys.argv[1] == 'select':
        from .select import main as select_main
        return select_main(sys.argv[2:])
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
    p = sub.add_parser("flicker", help="measure per-frame generation flicker of HD critter animations")
    p.add_argument("--game", required=True, help="your own Fallout 2 game folder")
    p.add_argument("--source", required=True, help="HD folder containing art/critters/")
    p.add_argument("--json", required=True, help="machine-readable report to write (never inside --source)")
    p.add_argument("--anim", action="append", help="only this animation (default: every one in --source)")
    p.add_argument("--control", choices=("nearest", "lanczos"),
                   help="score a deterministic original upscale instead of the HD frames")
    p.add_argument("--contact", help="contact sheet of the worst directions (never inside --source)")
    p.add_argument("--top", type=int, default=4, help="directions on the contact sheet")
    p.add_argument("--limit", type=float, help="exit nonzero if an animation scores above this")
    p = sub.add_parser("stabilise", help="CPU-only original-guided temporal stabilisation of HD critters")
    p.add_argument("--game", required=True, help="your own Fallout 2 game folder")
    p.add_argument("--source", required=True, help="HD folder containing art/critters/")
    p.add_argument("--out", required=True, help="empty separate destination HD folder")
    p.add_argument("--radius", type=int, default=9, help="native pixels for original-only patch matching")
    p = sub.add_parser("temporal", help="keyframe-anchored identity transfer for critter animations (CPU)")
    p.add_argument("--game", required=True, help="your own Fallout 2 game folder")
    p.add_argument("--source", required=True, help="HD folder containing art/critters/")
    p.add_argument("--out", required=True, help="empty separate destination HD folder")
    p.add_argument("--anim", action="append", help="only this animation (default: every one in --source)")
    p.add_argument("--components", default="colour,residual,texture,static",
                   help="comma list of colour,residual,texture,static (default: all four)")
    p.add_argument("--strength", type=float, default=1.0, help="scale every transfer (0..1)")
    p.add_argument("--radius", type=int, default=9, help="native pixels for original-only patch matching")
    p.add_argument("--keep-all", action="store_true",
                   help="write every direction, even when its gate margin gets worse")
    p.add_argument("--no-fallback", action="store_true",
                   help="keep a staged direction even when it stays over the direction cap")
    p.add_argument("--fallback-cap", type=float, default=2.0,
                   help="direction flicker above this x its animation mean becomes original art")
    p.add_argument("--json", help="per-direction before/after report (never inside --source)")
    for name in ("qa", "retry"):
        p = sub.add_parser(name, help="drop frames the model recoloured; --also adds another run's frames as candidates"
                           if name == "qa" else "sheets of the frames qa rejected, for another upscale run")
        p.add_argument("--game", required=True, help="your own Fallout 2 game folder")
        p.add_argument("--work", required=True, help="work folder (with hd/)")
        if name == "retry":
            p.add_argument("--sheet-size", type=int, default=512, help="native sheet size (256 for --render-scale 4)")
        if name == "qa":
            p.add_argument("--also", action="append", help="another work folder whose hd/ frames are candidates too")
            p.add_argument("--limit", type=float, default=12.0, help="largest average colour change kept (0-255)")
            p.add_argument("--keep-passing", action="store_true", help="keep a passing frame of hd/ even if an --also candidate scores lower")
    for name in ("list", "sheets", "upscale", "cut", "pack", "all"):
        p = sub.add_parser(name)
        if name in ("list", "sheets", "all"):
            p.add_argument("--game", required=True, help="Fallout 2 folder (with master.dat)")
            p.add_argument("--map", action="append", help="art used by this map, e.g. arvillag.map")
            p.add_argument("--type", action="append", choices=ART_TYPES, help="all art of a type")
            if name != "list":
                # Qwen keeps sprites in place up to ~1024 px of output; at
                # --render-scale 4 a 512 sheet (2048 px) drifts, so use 256.
                p.add_argument("--sheet-size", type=int, default=512, help="native sheet size (256 for --render-scale 4)")
        if name != "list":
            p.add_argument("--work", required=True, help="work folder")
        if name in ("upscale", "all"):
            p.add_argument("--backend", choices=("qwen", "lanczos"), default="qwen")
            p.add_argument("--comfy", default="http://127.0.0.1:8188")
            p.add_argument("--quality", choices=("auto", "fast", "full"), default="auto",
                           help="auto: Lightning (8 steps) for still art, full steps for animations; fast: Lightning for all; full: full steps for all")
            p.add_argument("--steps", type=int, default=30, help="steps of the full mode")
            p.add_argument("--seed", type=int, default=42)
            p.add_argument("--prompt", choices=("art", "animation"), default="art",
                           help="animation: for sheets packed one animation per row (critters)")
            p.add_argument("--render-scale", type=int, default=2, choices=(2, 3, 4),
                           help="draw sheets at this scale; cut shrinks them to 2x (4: finer detail, ~4x slower)")
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
