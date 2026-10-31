"""Durable, local-only HD production. See docs/AUTONOMOUS-PRODUCTION.md.

The optional ``--temporal`` critter stage (``temporal_stage`` plus the flicker
gate) is a rollout option that is deliberately *not* part of ``plan.json``: the
live root's plan is immutable and a restart refuses anything that differs byte
for byte, so the option is filed per job type in ``options.json`` and applies
only to job types that have not started (``docs/CRITTER-PRODUCTION-REPORT.md``).
"""

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

from PIL import Image

from . import qa, sheets
from .gamefiles import ART_TYPES, Game, frame_rgba, norm, read_frm, unique_directions
from .upscale import ANIMATION_PROMPT, PROMPT, NEGATIVE, LIGHTNING, QWEN_MODELS, Comfy, render_matches


VERSION = 2
NEEDS_REVIEW = 2
TEMPORARY_FAILURE = 75
ART_SUFFIX = re.compile(r"\.(frm|fr[0-5])$", re.I)
SETTINGS = {"backend": "qwen", "quality": "fast", "steps": 8,
            "cfg": 1.0, "render_scale": 4, "output_scale": 2, "sheet_size": 256,
            "models": QWEN_MODELS, "lora": LIGHTNING[8], "lora_strength": 1.0,
            "sampler": "euler", "scheduler": "simple", "denoise": 1.0,
            # Animation sheets (critters) render at the reduced denoise: at 1.0 the
            # model redrew helmets into bare bearded heads and slimmed the suits,
            # whatever the prompt said (docs/CRITTER-PROMPT-REPORT.md).
            "animation_denoise": 0.55,
            "auraflow_shift": 3.0, "upscale_method": "lanczos",
            "art_prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
            "animation_prompt_sha256": hashlib.sha256(ANIMATION_PROMPT.encode()).hexdigest(),
            "negative_prompt_sha256": hashlib.sha256(NEGATIVE.encode()).hexdigest()}

# Critter temporal stage + flicker gate (docs/CRITTER-TEMPORAL-REPORT.md).
# Recorded per job type in options.json rather than in plan.json.
TEMPORAL_SETTINGS = {"components": ["colour", "residual", "texture", "static"],
                     "strength": 1.0, "radius": 9, "fallback_cap": 2.0,
                     "gate": {"broad": 2.5, "material": 3.0, "flicker": 6.0},
                     "rule": "temporal-stage/1"}
OPTIONS = "options.json"


def production_model(seed=42, animated=False):
    return Comfy(seed=seed, steps=SETTINGS["steps"], cfg=SETTINGS["cfg"],
                 lora=SETTINGS["lora"],
                 prompt=ANIMATION_PROMPT if animated else PROMPT,
                 denoise=SETTINGS["animation_denoise"] if animated else SETTINGS["denoise"])
PRIORITY = ["tiles", "walls", "scenery", "items", "misc", "intrface", "inven",
            "backgrnd", "skilldex", "heads", "critters"]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"corrupted checkpoint {path}; preserve it and restore from a known checkpoint or use a new root") from exc


def atomic_copy(source, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".copy-", dir=destination.parent)
    os.close(fd)
    try:
        shutil.copyfile(source, temporary)
        with open(temporary, "rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def controller_lock(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / "controller.lock").open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another production controller holds the lock") from exc
        stream.seek(0)
        stream.truncate()
        stream.write(str(os.getpid()) + "\n")
        stream.flush()
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def discover(game):
    """Union archive/loose FRMs and list aliases; never invent critter filenames."""
    paths = {kind: set() for kind in ART_TYPES}
    blockers = []

    def add(name):
        name = norm(name)
        parts = name.split("/")
        if len(parts) >= 3 and parts[0] == "art" and parts[1] in paths and ART_SUFFIX.search(name):
            if ".." not in parts and not name.startswith("/"):
                paths[parts[1]].add(name)

    for dat in game.dats:
        for name in dat.names("art/"):
            add(name)
    if game.data_dir:
        for directory, _dirs, files in os.walk(game.data_dir):
            for name in files:
                add(os.path.relpath(os.path.join(directory, name), game.data_dir))
    for index, kind in enumerate(ART_TYPES):
        try:
            aliases = game.art_list(index)
        except Exception as exc:
            blockers.append({"source": f"art/{kind}/{kind}.lst", "error": str(exc)})
            continue
        if kind != "critters":
            for alias in aliases:
                # Some lists contain comma-separated metadata after a filename.
                alias = norm(alias.split(",", 1)[0])
                if ART_SUFFIX.search(alias):
                    add(alias if alias.startswith("art/") else f"art/{kind}/{alias}")
    return {kind: sorted(names) for kind, names in paths.items()}, blockers


def unsupported_visuals(game):
    names = {norm(name) for dat in game.dats for name in dat.names("art/")
             if norm(name).endswith((".rix", ".mve"))}
    if game.data_dir:
        for directory, _dirs, files in os.walk(game.data_dir):
            for name in files:
                rel = norm(os.path.relpath(os.path.join(directory, name), game.data_dir))
                if rel.startswith("art/") and rel.endswith((".rix", ".mve")):
                    names.add(rel)
    return sorted(names)


