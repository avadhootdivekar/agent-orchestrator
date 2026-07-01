# STATUS

- ID: `T-g7rjh0-real-llm-gated-harness`
- Updated At: 2026-07-01
- State: Done
- Owner: tester

## This update
- Implemented real-LLM tier: `tests/playground/test_sum_of_array_real_llm.py` with gated smoke test (double-gated: @pytest.mark.real_llm + AO_E2E_REAL_LLM=1).
- Added `requires_claude()` helper (already in harness.py) to skip gracefully when claude binary unavailable.
- AC met: Tests skipped by default (no token burn in CI), exit 0 with claude available, spine outputs exist, control files parse as JSON (structure only, never asserted content).
- Created HANDOFF.md documenting deterministic ↔ real tier bridge (one spec, two agents files, control-file pre-seeding vs agent-written).

## Evidence
- `tests/playground/test_sum_of_array_real_llm.py`:
  - Module-level pytestmark = pytest.mark.real_llm
  - Test 1: test_sum_of_array_real_completes() — exit 0, status="succeeded"
  - Test 2: test_sum_of_array_real_spine_outputs_exist() — spine outputs (design.md, design-review.md, etc.) exist
  - Test 3: test_sum_of_array_real_control_files_exist() — tasks-manifest.json and final-verdict.json parse as JSON with correct shape
  - All tests call requires_claude() at start to skip if binary unavailable
- `HANDOFF.md`: Tier bridge design, design facts, fixture-to-real mapping, extension pattern, future work

## Test Results
- Default run (`pytest -q tests/playground`): real_llm tests skip with reason "AO_E2E_REAL_LLM not set"
- With claude binary available and AO_E2E_REAL_LLM=1: smoke test passes (structure verified, no content assertions)
- Without claude binary: tests skip gracefully with "claude binary not available" reason

## Risks / Blockers
- Real agent responses nondeterministic; only structure asserted, never content
- Agent may occasionally produce unexpected manifest shape; mitigation: assert only well-formedness, not exact ids/paths
