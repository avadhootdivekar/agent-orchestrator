# STATUS

- ID: `T-g7rjh0-real-llm-gated-harness`
- Updated At: 2026-07-02
- State: Done
- Owner: developer

## Update 2026-07-02 — real-run hardening: turn budget, permissions, agent cwd
Three distinct failures surfaced (in order) once the tier ran repeatedly against the real
`claude`; each RCA'd from captured `stdout.txt`/`stderr.txt` + workspace file-presence checks:
- **#1 `architect-design` `error_max_turns`** (`subtype:error_max_turns`, `num_turns:6`,
  `permission_denials:[]` → NOT permissions). `effort:medium`→`--max-turns 5` was too tight for a
  task that non-deterministically needs 3–6 turns. **Fix:** `EFFORT_MAX_TURNS` → `{15,30,60}`
  (turns are only a loop-breaker; token budget is the real cost guard) + explicit
  `AgentSpec.max_turns` override + `ao run/resume --max-turns` flag + `MAX_TURNS`/`AO_MAX_TURNS`
  Makefile/harness plumbing.
- **#2 `taskreview-t1` missing `review.md`** (agent exited 0; Bash `python -m pytest …` in
  `permission_denials`). `acceptEdits` silently denies Bash, but `reviewer.md` requires running
  pytest → agent stalls, skips its write. **Fix:** all 5 playground agents → `bypassPermissions`.
- **#3 `architect-breakdown` manifest "not found"** (agent claimed success; manifest found at
  repo-root `./output/tasks-manifest.json`). `subprocess.run` set no `cwd`, so `claude` inherited
  the repo-root cwd and the agent's *relative* manifest write escaped the workspace. **Fix
  (framework-level, config-driven — NOT test-only):** `AgentSpec.working_dir` → engine resolves
  under `workspace_root` (path-guarded) → `TaskContext.cwd` → executor `subprocess.run(cwd=…)`;
  default cwd = workspace root. This crosses the epic's original NFR-1 "no `src/` changes" boundary
  by user direction — the working-dir contract belongs in the framework.
- **Verified:** real-LLM suite `3 passed in 578.31s` (`PYTEST_EXIT=0`, exit code captured directly,
  no `| tail` masking); manifest lands inside the workspace; fast suite `387 passed` (+4 new
  unit+integration tests: `test_cwd_passed_to_subprocess`, `test_cwd_none_when_unset`,
  `test_explicit_max_turns_overrides_effort`, `test_max_turns_not_injected_when_no_effort_or_override`,
  plus `TestAgentCwd` in `test_engine.py`); `mypy` clean; zero new lint.
- **Superseded from the 2026-07-01 update below:** `acceptEdits` → `bypassPermissions`; the
  workspace still lives under `playground/.tmp/` but the agent **cwd** is now set explicitly to the
  workspace root by the engine rather than inherited from wherever `ao` was invoked.

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
