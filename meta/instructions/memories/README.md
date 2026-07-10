# Repo-scoped memories

---
name: uv-is-the-package-manager
description: pip/pip3 are not installed; use uv run for all dev tooling
type: constraint
---

`pip` and `pip3` are not available in this environment. **Why**: uv is the sole package manager installed at `~/.local/bin/uv`. **Apply**: whenever installing packages, running pytest, ruff, mypy, or the `ao` CLI — prefix with `uv run` (e.g. `uv run pytest -q`, `uv run ao validate ...`). The CI workflow uses `pip install` for GitHub Actions runners, which is fine; locally always use `uv`.

---
name: subagent-lint-not-self-certifying
description: Subagents may claim ruff/mypy clean when errors remain; always re-run independently
type: pitfall
---

Developer subagents have reported "ruff: clean" after delivering code that had 16 lint errors. **Why**: the agent may run ruff on a subset of files or misread output. **Apply**: after any subagent delivers code, run `uv run ruff check .` and `uv run ruff format --check .` yourself before accepting the delivery as done.

---
name: workflow-id-must-be-lowercase
description: Workflow `id` field in JSON/YAML spec must be fully lowercase; uppercase letters (e.g. E- ticket prefix) fail schema validation
type: constraint
---

The `id` field in every workflow spec is validated against `^[a-z0-9][a-z0-9-_]*$`. **Why**: the JSON schema enforces this pattern and rejects anything with uppercase characters, including the standard ticket prefix `E-`. **Apply**: when instantiating any workflow (including the epic-lifecycle template), always use a lowercase id (e.g. `e-abc123-my-feature`). The `ad/tickets/` directory still uses uppercase `E-` — these are separate concerns.

---
name: cross-validate-arg-order
description: cross_validate(workflow, reposets, agents) — reposets is second, agents third; swapping silently misdirects validation
type: pitfall
---

`cross_validate` in `agent_orchestrator.spec` takes `(workflow, reposets_dict, agents_dict)`. **Why**: passing agents as the second argument causes every repo_set lookup to fail with "Unknown repo_set: X" — the error looks like a missing config entry but is actually an argument-order bug. **Apply**: any time you call `cross_validate` directly, double-check the order: workflow → reposets → agents.

---
name: uv-tool-install-noneditable-for-isolation
description: uv tool install must be non-editable for v1/v2 isolation; --editable makes the global `ao` share the working copy
type: pitfall
---

`uv tool install --editable .` makes the globally installed `ao` and the dev copy identical — any regression in v2 breaks v1. **Why**: editable mode imports live from `src/` with no separate snapshot. **Apply**: `install.sh` must use `uv tool install .` (no `--editable`) to produce a true v1 snapshot. Dual-invocation pattern: `ao` (global) = v1 stable; `uv run ao` in repo = v2 dev.

---
name: agents-json-required-fields
description: agents.json executor entries require `executor`, `command_template`, `prompt_template`; `type`/`description`/`env` are rejected
type: constraint
---

The agents JSON schema (`specs/agents.schema.json`) uses `additionalProperties: false`. Valid fields are `executor`, `command_template`, `prompt_template`, `context_window`. **Why**: `type`, `description`, and `env` look intuitive but are not in the schema and cause validation failure. **Apply**: when writing or scaffolding `agents.json`, match the `specs/examples/agents.json` field names exactly.

---
name: build-dag-infers-edges-from-paths
description: build_dag derives edges from matching input/output paths, so duplicated tasks sharing paths create spurious cycles
type: pitfall
---

`dag.build_dag` adds an edge whenever one task's output path equals another's input path — in addition to explicit `depends_on`. **Why**: any feature that clones or duplicates a task (loop iterations, fan-out, retries-as-tasks) while copying its `inputs`/`outputs` will make `build_dag` infer edges between the duplicates and their originals, producing false `CycleError`s against already-terminal tasks. **Apply**: when generating/cloning tasks at runtime, clear `inputs`/`outputs` on clones (as `engine._clone_body` does for `__iterN` loop tasks, ADR-007) or otherwise ensure duplicated tasks don't share artifact paths.