def source_stamp(game):
    """Cheap restart guard for archive/loose changes; per-art hashes are in inventories."""
    records = []
    for dat in game.dats:
        p = Path(dat.path)
        st = p.stat()
        records.append((str(p.resolve()), st.st_size, st.st_mtime_ns))
    if game.data_dir:
        for directory, dirs, files in os.walk(game.data_dir):
            # Generated HD art and saves are not source FRMs.
            dirs[:] = [d for d in dirs if d.lower() not in ("hd", "savegame")]
            for name in files:
                if ART_SUFFIX.search(name) or name.lower().endswith((".lst", ".pal")):
                    p = Path(directory) / name
                    st = p.stat()
                    records.append((str(p.resolve()), st.st_size, st.st_mtime_ns))
    return digest(json.dumps(sorted(records)).encode())


def plan_document(args, game, paths, unsupported):
    """The immutable plan of a production root.

    It contains no ``temporal`` key on purpose.  An existing root refuses to
    restart (exit 64) unless the new document equals its stored one byte for
    byte, so any new key here would make exactly the root we want to resume
    unusable.  ``--temporal`` is filed per job type in ``options.json`` instead
    (:meth:`Controller.job_options`); with the flag off this function returns
    exactly what earlier code wrote.
    """
    return {"version": VERSION, "game": str(Path(args.game).resolve()),
            "settings": SETTINGS, "seeds": args.seeds, "chunk_size": args.chunk_size,
            "seams": args.seams, "stabilise": args.stabilise,
            "adopt_world": str(Path(args.adopt_world).resolve()) if args.adopt_world else None,
            "coverage_scope": "FRM and .fr0-.fr5 only; excludes RIX splash images and MVE movies",
            "unsupported_visuals": unsupported,
            "sources": paths, "source_stamp": source_stamp(game)}


def gate_failures(metrics, limits):
    """Gate components a measurement violates; an unmeasured one cannot pass."""
    return [key for key in sorted(limits)
            if metrics.get(key) is None or metrics[key] > limits[key]]


def temporal_verdicts(report, settings):
    """Per-direction flicker-gate verdicts of one temporal stage report.

    A direction fails when the stage already replaced it with original art
    (over the direction cap), when its own after-staging metrics break the gate,
    or when its whole animation's pooled metrics break it.  Failing directions
    are not accepted: they go to the next seed attempt and, once the seed budget
    is spent, to the original-art fallback like every other rejection.

    The metrics come from the stage's own per-direction and pooled records,
    computed with ``flicker._measure`` on the exact arrays it writes, so the
    gate costs no second measurement pass over the tree.
    """
    limits = settings["gate"]
    directions = report.get("directions") or {}
    animations = report.get("animations") or {}
    verdicts = {}
    for key, value in sorted(directions.items()):
        metrics = value.get("after") or {}
        animation = animations.get(value.get("animation"), {}) or {}
        if value.get("fallback"):
            reason = "direction_cap"
        elif gate_failures(metrics, limits):
            reason = "flicker_gate"
        elif gate_failures(animation, limits):
            reason = "animation_flicker_gate"
        else:
            reason = None
        verdicts[key] = {"pass": reason is None, "reason": reason,
                         "direction": {k: metrics.get(k) for k in limits},
                         "animation": {k: animation.get(k) for k in limits},
                         "animation_name": value.get("animation"),
                         "keyframe": value.get("keyframe"), "limits": dict(limits)}
    return verdicts


def inventory(game, arts):
    """Decode strictly, retaining missing/unreadable sources as coverage blockers."""
    groups, frames, errors, hashes = {}, {}, [], {}
    palette = game.palette()
    for art in arts:
        try:
            raw = game.read(art)
            hashes[art] = digest(raw)
            frm = read_frm(raw)
            fixed = int(art[-1]) if art[-3:-1] == "fr" and art[-1] in "012345" else None
            directions = [0] if fixed is not None else unique_directions(frm)
            count = 0
            for native_direction in directions:
                direction = fixed if fixed is not None else native_direction
                key = f"{art.rsplit('.', 1)[0]}/{direction}"
                if key in groups:
                    raise ValueError(f"ambiguous output direction {key} (.frm/.frN collision)")
                group_frames = []
                for index, frame in enumerate(frm.directions[native_direction]):
                    if not frame.width or not frame.height:
                        continue  # empty source frames have no engine HD rectangle
                    if len(frame.pixels) != frame.width * frame.height:
                        raise ValueError(f"truncated pixels in direction {direction} frame {index}")
                    rgba = Image.frombytes("RGBA", (frame.width, frame.height), frame_rgba(frame, palette))
                    rel = f"{art.rsplit('.', 1)[0]}/{direction}_{index}.png"
                    group_frames.append({"rel": rel, "width": frame.width * 2, "height": frame.height * 2,
                                         "alpha": digest(rgba.getchannel("A").resize(
                                             (frame.width * 2, frame.height * 2), Image.Resampling.NEAREST).tobytes())})
                    frames[rel] = (art, direction, index, frame)
                    count += 1
                if group_frames:
                    groups[key] = {"art": art, "direction": direction, "frames": group_frames,
                                   "animated": "/critters/" in art or len(group_frames) > 1}
            if not count:
                raise ValueError("source has no nonempty frames")
        except Exception as exc:
            errors.append({"source": art, "error": str(exc)})
    return {"groups": groups, "source_errors": errors, "source_hashes": hashes}, frames


