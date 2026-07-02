# STATUS

- ID: `T-g7rjh0-real-llm-gated-harness`
- Updated At: 2026-07-01
- State: Done
- Owner: tester

## Update 2026-07-01 — real-run directory-access fix (verified end-to-end)
- **RCA**: the real-LLM tests used pytest's `tmp_path` (system `/tmp/pytest-of-…`), which is
  OUTSIDE the spawned `claude` subprocess's sandbox allow-list (the repo working directory).
  Every instruction read / output write under `/tmp` was denied, so no spine agent could write
  `output/design.md` and the run never reached `succeeded`. Paths handed to `ClaudeCliExecutor`
  are absolute (resolved under `workspace_root`), so relocating the workspace *inside* the repo
  restores access with no `--add-dir` and no unrestricted access.
- **Fix (no `src/` change, NFR-1 honored)**:
  - `tests/playground/conftest.py` — new `real_llm_workspace` fixture: per-test dir under
    `playground/.tmp/` (repo-local, gitignored, inside the allow-list), auto-cleaned on teardown.
  - `test_sum_of_array_real_llm.py` — all three tests use `real_llm_workspace` (not `tmp_path`).
  - `.gitignore` — ignore `playground/.tmp/`.
  - `playground/sum-of-array/agents.claude.json` — added `--permission-mode acceptEdits` so the
    headless agents write outputs without stalling (bounded: auto-accepts file edits, not bash).
- **Verified against the REAL `claude` interface** (`AO_E2E_REAL_LLM=1`, `claude` on PATH):
  - `test_sum_of_array_real_completes` → passed (exit 0, `state.status == "succeeded"`, 6m29s).
  - `test_sum_of_array_real_spine_outputs_exist` + `test_sum_of_array_real_control_files_exist`
    → both passed (13m30s); real agents wrote a well-formed `tasks-manifest.json` and
    `final-verdict.json` (structure only asserted).
  - Default suite unaffected: `uv run pytest -q` → 377 passed, 3 skipped (tier gated off).

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
