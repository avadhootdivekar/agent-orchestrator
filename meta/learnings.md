# Learnings — agent-orchestrator

---
Learning-ID: LRN-20260616-uv-not-pip
Learning: Use `uv run <tool>` for all dev tooling; `pip`/`pip3` are not installed in this environment.
Context: Discovered when verifying the MVP implementation — `pip install` failed, `uv run pytest` worked.
By: agent
Role: agent
Date: 2026-06-16
---

---
Learning-ID: LRN-20260616-subagent-lint-verify
Learning: Always re-run ruff/mypy yourself after a subagent delivers code; subagents may claim "clean" when errors remain.
Context: Developer subagent reported ruff clean but 16 lint errors were present; caught only by an independent re-run.
By: agent
Role: agent
Date: 2026-06-16
---

---
Learning-ID: LRN-20260616-ruff-fix-limit
Learning: `uv run ruff check --fix` auto-fixes most lint errors but leaves line-length (E501) violations for manual edits.
Context: 13 of 16 ruff errors were auto-fixed; 2 E501s in test files needed manual line-wrapping.
By: agent
Role: agent
Date: 2026-06-16
---

---
Learning-ID: LRN-20260616-workflow-id-lowercase
Learning: Workflow `id` in JSON spec must be fully lowercase (`^[a-z0-9][a-z0-9-_]*$`); uppercase `E-` ticket prefix violates the schema.
Context: Using `E-test01-example` as workflow id caused SpecValidationError; ticket ids (meta/tickets/) still use uppercase E-.
By: agent
Role: agent
Date: 2026-06-16
---

---
Learning-ID: LRN-20260616-cross-validate-arg-order
Learning: `cross_validate(workflow, reposets, agents)` — reposets before agents; swapping them produces a misleading "Unknown repo_set" error.
Context: Passed args as (wf, agents, reposets); error looked like a config problem but was an API misuse.
By: agent
Role: agent
Date: 2026-06-16
---

---
Learning-ID: LRN-20260616-ao-self-host-risk
Learning: AO cannot safely orchestrate its own development with the editable install: any regression breaks the CLI used to recover.
Context: Editable install means every `uv run agent-orchestrator` imports live from src/; no stable copy exists as fallback.
By: agent
Role: agent
Date: 2026-06-16
---

---
Learning-ID: LRN-20260616-tool-install-noneditable-isolation
Learning: Use `uv tool install .` (non-editable) for v1/v2 isolation; `--editable` makes v1 and v2 the same working copy.
Context: install.sh was generated with `--editable`; v1 broke whenever v2 regressed. Solution: non-editable snapshot + dual-invocation (`ao` = v1, `uv run ao` = v2).
By: agent
Role: agent
Date: 2026-06-16
---

---
Learning-ID: LRN-20260616-agents-json-schema-fields
Learning: `agents.json` schema requires `executor`, `command_template`, `prompt_template`; fields `type`, `description`, `env` are rejected as additional properties.
Context: Self-dev agents.json used `type`/`description`/`env` (intuitive names) and failed schema validation; had to match the example format exactly.
By: agent
Role: agent
Date: 2026-06-16
---

---
Learning-ID: LRN-20260618-venv-python-invocation
Learning: Run project tooling via `.venv/bin/python -m <tool>` — bare `python` is absent from PATH and system `python3` has no pytest.
Context: Capturing a test baseline failed with `python: command not found` then `No module named pytest`; only `.venv/bin/python` had the deps.
By: agent
Role: agent
Date: 2026-06-18
---

---
Learning-ID: LRN-20260618-build-dag-inferred-edges
Learning: `build_dag` infers DAG edges from matching input/output paths, not just `depends_on`; any cloned/duplicated task that copies those paths creates spurious `CycleError`s — clear inputs/outputs on clones.
Context: Loop-iteration clones (`__iterN`) initially carried the body's inputs/outputs and produced false cycles back to terminal tasks; fixed by emptying them in `_clone_body` (ADR-007).
By: agent
Role: agent
Date: 2026-06-18
---

---
Learning-ID: LRN-20260618-mypy-scope-src
Learning: Run `mypy src` (not `mypy .`) for a clean production signal — `mypy .` surfaces pre-existing errors in `tests/test_project_config.py` unrelated to current work.
Context: Subagents reported "mypy clean on production modules" while `mypy .` still showed 3 long-standing test errors; scoping to src disambiguates real regressions.
By: agent
Role: agent
Date: 2026-06-18
---

---
Learning-ID: LRN-20260618-runstate-new-field-default
Learning: Every new field added to `RunState` (or any model nested in it) must have a default/`default_factory`, or resuming a run created before the change fails to deserialize the persisted `state.json`.
Context: Budget work added `BudgetCounters` to `RunState` via `Field(default_factory=...)` specifically so older run states still load on resume.
By: agent
Role: agent
Date: 2026-06-18
---

