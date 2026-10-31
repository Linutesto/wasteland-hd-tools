# Autonomous local asset production

Run from the tools checkout with the player's readable Fallout 2 data:

```sh
python3 -u -m wasteland_hd.production \
  --game /home/yan/fallout2/gamedata \
  --root /ai/wasteland-hd/production-lightning4 \
  --adopt-world /ai/wasteland-hd/batch4/world
```

The controller writes only its production root. It does not manage services,
stop another runner, install assets, commit, delete Git content, or publish.
The older `/ai/wasteland-hd/production` controller still belongs to the
historical full-Qwen workflow. **Never point the Lightning controller at that
root or start it alongside the old GPU controller.** Hand over only after the
existing GPU workload has finished or has been cleanly stopped by its owner.
The default root is `/ai/wasteland-hd/production-lightning4`; its plan version
and immutable settings prevent accidental resume with a different workflow.
An advisory file lock prevents two controllers using the same production root.
Different roots do not share that lock; coordinate GPU ownership externally.
Before each new GPU command it waits for a healthy ComfyUI and an empty queue,
without interrupting existing work. This is a readiness check, not an exclusive
lock on the ComfyUI server.

Inspection commands never write or use the GPU:

```sh
python3 -m wasteland_hd.production --root /ai/wasteland-hd/production-lightning4 --status
python3 -m wasteland_hd.production --game /home/yan/fallout2/gamedata \
  --root /ai/wasteland-hd/production-lightning4 --adopt-world /ai/wasteland-hd/batch4/world --dry-run
```

`--status` reads the last atomic report; it is safe while the controller runs.
It does not perform a live file audit. `--dry-run` decodes source FRMs to derive
expected frame counts, and reports source failures without inventing frames.

## Source coverage and ordering

The inventory unions archive and loose `.frm`/`.fr0`–`.fr5` files with filename
aliases from every `ART_TYPES` list. Critter lists contain base names, so their
animation filenames come from actual archive/loose files. Each split-direction
file uses its extension's direction and its internal direction zero. Shared
FRM directions are counted once, matching the existing engine lookup scheme.
Zero-size frames have no HD rectangle and are excluded. Invalid/truncated
sources and missing list aliases are explicit `source_errors` blockers.
Missing aliases have **unknown expected frame counts**, not zero invented
frames. Source errors prevent coverage completion even if every readable FRM
frame has an accepted output.

Scope is **all supported FRM art categories**, including interface, heads,
inventory, backgrounds and skilldex. RIX splash pictures and MVE cutscenes are
listed under `unsupported_visuals`; they are not decoded or generated. FRM
coverage does not imply coverage of all visual formats or renderer support for
all these categories. The October 28 source audit found 235,139 readable FRM
frames and 54 absent list aliases; 7 RIX and 13 MVE files are outside this scope.
These are source-specific observations, not fixed expected totals in the code.

Jobs run in this order: tiles, walls, scenery, items, misc, intrface, inven,
backgrnd, skilldex, heads, critters. Each job has at most 300 source art files
(`--chunk-size 1..300`); it keeps only one job's decoded source frames in memory.
A single exceptionally large animation can still exceed typical memory needs.

## Rendering, adoption and rejection

New production renders use **Qwen Image Edit 2511 Lightning 8-step**, LoRA
`Qwen-Image-Edit-2511-Lightning-8steps-V1.0-bf16.safetensors` at strength 1,
**8 Euler/simple steps, cfg 1, seed 42, AuraFlow shift 3**, and Lanczos input
scaling; static sheets render at **denoise 1** and animation (critter) sheets at
**denoise 0.80** (`animation_denoise`), the highest tested rung that keeps
helmets, hoods and suit bulk exactly as drawn (`docs/CRITTER-PROMPT-REPORT.md`). Loaders are `qwen_image_edit_2511_fp8mixed.safetensors`,
`qwen_2.5_vl_7b_fp8_scaled.safetensors`, and `qwen_image_vae.safetensors`.
PROMPT, ANIMATION_PROMPT and NEGATIVE from `wasteland_hd/upscale.py` are pinned
by SHA256 in the production plan and verified against each PNG's ComfyUI graph.
Native sheets are **256**, Qwen renders at **4×**, and final RGBA frame output
is cut down to **2×**, with alpha copied by exact nearest-neighbour scaling.
The model/scale/seed settings are verified before a generated PNG can be cut.
The `upscale --quality full/auto` CLI modes, and the `--prompt-file`/`--denoise`
experiment overrides, remain for explicit experimental use only; the durable
production controller always selects Lightning, for animations as well as static
sheets, and passes its own denoise per sheet type. Oversize source
sprites necessarily use larger sheets, as in `sheets.pack`; that exception
can still cause model placement drift. Animated art and every critter use
`by_animation=True` packing and the animation prompt. Very long directions
can span sheets, so this does not guarantee a shared identity across every
sheet. A rejected frame causes the **entire animation direction** to be
redrawn; frames from different attempts are never combined for acceptance.