---
name: persisted-model-fields-need-defaults
description: New fields on RunState or any model nested in it must have defaults, or resuming an older run fails to deserialize state.json
type: constraint
---

`RunState` (and everything nested in it) is persisted to `state.json` and reloaded on resume. **Why**: a new field without a default makes pydantic reject any `state.json` written before the field existed, breaking resume for in-flight runs. **Apply**: when adding a field to `RunState` or a nested persisted model, always give it a default or `Field(default_factory=...)` (as the budget work did for `RunState.budget_counters` / `BudgetCounters`).

---
name: engine-api-tests-dont-cover-cli
description: Testing via Orchestrator() directly leaves `ao` CLI paths (resume, validate, flag parsing) uncovered even with thorough engine-API tests
type: pitfall
---

Engine-API integration tests (calling `Orchestrator(...).run(...)` directly) do not exercise the CLI layer. Dynamic injection, loop construct, and resume were all fully covered at engine level but had 0% CliRunner coverage. **Why**: CLI wiring, flag parsing, and command routing are separate code paths that only CliRunner tests exercise. **Apply**: for every significant feature, write both an engine-API integration test AND a CliRunner E2E test — treat them as different test layers, not substitutes.

---
name: loop-iterate-event-only-on-continue
description: The engine emits the `loop.iterate` log event ONLY when the gate says continue; a single-iteration loop never emits it
type: pitfall
---

A `LoopSpec` whose gate verdict is `{"continue": false}` on the first pass runs its body exactly once and **never emits a `loop.iterate` event** — the engine logs `loop.iterate` only on the branch that injects the next iteration's clones (`engine.py`, guarded by `should_cont`). Loop-body clones (ids like `bugfix__iter2`, `final-review__iter2`, `origin="loop"`) likewise only appear from iteration 2 onward. **Why**: a fixture/test that asserts `loop.iterate` (or `__iterN` clones) against a single-round happy path is unsatisfiable and will tempt someone to "fix" it by weakening the assertion. **Apply**: to observe `loop.iterate`/loop clones deterministically, pre-seed a 2-round gate sequence — iteration 1 gate `{"continue": true}` at `output/<gate>.json`, iteration 2 gate `{"continue": false}` at `output/<gate>__iter2.json` (the engine suffixes `__iterN` before the extension). Keep separate expected-event fixtures for the 1-round vs 2-round scenarios (see `playground/sum-of-array/fixtures/expected_events.json`). Related: driving specs through `CliRunner` needs absolute spec paths — `run_cli` in `tests/playground/harness.py` converts `--workflow/--reposets/--agents` values to absolute because CliRunner does not resolve them against a working dir.

---
name: real-llm-tests-not-tmp-path
description: Real ClaudeCliExecutor e2e tests must use a repo-local workspace, not pytest tmp_path (system /tmp is outside the claude sandbox)
type: pitfall
---

The `claude` subprocess spawned by `ClaudeCliExecutor` is sandboxed to the repo working directory; pytest's `tmp_path` (system `/tmp/pytest-of-…`) is **outside** that allow-list, so the agent's instruction reads and output writes there are denied and the run never reaches `succeeded` (fails with "Claude Code may only [access] the allowed working directories"). **Why**: executor paths are absolute (resolved under `workspace_root`), so the *location* of the workspace — not path relativity — is what the sandbox rejects; no `--add-dir` is needed if the workspace lives inside the repo. **Apply**: for any test that spawns the real `claude` CLI, use the `real_llm_workspace` fixture (`tests/playground/conftest.py`) which allocates a per-test, gitignored dir under `playground/.tmp/` inside the allow-list — never `tmp_path`. Fake-executor tiers can keep using `tmp_path`.

---
name: headless-claude-needs-acceptedits
description: A headless `claude -p` agent that must write files needs --permission-mode acceptEdits, or Write tool calls are silently denied
type: constraint
---

`claude -p` (headless) cannot prompt for tool approval, so in the default permission mode its Write/Edit calls are denied and a file-producing agent completes without writing anything. **Why**: no interactive approver exists in a spawned subprocess. **Apply**: give file-writing agents `--permission-mode acceptEdits` (auto-accepts file edits/writes, still gates bash) via the `command_template` in `agents.*.json` — a bounded choice preferable to `--dangerously-skip-permissions`; this is config-only, no `src/` change (see `playground/sum-of-array/agents.claude.json`).

