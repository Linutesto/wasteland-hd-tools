# Repair batch report — 2026-10-29

## Design

The repair controller now gathers every unresolved direction in a production job for each ladder step. It packs static and animated frames separately on 256-pixel native sheets using `sheets.pack`; animated sheets use `by_animation=True` and the animation prompt. One verified ComfyUI render is made per sheet, then `sheets.cut_sheet` restores the per-frame alpha and each direction passes the existing integrity, QA, invention, and seam gates. The denoise/seed ladder remains **0.55/1009**, then **0.4/1013**. Failed directions alone enter the second step. Edge lock and `source_floor_2x` remain the same final floor fallbacks.

Each job and step writes a fixed `attempts/batch/denoise-N/sheets.json` key snapshot, native sheet, rendered PNG, graph/source receipt, and render budget JSON under the repair output root. On restart, the snapshot keeps sheet contents and indices stable even if some directions have since been accepted. A rendered sheet with the correct dimensions, graph, settings, prompt, seed, denoise, and source receipt is cut again without another ComfyUI call. A valid PNG written just before an interrupted call is also reusable. Per-direction results still use `jobs/<job>/state.json` with hashes and provenance; the old accepted entries are read as-is and skipped. The repair plan format is unchanged.

Jobs remain sorted by their existing names. Step a visits all jobs in order before step b visits them; remaining fallbacks run afterward. A direction accepted in either step is saved immediately, so a later interruption retains its result. Packaging, coverage, and the report are written after the ladder and fallbacks complete.

## Verification and expected calls

`python3 -m pytest -q -p no:cacheprovider`: **78 passed**. Mocked ComfyUI tests cover shared sheets, static versus animated prompts, several sheets, restart after output but before checkpoint, reuse of old accepted per-direction state alongside new sheet state, one-direction ladder movement, both floor fallbacks, and step-a job order. No GPU call was made.

The number of ComfyUI calls is the number of 256 sheets packed from unresolved directions at step a, plus sheets packed from directions that fail step a at step b. It is not one call per direction. From the read-only `tiles-0000` production inventory, a first set of 100 tile directions packed into **10 sheets**; a fixed random sample of 100 also packed into **10**; all 300 packed into **30**. Thus a typical 100-direction repair set would take about **10 step-a calls**, plus any step-b sheets, rather than 100 calls. This is an estimate from native frame dimensions; the actual pending set, animation grouping, and oversized frames determine the count. The quality gates still decide acceptance per direction.

## Safe switch for Claude

The live `hd-repair` tmux loop and `/ai` output were untouched by this change. The existing loop script starts a fresh Python process on every iteration and sleeps 600 seconds after each successful pass. Switch during that sleep, after confirming the current pass exited:

1. From `/home/yan/wasteland-hd-tools`, confirm this change is present and run `python3 -m pytest -q -p no:cacheprovider`. Record the current `git rev-parse --short HEAD` and check `git status --short`.
2. Inspect the live loop: `tmux display-message -p -t hd-repair '#{pane_pid}'` gives its shell PID; `pgrep -af 'python3 -u -m wasteland_hd.repair'` shows any active repair pass; `tail -n 5 /ai/wasteland-hd/repair-lightning4.log` shows the latest `repair exit` line. Wait for an `exit 0` and for the repair Python process to disappear. Do not interrupt an active ComfyUI call.
3. While the loop is sleeping, stop only the loop shell with `kill -TERM <pane_pid>`. Confirm `tmux has-session -t hd-repair` fails and `pgrep -af 'python3 -u -m wasteland_hd.repair'` shows no repair process. If a pass was spawned during the handoff, wait for it to finish before starting a replacement.
4. Start the same loop with the new checkout: `tmux new-session -d -s hd-repair 'cd /home/yan/wasteland-hd-tools && exec bash /home/yan/fallout2/tools/hd-repair-loop.sh'`. No new output root or state migration is needed.
5. Check `tmux has-session -t hd-repair`, inspect the new log tail, and verify `jobs/<job>/attempts/batch/denoise-0/sheets.json` and `static-*-render.json` appear under `/ai/wasteland-hd/repair-lightning4` as the first pending job runs. Accepted old directions should remain accepted without new per-direction renders.

If a saved sheet snapshot ever conflicts with newly pending keys, the controller fails before rendering that step. Inspect the checkpoint and state together before deciding whether to use a separate repair output root; do not delete prior renders or state to make it run.
