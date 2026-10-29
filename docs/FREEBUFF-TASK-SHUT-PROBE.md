# Freebuff task: why the `shut` tile family fails, and a recipe that works (2026-10-29)

Repo `~/wasteland-hd-tools`. All 95 `art/tiles/shut*` tiles (production job tiles-0007) failed every
Lightning attempt and every repair rung (denoise 0.55, 0.4) and shipped as `source_floor_2x` (soft
original). Find out why and find a recipe that produces faithful HD for them.

Evidence (read-only): `/ai/wasteland-hd/production-lightning4/jobs/tiles-0007/` (attempt-*/ renders,
candidates, state.json issues), `/ai/wasteland-hd/repair-lightning4/jobs/tiles-0007/` (batch attempts,
state.json gate verdicts + metrics), `wasteland_hd/{upscale,sheets,qa,invention,floor_fidelity}.py`,
`docs/REPAIR-GATE-REPORT.md`. Originals: `qa.original_frame(Game('/home/yan/fallout2/gamedata'), {}, rel)`.

## Do
1. Contact sheets (orig 2x | best candidate per rung) for ~24 shut tiles into `tmp-shut/` (gitignored);
   LOOK at them; tabulate which gate rejects them and with which metric (novel area? flat damage?
   border? QA colour?). Say whether the gate is wrong (false rejects) or the model is wrong.
2. If the model is wrong: try up to **20 sheet renders total** of alternative recipes on 8 shut tiles —
   e.g. lower denoise (0.3, 0.2), a shut-specific prompt line (what the tiles depict), different sheet
   packing (one tile per sheet, more context), render-scale 2 — ONLY through the repo's upscale path
   (`python -m wasteland_hd upscale --work <dir> ...` which waits for an empty ComfyUI queue), writing ONLY
   under `/ai/wasteland-hd/shut-probe/`. Gate every result with `invention.score` + `qa.badness`.
   If the gate is wrong: propose the exact threshold/rule change with the evidence.
3. Recommend one recipe (or "keep the soft fallback") with numbers and sheets.

Rules: ComfyUI is shared with live production and repair — never stop/restart services, never call
interrupt/free endpoints, max 20 renders, no writes under /ai except shut-probe/. Don't edit
invention.py/repair.py/production.py (other agents own them); put your code in `tools/shut_probe.py`.
Do NOT commit. Finish by writing `docs/SHUT-PROBE-REPORT.md`.