---
name: agentspec-schema-must-stay-in-sync
description: specs/agents.schema.json uses additionalProperties:false — every new AgentSpec field must also be added to the schema or agents.*.json files fail validation
type: pitfall
---

`specs/agents.schema.json` has `"additionalProperties": false` for agent entries. **Why**: any field in `AgentSpec` (models.py) that isn't registered there will cause `ao validate` to reject any `agents.*.json` that uses it — including the playground files. **Apply**: whenever a new field is added to `AgentSpec`, add a matching entry to `$defs/agent/properties` in `specs/agents.schema.json` in the same commit. Current registered fields: `executor`, `command_template`, `prompt_template`, `context_window`, `extra_args`, `model`, `effort`.

---
name: e2e-fixtures-must-not-auto-delete
description: E2E test fixtures must NOT auto-delete workspaces on teardown; artifacts must survive for audit — use `ao prune` for manual cleanup
type: constraint
---

Auto-cleaning a real-LLM workspace in a `finally` block (`shutil.rmtree`) destroys agent outputs, logs, and control files that are essential for post-run debugging and audit. **Why**: e2e tests produce the same observable artifacts as production runs; deleting them by default violates the project's "no automatic artifact removal" policy. **Apply**: in `real_llm_workspace` (and any future e2e fixture), omit teardown cleanup — yield the path and let it persist. Run `ao prune --workspace <path>` explicitly when disk space needs reclaiming.

---
name: claude-max-turns-vs-orchestrator-max-attempts
description: "Reached maximum number of turns (N)" is the Claude CLI turn budget, not the orchestrator retry count — they are independent limits
type: pitfall
---

When a task fails with `"errors":["Reached maximum number of turns (N)"]`, that `N` is the Claude CLI's `--max-turns` (set by `effort` on `AgentSpec` via `EFFORT_MAX_TURNS` in `models.py`: low=15, medium=30, high=60 — raised from an earlier, too-tight 3/5/10 that caused flaky `error_max_turns` failures on trivial tasks). It is **not** the orchestrator's `max_attempts`. The orchestrator's retry count appears as `attempt X/Y` in its log line, where Y is `max_attempts`. **Why**: two limits operate at different layers — the CLI terminates one agent invocation; the orchestrator decides whether to invoke again. **Apply**: when diagnosing a failed task, read both: `attempt X/Y` (orchestrator retries) and `terminal_reason`/`errors` in the task error JSON (why the individual agent invocation ended). `attempt 1/1` means one attempt, no retry — regardless of what `max_turns` shows.

---
name: acceptedits-blocks-bash-bypasspermissions-for-code-agents
description: `acceptEdits` only allows file writes; agents that run Bash (compile, test) need `bypassPermissions` or they burn all turns on denials
type: constraint
---

`--permission-mode acceptEdits` auto-approves Write/Edit file operations but **denies Bash execution** in headless mode. An agent instructed to run `python -c`, `pytest`, or any shell command will receive a silent permission denial, burn its remaining turns on workarounds, and exit code 1 without producing outputs. The denial appears as `permission_denials: [{tool_name: "Bash", ...}]` buried in the task error JSON. **Why**: the constraint is non-obvious from the permission mode name. **Apply**: agents that only write files (architect, reviewer, integrator) → `acceptEdits`; agents that must run code (developer, test-writer) → `bypassPermissions`. Scope risk by running these agents inside the repo-local gitignored workspace (`playground/.tmp/`).

---
name: emit-tasks-skip-validation
description: Manifest-injected tasks bypass schema/cross-validation; unknown depends_on ids crash the engine instead of failing cleanly
type: pitfall
---

