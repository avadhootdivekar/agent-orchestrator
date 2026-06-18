# STATUS

- ID: `T-n7hmwj-token-estimator`
- Updated At: 2026-06-18
- State: Done
- Owner: manager

## This update
- Created `src/agent_orchestrator/estimator.py` with `TokenEstimator` ABC and `HeuristicTokenEstimator`.
- Added `ArtifactStore.size(path) -> int` abstract method + `LocalFsArtifactStore.size()` using `os.stat` (NFR-1 safe).
- `HeuristicTokenEstimator.estimate()`: sums sizes of instruction_path + input_paths + dynamic_input_paths, divides by `chars_per_token`, adds `output_allowance_tokens`, multiplies by `pessimism_buffer`, rounds up.
- All constants come from injected `EstimatorConfig` — no magic literals in `estimator.py`.
- Unit tests in `tests/test_estimator.py` (15 tests).
- AC-1 golden value confirmed: instruction=400B + input=800B, cfg defaults → estimate=520.

By: manager · Role: manager · Date: 2026-06-18 · Comment: Implemented and verified. 222 tests pass at completion.

## Evidence
- `src/agent_orchestrator/estimator.py` — new module
- `src/agent_orchestrator/artifacts.py` — `size()` method added
- `tests/test_estimator.py` — 15 unit tests
- `pytest -q`: 222 passed

## Risks / Blockers
- None. Known limitation: directory inputs use top-level stat only (documented; follow-on).

## Next actions
- Task complete.