---
Learning-ID: LRN-20260618-wait-needs-unsatisfiable-guard
Learning: A wait-on-exhaustion path must guard against an unsatisfiable charge (one task's estimate exceeding the entire total/rate-window cap) or the engine sleeps forever; detect and stop/fail instead of waiting.
Context: Token-budget engine added an `_is_unsatisfiable` check so a task whose estimate can never fit a window doesn't loop indefinitely under `on_exhaustion=wait`.
By: agent
Role: agent
Date: 2026-06-18
---

---
Learning-ID: LRN-20260620-api-tests-dont-cover-cli
Learning: Testing a feature via `Orchestrator(...)` directly does not cover the CLI surface; `ao resume`, `ao validate`, and dynamic/loop workflow paths had 0% CLI coverage despite thorough engine-API tests.
Context: E2E test audit found dynamic injection and loop construct fully tested at Python-API level but completely untested via CliRunner — resume and validate commands likewise skipped.
By: agent
Role: agent
Date: 2026-06-20
---

---
Learning-ID: LRN-20260620-fakeexecutor-manifest-auto
Learning: `FakeExecutor` automatically writes the task manifest for tasks with `emit_tasks=True`; no manual manifest pre-seeding is needed in CLI-level dynamic injection tests.
Context: Writing CliRunner-level dynamic injection test — expected manual setup but FakeExecutor already handles manifest writing, matching the production contract.
By: agent
Role: agent
Date: 2026-06-20
---

---
Learning-ID: LRN-20260620-resume-cli-test-pattern
Learning: For `ao resume` CLI tests, do the first (failing) run via Python API to capture `run_id`, then invoke `ao resume --run-id <id>` via CliRunner — avoids parsing run_id from CLI stdout.
Context: CliRunner-based resume test needed a reliable run_id; Python-API first run returns `RunState.run_id` directly.
By: agent
Role: agent
Date: 2026-06-20
---

---
Learning-ID: LRN-20260701-real-claude-subprocess-sandbox-tmp
Learning: The real `claude` subprocess spawned by `ClaudeCliExecutor` is sandboxed to the repo working directory; pytest's `tmp_path` (system `/tmp/pytest-of-…`) is OUTSIDE that allow-list, so instruction reads and output writes there are denied and the run never reaches `succeeded`. Fix: run real-LLM e2e tests in a repo-local, gitignored workspace (`playground/.tmp/` via a `real_llm_workspace` fixture) so it sits inside the allow-list — no `--add-dir` / unrestricted access needed. Executor paths are absolute (resolved under `workspace_root`), so only the workspace location changes.
Context: `test_sum_of_array_real_llm.py` failed/BLOCKED under `AO_E2E_REAL_LLM=1` with "Claude Code may only [access] the allowed working directories" for every spine agent. Moving off `tmp_path` fixed it; verified all 3 real-LLM tests pass against the real `claude` interface.
By: agent
Role: developer
Date: 2026-07-01
---

---
Learning-ID: LRN-20260701-headless-claude-permission-mode
Learning: A headless `claude -p` agent expected to WRITE files needs an elevated permission mode or its Write tool calls are denied (no interactive prompt to approve). Add `--permission-mode acceptEdits` (auto-accepts file edits/writes, still gates bash) — a bounded choice preferable to `--dangerously-skip-permissions`. This is config-only via `agents.*.json` `command_template`, no `src/` change.
Context: Real-LLM playground agents only read instructions/inputs and write markdown/JSON outputs; `acceptEdits` in `agents.claude.json` let them complete autonomously.
By: agent
Role: developer
Date: 2026-07-01
---

---
Learning-ID: LRN-20260702-agents-schema-must-track-agentspec
Learning: When adding new fields to `AgentSpec` in `models.py`, `specs/agents.schema.json` MUST also be updated — it uses `additionalProperties: false`, so unregistered fields in any `agents.*.json` file cause schema validation failure at `ao validate` time.
Context: Adding `model` and `effort` to `AgentSpec` without updating the schema would have made the playground `agents.claude.json` (and any user-authored agents file) fail to validate.
By: agent
Role: developer
Date: 2026-07-02
---

---
Learning-ID: LRN-20260702-no-auto-cleanup-e2e-fixtures
Learning: E2E test fixtures that auto-delete workspace directories on teardown (`shutil.rmtree` in `finally`) destroy debugging artifacts needed for post-run audit. Preserve workspaces by default; expose an explicit cleanup command (`ao prune`) instead.
Context: `real_llm_workspace` fixture auto-cleaned via `shutil.rmtree`; removed per user requirement that artifacts should survive test runs for debugging/audit.
By: agent
Role: developer
Date: 2026-07-02
---

---
Learning-ID: LRN-20260702-effort-maps-to-max-turns
Learning: The `effort` field on `AgentSpec` maps to `--max-turns` via `EFFORT_MAX_TURNS = {low:15, medium:30, high:60}` (models.py). Keep this mapping GENEROUS: `--max-turns` is only a runaway-loop breaker — the *real* cost guard is the token budget. Tight turn caps (the original 3/5/10) cause non-deterministic `error_max_turns` failures on trivial tasks that happen to need a few extra tool calls. An explicit `AgentSpec.max_turns` overrides the effort-derived value, and `ao run/resume --max-turns N` (also `MAX_TURNS`/`AO_MAX_TURNS` in the Makefile/harness) sets it on every agent.
Context: Corrects the earlier 3/5/10 convention — real-LLM `architect-design` failed `error_max_turns` (num_turns:6, permission_denials:[]) under `medium`→5; raising to 15/30/60 + adding the override/flag fixed it. Playground `agents.claude.json` still pins `claude-haiku-4-5-20251001` + `effort: medium`.
By: agent
Role: developer
Date: 2026-07-02
---

---
Learning-ID: LRN-20260702-max-turns-vs-max-attempts
Learning: "Reached maximum number of turns (N)" in a failed task error is the Claude CLI's per-invocation turn budget (from `effort`→`--max-turns`), NOT the orchestrator's `max_attempts`; `attempt X/Y` in logs is the orchestrator retry counter.
Context: `impl-t1` showed `attempt 1/1` (one orchestrator attempt) but error "max_turns (5)" — two independent limits that confused diagnosis until both were read together.
By: agent
Role: developer
Date: 2026-07-02
---

---
Learning-ID: LRN-20260702-bypasspermissions-for-bash
Learning: A headless agent's `--permission-mode` must match what its INSTRUCTION actually does. `acceptEdits` auto-accepts file writes but silently DENIES Bash; any agent whose instruction runs Bash (compile, run pytest, syntax-check) stalls and burns its turns before failing — sometimes exiting 0 while skipping a declared output. Use `--permission-mode bypassPermissions` for those. Simplest robust convention for an example: standardize ALL agents on `bypassPermissions` (the playground `agents.claude.json` does this for all 5 agents) rather than per-agent tuning that breaks whenever an instruction adds a Bash step.
Context: Two separate hits, same root cause — `impl-t1` developer wasted its turns on a denied `python -c` syntax check; later `taskreview-t1` reviewer (still on `acceptEdits`) exited 0 but skipped `review.md` because its required `python -m pytest` was denied. Both fixed by `bypassPermissions`.
By: agent
Role: developer
Date: 2026-07-02
---

---
Learning-ID: LRN-20260702-agent-cwd-workspace
Learning: The `claude` subprocess must be spawned with an explicit `cwd`, or it inherits whatever dir `ao` was invoked from (usually the repo root) and any agent that writes a RELATIVE output path (from its instruction/spec — e.g. a manifest at `output/tasks-manifest.json`) silently escapes the workspace, landing at repo root. The agent then reports success while the engine can't find the declared output. Fix is framework-level + config-driven, NOT a per-test patch: `AgentSpec.working_dir` (optional) → engine resolves it under `reposet.workspace_root` (path-traversal guarded) → `TaskContext.cwd` (absolute; "" = OS inherits) → `subprocess.run(cwd=ctx.cwd or None)`. Default cwd = workspace root, so relative agent writes resolve deterministically inside the run's workspace.
Context: Real-LLM `architect-breakdown` claimed success but the manifest was found at repo-root `./output/tasks-manifest.json` instead of the workspace; `subprocess.run` had no `cwd`. Note this deliberately adds `src/` changes (models/engine/executor/cli) — the working-directory contract belongs in the framework so `ao` commands honor configured/default cwd, not in tests. Covered by `test_cwd_passed_to_subprocess`/`test_cwd_none_when_unset` (executor) + `TestAgentCwd` (engine).
By: agent
Role: developer
Date: 2026-07-02
---

---
Learning-ID: LRN-20260702-empty-env-falsy-no-flag
Learning: `AO_MAX_ATTEMPTS=""` (Makefile default `MAX_ATTEMPTS ?=`) is falsy in Python, so harness.py's `if max_attempts:` never injects `--max-attempts` and the workflow runs with `max_attempts=1` (no retry) silently.
Context: Real-LLM test ran without retry; `MAX_ATTEMPTS` was unset, empty string was silently skipped by the `if` guard in `run_cli`.
By: agent
Role: developer
Date: 2026-07-02
---

---
Learning-ID: LRN-20260702-quota-exhaustion-vs-429
Learning: Claude's usage-quota exhaustion ("You've hit your * limit") is NOT a provider-level 429 rate limit. Treat them as distinct signals: quota requires a long wait (minutes to hours) + re-queue without consuming a retry attempt; 429 is transient and handled by the existing retry/backoff loop. Mixing them would either burn retry budget on quota waits or apply wrong recovery logic.
Context: Implemented `_CLAUDE_QUOTA_PATTERN` detection in `parse_usage_and_429()` (claude_cli.py) with an early-return before 429 detection so the signals can never overlap. `TaskResult.claude_quota_exhausted` carries the flag; engine handles it in a separate outer-loop block.
By: agent
Role: developer
Date: 2026-07-02
---

---
Learning-ID: LRN-20260702-quota-single-source-regex
Learning: Keep the Claude quota-message regex at exactly ONE editable location (`_CLAUDE_QUOTA_PATTERN` in `executors/claude_cli.py`). User requirement: when the exact quota string changes (e.g. "session limit" → "weekly limit"), there should be one grep-and-edit location. Spreading the pattern to tests or engine would require multi-file updates and risk drift.
Context: Tests import `_CLAUDE_QUOTA_PATTERN` or craft strings that match it; they don't re-declare the pattern. Engine and models carry only the `claude_quota_exhausted` boolean flag.
By: agent
Role: developer
Date: 2026-07-02
---

---
Learning-ID: LRN-20260702-quota-max-wait-per-episode
Learning: The quota max-wait timer (`_quota_exhausted_since`) is per-exhaustion-episode only. It resets to `None` after each successfully completed task. Do NOT accumulate wait time across a run — only time within a single continuous exhaustion episode counts against `quota_max_wait_seconds`. Misunderstanding this as a total-run budget would cause legitimate long runs to fail prematurely.
Context: User clarification: "quota ONLY for current quota exhaustion event. Does NOT accumulate." Implemented as `_quota_exhausted_since = None` after `done.add(tid)` on success.
By: agent
Role: developer
Date: 2026-07-02
---

---
Learning-ID: LRN-20260702-stdin-devnull-subprocess
Learning: Always use `stdin=subprocess.DEVNULL` when spawning Claude CLI subprocesses. Claude may wait for user input in some cases (e.g. quota exhaustion interactive prompt). Without `DEVNULL`, the subprocess blocks indefinitely even in `-p` (non-interactive) mode if the process tries to read stdin.
Context: Added to `ClaudeCliExecutor.execute()` as a belt-and-suspenders measure; specifically needed when Claude outputs a quota message and then waits for a keypress before exiting.
By: agent
Role: developer
Date: 2026-07-02
---

---
Learning-ID: LRN-20260702-three-layer-config-precedence
Learning: For any runtime setting that users might want to control (model, effort, max_attempts, max_turns, quota settings), always expose all three layers: CLI flag > env var > config file value. Implement in a single `_resolve_run_settings()` function so the merge logic is in one place, not duplicated across `run` and `resume` commands.
Context: `model`/`effort` were missing from CLI; `max_attempts`/`max_turns` were in CLI but not in config file or env. All 6 runtime settings now live in `ProjectConfig` (pydantic), are read from `AO_*` env vars, and have `--flag` equivalents. `_resolve_run_settings()` does the merge once.
By: agent
Role: developer
Date: 2026-07-02
---

---
Learning-ID: LRN-20260704-emit-tasks-skips-validation
Learning: Tasks injected via `task_manifest_path` skip schema/cross-validation; a bad `depends_on` id crashes the engine with an uncaught `KeyError`, not a clean failure.
Context: Found while researching `read_task_manifest`/`build_dag` — manifest TaskSpecs get only Pydantic field checks, no reference validation.
By: agent
Role: agent
Date: 2026-07-04
---

---
Learning-ID: LRN-20260704-dynamic-fanout-fixed-aggregator
Learning: For unknown-N dynamic fan-out, give the emitted aggregator a FIXED id+output path (even at N=0) so a static task attaches via inferred edges, not `depends_on`.
Context: Static tasks can't `depends_on` not-yet-injected ids; this resolves the repo's own documented OQ-2 aggregator gap.
By: agent
Role: agent
Date: 2026-07-04
---

---
Learning-ID: LRN-20260704-injected-ids-globally-unique
Learning: Injected task ids must be globally unique across the whole run, not just within one manifest — collisions with any prior static/injected id fail the run via `InjectionError`.
Context: Namespace emitted ids by something the emitter controls and knows is unique (e.g. `subreview-<module>`), not a bare counter.
By: agent
Role: agent
Date: 2026-07-04
---

---
Learning-ID: LRN-20260704-skipped-emit-task-never-injects
Learning: A task with `emit_tasks: true` that is skipped via `skip_if_outputs_exist` NEVER injects its manifest — the skip path `continue`s before the injection hook (`engine.py` runs injection only for `ts.status == "succeeded"` after real execution). Emitter tasks must set `skip_if_outputs_exist: false` or a fresh `ao run` over existing outputs strands every downstream consumer of the fan-out.
Context: Found while wiring a downstream runner's epic-runner workflow; `ao resume` is unaffected (injected tasks are restored from run state) — only fresh runs with pre-existing outputs hit this.
By: agent
Role: developer
Date: 2026-07-04
---

---
Learning-ID: LRN-20260709-model-override-clobbers-agents
Learning: Global `--model`/`--effort` (or `AO_MODEL`/`AO_EFFORT`/config) overwrites the field on EVERY AgentSpec, silently downgrading deliberately-pinned per-agent models (e.g. a downstream runner's `architect-opus`).
Context: Found in cli.py during ADR-0003 settings-precedence design; fix tracked in E-st5p3q — until then never combine global overrides with mixed-model agents.json.
By: agent
Role: architect
Date: 2026-07-09
---

---
Learning-ID: LRN-20260710-breaker-latch-persists-resume
Learning: `evaluate_breakers`'s trip latch is keyed on `state.tripped_breakers` for the run's lifetime — an already-tripped id never re-halts a resumed run, even if its condition is still true.
Context: Found in E-rc7k2v resume-replay (T-t4m8x1); only a condition never evaluated before the stop re-trips on resume.
By: agent
Role: developer
Date: 2026-07-10
---

---
Learning-ID: LRN-20260710-route-sink-required-per-branch
Learning: `ao validate` requires each router route to have its own sink (task with no successors) inside its exclusive cone — a downstream `join` task doesn't satisfy this.
Context: A route whose only task feeds a cross-route join fails validation; add a per-route terminal task before converging.
By: agent
Role: developer
Date: 2026-07-10
---

---
Learning-ID: LRN-20260710-doc-reconcile-search-whole-doc
Learning: A docs-refresh ticket correcting an implementation-vs-design drift must search the WHOLE doc for other passages stating the old claim, not just add a note at the discovery site.
Context: LLD §9 and a new §11 gotcha bullet stated opposite resume-breaker behavior until reconciled together in Wave 5 of E-rc7k2v.
By: agent
Role: developer
Date: 2026-07-10
---

---
Learning-ID: LRN-20260710-subagent-completion-vs-header
Learning: A subagent's Completion narrative can overstate what shipped (e.g. claiming an example spec demonstrates `join` when it doesn't) and leave the header `State`/`Status` on `Draft` despite declaring itself done.
Context: Caught in E-rc7k2v T-d8w4v2 by checking the actual spec file and STATUS.md header against the Completion section.
By: agent
Role: developer
Date: 2026-07-10
---

---
Learning-ID: LRN-20260710-retry-loop-discarded-usage
Learning: A retry loop that reassigns `last_result = result` on every attempt silently discards actual usage/cost data from failed attempts — only the winning attempt's numbers survive. The same overwrite-vs-sum bug also affects `output_dir` reuse across attempts (each retry clobbers the previous attempt's capture files) if the capture path isn't attempt-suffixed.
Context: `engine._run_with_retries` overwrote `TaskResult` token/cost fields on every attempt instead of summing; fixed in E-9h3m7k by accumulating across attempts and suffixing `output_dir` with `attempt-<N>/`. This also silently under-charged budget reconciliation for retried tasks (`_sum_actuals` only saw the last attempt).
By: agent
Role: developer
Date: 2026-07-10
---