`task_manifest_path` files are parsed via `TaskSpec(**t)` (Pydantic field-level only) — no JSON-schema `additionalProperties`/id-pattern check, no agent-exists check, no `depends_on`-reference check (`read_task_manifest` in `artifacts.py`). **Why**: `cross_validate`/schema validation run once, at CLI `validate`/`run` time, only against the statically authored `workflow.json` — never re-invoked on injected tasks. A `depends_on` id that doesn't match any known task doesn't raise a clean `InjectionError`; `build_dag` silently creates a phantom node for it, and the engine later crashes with an uncaught `KeyError` trying to run it (escapes the CLI's `except (OrchestratorError, CycleError)` handler as a raw traceback). **Apply**: when writing an instruction file that has an agent emit a task manifest, double- and triple-check every `depends_on` id in the manifest matches an id also in that manifest (or an already-existing task) exactly — this class of bug fails ugly, not clean.

---
name: dynamic-fanout-fixed-aggregator-contract
description: Unknown-N dynamic fan-out needs an emitted aggregator with a FIXED id+output path so static tasks can attach via inferred edges
type: convention
---

When an emitting task (e.g. a reviewer deciding how many modules/findings need follow-up work) fans out into N sibling tasks where N is only known at run time, it must also emit exactly one aggregator task with a **fixed** id and **fixed** output path in the same manifest batch, with `depends_on` listing every sibling id — even emitting a trivial aggregator when N=0, so the output path always exists. **Why**: a statically-authored downstream task cannot `depends_on` task ids that don't exist yet at author time (rejected by validation), and the engine's inferred-edge matching (`build_dag`) only works against a *fixed* path, not N variable ones. Fixing the aggregator's identity while leaving sibling count dynamic is what lets a static task depend on an unknown-count fan-out. **Apply**: any workflow using `emit_tasks` for a fan-out-then-aggregate pattern (see `docs-md/guide-dynamic-task-injection.md`, `specs/examples/workflow-dynamic-fanout.json`, `workflow-dynamic-pipeline.json`); this is also the resolution to the repo's own previously-deferred OQ-2 ("multi-chain aggregator") question in `docs-md/e2e-playground-testing.md`.

---
name: injected-task-ids-globally-unique
description: Emitted task ids must be unique across the whole run, not just within one manifest batch — collisions raise InjectionError
type: constraint
---

`Orchestrator._inject` checks every new manifest-emitted task id against ALL existing ids (static tasks, previously injected tasks, and earlier entries in the same manifest batch) — any collision raises a clean `InjectionError` and fails the run. **Why**: task ids are the sole addressing mechanism for `depends_on` and DAG nodes; the uniqueness check is run-wide, not scoped per-emitter or per-manifest. **Apply**: when an instruction file tells an agent to emit tasks, have it namespace ids by something it controls and knows is unique (e.g. `subreview-<module-name>`, `fix-<finding-id>`) rather than a bare counter — especially if multiple emitters or nested emission could occur in the same run.

---
name: skipped-emit-task-never-injects
description: An emit_tasks task skipped via skip_if_outputs_exist never injects its manifest; emitters must set skip_if_outputs_exist:false
type: pitfall
---

The engine's skip path (`should_skip` → mark `skipped` → `continue`) exits the task loop **before** the dynamic-expansion hook, which only fires for `ts.status == "succeeded"` after real execution. **Why**: on a fresh `ao run` where the emitter's outputs already exist, the emitter skips, no tasks are injected, and every static task waiting on the fan-out's aggregator output fails on a missing input — while `ao resume` works fine because injected tasks are restored from persisted run state. **Apply**: any task with `emit_tasks: true` must declare `skip_if_outputs_exist: false` (as the finplan epic-runner's `task-breakdown` does); treat "emitter skipped" as a red flag when diagnosing missing-input failures downstream of a fan-out.

---
name: global-model-override-clobbers-agents
description: ao run --model/--effort (and AO_MODEL/AO_EFFORT/config) overwrite every AgentSpec, silently downgrading pinned per-agent models
type: pitfall
---