def integrity(path, expected):
    try:
        with Image.open(path) as img:
            img.load()
            if img.mode != "RGBA":
                return "expected RGBA"
            if img.size != (expected["width"], expected["height"]):
                return "wrong dimensions"
            if digest(img.getchannel("A").tobytes()) != expected["alpha"]:
                return "alpha differs from exact source 2x nearest"
    except FileNotFoundError:
        return "missing"
    except Exception as exc:
        return f"unreadable PNG: {exc}"
    return None


def evaluate(game, group, frames, hd):
    issues, scores, valid, missing = {}, {}, 0, 0
    # Integrity of the COMPLETE direction is checked before any QA call.
    for expected in group["frames"]:
        error = integrity(Path(hd) / expected["rel"], expected)
        if error:
            issues[expected["rel"]] = error
            missing += error == "missing"
        else:
            valid += 1
    if not issues:
        for expected in group["frames"]:
            rel = expected["rel"]
            frame = frames[rel][3]
            orig = Image.frombytes("RGBA", (frame.width, frame.height), frame_rgba(frame, game.palette()))
            with Image.open(Path(hd) / rel) as img:
                score = qa.badness(img, orig, rel, qa.COLOUR_LIMIT)
            scores[rel] = float(score) if math.isfinite(score) else None
            if not math.isfinite(score) or score > 1:
                issues[rel] = "QA colour/structure rejected"
    return {"expected": len(group["frames"]), "valid": valid, "missing": missing,
            "rejected": len(group["frames"]) - missing if issues else 0,
            "accepted": 0 if issues else len(group["frames"]), "issues": issues,
            "scores": scores, "complete": not issues}


