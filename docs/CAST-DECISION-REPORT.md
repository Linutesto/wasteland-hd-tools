# Cast keep-or-cut report (task 2026-10-31)

Branch `agent/cast`, worktree `~/agent-wt/cast`. Answers the question in
`docs/FREEBUFF-TASK-CAST-KEEP-OR-CUT.md`: the chroma-cast code from
`docs/CHROMA-REPORT.md` (commit `9c0e7d4`) changed **zero** frames in the last
phone pack — why, is the tint visible to a human at real game scale, and does
the code stay. Everything below is CPU-only; `/ai`, `~/wasteland-hd-tools` and
the phone were read-only; production and repair were never stopped; nothing was
pushed. Working notes: `tmp-cast/PROGRESS.md` (untracked).

**Decision: DELETE.** `wasteland_hd/chroma.py`, `tests/test_chroma.py` and
every hook are gone; `invention.py`/`repair.py` are byte-identical to their
pre-chroma state (verified `git diff 9c0e7d4^ -- …` = 0 lines). 136 tests pass
(the one failure, `test_live_plan_differs_only_by_the_critter_recipe_change`,
reads the **live** production `plan.json` and failed identically *before* this
task's changes — production is live and its plan has drifted since that test
was written; unrelated to chroma).

## 1. Why it changed nothing

All three possible reasons are true, each on a different layer:

1. **Most flagged art has no correction path at all.** `cast_correct` was only
   ever called from one place: the wall-group repair ladder
   (`repair._repair_wall_group`). Scenery, items, misc and tiles casts — 498 of
   the 607 flagged frames — were measured and reported, then nothing anywhere
   consumed the measurement: `invention.score` adds a `cast` dict to its output
   but the verdict chain never reads it, and no selector, gate or pack step
   consumes the key.
2. **The wall path almost never runs.** `_repair_wall_group` is only called
   from `reconcile_walls`, only for wall directions whose junction delta
   exceeds the seam limit, and only after `edge-restore` **and**
   `junction-lock` have already failed the gate. In the entire live repair tree
   (33 jobs, `repair-lightning4`), the chroma-cast step was reached on 15
   frames — the `chroma-cast/` trial dirs in `walls-0002`/`walls-0003`, written
   02:42–02:43 today by the live loop (which runs main from
   `~/wasteland-hd-tools`, so chroma *was* live).
3. **Where it ran, it was a byte-identical no-op.** All 15 trial PNGs are
   byte-for-byte identical to the frames that ship (max RGB delta 0, alpha
   unchanged): `cast_correct` returns the input unchanged when no region is
   flagged, and none of those frames had a flagged region. No state entry in
   any of the 33 jobs has `chroma_cast` provenance or fallback (counts:
   edge_lock 5, edge_restore 14, junction_lock 75, original_game_art 98).

Pack-level proof: `~/fallout2/tests/hd-pack-20261031/hd` (built 09:45 today,
**after** chroma merged to main at 19:04 yesterday, i.e. with the code live in
both production and repair) versus the previous pack
`hd-pack-20261030b/hd` — SHA-256 of every PNG: **9,422 shared frames, 0 differ**;
the new pack adds only 1,352 heads frames from critter production. Chroma
shipped exactly what it was going to ship: nothing.

## 2. The 12 worst flagged walls/scenery, in real map context

Method (`tmp-cast/`, untracked):

* From the chroma worktree's full measurement (`tmp-chroma/cast.json`, 9,244
  unique frames, 336 unique flagged walls/scenery), every flagged frame was
  cross-checked against a placement index of all 155 game maps
  (`tmp-cast/placements.py` → 206,962 wall+scenery placements): **112 of the 336
  flagged frames are placed nowhere in any map** — a player can never see them
  (this includes the whole "UNUSED TILE"/`pooltbl`/`minect` placeholder class).
* The 12 worst *placed* flagged frames were each rendered at a real placement
  (`tmp-cast/render.py` → `tmp-cast/sheets/*.png`): one map crop composited
  twice — left = untouched game art 2x, right = the pack that ships today
  (hd-pack-20261031, falling back to original art exactly as the game does).
  Panels are 640x600 px with **one 2x-art pixel per pixel — the phone's 1:1
  view on the 6.8" 1344x624-ish screen; no zoom anywhere**. The target sprite's
  box is outlined identically in both panels.