A global model/effort setting is applied in `cli.py` by looping `spec.model = eff_model` over ALL agents — the most specific declaration (per-agent `model` in `agents.json`) loses to the most generic one. **Why**: running a workflow with mixed-model agents (e.g. finplan's `architect-opus`/`reviewer-opus` next to haiku workers) under `ao run --model haiku` silently downgrades the opus agents; outputs look normal but come from the wrong model. **Apply**: until epic `E-st5p3q-settings-precedence-policy` lands the specific-wins chain (ADR-0003), never pass `--model`/`--effort`/`AO_MODEL`/`AO_EFFORT` (or config `model:`/`effort:`) when the agents file pins per-agent values; check `agents.*.json` first.

---
name: breaker-latch-persists-across-resume
description: A circuit breaker id already recorded in tripped_breakers before a stop will not re-halt a resumed run, even if its condition is still true
type: constraint
---

`evaluate_breakers` latches per breaker id for the entire life of `state.tripped_breakers` (persisted across `ao resume`), not per in-memory session. **Why**: the latch check is `if spec.id in {tb.id for tb in state.tripped_breakers}: continue` — an id present from before the run stopped is never re-evaluated, regardless of whether its underlying condition (e.g. a `stop_file` still present, an `injected_task_count` still over cap) remains true. **Apply**: when documenting or reasoning about resume behavior for `circuit_breakers`, only a condition that was *never evaluated* before the stop (e.g. a crash mid-injection, or a different breaker/failure halted the run first) will trip fresh on resume; an operator must clear the condition itself (delete the stop file, prune injected tasks) to get past an already-latched breaker. See `docs-md/lld-run-control-routing-breakers.md` §9/§11.

---
name: route-needs-own-sink-before-join
description: ao validate requires every router route to contain its own terminal sink task in its exclusive cone; a shared downstream join task does not satisfy this
type: constraint
---

Rule 8 of `validate_run_control` (spec.py) checks that each route's *exclusive* cone (tasks reachable only from that route) contains at least one task with no successors anywhere in the graph. **Why**: if a route's only task feeds directly into a cross-route `join` task, that task is excluded from the route's exclusive cone (it's shared/converged), so the exclusive cone has no sink and validation fails with "no sink ... this route can never reach a terminal endpoint". **Apply**: when authoring a workflow spec that combines `branches` with a converging `join` task, give each route its own dedicated terminal task (e.g. `bug-report`, `doc-report`) in addition to the shared join/notify task fed by the routes' entry tasks — see `specs/examples/workflow-routing-breakers.json`.

---
name: docs-refresh-must-search-whole-doc
description: A ticket correcting a design-vs-implementation drift in a doc must grep the whole doc for other passages repeating the old (now-wrong) claim, not just add a note at the discovery site
type: convention
---

Design docs (HLD/LLD) are often written before implementation surfaces a real nuance; a later docs-refresh/close-out ticket that adds a corrective note near where the nuance was found can still leave the *original*, now-incorrect claim standing elsewhere in the same document. **Why**: `docs-md/lld-run-control-routing-breakers.md` had this happen for real — §9's original resume-semantics prose and an old §11 edge-case bullet both claimed structural breakers "re-trip immediately"/"re-trip if still true" on resume, contradicting a *new* §11 gotcha bullet correctly describing the actual (latched) behavior, until both were found and reconciled together. **Apply**: before closing out a docs-reconciliation ticket, `grep` the doc for every other mention of the concept you're correcting (not just the section you were told to update) and fix all of them consistently.

---
name: verify-completion-narrative-and-header
description: A subagent's STATUS.md Completion section can overstate what actually shipped and can leave the header State/Status field un-synced with its own "done" narrative
type: pitfall
---

Two independent failure modes were caught in the same ticket (E-rc7k2v `T-d8w4v2`): (1) the Completion section claimed an example spec "demonstrates branches+circuit_breakers+join" when the shipped file actually had no `join` at all (the ticket's own AC2 was unmet); (2) the STATUS.md header still read `State: Draft` / `Owner: architect (pending tester assignment)` and `TASK.md` still said `Status: Draft`, despite the Completion section declaring the ticket done. **Why**: a subagent's prose summary is not itself evidence — it can drift from the artifact it describes, and updating a Completion section doesn't automatically update the header fields the ticket-conventions system (`ad/tickets/README.md`) relies on for status sync. **Apply**: when accepting a subagent's ticket as done, (a) check the actual artifact against every acceptance criterion literally, not just the narrative, and (b) confirm the header `State`/`Status`/`Owner` fields match the Completion section — fix both if they don't, and note the fix rather than silently rewriting the subagent's original text.
