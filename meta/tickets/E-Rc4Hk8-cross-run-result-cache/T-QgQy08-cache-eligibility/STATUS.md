# STATUS

- ID: `T-QgQy08-cache-eligibility`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (Dev B)

## This update
Implemented `src/agent_orchestrator/cache/eligibility.py` and `tests/cache/test_eligibility.py` in
commit `f9d4f70` (branch `worktree-agent-a18ce2c08e42a3a5a`), following HLD §8.3.1/§8.3.2
verbatim. T-gDNjN2 may start on this dependency.

## Acceptance criteria
| AC | Result | Evidence |
|----|--------|----------|
| 1 U-E1 | PASS | `TestTripwires::test_u_e1_task_fields_are_all_classified` asserts `set(TaskSpec.model_fields) == TASK_ANY_VALUE \| set(TASK_VALUE_RULED)` with `TASK_TRIPWIRE_MESSAGE` (the §8.3.1 text, incl. "an approval/human-gate field (E-Ag7Pw3) is ALWAYS RULED") as the failure message; plus disjointness and "ruled reasons are real `REASON_*` constants" |
| 2 U-E2 | PASS | coverage maps cover every `WorkflowSpec` / `WorkflowDefaults` field; a second test asserts no stale keys |
| 3 runtime rules | PASS | `TestRuntimeUnknownFields`: `TaskSpec`/`WorkflowSpec`/`WorkflowDefaults` subclass field at default -> eligible; non-default -> `unknown_task_field` (`extra`), `unknown_workflow_field` (`extra`, `defaults.extra`); mutable `list` default compared by value |
| 4 U-E27 | PASS | `AgentSpec` subclass: default -> eligible, non-default -> `unknown_agent_field` + name; a `defaults.model` override (which `model_copy`s the agent) does not trip it; `forbidden_task_models` (NON_KEY) never trips |
| 5 U-E3..E26 | PASS | `TestEligibilityReasons`: one test per owned reason with `reason` and `detail`: `run_integration_active`, `emit_tasks`, `task_manifest_path`, `output_manifest`, `pre_hook`, `post_hook`, `isolation_worktree` (task and `defaults.isolation`; an explicit task `none` wins), `router_task`, `loop_member` (`dev`, `dev__iter3`, `gate`, `gate__iter2`), `no_outputs`, `agent_unknown`, `executor_not_cacheable` (+executor), `command_not_cacheable` (wrapper script, empty template, 1000-char basename clipped to `MAX_TEXT_CHARS`), `model_unresolved`, `verdict_sidecar_undeclared` (`review.md` and explicit `verdict_path`), `breaker_verdict_source` |
| 6 positive | PASS | `/usr/local/bin/claude`; `--model opus` baked into `command_template` with `model=None`; `--model=opus` and `-m opus` in `extra_args`; `fake`; task/`defaults.model` resolving a model-less agent; review task with declared sidecar; every ANY field set at once |
| 7 U-E28 | PASS | `TestStructuralConsistency`: emit, router, loop gate and `gate__iter4` are structural per `models._is_structural_task` (4 of 5 tasks) and all ineligible; the plain task is eligible. Production code does not call the private helper |
| 8 determinism / purity | PASS | 100 calls return one result; order follows §8.3.2 (ruled fields before isolation/structural rules, `no_outputs` before `agent_unknown`, integration first); `builtins.open`, `os.stat`, `subprocess.run` patched to raise inside `monkeypatch.context()` and the call still returns; `Eligibility` is frozen |
| 9 hygiene | PASS | commands below |

## Evidence
- `.venv/bin/python -m pytest -q tests/cache/test_eligibility.py` -> 57 passed.
- `.venv/bin/python -m pytest -q tests/cache` -> 426 passed (246 T-FJH6LI + 123 T-28J9oR + 57 here).
- `.venv/bin/ruff check src tests` -> All checks passed.
- `.venv/bin/ruff format --check src tests` -> only the pre-existing generated
  `src/agent_orchestrator/_build_info.py` would be reformatted (not part of this change).
- `.venv/bin/mypy src tests/cache` -> only the 4 pre-existing `_version.py` errors.
- `awk 'length>100'` over `src/agent_orchestrator/cache/*.py tests/cache/*.py` -> 0 lines.
- Full suite: the targeted run is sufficient for this task per the delivery instruction; the full
  suite was run once at the end of T-28J9oR (see that task's `STATUS.md`) on a tree that did not
  yet contain this module (a new, unimported module).

## Risks / Blockers
- No blockers.
- Expected: tripwires U-E1/U-E2 fail at the E-Ag7Pw3 merge until its new fields are classified in
  the tables here (task/workflow fields RULED; an approval/human-gate field is always RULED). The
  runtime rules keep cached runs safe meanwhile.
- `models.resolve_task_isolation` (public) logs its own one-line warning for a structural task
  that declared `worktree`; the predicate itself logs nothing and does no I/O. This duplicates the
  engine's existing warning only for that misconfiguration.

## Next actions
1. T-gDNjN2 consumes `check_eligibility` after the author-policy check.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD §23.5).
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Implemented as commit `f9d4f70`;
  State -> Done. All acceptance criteria pass. No deviation from HLD §8.3.2 other than the
  structure (shared `_first_unclassified_field` helper for the three runtime rules, same
  semantics); the tables use `Rule(ok=None, ...)` for `isolation` and `verdict_path`, which the
  pseudocode's `rule.ok is not None` check anticipates. This file, `TASK.md`, `HANDOFF.md` and the
  epic rollup agree.
