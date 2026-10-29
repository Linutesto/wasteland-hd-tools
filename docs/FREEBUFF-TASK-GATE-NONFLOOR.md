# Freebuff task: finish calibrating the invention gate for walls/scenery/items/misc (2026-10-29)

Take over from a Codex agent that hit its usage limit. Read `docs/CODEX-TASK-GATE-NONFLOOR.md`
(the brief; its rules and policy apply to you), then `docs/REPAIR-GATE-REPORT.md` and
`wasteland_hd/invention.py`, `wasteland_hd/repair.py`.

Already done by the previous agent (reuse it, don't redo): `tmp-audit/nonfloor/audit.py`,
`baseline.json` (4,997 rows scored with the CURRENT floor thresholds: walls 77 invented / 10 minor
of 1657, scenery 115/16 of 2707, items 7/1 of 298, misc 75/6 of 335), and contact sheets in
`tmp-audit/nonfloor/sheets/<kind>/<metric>-N.png` (orig 2x | HD pairs). Open and look at the sheets
(you can read PNG images) before changing thresholds.

Do steps 3-5 of the brief: per-kind rules in `invention.score(..., kind=...)` (alpha-boundary band
ignored for sprites, minimum opaque area, emissive pixels), floor/tiles behaviour byte-identical
(all existing tests + 20 calibration labels in docs/REPAIR-GATE-REPORT.md must still hold), wire
`kind` through `repair.py`, non-floor fallback = original art. ≥15 visually labelled examples per
category in a table. Whole suite: `python3 -m pytest -q` green.

Rules: no GPU, no services, no writes outside this repo, do not restart live loops, do NOT commit.
Finish by writing `docs/GATE-NONFLOOR-REPORT.md` (table, sheets reviewed, before/after fail rates per kind).
