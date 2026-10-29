# Freebuff task: wire the critter temporal stage into production (flag, default off) (2026-10-29)

Repo `~/wasteland-hd-tools` (main). Read `docs/CRITTER-TEMPORAL-REPORT.md` (its "Production
integration (proposed)" section), `docs/FLICKER-TEST-REPORT.md` (the gate), `docs/AUTONOMOUS-PRODUCTION.md`,
`wasteland_hd/production.py`, `temporal_stage.py`, `flicker.py`.

You own ONLY: `wasteland_hd/production.py`, `wasteland_hd/temporal_stage.py`, `tests/test_production.py`,
new tests. Another agent edits `invention.py` / `repair.py` — don't touch them.

## Do
1. Add `--temporal` (default OFF) to the production controller: after a critter job's directions are
   cut (and after the existing optional `--stabilise`), run the temporal stage per direction, then the
   flicker gate per animation (broad ≤ 2.5, material ≤ 3.0, flicker ≤ 6.0, direction cap 2x mean). Failing
   directions → next seed attempt; exhausted → `original_game_art_entire_direction` fallback, exactly like
   other rejections. Record the flicker metrics in coverage/state. Integrity + QA gates still apply after.
2. **CRITICAL compatibility:** the live root `/ai/wasteland-hd/production-lightning4` has an immutable
   `plan.json` (settings, seeds, chunk size, seams, stabilise...). With `--temporal` OFF the plan must be
   byte-for-byte what the current code writes, so restarting the live controller with your code resumes
   without exit 64. With `--temporal` ON, the option must enter the plan only in a way that lets the
   existing root resume: add it as a per-job-type setting that applies only to jobs not yet started
   (critters haven't started), and document it; if that is impossible, say so and propose the restart
   procedure instead. Add a test that loads a copy of the live plan.json (read it, don't modify it)
   and proves resume compatibility.
3. Tests with mocked Comfy and synthetic critter frames: stage runs only for critters, gate rejection
   moves to the next seed, fallback, resume after interruption. Whole suite green (`python3 -m pytest -q`).
4. Budget estimate for the 7,119 critter files (CPU time of the stage + gate).

Rules: no GPU, no services, don't write under /ai, don't restart anything, do NOT commit.
Finish by writing `docs/CRITTER-PRODUCTION-REPORT.md` (design, compatibility proof, exact restart
command for Claude, what happens on the first critter job).
