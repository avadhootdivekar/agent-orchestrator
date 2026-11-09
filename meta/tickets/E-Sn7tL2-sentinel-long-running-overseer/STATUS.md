# STATUS

- ID: `E-Sn7tL2-sentinel-long-running-overseer`
- Updated At: `2026-11-09`
- State: `Draft`
- Owner: `avadhoot`

## This update
- By: claude-opus-5-5 · Role: architect · Date: 2026-11-09 · Comment: Rev 2 of EPIC.md. Owner decisions D1–D4
  recorded (layered bandwidth; mechanical + LLM kill; pending-graph edits default on; engine-wide with live
  CLI/dashboard controls). Tick moved to the engine main thread; phasing reordered so mechanical health/recovery
  and bandwidth land before the LLM sentinel.

## Evidence
- Utilisation baseline from the last 10 overseer-runner runs (EPIC §1): 7–41% slot utilisation at P=6; 50–80% of
  wave span is tail after the median unit finishes.
- Code findings verified at HEAD of `ad/enhancements-20oct` (EPIC §2), notably DRAIN has no timeout (`engine.py:999`).

## Risks / Blockers
- In-flight graph rebuild safety unverified (P0 spike).

## Next actions
1. `T-Sn0Spk` spike (in-flight reshape, cooperative kill + liveness, tick cost, SIGTERM).
2. `T-Sn1Adr` ADR-0024 + `docs-md/sentinel-hld.md`; resolve EPIC §10 open points with owner.
