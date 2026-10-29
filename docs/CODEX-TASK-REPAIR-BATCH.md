# Codex task: batch the repair pass into sheets (2026-10-29)

Follow-up to your docs/REPAIR-GATE-REPORT.md (commit 6cd456d). `wasteland_hd.repair` is running live
(tmux `hd-repair`, loop script ~/fallout2/tools/hd-repair-loop.sh, out /ai/wasteland-hd/repair-lightning4)
next to hd-production. Measured: ~2.5 directions/min, because each failed direction is its own ComfyUI
render; production emits ~7 failures/min, so repair falls behind.

Do:
1. Pack the failed directions of a job per ladder step into shared sheets exactly like production
   (`sheets.pack`, static vs `by_animation` rules, 256 native sheets, render 4x, cut to 2x, alpha copy),
   one GPU call per sheet; per-direction gating/acceptance unchanged; a direction that fails moves to the
   next step in the next batch. Keep resumability (completed sheet renders reused after restart) and
   bounded budgets. Existing per-direction attempts already on disk must stay valid on resume (don't redo
   accepted ones; migrate or ignore stale in-progress state safely).
2. Print one flushed progress line per sheet and per job summary (`repair: <job> step=<..> sheet i/n`,
   `repair: <job> done clean=.. fixed=.. fallback=..`) so the log is useful.
3. Tests (mocked Comfy): batching, resume mid-batch, ladder progression across batches. Whole suite green.
4. Do NOT stop or restart the live repair process or production; don't write under /ai. Claude restarts it.
5. Commit if .git is writable, else leave changes unstaged. Write docs/REPAIR-BATCH-REPORT.md: design,
   expected GPU calls per job before/after, and the restart command.
Rules: no GPU calls, no services, no secrets.
