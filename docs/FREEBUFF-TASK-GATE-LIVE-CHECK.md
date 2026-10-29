# Freebuff task: check the calibrated gate against the live repair pass (2026-10-29)

Follow-up to your own `docs/FREEBUFF-TASK-GATE-NONFLOOR.md`. Only start this after
`docs/GATE-NONFLOOR-REPORT.md` is written — finish that first.

The repair loop (`~/fallout2/tools/hd-repair-loop.sh`, tmux `hd-repair`, output
`/ai/wasteland-hd/repair-lightning4`) reruns every ~10 minutes and picks up whatever is on disk in
this repo. It is running live next to `hd-production`. Your job is to confirm the new per-kind
calibration actually reaches a real repair pass, and to record what it does there.

## Do

1. **Read the wiring, don't guess**: confirm `wasteland_hd/repair.py` passes `kind=` into
   `invention.score` for every direction it scores (walls/scenery/items/misc and tiles), and that
   `invention.kind_of()` maps the paths you calibrated. Cite file:line.
2. **Catch a live pass**: watch `/ai/wasteland-hd/repair-lightning4.log` and
   `/ai/wasteland-hd/repair-lightning4/jobs/*/state.json` (read-only) until a pass that started
   *after* your code landed has finished. Record its job, accepted / fallback counts, and per-kind
   verdict counts.
3. **Regression guard**: `python3 -m pytest -q` must be all green, and the 20 tile calibration
   labels in `docs/REPAIR-GATE-REPORT.md` must still classify the same. Paste the numbers.
4. **No surprises**: no Traceback in the repair log, tiles behaviour unchanged (tiles must stay
   byte-identical to before your change), non-floor fallbacks go to original art and never to an
   invented texture.

## Finish

Write `docs/GATE-LIVE-CHECK.md`: what you verified with file:line, the live pass numbers, per-kind
verdicts, pytest result, and anything that still looks wrong.

## Rules

Do not restart or kill anything — no loops, no services, no ComfyUI, no GPU. Read-only outside this
repo (no writes under `/ai`, no edits in `~/fallout2`). Do NOT commit; Claude/Yan review and
commit. No network, no adb.
