# STATUS

- ID: `T-AHvmYR-hook-schema-models`
- Updated At: 2026-09-21
- State: Done
- Owner: developer (delegated by dev-epic)

## This update
- By: dev-epic
- Role: manager
- Date: 2026-09-21
- Comment: Ticket drafted with explicit acceptance criteria mirroring HLD §3-4. Will be updated
  again once delegated developer work lands and the full suite is re-run.

## Risks / Blockers
- None.

## Next actions
1. Delegate to `developer` alongside T-lzQEyy (same PR-shaped change, models first).
2. Confirm full test suite + ruff + mypy clean after landing.

## Rev 2 sync (post early-gate review)
- By: dev-epic
- Role: manager
- Date: 2026-09-21
- Comment: Acceptance criteria updated to match HLD Rev 2 (hooks moved to a `WorkflowSpec.hooks`
  named registry referenced by `HookRef`, T2 resolver-dispatch suppression added, `hooks.py`
  module extraction, error-detail folding for self-heal). See
  `meta/tickets/E-AMSSHX-task-lifecycle-hooks/STATUS.md` for the full review outcome.

## Implementation complete
- By: developer
- Role: developer
- Date: 2026-09-21
- Comment: All 11 ACs implemented additively in `src/agent_orchestrator/models.py`:
  `HookOnFailure`/`HookStatus` literals; `DEFAULT_HOOK_TIMEOUT_SECONDS`/
  `DEFAULT_PRE_HOOK_ON_FAILURE`/`DEFAULT_POST_HOOK_ON_FAILURE`; `HookSpec`/`HookRef`;
  `resolve_hook_on_failure()` (ref > hook > kind-default precedence, matching
  `resolve_task_isolation`'s "one place" docstring convention); `HookOutcome`;
  `WorkflowSpec.hooks: dict[str, HookSpec] = {}`; `TaskSpec.pre_hook`/`post_hook`;
  `TaskResult`/`TaskRunState.pre_hook_result`/`post_hook_result`.

## Evidence
- `ruff check`/`ruff format --check`/`mypy` clean on `models.py`.
- `tests/test_isolation_models.py` + `tests/test_routing_breaker_models.py`: 141 passed,
  zero regressions.
- Full repo suite after all 4 tasks landed: 3830 passed, 8 skipped, 1 pre-existing failure
  unrelated to this epic (see epic STATUS.md).
- Commit: `316214c` on `ad/cost-perf-hooks-skills`.