Seeds default to `42,7,99` (`--seeds` changes the bounded budget). An accepted
direction is copied into the job's `hd/` and committed to atomic state only
after every frame passes. Exhausted directions are removed from that accepted
tree; candidates remain in attempt directories. Their fallback is explicitly
`original_game_art_entire_direction`. Processing continues with other jobs.

Before colour/structure QA, the entire direction must have readable RGBA PNGs,
exact 2x dimensions and alpha byte-identical to nearest-neighbour source alpha.
**Raw** candidates must pass QA before optional CPU processing. Floor tiles
also require spatial fidelity: after reducing to native size and blurring at
sigma 1.5 native pixels, mean RGB error must be at most 6/255. Invisible RGB
is replaced with the native background before this comparison. This was added
after the Arroyo 4x A/B test passed invented planks through mean-colour QA.
The threshold is conservative and does not establish semantic correctness or
scene-wide coherence; mixed original/HD fallbacks still require visual review.
Floor seams
are enabled by default (`--no-seams` disables them); `--stabilise` optionally
runs the existing critter stabiliser. Postprocessed candidates must pass the
integrity and QA gates again. Stabilisation is off by default: it adds CPU work
and its measured benefit has been limited. Neither QA nor stabilisation proves
semantic fidelity or costume consistency.

Adoption snapshots completed legacy sheets into each job's `adopted/`, retains
their original PNG metadata, hashes and paths, and never modifies legacy work.
Only exact Lightning-compatible graphs validated by `upscale.render_matches`
enter the accepted pipeline: 8 steps, cfg 1, seed 42, render4, Lightning LoRA,
the correct art/animation and negative prompts, sampler, scheduler, shift, model
loaders and LoRA strength. Full-Qwen, missing metadata, incomplete sheets and
missing directions require fresh source-derived attempts. Certified adopted
directions have `all_lightning=true`; unverified/mixed historical snapshots
retain `historical_mixed_or_unknown` provenance and are never certified.
Per-sheet CLI skip validation is also used for newly generated work.

## Durable state and artifacts

The root contains `plan.json`, `report.json`, `controller.lock`, `jobs/`, and
local `staging/`. Every job has:

- `inventory.json`: source hashes, expected directions, dimensions, alpha hashes,
  and source errors derived independently of generated PNGs.
- `state.json`: accepted direction hashes, provenance, seeded attempt budgets,
  missing/rejected counts, reasons and explicit fallbacks.
- `coverage.json`: expected, valid, accepted, missing and rejected frame counts
  per direction. Valid means integrity passed; a rejected direction can contain
  individually valid frames. Acceptance always applies to the whole direction.
- `attempt-NN/`: sheets, cut candidates, CPU stages and per-sheet command logs.
  `command.json` records durable invocation budgets; `cut.json` records completed
  render hashes. Restarting reuses completed renders and rechecks accepted files.
  If generation succeeded but `cut.json` was interrupted, an intact matching
  PNG is certified without another GPU call. If a previously successful render
  is missing, corrupt, or has the wrong graph/hash, the old evidence (including
  its command checkpoint and log) is moved into `renders/NAME/stale/TIMESTAMP/`
  before a fresh bounded invocation. No mismatched PNG is silently adopted.
- `hd/`: accepted outputs only. Atomic state is authoritative during a crash;
  an interrupted direction copy can leave files that have not been accepted yet.