---
Learning-ID: LRN-20260710-uv-force-not-enough
Learning: `uv tool install <dir> --force` can resolve a stale cached build despite reporting success — observed reinstalling a snapshot missing an entire epic's worth of code (no `compute_cones`) right after a "successful" `--force` reinstall. `install.sh --check`'s git-commit-stamp comparison doesn't catch this either (it's blind to uncommitted working-tree changes, the common mid-session case).
Context: Adding `--reinstall` to the `uv tool install` invocation in `install.sh`'s `_do_install` fixed it — confirmed via a marker-line round-trip that `--force --reinstall` picks up live source content without needing `uv cache clean`/`--no-cache`. See [[project_ao_install_staleness]].
By: agent
Role: developer
Date: 2026-07-10
---

---
Learning-ID: LRN-20260714-wall-clock-started-at-frozen
Learning: `RunState.started_at` is set once at run creation and never updated on resume, so `run_wall_clock_seconds` counts pause/resume gaps as elapsed. Use the new `run_active_seconds` condition (E-3JTmVu) when gaps must not count.
Context: `prepare_resume()` only resets status to running; started_at is untouched — confirmed while designing the pause-immune breaker.
By: agent
Role: developer
Date: 2026-07-14
---

---
Learning-ID: LRN-20260714-task-started-at-single-writer
Learning: `TaskRunState.started_at` must be set only on a task's FIRST dispatch — quota/429/budget-wait redispatch loops (`cursor -= 1; continue`) that skip the guard silently overwrite it, undercounting `run_active_seconds`'s duration sum.
Context: Fixed with `if ts.started_at is None:` guard in engine.py during E-3JTmVu's reviewer pass; single writer, single reader field.
By: agent
Role: developer
Date: 2026-07-14
---

---
Learning-ID: LRN-20260714-set-repo-local-git-identity
Learning: Sandbox has no git `user.name`/`user.email` at any scope; set them repo-local before committing or git fails or auto-derives a machine-hostname identity, leaking the host into public history.
Context: Public-sanitization commit — `git config user.email` returned empty here.
By: agent
Role: agent
Date: 2026-07-14
---

---
Learning-ID: LRN-20260714-exists-guard-silent-skip-test
Learning: A test wrapping assertions in `if abs_path.exists():` around a hardcoded absolute path runs ZERO assertions wherever that path is absent (CI/other machines) — a green no-op; build paths via `Path(__file__).resolve().parents[N]`.
Context: `test_schema_round_trip` asserted nothing off-machine until its absolute path was made repo-relative.
By: agent
Role: agent
Date: 2026-07-14
---

---
Learning-ID: LRN-20260714-stash-baseline-splits-renames
Learning: `git stash`/`pop` to snapshot a baseline on a `git mv`-heavy tree de-consolidates rename staging (pop yields unstaged deletions + staged adds, not renames); re-run `git add -A` and re-verify.
Context: Baseline pytest via stashing 140 renames; pop left ad/ as ` D`, meta/ as `A`.
By: agent
Role: agent
Date: 2026-07-14
---

---
Learning-ID: LRN-20260714-toplevel-dir-rename-preserves-depth
Learning: Renaming one top-level dir to another (`ad/`→`meta/`) preserves every relative-link depth, so a plain string-swap of the path prefix in `../..` links is correct without recomputing `../` counts.
Context: Migrated 148 files; `../../ad/tickets`→`../../meta/tickets` resolved with no depth change.
By: agent
Role: agent
Date: 2026-07-14
---

