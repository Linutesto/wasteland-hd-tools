# Critter soft fallback

Repair now uses `source_soft_2x` as the last rung for `art/critters/` directions. Each original frame is processed on the CPU in the same deterministic way: transparent RGB is filled from the nearest visible source pixel, RGB is resized with Lanczos, and alpha is replaced with the original's exact nearest-neighbour 2x alpha. Fully transparent frames retain their nearest-neighbour pixels. Tiles, walls and other art keep their existing fallback paths.

On the next normal repair pass, a saved accepted critter direction whose fallback or provenance is `original_game_art` is regenerated directly from the original frames, checked by integrity, QA and invention gates, copied to the repaired HD tree, and checkpointed as `source_soft_2x`. It does not enter the render ladder. Packaging includes the new frames in the repair staging ZIP. The repair plan and its digest are unchanged. Subsequent runs reuse the checkpoint when its hashes match. If the soft frames fail a gate, repair reports an error rather than silently shipping unverified output.

`select` already discovers the repair HD tree as `repair_accepted` and ranks gate-passing frames ahead of its original-art fallback. The synthetic test exercises this path; no selection rule change was necessary.

## Verification

- `python -m pytest -q`: **139 passed in 5.20s**. New tests cover transparency and halo avoidance, exact alpha, determinism, fresh fallback, legacy migration, plan digest, staging ZIP, selection, and repeat-run reuse with rendering forbidden.
- Read-only game data from `/home/yan/fallout2/gamedata`: all **17** frames of `hanpwrbl/0` and **22** frames of `harobebf/1` passed QA and invention with the soft result. The latter uses per-direction FR1 data.
- Inspected `tmp-soft/hanpwrbl-0.png` and `tmp-soft/harobebf-1.png`, plus enlarged first-frame details in the same directory. The soft versions reduce internal blockiness while preserving the silhouette and source colours; no obvious dark or coloured fringe was visible in these samples. This is a visual spot check, not an assessment of every game sprite.
- No GPU, ComfyUI, services, or production files were used or modified for these checks.
