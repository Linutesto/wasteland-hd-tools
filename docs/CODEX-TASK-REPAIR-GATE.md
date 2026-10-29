# Codex task: invention gate + automatic corrective retry (2026-10-29)

From Claude. Repo `~/wasteland-hd-tools` (main, HEAD 7559487, 66 tests pass).
Read `docs/AUTONOMOUS-PRODUCTION.md` and `wasteland_hd/{production,qa,floor_fidelity,seams,upscale}.py` first.

## Context

`hd-production` (systemd --user) is running the Lightning controller on
`/ai/wasteland-hd/production-lightning4` and MUST keep running. Do not stop it,
do not write into that root, do not touch ComfyUI/qwen38, do not call the GPU.
Everything you build is CPU-tested with mocks, like `tests/test_production.py`.

Claude's validation of jobs tiles-0000..0002 (617 accepted frames):
`/ai/wasteland-hd/lightning-validation-20261029/REPORT.md` (+ contact sheets,
`local-drift.json`, `novel.json`). All existing gates pass, yet some accepted
tiles have invented content, because the floor gate (blur σ1.5, mean RGB ≤6/255)
averages small/medium structure away.

## Owner's policy (Yan) — this is the spec

- ACCEPTABLE (do not fail): a small local addition on an otherwise faithful
  tile — a plant, a pipe, a skull, a rock in the desert. Flag it as `minor_invention`
  in coverage for human review, keep it accepted.
- FAIL — "totally invented texture": the tile's material/pattern was replaced
  or a large part of it is new structure (e.g. a flat dim floor turned into a
  brick/bar grid; a flat magenta/flat-colour region given texture or stripes).
- FAIL — "unmatched floor": the tile no longer matches its neighbours: its
  border band disagrees with the original's border (seams appear when tiled), or,
  for tiles that are actually adjacent in real maps, the HD pair shows a seam
  that the original pair does not.

Calibration labels from Claude's visual review (tiles in production-lightning4, jobs 0000-0002):
- must FAIL (invented texture): brick14, brick20, chur071, brdb045, brdb004, brda057, brda058
- borderline, decide by measurement and document: curb29, curb32, enfl032, carpet24, crta006
- must PASS as `minor_invention`: edg7002 (red objects + pipes added on dirt)
- must PASS clean (faithful sharpening, high raw error): bld1043, bld1034, bld1054, enfl012, bld1021, bld1069, brdb016
Look at the PNG pairs yourself (original = `qa.original_frame`, HD = `jobs/<job>/hd/<rel>`) before trusting any metric.

## Do

1. `wasteland_hd/invention.py`: CPU metrics on (hd, original) per frame, at
   native and 2x scale: novel-edge area map (edges in HD absent from a dilated
   original edge map; see Claude's prototype
   `/ai/wasteland-hd/lightning-validation-20261029/novel.py`),
   largest connected novel region and total novel fraction, flat-region
   preservation (regions of near-constant colour in the original must stay
   near-constant), border-band agreement, and map-adjacency seam delta using real
   map placements (`gamefiles.map_art` / existing `seams.py`; build the adjacency
   list once, cache it under the output root). Return a verdict
   `clean | minor_invention | invented_texture | unmatched_floor` with numbers.
   Thresholds are module constants, calibrated on the labels above; every label
   must classify as stated (borderline: document what they got and why).
2. `wasteland_hd/repair.py` (`python -m wasteland_hd.repair --root <production root> --out <repair root>`):
   - reads only completed jobs of the production root (state/inventory/hd), never writes there;
   - re-scores every accepted direction with the new gate, and also takes the
     production's rejected directions (QA colour/structure) as repair candidates;
   - corrective retry ladder per failed direction, each step through the same
     integrity + QA + floor + new gate before acceptance:
     a. Lightning with reduced denoise on the Lanczos-upscaled source (structure-preserving; try e.g. 0.55 then 0.4),
        new seeds, same models/prompts otherwise; add the graph difference to `render_matches`/settings provenance;
     b. CPU correction for `unmatched_floor` only: replace the border band with `floor_fidelity.source_floor_2x`
        border (edge_lock) and feather inwards; re-gate;
     c. final fallback for floors: `source_floor_2x` (soft source) — never ship an invented texture.
   - GPU access only through the existing `Comfy` readiness wait (empty queue) so it can
     interleave with production; bounded budgets persisted like production (resumable, atomic JSON, lock on its own root);
   - output: `<out>/jobs/<job>/hd/` overrides + `<out>/staging/<job>.zip` that is the production
     zip with overrides applied and gate-failed directions removed/replaced, plus a receipt and
     `coverage.json` listing verdicts, `minor_invention` flags and fallbacks.
   - `--audit-only`: score and report, no GPU, no outputs except a report JSON.
3. Tests in `tests/test_invention.py` and `tests/test_repair.py`: synthetic images for each
   verdict, mocked Comfy, resume after interruption, never writes into the production root,
   ladder order, fallback. Whole suite must stay green.
4. Run `python -m wasteland_hd.repair --root /ai/wasteland-hd/production-lightning4 --out <repo>/tmp-audit --audit-only`
   (tmp-audit gitignored) on all completed jobs and put the counts per verdict per job and
   a few named examples per class in the report.
5. Update `docs/AUTONOMOUS-PRODUCTION.md` with a short "Repair pass" section.
6. Commit to main (`feat(repair): …`), don't push.

## Verify

`python3 -m pytest -q` all pass; the audit command exits 0; label table in the report.

## Rules

No GPU calls, no service changes, no writes outside this repo, no secrets printed.
End by writing `docs/REPAIR-GATE-REPORT.md`: what was built, calibration table
(label → verdict → numbers), audit counts, the exact command Claude should run to
start the real repair pass, and anything you're unsure about.