---
Learning-ID: LRN-20260714-git-grep-publish-surface-sweep
Learning: For "what gets published" audits, sweep with `git grep`/`git ls-files` (tracked-only, includes dotfiles) not `rg` (skips hidden, honors .gitignore); anchor bare tokens `(^|[^[:alnum:]/])ad/` to skip read/head/load and `uv.lock` fragments.
Context: Public-sanitization; unanchored `ad/` matched read//head/ and lockfile hashes.
By: agent
Role: agent
Date: 2026-07-14
---

---
Learning-ID: LRN-20260714-validator-allowlists-derive-from-registry
Learning: A validator's "implemented features" allowlist must be derived from the implementation registry, never hand-copied: spec.py's hardcoded `_MVP_BREAKER_CONDITIONS` silently drifted when E-3JTmVu registered `run_active_seconds` in `BREAKER_REGISTRY` + schema but not in the allowlist, so `ao validate` rejected a fully-implemented condition as "not implemented". Fixed by `frozenset(BREAKER_REGISTRY)` + a parametrized test pinning validate-acceptance to every registry key.
Context: Same drift class as the agents.schema.json additionalProperties pitfall; the epic's test only exercised jsonschema, not validate_run_control — always test the acceptance path at the validator layer too.
By: agent
Role: developer
Date: 2026-07-14
---

---
Learning-ID: LRN-20260714-install-yes-noops-on-matching-stamp
Learning: `bash install.sh --yes` no-ops ("already up to date") whenever the commit stamp matches HEAD — it is blind to uncommitted src/ changes. To promote working-tree edits into the global `ao` snapshot, run `bash install.sh --force` (its `uv tool install --force --reinstall` correctly picks up live tree content).
Context: Reinstall after the run_active_seconds fix silently skipped; probing the tool venv (`assert _MVP_BREAKER_CONDITIONS == frozenset(BREAKER_REGISTRY)`) caught it.
By: agent
Role: developer
Date: 2026-07-14
---

---
Learning-ID: LRN-20260715-dispatchexecutor-fake-uncontrollable-via-cli
Learning: `DispatchExecutor` (used by every real `ao run`/`ao resume` invocation) always constructs a bare `FakeExecutor()` with zero configuration — there is no way to make an `executor: "fake"` agent fail deterministically (custom error text, fail-then-succeed sequencing, rate-limit/quota simulation) through the unmodified CLI path, only via the engine-API (`Orchestrator(FakeExecutor(behaviors=...), ...)` constructed directly in test code).
Context: Discovered independently while writing CliRunner e2e tests for self-heal (needed a genuine dispatch failure with a specific transient-vs-non-transient error message); then found `tests/test_cli.py::TestRunCommand::test_failed_run_exits_1`'s own comment already documents hitting the identical wall and working around it via a missing-input failure instead. Workaround used here: an `executor: "claude_cli"` agent with a deterministic, network-free `sh -c "echo '...' >&2; exit 1"` `command_template` in place of `claude` — genuinely real subprocess failure, no API key/binary needed, and it lets the error text be controlled (unlike a missing-input failure).
By: agent
Role: developer
Date: 2026-07-15
---

---
Learning-ID: LRN-20260715-wheel-packaging-excludes-specs-bake-templates
Learning: `pyproject.toml`'s `[tool.hatch.build.targets.wheel] packages = ["src/agent_orchestrator"]` means `specs/` (and anything else outside the package dir) is NOT shipped in the built wheel. Any feature that needs bundled template/instruction content at runtime (not just at dev-time from a repo checkout) must bake that content as a Python string constant inside the package, never reference it via a repo-relative path — a `uv tool install`ed `ao` binary has no `specs/` directory alongside it once installed.
Context: `AgentMonitor` (E-XyfjuZ) needs to hand a monitor-agent subprocess an instruction file; a `specs/examples/instructions/*.md` path would silently 404 for any installed (non-editable) `ao`. Resolved by baking the instruction text as a module-level string constant in `monitoring.py`, written out fresh into the run's own `.orchestrator/runs/.../monitor/.../instruction.md` at consult time.
By: agent
Role: developer
Date: 2026-07-15
---

---
Learning-ID: LRN-20260715-phantom-failure-technique-for-breaker-boundary-tests
Learning: To engine-integration-test breaker logic that depends on accumulated failure counts (`task_failures`, `consecutive_failures`) via a REAL `orch.run()`, seed a "phantom" already-`"failed"` `TaskRunState` entry directly into the initial `RunState` under an id that is NOT one of the workflow's real tasks. `evaluate_breakers`'s count-based conditions scan ALL of `state.tasks.values()` regardless of whether an id belongs to the current `WorkflowSpec`, so the phantom count is picked up — while the REAL dispatched task can still cleanly succeed, letting the breaker trip at a boundary the run would otherwise sail past. This matters because a genuinely-dispatched task failure ends the whole run via a separate, unconditional `if ts.status not in ("succeeded","skipped"): failed=True; break` check regardless of any breaker's threshold, so a real multi-failure accumulation scenario can never be driven through the sequential engine loop directly (existing unit tests for these two conditions construct a synthetic `RunState` and call `Breaker.evaluate()` directly for exactly this reason — the phantom-entry technique extends that to a full `orch.run()` integration test).
Context: Used throughout `tests/test_monitoring_breaker_consult.py` (E-XyfjuZ Consult Point A) to prove the monitor-consult wiring fires at a real breaker-trip boundary without needing an actually-failing dispatch.
By: agent
Role: developer
Date: 2026-07-15
---

---
Learning-ID: LRN-20260715-headless-claude-p-background-tools-and-disallow-policy
Learning: `claude -p` (headless) runs the agent loop and exits the moment the model ends a turn with no pending tool calls — there is NO persistent session to observe a `run_in_background` shell finishing or to be re-invoked on its completion, so a background shell is torn down with the process (same process group) or orphaned unobserved, and its monitor tools (`BashOutput`, `KillShell`/`KillBash`) read nothing. "Start in background, check later" is therefore unreliable BY CONSTRUCTION in every `ClaudeCliExecutor` task; foreground `Bash` (blocks within the turn) is the correct pattern for anything that must complete. Tool policy for `claude -p` is provider-specific and lives in the EXECUTOR, not the core: `AgentSpec.disallowed_tools: list[str] = []` (default allow-all — web/TodoWrite/subagents stay ON) → executor appends `--disallowedTools <names>`, SKIPPED if the agent already set `--disallowedTools`/`--allowedTools`/`--tools` (either spelling / `=`-form) in command_template/extra_args (explicit flag wins). `--disallowedTools` is VARIADIC (`<tools...>`) so it MUST be injected BEFORE the `--output-format` stream flags or the parser consumes them as tool names; it matches tool names exactly and ignores unknowns. List BOTH `KillShell` AND `KillBash` — Claude Code v2 renamed KillBash→KillShell and v2.1.209 still ships both literals, so listing both is correct across versions and a harmless no-op. Adding the field also required updating `specs/agents.schema.json` (additionalProperties:false).
Context: prompt.md "Current Ask" second half; user directive "ALLOW ALL … provide flags to disable optionally" → allow-all default with opt-in per-agent disable (ADR-0005). `RECOMMENDED_HEADLESS_DISALLOWED_TOOLS = ("BashOutput","KillShell","KillBash")` names the background set for copy-paste opt-in.
By: agent
Role: developer
Date: 2026-07-15
---

---
Learning-ID: LRN-20260715-agents-schema-drift-max-turns-working-dir-missing
Learning: `specs/agents.schema.json` has `additionalProperties: false` on the agent object, so any field on `AgentSpec` it omits is silently rejected by `ao validate` (config.py `load_agents` → `_validate_against_schema`) even though the engine would run it — a latent drift bug. Discovered while adding `disallowed_tools`: `max_turns` and `working_dir` had been on `AgentSpec` but were MISSING from the schema. FIXED 2026-07-15: both added to the schema alongside `disallowed_tools`, with a regression test (`tests/test_config.py::TestLoadAgentsSchema`) asserting they validate AND that an unknown field still fails.
Context: The general rule (LRN: "Adding a field to AgentSpec also requires updating specs/agents.schema.json") was under-applied for max_turns/working_dir when they landed; grep the schema against `AgentSpec.__fields__` when touching either.
By: agent
Role: developer
Date: 2026-07-15
---

