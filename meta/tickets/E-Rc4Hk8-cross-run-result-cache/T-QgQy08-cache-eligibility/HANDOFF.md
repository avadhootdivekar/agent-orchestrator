# HANDOFF: T-QgQy08-cache-eligibility

- Task: `T-QgQy08-cache-eligibility`
- State: `Done (handoff available)`
- From: `developer` (Dev B)
- To: T-gDNjN2; the parent merging E-Ag7Pw3
- Commit: `f9d4f70` on branch `worktree-agent-a18ce2c08e42a3a5a`.

## What was delivered
- `agent_orchestrator.cache.eligibility`:
  - `Eligibility(eligible, reason=None, detail=None)` (frozen dataclass);
  - `Rule(ok, reason)` (frozen dataclass; `ok=None` means "judged by a dedicated rule below");
  - `check_eligibility(task, workflow, agents, *, integration_active) -> Eligibility`;
  - tables `TASK_ANY_VALUE` (frozenset), `TASK_VALUE_RULED` (`dict[str, Rule]`),
    `WORKFLOW_FIELD_COVERAGE`, `DEFAULTS_FIELD_COVERAGE` (`dict[str, str]`: field -> how it is
    covered; the runtime rule only needs the keys);
  - `TASK_TRIPWIRE_MESSAGE` (the §8.3.1 failure text, reused by the U-E1 assertion).
- Pure: no I/O, no logging; imports `models` (`resolve_task_isolation`, `strip_iter_suffix`,
  `resolve_effective_agent`, `ISOLATION_NONE`), `usage.verdict_path_for` and `cache.constants`.

## Frozen names / contracts
- The reason strings of HLD §8.3.3 (the `constants.REASON_*` values), including
  `unknown_agent_field`. Detail values: the field name for the three `unknown_*` reasons
  (`defaults.<name>` for a defaults field), the executor for `executor_not_cacheable`, the
  command basename (clipped to `MAX_TEXT_CHARS`) for `command_not_cacheable`; `None` otherwise.
- First-violation order is the §8.3.2 order: integration, task fields (sorted: ruled fields in
  name order, then unknown), workflow and defaults unknown fields, resolved isolation, router,
  loop, no outputs, agent unknown, agent unknown-field, executor, command and model, verdict
  sidecar, breaker verdict source.
- The coordinator (T-gDNjN2) checks the author policy BEFORE calling this; not-opted-in tasks
  never reach it and get no record.
- An approval or human-gate field is always RULED; a new `AgentSpec` field is classified in
  `constants.AGENT_KEY_FIELDS` / `AGENT_NON_KEY_FIELDS`.

## Verification the receiver should run
- `pytest -q tests/cache/test_eligibility.py` (57 tests)

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: `unknown_agent_field`.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done; handoff available
  (commit `f9d4f70`). Names above are the frozen contract. No deviation from the HLD.
