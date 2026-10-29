# Freebuff task: walls must match their neighbours inside buildings (2026-10-29)

Repo `~/wasteland-hd-tools` (main, 101 tests). Yan played the HD build on his Pixel: **inside the big
building of the first city the wall textures don't match each other** (one wall piece's HD texture /
colour / brick pattern / edge doesn't continue into the next piece). Floors already have a map-
adjacency seam check (`invention.adjacent_floor_pairs` / `seam_index` / `seam_delta`, verdict
`unmatched_floor`); walls have none. Build the same for walls, then make the repair pass fix them.

## Context
- Gate: `wasteland_hd/invention.py` (per-kind rules from `docs/GATE-NONFLOOR-REPORT.md`), repair:
  `wasteland_hd/repair.py` (batched ladder: denoise 0.55 → 0.4 → edge correction → fallback; for
  non-floor the fallback is original art). Live loop: tmux `hd-repair` reruns the working tree every
  ~10 min on `/ai/wasteland-hd/production-lightning4` → `/ai/wasteland-hd/repair-lightning4`.
- Walls in production-lightning4: jobs `walls-0000`.. (being produced now). Older full-Qwen walls
  (what the phone has today): `/ai/wasteland-hd/world-seam/hd/art/walls` (read-only, 1,657 frames).
- Map format: `gamefiles.map_art` scans object records (fid at +0x20, elevation +0x28, pid +0x2C).
  You need each wall object's **tile (hex) number** and elevation too (object record: tile at +0x04,
  see fallout2-ce `src/object.cc objectRead`: id, tile, x, y, sx, sy, frame, rotation, fid, flags,
  elevation, pid ...) and the engine's hex→screen mapping (`src/tile.cc tileToScreenXY` in
  `~/fallout2/src/imbaner_fallout2-ce-mobile`) plus each FRM's frame offsets, so you can place wall
  sprites on screen exactly like the engine (bottom-centre at the hex's screen point + offsets).

## Do
1. `wall_pairs(game)`: for every map (all of `maps/*.map`, elevations 0-2), place wall objects and
   list pairs of DIFFERENT wall FRMs (and same-FRM repeats) whose opaque sprite areas touch or overlap
   on screen (junctions along a wall line, corners). Cache it like the floor index. Report counts;
   include the Arroyo and Klamath maps (first cities: `arvillag`, `arcaves`?, `klamall`, `kladwtwn`,
   `klatoxcv`, …) in a printed sample.
2. `wall_seam_delta(hd_a, hd_b, orig_a, orig_b, placement)`: composite both pieces at their real
   relative offset at 2x (original at 2x nearest vs HD), measure discontinuity across the junction
   band (e.g. 6 px each side of where the two alphas meet): extra colour step / pattern break in HD
   beyond what the original pair has. Verdict `unmatched_wall` above a calibrated limit.
3. Calibrate on the world-seam walls (what the phone shows now): make contact sheets of the worst
   junctions (orig pair | HD pair, 2x) in `tmp-wallseam/`, LOOK at them, label ≥30 junctions, pick
   the limit. Also render 4 real interiors composited from placements (Arroyo + Klamath buildings)
   original vs world-seam HD vs production-lightning4 HD where available → `tmp-wallseam/interiors/`.
4. Wire it into `repair.py`: a wall direction that is `unmatched_wall` against any neighbour that is
   itself accepted enters the ladder; add a CPU correction step for walls like the floor edge lock
   (blend the junction band toward the original 2x edge colours / the accepted neighbour) before
   falling back to original art. Never leave a junction where one side is HD and the other a
   fallback without checking that junction too.
5. Tests with synthetic pieces + mocked Comfy; whole suite green; tiles behaviour byte-identical.

Rules: no GPU calls yourself, no services, don't write under /ai, don't restart live loops, do NOT
commit. Finish by writing `docs/WALL-SEAMS-REPORT.md`: pairs found, calibration table, before/after
unmatched counts on world-seam and on the walls jobs already produced, interior renders, and what
the repair will do.