---
Learning-ID: LRN-20260715-verbatim-extraction-for-byte-identical-regression-gate
Learning: When refactoring a large control-flow method into a scheduler/split of responsibilities where a regression gate demands byte-identical behavior at the default setting, extract the original code VERBATIM via anchor-based, assert-verified string substitutions (each anchor checked to match exactly once before substitution) rather than hand-retyping from memory — then prove it by running the EXISTING test suite UNEDITED at the default setting, not by writing new assertions. A test suite passing with zero edits is a far stronger byte-identical proof than a hand-derived "looks equivalent" review.
Context: T-j8YLGd (`E-IasNXu-parallel-execution`) split `Orchestrator.run()`'s ~800-line inline per-task body into `_prepare_and_maybe_dispatch`/`_settle_completed_task` via `sed`-extracted line ranges + four sanctioned mechanical transforms, each anchor-verified pre/post; `tests/test_engine*.py` (58 tests) passed unedited at `max_parallel=1`, the epic's blocking AC-1 gate. Re-verified unedited again at T-VSfAUN and T-TNleFt handoff (0 deletions across the whole epic per `git diff --numstat tests/`).
By: agent
Role: developer
Date: 2026-07-15
---

---
Learning-ID: LRN-20260715-serialized-core-worker-dispatch-concurrency-pattern
Learning: To add opt-in concurrency to a stateful, single-writer engine loop without a rewrite: keep 100% of state mutation (RunState writes, `save()`, budget/breaker/router/injection decisions) on the main thread and push ONLY the pure-read, side-effect-free unit of work onto worker threads. Tasks that mutate shared topology/routing/loop bookkeeping must run as solo "barriers" — nothing else in flight when one starts, nothing new admitted until it settles — so their side effects stay atomic without a single lock anywhere in the engine.
Context: ADR-0007 D3/D4 (`E-IasNXu-parallel-execution`). `ThreadPoolExecutor(max_workers=max_parallel)` submits only `_run_with_retries` (task/workflow/agents/repo_paths/`state.run_id` in, `TaskResult` out — mutates nothing). `_is_barrier()` treats `emit_tasks`, loop-gate (incl. `__iter` clones), and router tasks as barriers, reusing existing `_loop_for_gate`/`_router_for_task` lookups rather than hand-rolled id matching. Zero locks were needed anywhere.
By: agent
Role: developer
Date: 2026-07-15
---

---
Learning-ID: LRN-20260715-blocked-drain-not-sleep-while-sibling-holds-capacity
Learning: When a resource gate (budget/rate window, quota, etc.) says "wait" under concurrency, never sleep inline while a sibling task already holds the capacity that might free the window — that is a guaranteed deadlock (the engine blocks itself from ever draining the very completion that would unblock it). Instead, return a distinct non-terminal "blocked" signal that stops filling the current wave and falls through to draining an in-flight completion (which reconciles actuals and may free the window); only sleep inline when nothing is in flight to drain (the `N=1` case, where this collapses to the original inline-sleep behavior — byte-identical by construction).
Context: T-VSfAUN FR-6 / ADR-0007 §7.1, closing `EPIC.md` risk R3. `_prepare_and_maybe_dispatch(..., in_flight_nonempty: bool)` branches exactly on this. `TestNoBudgetDeadlockOnRollingWindow` proves forward progress via the exact `run.log` event order (`gate_block` → no `wait` → `reconcile` → `gate_block` again → now `wait`) rather than relying on thread timing.
By: agent
Role: developer
Date: 2026-07-15
---

---
Learning-ID: LRN-20260715-int-flag-zero-falls-through-to-default-not-error
Learning: In an `x = cli_flag or env_var or config_value or DEFAULT` precedence chain, an explicit `0` from ANY source is indistinguishable from "unset" (falsy in Python) and silently falls through to the default — it never reaches a `< 1` validation guard. This is not a per-flag special case to fix; it is the established, consistent behavior of every int runtime setting in this codebase (`--max-attempts 0`, `--quota-max-wait 0`, and now `--max-parallel 0` all resolve to their default rather than erroring). Only a genuinely negative value survives the chain (negative ints are truthy) and hits the guard.
Context: T-JXiI9j (`E-IasNXu-parallel-execution`) flagged this against `TASK.md`'s own AC-2 prose, which had grouped "0 and negative" under "exits 1" — the ticket's own executable pseudocode already showed the `or`-chain treatment. T-TNleFt's CliRunner e2e tests confirmed the shipped behavior (`--max-parallel 0` exits 0/serial, `--max-parallel -1` exits 1) and the ticket wording was corrected to match rather than the code changed to match stale prose.
By: agent
Role: developer
Date: 2026-07-15
---

---
Learning-ID: LRN-20260715-verify-verbatim-extraction-via-static-diff
Learning: Independently verify a subagent's "verbatim extraction" refactor with a static diff, not only the passing suite: `sort -u` the normalized old (`git show HEAD:file`) and new source (strip whitespace + blank/comment lines), `comm -23` for lines only-in-old, and compare fixed-string call-counts per critical function old-vs-new. Any only-in-old business logic, or a per-function call-count that changed, that is NOT explained by a sanctioned mechanical transform / formatter reflow / context-object threading is a dropped-or-altered statement — potentially on a code path the existing tests never exercise.
Context: Validated T-j8YLGd's ~800-line settle-body extraction (E-IasNXu). "Existing suite passes unedited" only proves the tested paths; the line-set + call-count diff proves the rest (it surfaced one benign call-count delta that turned out to be an added comment, not a double-charge).
By: agent
Role: reviewer
Date: 2026-07-15
---

---
Learning-ID: LRN-20260715-default-grep-is-ugrep-use-fixed-string-for-parens
Learning: The default `grep` in this environment is ugrep, not GNU grep: `grep -E 'evaluate_breakers('` aborts with "mismatched ( )" because `-E` reads a literal `(` as a regex group-open. Use `grep -F` (fixed-string) — or backslash-escape the parens — when counting or matching literal function-call patterns; plain-word searches are unaffected.
Context: Cost a re-run mid-verification while comparing per-function call counts old-vs-new in engine.py (E-IasNXu); `grep -Fc 'name('` is the reliable form.
By: agent
Role: reviewer
Date: 2026-07-15
---

---
Learning-ID: LRN-20260715-commit-per-task-in-stacked-epics
Learning: When orchestrating a multi-task epic, commit (or at minimum record coverage numbers) after each validated task. If every task's changes stay uncommitted and stacked in the working tree, `git diff` can only show the whole epic vs pre-epic HEAD — you cannot isolate a single task's incremental diff, and you cannot recover the pre-epic coverage baseline for a quantitative delta without unstacking (stash or a throwaway worktree).
Context: All four implementation tasks of E-IasNXu were left stacked uncommitted; this blocked both per-task diff isolation and the exact pre→post coverage delta the prompt's checklist demanded (fell back to the measured post number + the +58/0-removed test delta).
By: agent
Role: manager
Date: 2026-07-15
---

---
Learning-ID: LRN-20260722-date-suite-keyed-artifact-name-overwrites-on-regen
Learning: An output artifact directory/file named only from (date + logical-key) — with no hash or version of the actual input SET that produced it — silently overwrites on any same-day regeneration with a different input set, even though nothing "wrong" happened at write time (write-temp + atomic rename can still make each individual write safe). If the artifact is meant to be a durable, git-committed comparison/report over a variable set of inputs (not a single deterministic re-run of the same inputs), the name must also encode the input-set identity (a hash of the sorted input ids, or a monotonic counter) or the tool must refuse to overwrite without an explicit flag.
Context: `ao-bench`'s `compute_compare_id` (`bench/results.py`) derives `<date>-<suite_id>-compare` from date+suite only. `benchmarks/results/2026-07-22-dev-core-compare/` was regenerated three times the same day as more real-subject runs (haiku→sonnet→opus) landed, each `ao-bench report` invocation silently overwriting the prior comparison.md/json in the working tree — the final committed file reflects the last (5-subject) run, not the original 3-subject `make bench-smoke` set. Per-run `run.json`/`summary.md` dirs are unaffected (subject-id-keyed, so each subject gets its own dir). Accepted as an MVP limitation (E-9Qk4Zt/T-Dcs2Rk), documented rather than fixed — a subject-set hash suffix is the natural fix if ever needed.
By: agent
Role: developer
Date: 2026-07-22
---

