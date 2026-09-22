# STATUS

- ID: `T-lzQEyy-engine-hook-dispatch`
- Updated At: 2026-09-21
- State: Done
- Owner: developer (delegated by dev-epic)

## This update
- By: dev-epic
- Role: manager
- Date: 2026-09-21
- Comment: Ticket drafted with explicit acceptance criteria mirroring HLD §5-6, including exactly
  which of `_run_with_retries`'s 3 early-return points get post-hook wrapping.

## Risks / Blockers
- None yet.

## Next actions
1. Delegate to `developer` together with T-AHvmYR.
2. Verify AC-9 (provable no-op) via T-jI3P4p before closing.

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
- Comment: All 13 ACs implemented. New `src/agent_orchestrator/hooks.py` (`run_hook`, never
  raises, mirrors `bench/graders.py::_run_command`'s idiom; result-file exists-first read
  sequencing; `HOOK_CAPTURE_CAP_BYTES` bounded stdout/stderr capture). `engine.py`'s
  `_run_with_retries` gained a pre-hook gate before the attempt loop (true no-op when
  `task.pre_hook is None`, FR-4) and a `_finalize_with_post_hook` closure wrapping ONLY the
  `"succeeded"` early return and the exhausted-attempts `return last_result` -- the
  `cancelled` and `claude_quota_exhausted` returns are confirmed untouched, per AC-6.
  `_settle_completed_task` mirrors `pre_hook_result`/`post_hook_result` next to the existing
  `output_artifact_path` mirror. AC-8 (T2 suppression, the BLOCKING finding from both
  early-gate reviews): `isolation/escalation.py::build_resolver_dispatch`'s existing
  `task.model_copy(...)` now also clears `pre_hook`/`post_hook`; `build_rerun_task` (T3) is
  confirmed unchanged -- verified directly, not just by inspection.

  Deviation from the ticket's literal `hooks.run_hook(...)` signature: added an explicit
  `artifact_store: ArtifactStore` parameter (engine passes `self._store`), needed for AC-4's
  `artifact_store.exists(...)`/`artifacts.read_control(artifact_store, ...)` calls -- the
  ticket's paraphrased signature omitted it. Flagged, not a silent deviation.

## Evidence
- `ruff check`/`ruff format --check`/`mypy` clean on `hooks.py`, `engine.py`,
  `isolation/escalation.py`.
- `pytest -q tests/test_engine.py tests/test_isolation_models.py
  tests/test_engine_conflict_escalation.py tests/test_engine_isolation.py` -> 192 passed,
  zero regressions.
- Manual 5-scenario engine-level smoke run via `Orchestrator.run()` + `FakeExecutor`: (a)
  pre_hook fail_task -> task fails with `attempts=0`, `attempt-1/` dir never created (zero
  agent spend), `pre_hook_result.status == "failed"`; (b) post_hook fail + `on_failure:
  ignore` (default) -> task stays `succeeded`; (c) post_hook fail + `on_failure: fail_task`
  -> `succeeded` downgraded to `failed`; (d) no hooks declared -> no `pre_hook`/`post_hook`
  capture dirs created at all, both result fields `None`; (e) pre_hook passes -> task
  proceeds normally, `pre_hook_result.status == "passed"`.
- `unittest.mock.patch("agent_orchestrator.hooks.run_hook")` around a hookless run ->
  `call_count == 0` (FR-4 provable no-op, the exact mechanism T-jI3P4p's own test will use).
- Direct `isolation.escalation.build_resolver_dispatch(...)` call with a task carrying
  `pre_hook`/`post_hook` -> both `None` on the resolver's task copy; direct
  `build_rerun_task(...)` call -> both preserved unchanged.
- Commits: `4671e3d` (hooks.py), `11f21e9` (engine.py + escalation.py wiring) on
  `ad/cost-perf-hooks-skills`.
