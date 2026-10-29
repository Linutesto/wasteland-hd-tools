# Automatic HD selection — 2026-10-29

The selector lives in `wasteland_hd/select.py` and runs with `python -m wasteland_hd select`. It reads completed production inventories and repair scoring, considers production accepted frames, production attempt cuts, repair rung cuts, accepted repair output, and original-art fallback. It checks the full direction before choosing it: RGBA mode, exact 2× size and alpha, QA badness ≤1, and the current invention verdict. Wall candidates also receive map junction measurements against the current accepted neighbor art. `minor_invention` remains eligible.

Eligible candidates receive a score for added high-frequency detail near edges in the original, with novelty and QA drift penalties. Weights are fitted on NIM vision pair judgments. The judge sees original, A and B in one image. Fifty pairs are asked again in reverse order; a consistency rate below 80% disables judge tie breaks. Responses are cached by image hash in `tmp-select/judge-cache/` and the daily run is capped at 150 calls (below the 600-call ceiling). No GPU or service is used.

## Integration call for the pack builder

Run the selector before the pack builder collects archives:

```python
import subprocess, sys
subprocess.run([sys.executable, '-m', 'wasteland_hd', 'select',
                '--production', '/ai/wasteland-hd/production-lightning4',
                '--repair', '/ai/wasteland-hd/repair-lightning4',
                '--out', '/home/yan/wasteland-hd-tools/tmp-select'], check=True)
```

For each eligible job, use `/home/yan/wasteland-hd-tools/tmp-select/jobs/<job>/hd/art/...` as the pack input tree. `selection.json` alongside it names each direction's chosen candidate and score. `tmp-select/spotcheck.json` lists 40 examples for review. The builder should verify each selected PNG with its existing original-frame integrity check before packaging. The pack builder was not edited because another agent is changing its category policy.

## Run result

The completed run covered 5,055 directions in 18 jobs. It selected 2,903 production directions, 60 repaired directions, and 2,092 integrity-safe fallbacks. The calibration used the full 150-call budget: fitted-score agreement was 100%, swapped-order consistency was 84.375%, and the vision judge remained trusted above the 80% cutoff.

The final artifacts are under `tmp-select/`: per-job override trees and `selection.json` records, a 40-item `spotcheck.json`, fitted `weights.json`, and the three requested contact sheets (`switched-to-production.png`, `switched-to-repair.png`, and `kept.png`). The complete repository suite passes: `121 passed`.