---
Learning-ID: LRN-20260722-uniform-model-workaround-for-global-model-clobber-defect
Learning: Given the known "global `--model`/`AO_MODEL` override silently clobbers every `AgentSpec.model`" defect (LRN-20260709-model-override-clobbers-agents, still live), a multi-agent workflow spec can stay entirely safe from it without waiting for the core fix: simply never set a per-agent `model` field. If no agent declares its own model, the global override has nothing to clobber — every agent already inherits the run-level model uniformly by design, so the defect's blast radius (silent per-agent downgrade) collapses to a no-op. This is a spec-authoring discipline, not a code fix, and is only safe when the workflow genuinely wants one uniform model across all its agents (which a benchmark "subject" always does — mixed-model subjects would confuse cost/capability attribution anyway).
Context: `benchmarks/subjects/ao-epic/agents.json` (E-9Qk4Zt/T-Fx6Dp0): neither the `developer` nor the `tester` agent sets `model`; the `ao_workflow` bench subject's own `model` field flows through `AO_MODEL` to both uniformly. Verified by a dedicated regression test (`test_ao_epic_subjects_pin_model_via_subject_not_per_agent`) asserting neither agent config carries a `model` key.
By: agent
Role: developer
Date: 2026-07-22
---

---
Learning-ID: LRN-20260722-directory-scan-attribution-needs-exactly-one-match-enforced
Learning: When attributing cost/usage/output to "the one thing that just ran" by scanning a directory for run artifacts after the fact (rather than capturing an explicit id at invocation time), the ONLY safe contract is "this directory must contain exactly one candidate" — enforced by raising a typed error on zero OR more than one match, never by silently taking the latest/first/sorted-last one. A silent pick is a latent correctness bug: it works fine until a stale artifact from a prior run lingers (partial cleanup, a retried invocation, a shared directory reused across calls), at which point it attributes cost to the wrong run without any visible symptom. The cheap, robust fix is architectural, not defensive code: guarantee the precondition by construction (a fresh, freshly-created directory per invocation) so the "exactly one" assumption is actually true, and still assert it rather than trusting it blindly.
Context: `bench/subjects.py`'s `AoWorkflowSubject` attributes cost by scanning `<workspace>/.orchestrator/runs/` for the `ao` run directory the subprocess just produced (there is no other channel back from a black-box `uv run ao run` subprocess). `_latest_run_dir` raises `SubjectError` on 0 or >1 candidates rather than picking one (ASSUMPTION A4 / Risk R2 in the design doc); a fresh, disposable workspace per (subject, task) makes "exactly one" true by construction, and the assertion catches it if that invariant is ever violated (e.g. a future subject reusing a workspace across tasks).
By: agent
Role: developer
Date: 2026-07-22
---

---
Learning-ID: LRN-20260722-parallel-dev-agents-strict-ownership-plus-arbitrated-shared-files
Learning: Multiple developer subagents can safely implement adjacent parts of one epic in parallel (not just sequentially) if each task's "files you own" list is an explicit, disjoint set, AND any file that multiple tasks must touch (a shared registry, a shared "start empty" test asserting a state only true before ANY of them land) is flagged up-front as orchestrator-arbitrated rather than owned by either task. When two concurrent tasks both need to mutate the same shared file (e.g. both register into the same registry), the correct pattern is: each task edits only its OWN new files, and treats a conflict in the shared file as a "flag for arbitration, do not silently fix" finding — the orchestrator (or a designated task) resolves it once, after seeing both sides, rather than either subagent guessing or one silently overwriting the other's edit.
Context: E-9Qk4Zt's `T-Sbj9Ka` (subjects) and `T-Grd7Vx` (graders) ran concurrently, both registering into shared registries; both independently flagged the same pre-existing `test_registries.py::test_registries_start_empty` (asserted an empty registry, false the moment either task's `register_*` calls landed) as a cross-task conflict rather than fixing it unilaterally. It was resolved once, by whichever agent's registration landed second, replacing the stale assertion with a membership check — verified by both tasks' independent test runs showing zero unexpected failures. No merge conflict or silently-clobbered edit resulted.
By: agent
Role: manager
Date: 2026-07-22
---

---
Learning-ID: LRN-20260722-trivial-benchmark-suite-shows-orchestration-cost-not-solve-rate-gain
Learning: On a benchmark suite trivial enough for a single bare LLM CLI call to already solve every task (6/6), an orchestrated multi-agent workflow (implement-agent → verify-agent) built on top of that same model produces the SAME solve rate but costs materially more — observed ~1.8-2.4x the bare-CLI cost and ~2.1-3.2x the wall-clock, scaling with model tier (larger/slower models amplify both the base cost AND the second agent's overhead). This is the expected, uninteresting result for an easy suite, not a signal that the orchestration adds no value: a verify/retry loop only pays for itself in solve-rate terms when the base model has non-trivial odds of getting it wrong on the first pass. Do not use a trivial/smoke suite's numbers as evidence for or against a multi-agent workflow's value — a suite needs genuine first-pass failure headroom (harder tasks, tighter constraints, ambiguous specs) before "solve rate" becomes a discriminating axis; cost/wall-clock overhead is visible even on an easy suite and is the correct axis to sanity-check there instead.
Context: E-9Qk4Zt real dev-core runs (2026-07-22, all 6/6): claude-haiku $0.3732 vs ao-epic-haiku $0.6617 (1.77x); claude-sonnet $1.2148 vs ao-epic-sonnet $2.8744 (2.37x); wall-clock ratios higher still (2.06x / 3.17x) because ao-epic's two sequential agent turns each pay their own claude-CLI startup/turn overhead. Documented in `docs-md/benchmarking-framework-hld.md` §11.1 and `benchmarks/README.md`; the epic's own scope note (EPIC.md) explicitly defers the real Sonnet/Opus/harder-suite Phase-2 comparison as a separate follow-up for exactly this reason.
By: agent
Role: developer
Date: 2026-07-22
---

---
Learning-ID: LRN-20260723-self-authored-tasks-saturate-under-generous-turn-budgets
Learning: A self-authored benchmark suite of genuinely longer/harder tasks (multi-file, 30–90 minutes of agent
work, misleading-symptom bugs, held-out grading) can still fail to discriminate between subjects if every subject
runs at a generous turn/investigation budget — modern agents (bare `claude -p` included) reliably solve
well-specified, single-repo tasks given enough turns, regardless of how "hard" the task design intends to be. This
was proven directly: `dev-medium`'s 6 tasks solved 6/6 by ALL of `claude-sonnet`/`claude-opus`/`ao-epic-sonnet`/
`ao-epic-plus-sonnet` at the `dev-core`-tuned `max_turns=30` default, with genuinely-correct (diff-verified, not
shallow/gamed) fixes. The same 6 tasks solved only 2/6 (33%) when `max_turns` was capped to 6 in an authoring-time
diagnostic probe. Two practical implications: (1) if the goal is a solve-RATE discrimination signal at a self-
authored suite's natural difficulty, the single-agent baselines need a MATERIALLY tighter turn/cost budget than
whatever a prior, easier suite was tuned for — otherwise the discrimination signal shows up only in
cost_per_solved/turns/wall-clock, not solve rate; (2) genuine capability discrimination (not just budget-starved
discrimination) needs an externally-credible, independently-harder suite — SWE-bench Verified real repo issues
discriminated for real (both bare models failed one real instance; the orchestrated workflow solved it) without
any turn-budget tightening at all.
Context: `E-Bt4Xk9-complex-benchmark-tiers` (`T-Md7Vc3` authoring gate + the PLAN medium/large campaigns,
2026-07-22/23). `dev-medium` campaign: all 4 subjects 6/6 at `max_turns=30` (`benchmarks/results/
2026-07-22-dev-medium-campaign/campaign.json`); scratchpad-only `max_turns=6` probe: 2/6 (documented in
`T-Md7Vc3`'s STATUS.md, no committed subject config touched). `swe-verified-mini` large-tier campaign: both
`claude-sonnet`/`claude-opus` failed `pytest-dev__pytest-10356` ("1-4 hours" labeled difficulty);
`ao-epic-sonnet` solved it (`benchmarks/results/2026-07-22-swe-verified-mini-*/run.json`).
By: agent
Role: developer
Date: 2026-07-23
---

---
Learning-ID: LRN-20260723-orchestration-overhead-shrinks-as-task-duration-grows
Learning: An orchestrated multi-agent workflow's cost overhead over a bare single-CLI-call baseline (same model)
shrinks as task duration/real-world difficulty grows, and can flip to a net WIN on the hardest tasks. Measured
across three tiers on the SAME model (sonnet) and the SAME 2-agent `ao-epic` workflow: trivial spot-fix tasks
(`dev-core`, 6/6 saturated) — `ao-epic-sonnet` $2.8744 vs bare `claude-sonnet` $1.2148, **+136.6% overhead**;
genuinely longer multi-file tasks (`dev-medium`, 6/6 saturated) — `ao-epic-sonnet` $4.2324 vs bare $1.9346,
**+118.8% overhead**; real SWE-bench Verified repo issues (`swe-verified-mini`, 9/10) — `ao-epic-sonnet` $8.1571
vs bare $7.5931, **+7.4% overhead only** — AND on the single hardest instance in that set
(`pytest-dev__pytest-10356`), `ao-epic-sonnet` solved what BOTH bare `claude-sonnet` and bare `claude-opus`
missed, at a cost 18% below bare opus. The mechanism: a fixed per-turn orchestration tax (an extra agent's own
CLI startup + turn overhead) is a large RELATIVE cost on a task the base model solves in one or two turns anyway,
but amortizes toward negligible as the task itself grows longer/harder — and on tasks hard enough that the base
model's first pass can genuinely fail, the second agent's verify/investigate capacity can turn that fixed tax into
a net solve-rate win instead of pure overhead. Do not judge an orchestration workflow's cost efficiency from a
trivial/saturated suite alone — the overhead ratio is itself a function of task difficulty, not a fixed property
of the workflow.
Context: `E-Bt4Xk9-complex-benchmark-tiers` PLAN Run 1 (`benchmarks/results/2026-07-22-dev-medium-campaign/
campaign.json`) + PLAN Run 2 (`benchmarks/results/2026-07-22-swe-verified-mini-campaign/campaign.json`), cross-
checked against `E-9Qk4Zt`'s earlier `dev-core` numbers (`benchmarks/results/2026-07-22-dev-core-*/run.json`,
already captured in `LRN-20260722-trivial-benchmark-suite-shows-orchestration-cost-not-solve-rate-gain`). Ratios
computed directly from the three committed `campaign.json`/`run.json` cost figures above.
By: agent
Role: developer
Date: 2026-07-23
---

---
Learning-ID: LRN-20260723-benchmark-ao-workflow-subjects-should-consider-max-attempts
Learning: A benchmark `ao_workflow` subject that runs with the engine's default `max_attempts=1` (no retry) is
vulnerable to a transient, non-capability harness abort costing a full solve-rate point on an otherwise-real
capability comparison — indistinguishable in the aggregate solve-rate number from a genuine miss unless someone
inspects the per-task record. Observed for real: `ao-epic-sonnet`'s one miss out of 10 large-tier instances
(`django__django-11138`) was a 4.5-second `missing_outputs` abort on the `implement` task (1 turn, empty patch) —
not a capability failure. A same-instance diagnostic re-run (uncommitted, scratch dir) solved it cleanly 1/1 at
$1.1492/252s. The engine already supports `max_attempts>1` retries; this is a benchmark-SUBJECT config choice
(`ao-epic-sonnet.json`'s own workflow/agents templates, or the `ao run --max-attempts` flag the subject threads
through), not an engine gap. The trade-off to weigh before defaulting every benchmark subject to `max_attempts>1`:
retries cost extra $/tokens and change what's being compared (a single-attempt bare-CLI baseline is not directly
cost-comparable to a multi-attempt orchestrated subject unless the baseline gets the same retry allowance) — so
this is a considered subject-authoring decision, not an unconditional "always set max_attempts>1" rule.
Context: `E-Bt4Xk9-complex-benchmark-tiers` PLAN Run 2 (large tier). `benchmarks/results/
2026-07-22-swe-verified-mini-ao-epic-sonnet/run.json`'s `django__django-11138` task record:
`subject_status="failed"`, `cost_usd=0.0933876`, `wall_clock_seconds=4.520933910011081`. Diagnostic re-run
(reported by the orchestrator, not committed to `benchmarks/results/`): solved 1/1, $1.1492, 252s.
By: agent
Role: developer
Date: 2026-07-23
---

