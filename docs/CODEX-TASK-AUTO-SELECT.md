# Codex task: pick the best version of every texture automatically (2026-10-29)

Yan (owner): "how do you want me to approve everything manually when there are thousands of
textures — and some HD [production] versions are better than the repaired ones." Manual review is
only for a small spot-check sample. Build automatic best-of selection.

Repo `~/wasteland-hd-tools` (main). Read `docs/REPAIR-GATE-REPORT.md`, `docs/REPAIR-BATCH-REPORT.md`,
`docs/GATE-NONFLOOR-REPORT.md`, `wasteland_hd/{invention,qa,repair}.py`, and the pack builder
`~/fallout2/tools/hd-pack-build.py` + `~/fallout2/docs/HD-PACK-BUILD-REPORT.md`.
Other agents are editing `invention.py`, `repair.py` (wall joins) and `~/fallout2/tools/hd-pack-build.py`
(per-category policy): **do not edit those files**. Put everything in a new module
`wasteland_hd/select.py` (+ CLI `python -m wasteland_hd select`) that the pack builder will call later.

## Candidates per direction
- production accepted output (`production-lightning4/jobs/<job>/hd/`), and production attempt candidates
  that passed integrity but were rejected only by QA/invention (in `attempt-*/` cut candidates);
- every repair rung output (`repair-lightning4/jobs/<job>/attempts/...` and its `hd/`);
- the fallbacks (`source_floor_2x` for floors, original art otherwise).

## Selection
1. Hard constraints (never relaxed): integrity (RGBA, exact 2x, exact alpha); not `unmatched_floor` /
   `unmatched_wall` (when wall verdicts exist); not `invented_texture` per the current gate; QA colour
   badness ≤ 1. A `minor_invention` candidate is allowed (Yan: small additions like a pipe/skull are fine).
2. Among survivors, score = detail/sharpness gain over the original 2x nearest (high-frequency energy
   that is consistent with the original's structure at native scale), minus penalties for novelty
   area and colour drift. Fit the weights so the choice agrees with a judge (step 3), not by hand.
3. Vision judge for close calls and for calibration: NVIDIA NIM (OpenAI-compatible
   `https://integrate.api.nvidia.com/v1/chat/completions`, key in `~/.config/nvidia-nim/env` as
   `NVIDIA_API_KEY` — never print it) with `nvidia/nemotron-3.5-super-vl-120b-a12b` (fallback
   `meta/llama-3.2-90b-vision-instruct`). Show original (2x nearest) + candidate A + B side by side, ask which is the
   better faithful HD version of the original (same content, sharper, no invented objects/materials),
   answer A/B/tie as JSON. Cache every judgement on disk. Budget: ≤ 600 judge calls today. Measure
   judge consistency (swap A/B order on 50 pairs) and report it; if inconsistent, don't let it decide.
4. Output `selection.json` per job: chosen source + reason + scores, and an override tree
   `<out>/jobs/<job>/hd/` with the chosen PNGs, plus a `spotcheck.json` of 40 stratified examples
   (most-changed decisions first) for Yan's review app.

## Deliver
Run it on all completed jobs (tiles-0000..0010, walls-0000..0005, scenery done so far) with
`--out ~/wasteland-hd-tools/tmp-select` (gitignored); report how many directions switch from repaired
→ production and vice versa per job, judge agreement, and 3 contact sheets (switched-to-production,
switched-to-repair, kept). Tests with synthetic images + mocked judge; whole suite green.
Commit if `.git` is writable, else leave unstaged. Rules: no GPU, no services, no writes under /ai,
don't print the key. Finish with `docs/AUTO-SELECT-REPORT.md`, including the exact integration call
for the pack builder.