`staging/JOB.zip` contains only accepted `hd/...` paths, compatible with the
existing pack installer. `staging/JOB.json` records SHA-256, byte size, coverage
and pending visual review. Coverage remains in external JSON, outside the ZIP.
Even a partial or empty job produces a clearly labelled review artifact.
No pack is automatically installed or uploaded.

JSON checkpoints, reports and package receipts use fsync plus atomic rename.
ZIP replacement is atomic and its checksum is in the subsequently replaced
receipt; an interruption between those writes can leave a stale checksum,
which a resumed run rebuilds. Accepted file hashes and source dimensions/alpha
are rechecked on resume. Changing source files, quality, seeds, chunk boundaries,
adoption path or postprocessing options requires the original options or a new
root. Corrupt JSON fails with its path; it is never silently reset to an empty
successful checkpoint. Keep it for diagnosis or restore a known checkpoint.

## Wrapper contract

| Exit | Meaning | Wrapper action |
| --- | --- | --- |
| 0 | Read-only status/dry-run completed | No production completion implied |
| 2 | Production exhausted its work; `needs_review` (also returned by terminal status) | Stop automatic restart; inspect coverage and staging |
| 64 | Plan mismatch, invalid checkpoint/configuration | Stop; operator correction required |
| 75 | Busy/unavailable server, lock held or other temporary controller failure | Retry with backoff |

Argparse usage errors also return 2 and print usage to stderr, before production.
Unexpected programming exceptions exit nonzero with a traceback. A wrapper
should cap automatic infrastructure retries and alert on repeated failures.
Terminal `needs_review` can have incomplete coverage because of source aliases,
rejected directions or unsupported semantics; it never means visual approval.

`--ready-timeout` defaults to 600 seconds; `--timeout` is 2100 seconds **per
sheet/CPU child**, not per 300-art job. `--command-retries` defaults to 2 and is
persisted per sheet across process restarts. GPU errors exhaust that sheet's
budget and processing advances to other sheets and later seeded attempts.
Readiness timeouts and CPU postprocess failures retain the GPU attempt so a
wrapper restart does not consume a new seed or redraw successful sheets.
A timed-out HTTP client can leave a prompt running on the server; the next
attempt waits for the queue to clear and never calls a global interrupt.
CPU-stage temporary directories may remain after failures for diagnosis.

Progress is printed with flushing per job and generated sheet; child detail is
in per-command logs. Full production can take weeks. Automatic metrics cannot
guarantee perfection, readable UI text, correct faces, costumes or identities.
Review representative animations, clothing, interfaces and map combinations
before any distribution.

## CPU verification

```sh
python3 -m pytest -q tests/test_production.py
python3 -m pytest -q
```

The controller tests mock subprocess generation and HTTP readiness. They do
not start GPU jobs, modify legacy data or prove visual quality.

## Repair pass

`python3 -m wasteland_hd.repair --root PRODUCTION_ROOT --out REPAIR_ROOT --audit-only`
rescans completed jobs with the CPU invention gate and writes only
`REPAIR_ROOT/report.json`. The gate measures absent edges at native and 2×
resolution, connected novel regions, damage to originally flat areas, tile
border drift, and extra seam contrast for tile pairs placed next to each other
in the game's map grids. Real map adjacency is cached in the repair root during
a repair run. `minor_invention` remains accepted and is marked in coverage;
`invented_texture` and `unmatched_floor` require repair.

Without `--audit-only`, the separate repair root gets resumable, bounded
Lightning attempts at denoise 0.55 then 0.4, each certified by its PNG graph
and checked by integrity, existing QA and the invention gate. Unmatched floors
can then receive a feathered source border. Remaining floors use the safe
`source_floor_2x` fallback; other failed directions are omitted from the new
pack. Its `jobs/JOB/hd/` contains overrides, and `staging/JOB.zip` copies the
production pack with overrides and failed directions removed. `coverage.json`
and the ZIP receipt record verdicts, fallbacks and hashes. Every pack remains
pending human review. The repair controller reads production files only and
waits for the existing ComfyUI readiness check before each GPU call. Queue
readiness is not a server-wide reservation; coordinate concurrent controllers.