---
Learning-ID: LRN-20260723-swebench-keep-images-env-var-saves-repull-time-across-subjects
Learning: A multi-subject campaign against the SAME SWE-bench-sourced suite re-pulls each instance's ~1 GB Docker
image once PER SUBJECT if per-instance cleanup (`docker rmi` + build-cache prune after every graded instance,
the disk-safety default) stays on — for a 10-instance/3-subject campaign, that is up to 2 avoidable re-pulls ×
~4 minutes × 10 instances (~80 minutes) of pure network/pull wall-clock. Setting `AO_BENCH_SWEBENCH_KEEP_IMAGES=1`
for the whole campaign (not per-run) keeps pulled images resident across all subjects instead, trading ~10 GB of
retained disk (bounded by instance count × ~1 GB/image) for that ~80 minutes saved — well within a ~37 GB disk
budget for a 10-instance suite, but a real per-suite-size tradeoff to recompute if N grows. Cleanup at the end is
then a manual step (`docker rmi`/`docker image prune`), not automatic per-instance.
Context: `E-Bt4Xk9-complex-benchmark-tiers` `T-Sg6Jf2` (grader) forward note, applied by `T-Cm9Tb4`'s
`make bench-large` recipe (`AO_BENCH_SWEBENCH_KEEP_IMAGES=1` exported for the whole invocation) and confirmed
practical by the real PLAN Run 2 large-tier campaign (`benchmarks/results/2026-07-22-swe-verified-mini-campaign/`,
~45.5 min wall-clock for all 3 subjects against 10 instances each — well under the un-cached-repull estimate).
By: agent
Role: developer
Date: 2026-07-23
---

---
Learning-ID: LRN-20260723-git-diff-misses-untracked-files
Learning: Stage everything (`git add -A`) before extracting a patch via `git diff HEAD` — untracked new files are silently omitted, corrupting downstream grading or patch application with no error.
Context: Reviewer C1 on SweBenchGrader: an agent fix that adds a new file would grade resolved=False silently.
By: agent
Role: agent
Date: 2026-07-23
---

---
Learning-ID: LRN-20260723-threading-locks-dont-serialize-processes
Learning: A threading.Lock serializes only within one process; disk/Docker safety guards for concurrently-invokable CLIs need OS file locks (fcntl.flock) or PID lockfiles.
Context: Reviewer W1: two parallel `ao-bench run` shells defeat the Docker-eval serialization the disk-safety design assumes.
By: agent
Role: agent
Date: 2026-07-23
---

---
Learning-ID: LRN-20260723-config-defaults-must-preserve-legacy-noflag-behavior
Learning: When wiring config-file-derived defaults into an existing CLI, preserve legacy no-flag semantics — resolving small-tier's $5 cap silently changed `ao-bench run` from unlimited to capped.
Context: Reviewer W2 on tier defaults; harmless at today's costs but would truncate a pricier rerun mid-suite.
By: agent
Role: agent
Date: 2026-07-23
---

---
Learning-ID: LRN-20260723-per-task-turn-budgets-multiply-workflow-allowance
Learning: max_turns applies per DAG task, so an N-agent workflow gets N× the total turn budget of a bare single-call subject — disclose the asymmetry and normalize comparisons on cost.
Context: Reviewer methodology note on ao-epic-plus; medium-tier data showed the extra budget bought nothing at saturation.
By: agent
Role: agent
Date: 2026-07-23
---

---
Learning-ID: LRN-20260723-session-limit-kills-subagents-midwrite
Learning: Claude session usage limits kill subagents mid-task with uncommitted multi-file work; diff-inspect the partial state before redoing — the work may be nearly complete and salvageable.
Context: T-Dc1Yg7 docs agent died at the session limit after substantially editing all five owned files.
By: agent
Role: agent
Date: 2026-07-23
---

---
Learning-ID: LRN-20260724-zombie-pid-defeats-liveness-probe
Learning: `os.kill(pid, 0)` succeeds for an exited-but-unreaped child, so PID-probe liveness reports finished subprocesses as alive forever; retain the `Popen` and `poll()` it to reap.
Context: Broke cancel and `is_running` in the dashboard's ProcessSupervisor; caught only by a test asserting a noop child stops being "running".
By: agent
Role: agent
Date: 2026-07-24
---

---
Learning-ID: LRN-20260724-spa-catchall-shadows-api-404
Learning: A FastAPI SPA fallback `@app.get("/{full_path:path}")` also matches unknown `/api/*` paths, returning index.html with 200 instead of a JSON 404; exclude the API prefix inside the handler.
Context: A typo'd or removed endpoint returned HTML, turning a clear client error into a JSON-parse hunt.
By: agent
Role: agent
Date: 2026-07-24
---