| # | frame | map | cast_area | what it looks like at game scale |
|---|-------|-----|-----------|--------------------------------|
| 00 | `scenery/shuttl04` | sfshutl2 | 0.999 | black void + red "UNUSED ART" stamps |
| 01 | `scenery/shuttl05` | sfshutl2 | 0.999 | same |
| 02 | `scenery/shuttl01` | sfshutl2 | 0.996 | same |
| 03 | `walls/jas1000` | v15ent | 0.951 | dirt/rock wall texture; panels differ in redraw brightness/texture, **no hue difference I could see** |
| 04 | `scenery/goop02` | klatoxcv | 0.906 | glowing green goo on cave floor; panels near-identical |
| 05 | `scenery/goop01` | klatoxcv | 0.896 | same |
| 06 | `walls/shutl21` | sfshutl2 | 0.873 | black void + "UNUSED ART" stamps |
| 07 | `scenery/gx015` | newr2 | 0.865 | New Reno street facade; panels near-identical |
| 08 | `scenery/sign45` | geckjunk | 0.854 | junkyard shack; panels near-identical |
| 09 | `walls/shutl14` | sfshutl2 | 0.851 | black void + "UNUSED ART" stamps |
| 10 | `walls/shutl19` | sfshutl2 | 0.831 | same |
| 11 | `walls/shutl05` | sfshutl2 | 0.827 | same |

**Honest verdict: zero of the 12 would be noticed by a player while playing.**

* 7 of the 12 sit on `sfshutl2`, a mostly-empty black map whose objects are
  literal red-on-black **"UNUSED ART" placeholder stamps**. The largest real
  colour difference there is that the shipped floor tiles draw a faint grid
  the original's near-black floor hides — not a tint.
* The most-placed flagged wall by far, `jas1000` (463 placements, vault
  entrances like v15ent), does differ between the panels — but as redraw
  texture/brightness (a patchier, brighter dirt), which is the redraw itself,
  not a colour cast; `cast_correct` keeps luminance and detail, so it would not
  have changed what is different. The measured warm shift (~+6, +7 Lab) is a
  brown wall being slightly browner: invisible against rock and dirt.
* `goop01/02` (toxic cave goo), `gx015` (New Reno wall), `sign45` (Geck
  junkyard): left and right panels are indistinguishable at 1:1 phone scale.

And this is the *upper bound* of concern: these are the worst placed frames
that could be found anywhere in the pack. Everything else flagged is smaller,
unplaced, or on tiles (which chroma never corrected at all).

## 3. The decision and what was removed

Yan's rule decides it: **no code that changes nothing the player can see.**
The tint is not visible at real game scale on the worst placed frames, the
correction changed zero of 9,422 shipped frames, and its live trials were
byte-identical no-ops. There is no "handful of noticeable cases" to target —
there is nothing. So this is a delete, not a targeted fix:

* `wasteland_hd/chroma.py` — deleted (233 lines).
* `tests/test_chroma.py` — deleted (5 tests; suite 141 → 136).
* `wasteland_hd/invention.py` — restored byte-exact to `9c0e7d4^`: the
  `cast_measure` import, the `cast_tiles` parameter and the `cast` output key
  are gone; `score()`'s signature and return match pre-chroma exactly.
* `wasteland_hd/repair.py` — restored byte-exact to `9c0e7d4^`: the
  `cast_correct` import, the `chroma-cast` rung of the wall ladder and the
  `'chroma_cast'` provenance whitelist entry are gone; the ladder is
  `edge-restore → junction-lock → original art` again.
* `docs/CHROMA-REPORT.md` — kept as the historical record with a superseded
  banner pointing here. The code itself is one `git show 9c0e7d4` away.

Nothing else consumes the measurement: no selector, QA gate, pack build, or
production step ever read `score()['cast']` (verified by grep across the repo
and `~/fallout2/tools/hd-pack-build.py`).

## 4. If a tint ever must be fixed

The measurement (`cast_measure`) was honestly calibrated and the report's
labels were real — the failure mode is not the metric, it is impact: the worst
cases are unplaced placeholders or sub-perceptual shifts on already-passing
redraws. If a specific wall ever reads wrong to a human **in game**, take that
one frame for a targeted manual correction rather than reviving a
pack-wide gate; the labelled sheets in the chroma worktree
(`~/agent-wt/chroma/tmp-chroma/`) remain available for that.
