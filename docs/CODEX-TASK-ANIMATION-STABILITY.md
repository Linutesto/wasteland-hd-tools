# Codex task: stop HD critter animations from "boiling" (2026-10-28, job 4)

From Claude. Repo `~/wasteland-hd-tools` (main). Read README.md and
`wasteland_hd/{sheets,qa,seams}.py` first.

## The problem

Every critter frame is redrawn by Qwen-Image-Edit independently. Each frame
alone looks great, but details differ between consecutive frames of one
animation: a beard appears in 2 of 8 walk frames, an armband comes and goes,
trouser stripes and ankle wraps change pattern. Played as an animation the
character flickers ("boils"). Yan: "this is the only stuff that's not really
improved because the animation gets weird, we have to fix it".

Test data (read-only, outside this repo), the Arroyo warrior
`art/critters/hmwarrab` (walk, 6 directions x 8 frames) and `hmwarraa`
(idle, 6 x 12), all frames exactly 2x the original:
- `/ai/wasteland-hd/critter-test/{full,fast}/hd/`: 2x drawing, full / Lightning
- `/ai/wasteland-hd/critter-test/{full4s,fast4s}/hd/`: drawn at 4x on small sheets, shrunk to 2x
- soon `/ai/wasteland-hd/critter-test/{rows4,rows4full}/hd/` (+ `hfprimab`,
  `nmpeasab` walks): one animation per sheet row with a "keep every detail
  identical" prompt (Claude's sheet-side fix; check whether it helped).
Originals: `wasteland_hd.gamefiles.Game("~/fallout2/gamedata")`, `read_frm`,
`qa.original_frame(game, cache, "art/critters/hmwarrab/2_3.png")`.
Frames move: frame i is drawn at the accumulated per-frame shifts
(`frame.x`, `frame.y`), anchored bottom-centre (see `/ai/wasteland-hd/critter-test/anim_gif.py`).

## Do

1. **A flicker metric** (`wasteland_hd/temporal.py`): for consecutive frames
   of one animation direction, how much the HD frames change *beyond* what
   the original frames change. E.g. align by the game's shifts, compare
   high-frequency detail of HD_t vs HD_t-1 where the original pixels did not
   change (static body parts), normalised. It must rank: original-2x-nearest
   ≈ 0, `full4s` lower than `fast` (look at the frames to confirm what's
   visibly worse). Report it per animation for all test sets.
2. **A stabiliser**, CPU only, deterministic, no new model: make each
   animation's frames agree while keeping the HD detail. Ideas to measure:
   - propagate detail from a keyframe: for each frame, where the ORIGINAL
     pixels match the keyframe's (after the per-frame shift, or with block
     matching / optical flow computed on the originals, which are exact and
     shared), copy the keyframe's HD high-frequency layer there; elsewhere
     keep the frame's own detail; feather the seams;
   - temporal median of the high-frequency layer across motion-compensated
     neighbours;
   - anchor low frequencies to the original like `seams.py` so colours can't
     drift between frames.
   Keep the exact output size and the original's alpha (2x nearest).
3. CLI: `python -m wasteland_hd stabilise --game DIR --source WORK/hd --out OUT/hd`
   (separate output dir; only `art/critters/` frames change, everything else
   copied byte-identical), plus tests in `tests/` (synthetic frames: a static
   sprite with per-frame noise must come out with near-zero flicker; size/alpha
   preserved; non-critter files byte-identical).
4. Evidence for Yan: GIFs (original | before | after) for hmwarrab and
   hmwarraa, direction 2, using the game placement, in
   `~/wasteland-hd-tools/work/stability/` (gitignored, never commit art),
   and the metric before/after for every test set. Look at them yourself:
   the goal is what a player sees, not just a number.
5. Commit code + tests (`feat: stabilise HD critter animations …`), don't push.
   Write `docs/ANIMATION-STABILITY.md`: method, numbers, what to try next.

## Rules

CPU only: the GPU runs a long ComfyUI batch, don't touch ComfyUI or qwen38.
Don't write under /ai/wasteland-hd. No art in git. `pip install --user`
only if a dependency is truly needed (numpy/scipy/Pillow are installed; check
for OpenCV with `python3 -c "import cv2"` before relying on it).