class Controller:
    def __init__(self, args, game=None):
        self.args = args
        self.root = Path(args.root).resolve()
        self.game = game if game is not None else Game(args.game)
        self.paths, self.blockers = discover(self.game)
        self.unsupported = unsupported_visuals(self.game)
        self.plan = plan_document(args, self.game, self.paths, self.unsupported)
        self.jobs = [(f"{kind}-{i // args.chunk_size:04d}", kind, self.paths[kind][i:i + args.chunk_size])
                     for kind in PRIORITY if kind in self.paths
                     for i in range(0, len(self.paths[kind]), args.chunk_size)]
        self.adopt_index = None
        self.options = {}
        self.temporal_gate = {}
        self.temporal_state = {}

    def job_options(self):
        """Per-job-type options; an option cannot change a type that has started.

        ``plan.json`` stays byte-identical to what earlier code wrote (see
        :func:`plan_document`), so the live root resumes; a rollout option lives
        here instead and is honoured only while every job of that type is still
        unstarted.  Changing it afterwards is refused rather than silently mixed
        into frames a different rule already produced.
        """
        previous = read_json(self.root / OPTIONS, {}) or {}
        wanted = {kind: dict(value) for kind, value in previous.items()}
        temporal = {**TEMPORAL_SETTINGS, "enabled": bool(self.args.temporal)}
        if self.args.temporal or (previous.get("critters") or {}).get("temporal"):
            wanted["critters"] = {**(previous.get("critters") or {}), "temporal": temporal}
        for kind, value in sorted(wanted.items()):
            if previous.get(kind) == value:
                continue
            started = [name for name, job_kind, _arts in self.jobs
                       if job_kind == kind and self.started(name)]
            if started:
                raise ValueError(f"{kind} options changed after {started[0]} started; "
                                 f"restart with the options it ran with, or use a new --root")
        return wanted

    def started(self, name):
        work = self.root / "jobs" / name
        state = read_json(work / "state.json", {}) or {}
        return bool(state.get("attempts")) or any(work.glob("attempt-*"))

    def temporal_for(self, kind="critters"):
        """The temporal setting this job type runs with, or None when off."""
        setting = (self.options.get(kind) or {}).get("temporal") or {}
        return dict(setting) if setting.get("enabled") else None

    def wait_ready(self):
        deadline = time.monotonic() + self.args.ready_timeout
        error = "not ready"
        while True:
            try:
                with urllib.request.urlopen(self.args.comfy.rstrip("/") + "/system_stats", timeout=5) as reply:
                    json.load(reply)
                with urllib.request.urlopen(self.args.comfy.rstrip("/") + "/queue", timeout=5) as reply:
                    queue = json.load(reply)
                if not queue.get("queue_running") and not queue.get("queue_pending"):
                    return
                error = "ComfyUI queue is occupied; preserving current GPU job"
            except Exception as exc:
                error = str(exc)
            if time.monotonic() >= deadline:
                raise TimeoutError(f"readiness timeout: {error}")
            time.sleep(min(5, max(0, deadline - time.monotonic())))

    def command(self, arguments, checkpoint, gpu=False):
        """Failures are budgeted durably, including a crash during the child call."""
        state = read_json(checkpoint, {"calls": 0})
        if state.get("status") == "ok":
            return
        while state["calls"] < self.args.command_retries:
            if gpu:
                self.wait_ready()
            state["calls"] += 1
            state["status"] = "running"
            atomic_json(checkpoint, state)
            try:
                with Path(str(checkpoint) + ".log").open("ab") as log:
                    subprocess.run([sys.executable, "-m", "wasteland_hd", *map(str, arguments)],
                                   cwd=Path(__file__).resolve().parent.parent,
                                   stdout=log, stderr=subprocess.STDOUT,
                                   check=True, timeout=self.args.timeout)
                state["status"] = "ok"
                atomic_json(checkpoint, state)
                return
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
                state.update(status="failed", error=str(exc))
                atomic_json(checkpoint, state)
        raise RuntimeError(f"bounded command failures at {checkpoint}: {state.get('error', 'interrupted')}")

    def adoption_index(self):
        if self.adopt_index is None:
            self.adopt_index = {}
            if self.args.adopt_world:
                source = Path(self.args.adopt_world)
                for metadata in sorted((source / "sheets").glob("*.png.json")):
                    try:
                        for p in read_json(metadata):
                            rel = f"{p['art'].rsplit('.', 1)[0]}/{p['direction']}_{p['frame']}.png"
                            self.adopt_index.setdefault(rel, []).append(metadata.name[:-5])
                    except (OSError, ValueError, KeyError, TypeError):
                        continue  # source coverage still requires a fresh candidate
        return self.adopt_index

    def adopt(self, work, manifest):
        """Snapshot complete sheets only. Never write to or run upscale on legacy work."""
        adopted = work / "adopted"
        receipt = read_json(adopted / "provenance.json", {})
        if not self.args.adopt_world:
            return adopted / "hd", receipt
        source = Path(self.args.adopt_world)
        names = set()
        for group in manifest["groups"].values():
            for expected in group["frames"]:
                names.update(self.adoption_index().get(expected["rel"], []))
        for name in sorted(names):
            up = source / "upscaled" / name
            native = source / "sheets" / name
            if name not in receipt:
                try:
                    with Image.open(up) as img, Image.open(native) as ref:
                        img.load()
                        if img.size != (ref.width * 4, ref.height * 4):
                            continue
                        embedded = img.info.get("prompt")
                    snapshot = adopted / "sheets" / name
                    for suffix in ("", ".json", ".mask.png"):
                        atomic_copy(str(native) + suffix, str(snapshot) + suffix)
                    atomic_copy(up, adopted / "upscaled" / name)
                    animation = name.startswith("anim-") or any(
                        manifest["groups"][key]["animated"] and any(
                            name in self.adoption_index().get(f["rel"], [])
                            for f in manifest["groups"][key]["frames"])
                        for key in manifest["groups"])
                    compatible = render_matches(str(adopted / "upscaled" / name), str(snapshot), 4,
                                                production_model(animated=animation))
                    receipt[name] = {"origin": str(up.resolve()), "sha256": file_digest(adopted / "upscaled" / name),
                                     "settings": SETTINGS if compatible else "historical_mixed_or_unknown",
                                     "all_lightning": compatible, "prompt": "animation" if animation else "art",
                                     "lightning_compatible": compatible,
                                     "embedded_prompt": embedded}
                    atomic_json(adopted / "provenance.json", receipt)
                except (OSError, ValueError):
                    continue
            # Recut on restart, so partial cuts never become an acceptance marker.
            if receipt[name].get("lightning_compatible"):
                sheets.cut_sheet(str(adopted / "sheets" / name), str(adopted / "upscaled" / name), str(adopted))
        return adopted / "hd", receipt

    def prepare(self, attempt, wanted, manifest, frames):
        marker = attempt / "sheets.json"
        previous = read_json(marker)
        if previous is not None:
            return previous
        plans = []
        for animated in (False, True):
            chosen = [frames[f["rel"]] for key in wanted for f in manifest["groups"][key]["frames"]
                      if manifest["groups"][key]["animated"] == animated]
            for n, items in enumerate(sheets.pack(chosen, sheet=256, by_animation=animated) if chosen else []):
                name = f"{'anim' if animated else 'static'}-{n:05d}.png"
                target = attempt / "sheets" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                sheets.render_sheet(items, self.game.palette(), str(target), sheet=256)
                plans.append({"name": name, "prompt": "animation" if animated else "art"})
        atomic_json(marker, plans)
        return plans

    def render(self, attempt, seed, plans):
        errors = {}
        for item in plans:
            try:
                self.render_sheet(attempt, seed, item)
            except TimeoutError:
                raise
            except (RuntimeError, OSError) as exc:
                errors[item["name"]] = str(exc)
                atomic_json(attempt / "render-errors.json", errors)

    def render_sheet(self, attempt, seed, item):
        name = item["name"]
        job = attempt / "renders" / Path(name).stem
        src = attempt / "sheets" / name
        dst = job / "sheets" / name
        if not dst.exists():
            atomic_copy(src, dst)
        output = job / "upscaled" / name
        done = read_json(job / "cut.json")
        model = production_model(seed, item["prompt"] == "animation")
        valid_graph = (output.is_file() and render_matches(str(output), str(src),
                                                          SETTINGS["render_scale"], model))
        # A crash after PNG generation but before cut.json is recoverable
        # without a second GPU invocation. Never forgive a mismatched hash.
        trustworthy = valid_graph and (not done or done.get("sha256") == file_digest(output))
        compatible = trustworthy and done and done.get("settings") == SETTINGS
        if trustworthy and not compatible:
            atomic_json(job / "cut.json", {"sha256": file_digest(output), "settings": SETTINGS,
                                          "prompt": item["prompt"], "seed": seed})
            compatible = True
        if not compatible:
            print(f"production: seed={seed} sheet={name} prompt={item['prompt']}", flush=True)
            # command.json='ok' cannot be reused to regenerate a missing or
            # invalid PNG. Archive the whole old attempt before resetting the
            # budget; never delete a historical image, checkpoint or log.
            checkpoint = job / "command.json"
            if read_json(checkpoint, {}).get("status") == "ok":
                stale = job / "stale" / str(time.time_ns())
                stale.mkdir(parents=True)
                for previous in (checkpoint, Path(str(checkpoint) + ".log"), job / "cut.json",
                                 output, Path(str(output) + ".render.json")):
                    if previous.exists():
                        previous.rename(stale / previous.name)
            self.command(["upscale", "--work", job, "--backend", SETTINGS["backend"],
                          "--quality", SETTINGS["quality"], "--steps", str(SETTINGS["steps"]),
                          "--seed", str(seed), "--render-scale", str(SETTINGS["render_scale"]),
                          "--prompt", item["prompt"],
                          "--denoise", str(SETTINGS["animation_denoise"]
                                           if item["prompt"] == "animation" else SETTINGS["denoise"]),
                          "--comfy", self.args.comfy], checkpoint, gpu=True)
            with Image.open(output) as image, Image.open(src) as native:
                image.load()
                if image.size != (native.width * 4, native.height * 4):
                    raise RuntimeError(f"invalid render dimensions: {output}")
            if not render_matches(str(output), str(src), SETTINGS["render_scale"], model):
                raise RuntimeError(f"render graph does not match Lightning production settings: {output}")
            atomic_json(job / "cut.json", {"sha256": file_digest(output), "settings": SETTINGS,
                                          "prompt": item["prompt"], "seed": seed})
        sheets.cut_sheet(str(src), str(output), str(attempt))

    def run_temporal(self, hd, stage, manifest, settings):
        """Temporal stage + per-animation flicker gate over the critter tree.

        Runs on complete, integrity-checked directions only, after seams and
        ``--stabilise``; its output is what integrity and QA then see.  The
        stage rewrites RGB only (the source alpha at 2x nearest is copied
        through), so both gates keep applying unchanged.
        """
        out = stage / "temporal" / "hd"
        report_path = stage / "temporal-report.json"
        try:
            self.command(["temporal", "--game", self.args.game, "--source", hd, "--out", out,
                          "--components", ",".join(settings["components"]),
                          "--strength", str(settings["strength"]),
                          "--radius", str(settings["radius"]),
                          "--fallback-cap", str(settings["fallback_cap"]),
                          "--json", str(report_path)], stage / "temporal.json")
        except RuntimeError as exc:
            raise TimeoutError(f"CPU temporal stage failed; GPU seed checkpoint retained: {exc}") from exc
        report = read_json(report_path, {}) or {}
        # The stage names a direction ``<art stem>/<direction>``; the manifest
        # (and therefore state/coverage) names it ``art/critters/<stem>/<direction>``.
        by_report = {f"{Path(group['art']).stem}/{group['direction']}": key
                     for key, group in manifest["groups"].items()
                     if key.startswith("art/critters/")}
        verdicts = {}
        for name, verdict in temporal_verdicts(report, settings).items():
            key = by_report.get(name)
            if key is not None:
                verdicts[key] = verdict
        # The stage's cap stand-in is original art at 2x nearest; production must
        # not ship it.  Delete those frames (and every gate failure's) so the
        # direction is missing, exactly like any other rejected direction.
        for key, verdict in verdicts.items():
            if verdict["pass"]:
                continue
            for expected in manifest["groups"].get(key, {}).get("frames", []):
                (out / expected["rel"]).unlink(missing_ok=True)
        gate = stage / "temporal" / "gate.json"
        state = {"settings": settings, "report": str(report_path), "gate": str(gate),
                 "animations": report.get("animations", {}), "verdicts": verdicts}
        atomic_json(gate, state)
        self.temporal_gate, self.temporal_state = verdicts, state
        return out

    def postprocess(self, work, source, manifest, keys, frames):
        """Only integral whole directions enter CPU postprocessing; keep input intact."""
        checkpoint = work / "post.json"
        temporal = self.temporal_for()
        self.temporal_gate, self.temporal_state = {}, {}
        payload = {key: [(f["rel"], file_digest(source / f["rel"]) if (source / f["rel"]).is_file() else None)
                         for f in manifest["groups"][key]["frames"]]
                   for key in keys}
        # The stage changes what the checkpointed output means, so its options
        # are part of the signature; with the stage off the signature is exactly
        # what earlier code computed, so an in-flight attempt resumes unchanged.
        if temporal:
            payload["temporal"] = temporal
        signature = digest(json.dumps(payload, sort_keys=True).encode())
        previous = read_json(checkpoint)
        if previous and previous.get("signature") == signature:
            if previous.get("gate"):
                saved = read_json(previous["gate"], {}) or {}
                self.temporal_gate = saved.get("verdicts", {})
                self.temporal_state = saved
            return Path(previous["hd"])
        stage = Path(tempfile.mkdtemp(prefix="post-", dir=work))
        hd = stage / "input" / "hd"
        hd.mkdir(parents=True)
        copied = []
        for key in keys:
            group = manifest["groups"][key]
            if not evaluate(self.game, group, frames, source)["complete"]:
                continue
            for f in group["frames"]:
                atomic_copy(source / f["rel"], hd / f["rel"])
            copied.append(key)
        if self.args.seams and (hd / "art" / "tiles").exists():
            out = stage / "seams" / "hd"
            shutil.copytree(hd, out)
            try:
                self.command(["seams", "--game", self.args.game, "--source", hd, "--out", out], stage / "seams.json")
            except RuntimeError as exc:
                raise TimeoutError(f"CPU seam stage failed; GPU seed checkpoint retained: {exc}") from exc
            hd = out
        if self.args.stabilise and (hd / "art" / "critters").exists():
            out = stage / "stabilised" / "hd"
            try:
                self.command(["stabilise", "--game", self.args.game, "--source", hd, "--out", out], stage / "stabilise.json")
            except RuntimeError as exc:
                raise TimeoutError(f"CPU stabilise stage failed; GPU seed checkpoint retained: {exc}") from exc
            hd = out
        # Critter animations are the only art the temporal stage owns; a tree
        # without a multi-frame direction is left alone (the stage would refuse it).
        if temporal and (hd / "art" / "critters").exists() and any(
                len(manifest["groups"][key]["frames"]) > 1 for key in copied
                if key.startswith("art/critters/")):
            hd = self.run_temporal(hd, stage, manifest, temporal)
        atomic_json(checkpoint, {"hd": str(hd), "signature": signature,
                                 **({"gate": self.temporal_state["gate"]} if self.temporal_state else {})})
        return hd

    def accept(self, work, state, key, group, frames, candidate, provenance, raw_source=None):
        result = evaluate(self.game, group, frames, raw_source if raw_source is not None else candidate)
        if result["complete"] and raw_source is not None:
            result = evaluate(self.game, group, frames, candidate)
        gate = self.temporal_gate.get(key)
        if gate is not None:
            result["flicker"] = {"pass": gate["pass"], "reason": gate["reason"],
                                 "direction": gate["direction"], "animation": gate["animation"],
                                 "limits": gate["limits"], "keyframe": gate["keyframe"]}
            if not gate["pass"]:
                result.update(complete=False, accepted=0, rejected=result["expected"],
                              issues={"temporal_gate": gate["reason"]})
        state["groups"][key] = {**state["groups"].get(key, {}), **result, "provenance": provenance}
        if result["complete"]:
            for expected in group["frames"]:
                atomic_copy(candidate / expected["rel"], work / "hd" / expected["rel"])
            state["groups"][key]["hashes"] = {f["rel"]: file_digest(work / "hd" / f["rel"]) for f in group["frames"]}
        atomic_json(work / "state.json", state)
        return result["complete"]

    def package(self, work, manifest, state):
        """Only authoritative accepted groups; ZIP is local, review is always pending."""
        staging = self.root / "staging"
        staging.mkdir(exist_ok=True)
        archive = staging / (work.name + ".zip")
        fd, temporary = tempfile.mkstemp(prefix=".pack-", suffix=".zip", dir=staging)
        os.close(fd)
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as pack:
                for key, group in manifest["groups"].items():
                    if state["groups"].get(key, {}).get("complete"):
                        for f in group["frames"]:
                            pack.write(work / "hd" / f["rel"], "hd/" + f["rel"])
            with open(temporary, "rb") as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, archive)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        checksum = file_digest(archive)
        atomic_json(staging / (work.name + ".json"), {"zip": str(archive), "sha256": checksum,
                    "size": archive.stat().st_size, "status": "needs_review", "visual_review": "pending",
                    "warning": "Metrics cannot guarantee identity, costume, text or semantic fidelity.",
                    "coverage_complete": state["coverage_complete"]})
        return {"zip": str(archive), "sha256": checksum, "visual_review": "pending"}

    def run_job(self, name, kind, arts):
        work = self.root / "jobs" / name
        work.mkdir(parents=True, exist_ok=True)
        manifest, frames = inventory(self.game, arts)
        old_manifest = read_json(work / "inventory.json")
        if old_manifest is not None and old_manifest != manifest:
            raise ValueError(f"source inventory changed for {name}; use a new root")
        atomic_json(work / "inventory.json", manifest)
        state = read_json(work / "state.json", {"groups": {}, "attempts": {}})
        # Never trust a .done marker or a remembered accepted count after a restart.
        for key, group in manifest["groups"].items():
            old = state["groups"].get(key, {})
            if old.get("complete"):
                result = evaluate(self.game, group, frames, work / "hd")
                if any(not (work / "hd" / rel).is_file() or file_digest(work / "hd" / rel) != h
                       for rel, h in old.get("hashes", {}).items()):
                    result.update(complete=False, accepted=0, rejected=result["expected"],
                                  issues={"checkpoint": "accepted output changed"})
                old.update(result)
            state["groups"][key] = old
        # Adoption is extra history, not one of the fresh seeded retry attempts.
        if self.args.adopt_world and kind in ("tiles", "walls", "scenery", "items", "misc"):
            source, receipt = self.adopt(work, manifest)
            pending = [key for key in manifest["groups"] if not state["groups"][key].get("complete")
                       and "adoption" not in state["groups"][key]]
            if pending:
                processed = self.postprocess(work, source, manifest, pending, frames)
                for key in pending:
                    # Do not label an absent sheet as an exhausted adoption: a later
                    # restart can snapshot new legacy completions.
                    if all((source / f["rel"]).exists() for f in manifest["groups"][key]["frames"]):
                        self.accept(work, state, key, manifest["groups"][key], frames, processed,
                                    {"kind": "adopted", "all_lightning": True,
                                     "receipt": str(work / "adopted" / "provenance.json"),
                                     "note": "Lightning graph certified for this direction; other historical renders may be mixed/unknown"}, raw_source=source)
                        state["groups"][key]["adoption"] = True
        for index, seed in enumerate(self.args.seeds):
            attempt = work / f"attempt-{index:02d}"
            record = state["attempts"].get(str(index))
            if record and record.get("status") == "done":
                continue
            wanted = record["groups"] if record else [key for key in manifest["groups"]
                                                      if not state["groups"][key].get("complete")]
            if not wanted:
                break
            attempt.mkdir(exist_ok=True)
            state["attempts"][str(index)] = {"groups": wanted, "seed": seed, "status": "running"}
            atomic_json(work / "state.json", state)
            try:
                plans = self.prepare(attempt, wanted, manifest, frames)
                self.render(attempt, seed, plans)
                processed = self.postprocess(attempt, attempt / "hd", manifest, wanted, frames)
                provenance = {"kind": "generated+temporal" if self.temporal_gate else "generated",
                              "settings": SETTINGS, "seed": seed, "attempt": index,
                              "all_lightning": True}
                if self.temporal_gate:
                    provenance["temporal"] = self.temporal_for()
                for key in wanted:
                    self.accept(work, state, key, manifest["groups"][key], frames, processed,
                                dict(provenance), raw_source=attempt / "hd")
            except TimeoutError:
                raise  # unavailable/busy server: wrapper may retry without consuming a seed
            except (RuntimeError, ValueError, OSError) as exc:
                state["attempts"][str(index)]["error"] = str(exc)
                for key in wanted:
                    if not state["groups"][key].get("complete"):
                        state["groups"][key] = {"expected": len(manifest["groups"][key]["frames"]),
                                                "accepted": 0, "valid": 0, "missing": 0,
                                                "rejected": len(manifest["groups"][key]["frames"]),
                                                "complete": False, "issues": {"stage": str(exc)}}
            state["attempts"][str(index)]["status"] = "done"
            atomic_json(work / "state.json", state)
        # Rejected whole directions are absent from the accepted output. Their
        # candidate files remain in attempt directories, with explicit fallback.
        for key, group in manifest["groups"].items():
            if not state["groups"][key].get("complete"):
                state["groups"][key]["fallback"] = "original_game_art_entire_direction"
                for f in group["frames"]:
                    (work / "hd" / f["rel"]).unlink(missing_ok=True)
        if self.temporal_state:
            state["temporal"] = {key: value for key, value in self.temporal_state.items()
                                 if key != "verdicts"}
            state["temporal"]["directions"] = {
                key: {name: value[name] for name in ("pass", "reason", "direction", "animation")}
                for key, value in self.temporal_state["verdicts"].items()}
        state["source_errors"] = manifest["source_errors"]
        state["coverage_complete"] = not manifest["source_errors"] and all(
            state["groups"][key].get("complete", False) for key in manifest["groups"])
        state["status"] = "needs_review"
        state["visual_review"] = "pending"
        atomic_json(work / "state.json", state)
        package = self.package(work, manifest, state)
        coverage = {key: {k: v for k, v in value.items() if k not in ("hashes", "scores", "provenance")}
                    for key, value in state["groups"].items()}
        result = {"type": kind, "coverage_complete": state["coverage_complete"], "groups": coverage,
                  "source_errors": manifest["source_errors"], "package": package,
                  "temporal": state.get("temporal"),
                  "status": "needs_review", "visual_review": "pending"}
        atomic_json(work / "coverage.json", result)
        return result

    def run(self):
        with controller_lock(self.root):
            old = read_json(self.root / "plan.json")
            if old is not None and old != self.plan:
                raise ValueError("production plan/source changed; use original options or a new --root")
            atomic_json(self.root / "plan.json", self.plan)
            self.options = self.job_options()
            if self.options:
                atomic_json(self.root / OPTIONS, self.options)
            temporal = self.temporal_for()
            if temporal:
                print(f"production: critter temporal stage ON ({temporal['rule']})", flush=True)
            report = {"status": "running", "visual_review": "pending", "settings": SETTINGS,
                      "coverage_scope": self.plan["coverage_scope"], "unsupported_visuals": self.unsupported,
                      "source_errors": self.blockers, "jobs": {},
                      "types": {kind: {"source_files": len(arts)} for kind, arts in self.paths.items()}}
            # Preserve finished job reports during an ordinary interrupted run.
            for name, _kind, _arts in self.jobs:
                previous = read_json(self.root / "jobs" / name / "coverage.json")
                if previous:
                    report["jobs"][name] = previous
            atomic_json(self.root / "report.json", report)
            for name, kind, arts in self.jobs:
                print(f"production: {name} ({len(arts)} source files)", flush=True)
                report["active_job"] = name
                atomic_json(self.root / "report.json", report)
                report["jobs"][name] = self.run_job(name, kind, arts)
                atomic_json(self.root / "report.json", report)
            report["coverage_complete"] = not self.blockers and bool(self.jobs) and all(
                job["coverage_complete"] for job in report["jobs"].values())
            report.update(status="needs_review", active_job=None,
                          note="Local staging only. Visual review pending, especially semantic/costume drift.")
            atomic_json(self.root / "report.json", report)
            return NEEDS_REVIEW

    def dry_run(self):
        result = {"status": "dry_run", "settings": SETTINGS, "source_errors": self.blockers,
                  "coverage_scope": self.plan["coverage_scope"], "unsupported_visuals": self.unsupported,
                  "jobs": {}, "adopt_world": self.args.adopt_world, "writes": False}
        for name, kind, arts in self.jobs:
            manifest, _frames = inventory(self.game, arts)
            result["jobs"][name] = {"type": kind, "source_files": len(arts),
                                    "expected": sum(len(g["frames"]) for g in manifest["groups"].values()),
                                    "groups": len(manifest["groups"]), "source_errors": manifest["source_errors"]}
        return result


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default="/ai/wasteland-hd/production-lightning4")
    p.add_argument("--game")
    p.add_argument("--adopt-world")
    p.add_argument("--status", action="store_true", help="read saved report only; never contact ComfyUI")
    p.add_argument("--dry-run", action="store_true", help="source inventory only; no writes or generation")
    p.add_argument("--comfy", default="http://127.0.0.1:8188")
    p.add_argument("--chunk-size", type=int, default=300)
    p.add_argument("--seeds", type=lambda value: [int(s) for s in value.split(",")], default=[42, 7, 99])
    p.add_argument("--timeout", type=float, default=2100, help="seconds per child command")
    p.add_argument("--ready-timeout", type=float, default=600)
    p.add_argument("--command-retries", type=int, default=2)
    p.add_argument("--no-seams", dest="seams", action="store_false", default=True)
    p.add_argument("--stabilise", action="store_true", help="optional CPU critter stabilisation")
    p.add_argument("--temporal", action="store_true",
                   help="critter temporal stage + flicker gate (CPU; per job type in options.json)")
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if args.status:
        try:
            report = read_json(Path(args.root) / "report.json", {"status": "not_started"})
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 64
        print(json.dumps(report, indent=2, sort_keys=True))
        return NEEDS_REVIEW if report.get("status") == "needs_review" else 0
    if not args.game:
        p.error("--game is required except with --status")
    if not 1 <= args.chunk_size <= 300 or args.command_retries < 1 or not args.seeds or len(set(args.seeds)) != len(args.seeds):
        p.error("chunk-size must be 1..300; retries positive; seeds nonempty and distinct")
    if args.timeout <= 0 or args.ready_timeout < 0 or any(s < 0 for s in args.seeds):
        p.error("invalid timeout or seed")
    try:
        controller = Controller(args)
        if args.dry_run:
            print(json.dumps(controller.dry_run(), indent=2, sort_keys=True))
            return 0
        return controller.run()
    except (TimeoutError, RuntimeError, OSError) as exc:
        print(f"production: {exc}", file=sys.stderr)
        return TEMPORARY_FAILURE
    except ValueError as exc:
        print(f"production: {exc}", file=sys.stderr)
        return 64


if __name__ == "__main__":
    sys.exit(main())
