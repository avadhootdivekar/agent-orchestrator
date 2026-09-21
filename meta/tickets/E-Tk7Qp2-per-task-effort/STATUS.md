# STATUS

- ID: `E-Tk7Qp2-per-task-effort`
- Updated At: 2026-08-28
- State: Done
- Owner: developer (agent)

## This update
- Implemented per-task `model`/`effort`/`max_turns` overrides on `TaskSpec`
  (`src/agent_orchestrator/models.py`), a shared `EffortLevel` Literal
  (`low|medium|high|xhigh`) reused by `AgentSpec.effort` and `TaskSpec.effort`, and a
  new `"xhigh"` effort tier (`EFFORT_MAX_TURNS["xhigh"] = 120`).
- Added `models.resolve_effective_agent(task, agent)` as the single place task > agent
  precedence is applied (per-field fill-in, never a clobber; returns the same `AgentSpec`
  object unchanged when a task declares no overrides). Wired into
  `engine.py::_run_with_retries` right before `TaskContext` is built, so
  `executors/claude_cli.py`'s argv-injection logic needed ZERO changes — it already read
  `ctx.agent.model/effort/max_turns` generically.
- `executors/fake.py`: added `FakeExecutor.resolved_agents: dict[str, AgentSpec]`
  (mirrors the existing `self.prompts` recording pattern) so tests can assert the
  resolved settings reached dispatch without spawning a subprocess.
- Schemas: `specs/workflow.schema.json` task properties gained `model`/`effort`
  (enum incl. `xhigh`)/`max_turns`; `specs/agents.schema.json` effort enum gained
  `xhigh`. No hand-maintained allowlist existed elsewhere for effort values (checked
  `spec.py`/`validate.py`) — schema + pydantic (`load_workflow`/`load_agents`) is the
  full validate-layer surface here, confirmed by a dedicated test class exercising that
  exact layer (not raw jsonschema), per the E-3JTmVu learning.
- Confirmed (via `read_task_manifest`'s existing `TaskSpec(**t)` construction, which
  needed no changes) that an `emit_tasks` manifest entry's `effort`/`model` round-trip
  through injection to dispatch identically to a statically-declared task; added an
  integration test proving it end-to-end through the engine.
- Templates: `templates/builtin/routed-runner/breakdown-contract.md.tmpl` gained an
  "Effort & model per task" section (default `~10 min` → `"effort": "medium"`; bigger
  only when genuinely warranted) and the 5-entry pipeline's `impl`/`test` example
  entries now carry `"effort": "medium"`; rule 5's field allowlist widened; `review`/
  `aggregate` intentionally left without an explicit `effort` (inherit the agent's own)
  per the task's "your call" guidance. `instructions/07-task-breakdown.md` and the
  template `README.md` updated to match — the breakdown agent owns the per-`<tid>`
  size/effort/model call.
- Docs: `docs-md/workflow-templates-hld.md` §2.8 gained a per-task effort/model note;
  `docs-md/adr/ADR-0003-settings-precedence-policy.md` decision 2 gained a "Shipped"
  sub-note (no new ADR — per the assigning brief, ADR-0003 already blessed this).
- Explicitly OUT of scope (file-ownership boundary with the parallel agent on this
  branch): `cli.py`'s global `--model`/`--effort`/`_valid_efforts` (does not accept
  `"xhigh"` at the invocation level; only per-task/per-agent fields do) and the known
  live "invocation clobbers every AgentSpec" defect (ADR-0003 "Live defect") — untouched,
  and irrelevant to correctness here since the clobber loop only ever touches
  `AgentSpec`, never `TaskSpec`, so task-level overrides win regardless.
  `project_config.py`'s `effort` docstring — same boundary, same reasoning.

## Evidence
- Baseline (before this work, same branch): `uv run pytest -q` → **1793 passed, 7
  skipped**.
- Final: `uv run pytest -q` → **1817 passed, 7 skipped** (2 consecutive full-suite runs
  green; a single interleaved run hit 1 failure in
  `tests/test_wave_scheduler.py::TestParallelDispatchProof::
  test_two_independent_tasks_overlap_at_max_parallel_two`, a pre-existing thread-timing
  overlap assertion unrelated to this change — passed in isolation and on the next full
  run; this epic touches no scheduling/parallelism code path). Net +24 tests, 0
  regressions.
- `uv run ruff check .` → all checks passed (0 issues) on every touched file.
- `uv run ruff format --check .` → clean on every file this epic touched; 1 pre-existing
  unrelated failure (`tests/test_e2e_builtin_routed_runner.py`, confirmed via
  `git stash` to fail identically before this epic's changes — not touched by this epic).
- `uv run mypy src` → identical 4 pre-existing errors in `_version.py` before and after
  (confirmed via `git stash`; that file is untouched by this epic); 0 new errors.
  `uv run mypy src/agent_orchestrator/models.py src/agent_orchestrator/engine.py
  src/agent_orchestrator/executors/fake.py src/agent_orchestrator/executors/claude_cli.py`
  → clean.
- New tests: `tests/test_task_settings_override.py` (22 tests: unit `EFFORT_MAX_TURNS`/
  `resolve_effective_agent`, validate-layer schema accept/reject, engine-dispatch
  integration incl. `emit_tasks`, CliRunner `ao validate` e2e) +
  `tests/test_executor.py` (+2: xhigh argv injection, resolved-effective-agent-to-argv).
- `tests/test_builtin_routed_runner_assets.py` + `tests/test_e2e_builtin_routed_runner.py`
  rerun after the template edits: 23/23 passed, unaffected.

## Risks / Blockers
- None blocking. Open follow-up (tracked, not fixed here): `cli.py`'s global
  `--effort`/`AO_EFFORT`/config `effort` doesn't accept `"xhigh"`; the invocation-clobber
  defect (ADR-0003) is still live at the agent level.

## Next actions
1. Sibling repo `../ao-runner-finplan/workflows/epic-runner/template/` mirrors this
   template and needs the SAME manual edits applied there (NOT done — out of scope,
   listed for a follow-up in that repo):
   - `breakdown-contract.md.tmpl`: add the "Effort & model per task" section and
     `"effort": "medium"` on the `impl1-<tid>`/`test1-<tid>`/`impl2-<tid>`/`test2-<tid>`
     example entries; widen the "do not add fields beyond..." rule to include
     `effort`/`model`/`max_turns`.
   - `workflows/epic-runner/instructions/07-task-breakdown.md` (or that repo's
     equivalent stage-7 instruction): add the "Task sizing, effort, and model" guidance
     and widen the "no extra fields" hard rule the same way.
   - No engine/schema changes needed there — `ao` is installed via `install.sh`
     (non-editable `uv tool` snapshot) from THIS repo, so once this epic merges and
     `install.sh --force`/`--reinstall` is re-run (per the install-staleness learning),
     the sibling repo's `ao` binary already understands `TaskSpec.model/effort/
     max_turns` and `"xhigh"` — only the template CONTENT needs porting by hand.
2. (Optional, separate epic) Extend `cli.py`'s global `--effort`/config `effort` to
   accept `"xhigh"`, and revisit the invocation-clobbers-agents defect per ADR-0003
   §1.1 — both out of this epic's file-ownership scope.