---
Learning-ID: LRN-20260724-general-instructions-additive-not-precedence
Learning: `general_instructions` merges as a UNION across config/env/CLI/workflow — deliberately NOT the usual CLI>env>config chain, because a precedence chain lets one `--general-instruction` silently drop the workspace's house rules.
Context: ADR-0010 D1. Qualifies LRN-20260710 three-layer precedence; do not "fix" this to match other settings.
By: agent
Role: agent
Date: 2026-07-24
---

---
Learning-ID: LRN-20260724-runstate-save-restamps-updated-at
Learning: `RunStateStore.save()` overwrites `state.updated_at` from its own clock, so a test fixture that sets `updated_at` has it silently discarded; inject `clock=lambda: pinned` for deterministic wall-clock assertions.
Context: Wall-time stat tests read "now minus fixture start" until the clock was pinned.
By: agent
Role: agent
Date: 2026-07-24
---

---
Learning-ID: LRN-20260724-reposet-file-key-is-repo-sets
Learning: The reposet FILE's top-level key is `repo_sets`, even though the CLI flag is `--reposets` and the config-file key is `reposets`; writing `reposets` in the file fails with "Additional properties are not allowed".
Context: Cost a full test-suite round-trip when hand-writing fixture specs.
By: agent
Role: agent
Date: 2026-07-24
---

---
Learning-ID: LRN-20260724-vitest-config-must-be-separate-file
Learning: Under Vite 8 / Vitest 4 the `test` key is no longer part of Vite's config type, so a `test:` block in vite.config.ts fails `tsc -b`; put it in a separate vitest.config.ts via `mergeConfig`.
Context: ui/ frontend typecheck failed on an otherwise-standard colocated config.
By: agent
Role: agent
Date: 2026-07-24
---

---
Learning-ID: LRN-20260724-fake-executor-clobbers-declared-outputs
Learning: `FakeExecutor` unconditionally writes stub content to every DECLARED output path, so pre-seeding a router's `route-verdict.json` (a declared output of `classify`) cannot survive a fake run — routed workflows can never COMPLETE under fake executors. The finplan "pre-seed" dry-run technique works only for `task_manifest_path` files, which are not declared outputs. Cover run-to-completion e2e with a non-routed fixture template instead, and assert dispatch (not completion) for routed DAGs.
Context: Discovered while writing the builtin routed-runner e2e for E-Tpl3x9; an agent initially assumed the pre-seed would work based on the finplan README.
By: agent
Role: tester
Date: 2026-07-24
---

---
Learning-ID: LRN-20260724-template-render-needs-json-escaping-and-real-schema
Learning: Any `{{ var }}`-substitution into a JSON spec must JSON-escape values (chars only, keep bare numerics raw) AND validate the rendered result against the real WorkflowSpec loader — shallow "has id/tasks" checks let structural injection through. Same lesson for reads: `Path(dir) / rel` silently discards `dir` when `rel` is absolute, so template `source:` fields need absolute-path rejection plus resolve()+is_relative_to containment.
Context: Two execution-confirmed review BLOCKERs (B1/B2) in the E-Tpl3x9 templates module, fixed same-day; see meta/tickets/E-Tpl3x9-workflow-templates/T-Te5rev-e2e-review/REVIEW.md.
By: agent
Role: reviewer
Date: 2026-07-24
---

---
Learning-ID: LRN-20260724-install-sh-missed-ui-extra
Learning: `install.sh`'s `uv tool install "$SCRIPT_DIR"` omitted the optional `[ui]` extra (fastapi/uvicorn/httpx), so the globally installed snapshot had a fully working `ao ui` *command* that raised at runtime asking for the extra it was never given. `uv tool install` has no `--extra` flag; the extra must be requested via package-spec brackets: `uv tool install "$SCRIPT_DIR[ui]"`. Fixed in install.sh.
Context: Found while installing the ad/workflow-templates snapshot globally and verifying `ao ui` actually serves for a consumer workspace (ao-runner-finplan) — the pre-existing 8765/8766 dashboards on this host were both running from the local dev venv (`uv run`, editable), never from the global uv-tool snapshot, which is why the gap went unnoticed.
By: agent
Role: developer
Date: 2026-07-24
---

---
Learning-ID: LRN-20260730-sanitizer-blocklists-must-enumerate-declarative-animation
Learning: An HTML/SVG sanitizer's element/attribute blocklist must separately enumerate DECLARATIVE ANIMATION (SVG SMIL: `<animate>`, `<set>`, `<animateTransform>`, `<animateMotion>`, `<discard>`) as its own URL-bearing construct class — it is not covered by stripping `href`/`src`-shaped attributes, because `<animate attributeName="href" values="http://attacker.example/...">` retargets an attribute to an attacker-chosen value AFTER those attributes have already been sanitized/stripped, live, on a timer, with no script execution and no user interaction beyond rendering. It also survives a scripting sandbox: SMIL animation is a declarative browser engine feature, not JavaScript, so `sandbox=""` (no `allow-scripts`) does not disable it. Confirmed in real headless Chrome: a synthetic click on an `<a>` whose `href` had been live-retargeted by a sibling `<animate>` made the (sandboxed, srcdoc) iframe navigate to the attacker URL — a genuine one-click network-egress/redirect primitive, not merely a theoretical gap. This also invalidates the common sanitizer-author assumption "a sandboxed iframe can't navigate anywhere, so a live href is harmless dead UI" — a sandboxed iframe without `allow-top-navigation` cannot navigate OTHER frames, but can always navigate ITSELF.
Context: Execution-confirmed by a dedicated adversarial security audit of the `agent_orchestrator/ui/htmlpreview.py` sanitizer (E-Tpl3x9 follow-on dashboard file-preview work, H1 finding) and independently reproduced by the coordinator via the live `/api/files/html` endpoint before the fix landed. Fixed by adding the SMIL element names to `DROPPED_ELEMENTS_WITH_SUBTREE` outright — there is no legitimate use for live attribute retargeting in a static preview, so this is a zero-functionality-tradeoff closure, not a mitigation.
By: agent
Role: developer
Date: 2026-07-30
---

---
Learning-ID: LRN-20260831-launch-records-unequal-history
Learning: `LaunchRecord`s are an append-only history of unequal quality — failed attempts write records too, and `reconcile()` returns newest-first. Recover a run's launch parameters by reducing across all its records, never from "the latest".
Context: Boot-resume read the newest record — the information-free one its own failures had written.
By: agent
Role: agent
Date: 2026-08-31
---

---
Learning-ID: LRN-20260831-fix-must-survive-its-own-wreckage
Learning: When a defect persists artifacts on failure, verify the fix against already-damaged production state, not just clean fixtures — prior failures can poison the very data the fix reads.
Context: A correct-looking boot-resume fix would have silently failed on the one machine it was written to repair.
By: agent
Role: agent
Date: 2026-08-31
---

---
Learning-ID: LRN-20260831-ui-unreachable-two-distinct-gates
Learning: "UI unreachable" has two independent gates: the socket bind and `resolve_allowed_hosts`. Binding `0.0.0.0` allowlists that literal, which browsers never send as Host — LAN clients get 421. A TCP refusal means the bind is loopback instead.
Context: Dashboards 421'd on the LAN; the hub refused outright, its bind hardcoded.
By: agent
Role: agent
Date: 2026-08-31
---

---
Learning-ID: LRN-20260831-reconcile-stamps-dead-pids-finished
Learning: `ProcessSupervisor.reconcile()` stamps `finished_at` on any record whose PID is dead, so a test needing a "still running" launch must use a genuinely live PID (`os.getpid()`), not a reaped one.
Context: A liveness-suppression test passed a dead PID and silently asserted the wrong branch.
By: agent
Role: agent
Date: 2026-08-31
---

---
Learning-ID: LRN-20260906-parallel-architects-reserve-shared-ids
Learning: When running two architect subagents concurrently, pre-assign every shared sequential identifier (ADR numbers) and fence shared files (ROADMAP, learnings, CLAUDE.md) out of both briefs; the orchestrator applies the cross-epic edits afterwards, including any interaction the two designs did not see (here: per-run checkout fast-forward vs. scheduler overlap policy).
Context: E-Wk9Tz3 (isolation) and E-Sc9Rt4 (scheduler) were designed in parallel; both would otherwise have claimed ADR-0013 and edited ROADMAP §3 concurrently.
By: agent
Role: architect
Date: 2026-09-06
---
