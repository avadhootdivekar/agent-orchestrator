# HLD — Task Lifecycle Hooks (Epic A)

- Epic: `E-AMSSHX-task-lifecycle-hooks`
- Status: Draft → In Review
- Author: `dev-epic` agent, 2026-09-21
- Related: `docs-md/hld-agent-orchestrator.md`, `docs-md/lld-agent-orchestrator.md`,
  ADR-0003 (settings precedence), ADR-0007 (parallel execution), `E-Wk9Tz3` (task isolation),
  `E-XyfjuZ` (monitoring/self-healing), `src/agent_orchestrator/bench/graders.py`

## 1. Scope

Engine/DAG-level **pre/post hooks per task**: a `pre_hook` / `post_hook` field on `TaskSpec`,
config-driven (JSON/YAML), executed by the orchestrator engine itself immediately before a task
dispatch begins and immediately after it concludes. This is explicitly **not** Claude Code's own
session hooks (PreToolUse/PostToolUse, `settings.json`) — it is orchestrator-engine functionality,
analogous to `IntegrationSpec.verify_command` / `RegenerateRule.command`.

Non-goals (this epic): a workflow-level default hook applied to every task; hook-level retry
policy independent of the task's own `RetryPolicy`; token/cost accounting for a hook's own
execution (hooks are plain subprocesses, not LLM calls); a CLI/env-level global hook toggle.
These are called out below as Non-MVP/deferred, not silently dropped.

## 2. Why this shape (read the current engine first)

`Orchestrator._run_with_retries` (engine.py) is the ONE place a task's agent executor is actually
invoked, on a worker thread (ADR-0007 D3), once per **dispatch cycle** (`TaskRunState.
dispatch_cycle`, R-21) — a task is redispatched through a *fresh* call to this function on:
self-heal retry (Consult Point B), a T2/T3 conflict-ladder resolve/rerun (`E-Wk9Tz3`), and a
quota/429 requeue. Every task kind (ordinary, router, loop-gate, emit_tasks) dispatches through
this same function with the same shape. `_run_with_retries` performs **no `RunState` mutation**
(NFR-3) — it is a pure function of its arguments that returns a `TaskResult`; the caller
(`_run_and_integrate`, still worker-thread) then gates isolation/`integrate()` on
`result.status == "succeeded"`, and `_settle_completed_task` (main thread) is the only place that
mutates `RunState`.

**Design decision D1 — hooks live entirely inside `_run_with_retries`.** A `pre_hook` runs once,
before the attempt loop; a `post_hook` runs once, after the attempt loop produces a *terminal*
result. Both build and return an ordinary-shaped `TaskResult` (see §4) with zero new call sites in
`_run_and_integrate`, `_settle_completed_task`, isolation, budget, retries, breakers, or
self-healing. This is what keeps the change narrow (CLAUDE.md's "smallest correct change" /
parallel-epics change-scope policy): every one of those subsystems already only look at
`TaskResult.status` / `TaskRunState`, so a hook that flips `status` to `"failed"` is
indistinguishable, to everything downstream, from an ordinary agent failure — self-heal
eligibility, budget reconcile, missing-outputs gate skip, and integration-skip-on-failure all
apply automatically, with no special-casing.

**Design decision D2 — hooks are argv commands, not Python callables, declared in the spec.**
This matches the *existing* pattern for `IntegrationSpec.verify_command` and
`ResolverConfig.regenerate[].command`: `list[str]`, never a shell string, spawned via
`subprocess.run(argv, shell=False, ...)`, bounded by a `timeout_seconds`. It is the one shape
consistent with the repo's "Declarative, structured specs... payloads referenced by path, never
inlined into the engine" design principle (CLAUDE.md) — a hook is orchestration metadata, not
engine-internal code. (Python-level extension points like `resolver_hook`/`escalation_hook`/
`Monitor` *do* exist in this codebase, but those are constructor-injected engine extensibility
points for a caller embedding `Orchestrator` in-process — not something a workflow *spec* can
declare. A task-spec-level hook has to be a command, the same way `verify_command` is.)

**Design decision D3 — verdict is the process exit code; a JSON result file is optional,
additive detail, never an override.** Exit `0` = passed, non-zero = failed (mirrors
`bench/graders.py`'s `CommandGrader`: `solved = (rc == 0)`, an intentional reuse of an idiom this
codebase already has). If the hook additionally writes a small JSON object to the path handed to
it via `AO_HOOK_RESULT_PATH`, the engine reads it back (bounded, via the *existing*
`artifacts.read_control` — the audited NFR-1 exception for machine-written control files, shared
with the loop gate/router/verdict-breaker readers) and stores it verbatim as `HookOutcome.detail`.
The JSON file is pure information capture; it can never change `passed`/`failed` — this keeps the
contract unambiguous (no "which one wins" question) and, critically, is exactly the shape
`bench/graders.py`'s `Grader.grade() -> GradeResult(solved, score, detail, raw_tail)` already
produces, so a future grading post-hook script only has to do
`sys.exit(0 if result.solved else 1)` plus write `{"solved":..., "score":..., "detail":...}` —
see §7.

## 3. Spec surface

```jsonc
// TaskSpec (models.py) — both optional, default None (opt-in only)
{
  "id": "build",
  "agent": "claude",
  "instruction": "instructions/build.md",
  "pre_hook": {
    "command": ["python3", "scripts/hooks/check_disk_space.py"],
    "timeout_seconds": 60,          // default DEFAULT_HOOK_TIMEOUT_SECONDS = 120
    "on_failure": "fail_task"       // default for pre_hook when omitted
  },
  "post_hook": {
    "command": ["python3", "scripts/hooks/grade.py"],
    "timeout_seconds": 120,
    "on_failure": "ignore"          // default for post_hook when omitted
  }
}
```

`HookSpec` (new, `models.py`):

```python
DEFAULT_HOOK_TIMEOUT_SECONDS: int = 120  # matches DEFAULT_REGENERATE_TIMEOUT_SECONDS / bench's
                                          # _DEFAULT_GRADER_TIMEOUT_SECONDS — one bounded-subprocess
                                          # convention, reused, not re-invented.
HookOnFailure = Literal["ignore", "fail_task"]
DEFAULT_PRE_HOOK_ON_FAILURE: HookOnFailure = "fail_task"   # safe-by-default: a broken
                                                            # precondition blocks dispatch.
DEFAULT_POST_HOOK_ON_FAILURE: HookOnFailure = "ignore"     # safe-by-default: observational
                                                            # unless a workflow author opts in
                                                            # (e.g. Epic B's grading hook).

class HookSpec(BaseModel):
    command: list[str] = Field(min_length=1)
    timeout_seconds: int = Field(default=DEFAULT_HOOK_TIMEOUT_SECONDS, ge=1)
    on_failure: HookOnFailure | None = None   # None -> resolve_hook_on_failure() fills in the
                                               # kind-specific default (D-4 below); never
                                               # duplicated as two separate model classes.

def resolve_hook_on_failure(hook: HookSpec, kind: Literal["pre_hook", "post_hook"]) -> HookOnFailure:
    """The ONE place a hook's on_failure default is resolved — mirrors resolve_task_isolation /
    resolve_effective_agent's own 'one place' convention. An explicit hook.on_failure always wins;
    otherwise pre_hook defaults to 'fail_task', post_hook to 'ignore'."""
```

`HookSpec` is intentionally `pre_hook`/`post_hook`-shape-agnostic (same class for both) — only the
*default* differs, resolved centrally, so there is exactly one model to validate/document, and a
task that wants symmetric behavior can just set `on_failure` explicitly on either.

`TaskSpec.pre_hook: HookSpec | None = None`, `TaskSpec.post_hook: HookSpec | None = None`.
Workflow-level default hooks (applied to every task, mirroring `general_instructions`) are
**Non-MVP** — noted in §9.

## 4. Result shape

```python
HookStatus = Literal["passed", "failed", "error", "timed_out"]

class HookOutcome(BaseModel):
    kind: Literal["pre_hook", "post_hook"]
    status: HookStatus
    exit_code: int | None = None
    duration_ms: int | None = None
    detail: dict = {}       # verbatim JSON the hook wrote to AO_HOOK_RESULT_PATH, if any —
                             # informational only (D3); {} when no result file was written/valid.
    error: str | None = None  # engine-side note: timeout, spawn failure, malformed result file.
```

`TaskResult` gains `pre_hook_result: HookOutcome | None = None`,
`post_hook_result: HookOutcome | None = None` (both `None` for a task with no declared hooks —
byte-identical serialization for every pre-epic run). `TaskRunState` mirrors both fields the same
way `output_artifact_path` is already mirrored from `TaskResult` in `_settle_completed_task`
(engine.py ~line 1693-1694) — a two-line, additive change at that exact site.

## 5. Execution mechanics

**Capture layout** — sibling directories to the existing `attempt-<n>/` capture dirs, under the
same per-cycle output_dir (`.orchestrator/runs/<run_id>/<task_id>/[cycle-<n>/]`):
`pre_hook/{context.json, result.json?, stdout.txt, stderr.txt}` and `post_hook/{...}` the same
shape. This reuses the exact directory-naming convention `_run_with_retries` already establishes
for `attempt-<n>/` (R-21), so nothing new is invented for "where does this live."

**`context.json`** (engine-written, paths only — NFR-1): `run_id`, `task_id`, `hook_kind`,
`dispatch_cycle`, `instruction_path`, `general_instruction_paths`, `input_paths`, `output_paths`,
`dynamic_input_paths`, `repo_paths`, `output_dir` (this cycle's capture root); `post_hook` adds
`status`, `attempts`, `exit_code`, `error`, `output_artifact_path` (the agent's own attempt
capture dir) from the just-produced agent `TaskResult`. Never file *contents* — same NFR-1
boundary the rest of the engine already enforces.

**Subprocess env**: inherits `os.environ` merged with the SAME `env_overlay` the agent executor
received (`ctx.env` — isolation's `AO_ISOLATION`/`AO_TASK_BRANCH`/etc. when the task is isolated),
plus `AO_RUN_ID`, `AO_TASK_ID`, `AO_HOOK_KIND`, `AO_DISPATCH_CYCLE`, `AO_HOOK_CONTEXT_PATH`,
`AO_HOOK_RESULT_PATH`. `cwd` = the same resolved agent working directory. This means a hook on an
isolated task transparently sees that task's own worktree — no extra isolation-aware code, it's
the same values the caller already resolved for the executor.

**`_run_hook(...)` never raises** (mirrors `bench/graders.py::_run_command`'s "never raises"
contract, deliberately reused): a timeout, missing binary, or malformed `result.json` all degrade
to a `HookOutcome` with `status` set from the best information available (`"timed_out"`,
`"error"`) and `error` naming the cause — never an uncaught exception reaching the worker thread.

**True no-op path (locked-in requirement)**: `_run_with_retries` calls into hook dispatch through
two guarded sites — the pre-hook check is `if task.pre_hook is not None: ...` before the attempt
loop, and the post-hook path is centralized in one small helper
(`_finalize_with_post_hook(task, result, ...)`) whose *first statement* is
`if task.post_hook is None: return result`. When neither field is set, **zero** hook-related work
happens: no env dict is built, no `context.json` is written, no directory is created, no
`subprocess` call is made. This is provable, not just "cheap": §8 adds a unit test that patches
`Orchestrator._run_hook` with a `Mock` and asserts `call_count == 0` for a task with no hooks
declared, across succeeded/failed/timed_out outcomes — not merely a fast exit-code check, an
actually-skipped code path.

## 6. Failure semantics (explicit decisions)

| Question | Decision | Rationale |
|---|---|---|
| Pre-hook fails (non-zero exit/timeout), `on_failure="fail_task"` (default) | Task dispatch aborts. Returns `TaskResult(status="failed", attempts=0, ...)` **before the executor is ever invoked** — zero agent spend. | Safe-by-default: a broken precondition should block, not silently proceed to spend agent budget. |
| Pre-hook fails, `on_failure="ignore"` | Logged + recorded in `pre_hook_result`; the attempt loop runs normally. | Opt-in escape hatch for a purely advisory/notification pre-hook. |
| Post-hook fails, `on_failure="ignore"` (default) | Logged + recorded in `post_hook_result`; the agent's own `TaskResult.status` is **never** overwritten. | Grading/observability must not silently start failing pipelines the day it's turned on; failing must be an explicit opt-in (this is exactly what lets Epic B's grader ship as observe-only first). |
| Post-hook fails, `on_failure="fail_task"` | If the agent's own result was `"succeeded"`, it is downgraded to `"failed"` (error names `post_hook_failed`). An already-`"failed"`/`"timed_out"` result is left as-is (a hook can never *upgrade* status, and there is no second failure to report over the first). | Post-hook can only ever make a result *stricter*, never *laxer* — no path lets a hook turn a genuine agent failure into a reported success. |
| Are hooks retried under the task's own `RetryPolicy`? | **No.** A hook fires exactly once per **dispatch cycle** (not once per attempt). If a hook needs its own retry, that is the hook script's own responsibility (a hook is a single argv command, not a retry loop). | Hooks doing side-effecting prep (a pre-hook provisioning something) must not silently re-run N times for N agent retries — that would break idempotency (CLAUDE.md design principle). `RetryPolicy.max_attempts` governs the AGENT's attempts, which is a distinct axis. |
| Are hooks retried on self-heal / T2-T3 conflict-ladder / quota requeue? | Implicitly yes, once per new dispatch cycle — each of those triggers a **fresh call** to `_run_with_retries` (a new `dispatch_cycle`), which reruns pre/post hooks like any other part of that dispatch. No extra code: this falls out of D1 for free. | Consistent with the existing "each dispatch cycle is independent" model (R-21) — a hook re-observing a re-dispatched task is the same behavior a re-dispatched *agent* already gets. |
| Does a `fail_task` post-hook feed self-heal (Consult Point B)? | Yes, automatically — self-heal keys off `result.status == "failed"`, and a hook-downgraded result has exactly that status. No special-casing added. | Consistent, not a gap: an operator who wants a graded failure to be heal-eligible gets it for free; one who doesn't can leave `self_heal_enabled=False` (already the default) or set the hook's own `on_failure="ignore"`. |
| Cancelled run (`cancel_fn()` true) | Post-hook **does not** fire. | Nothing meaningful completed; there's nothing to grade/observe, and running a hook against a cancelled task risks doing work during a shutdown that should be as fast as possible. |
| Quota-exhaustion early return (`claude_quota_exhausted`) | Post-hook **does not** fire. | This is an outer-loop "not done yet, will be retried" signal, not a terminal dispatch outcome — firing a hook here would misreport a task that hasn't actually finished. |
| Missing declared `task.outputs` (R-2 gate) | Post-hook fires **before** that gate (it lives inside `_run_with_retries`, which returns before `_run_and_integrate`'s missing-outputs check runs). | Keeps hook dispatch independent of isolation/integration internals (D1). A grading hook that needs outputs to exist can check for them itself via the paths in `context.json` and treat a missing path as a failed grade — self-correcting, no engine coupling required. |
| Budget/token accounting for the hook's own execution | None in this epic — a hook is a plain subprocess, not counted against `BudgetSpec`/actuals. | Out of scope; if a future hook itself calls an LLM, that is a Non-MVP follow-on (§9). |

## 7. Forward-compatibility for Epic B (grading post-hook)

Epic B needs a post-hook that runs an accuracy/success check against a completed task's output
and records pass/fail, generalizing `bench/graders.py`'s `Grader.grade(cfg, ctx) -> GradeResult
(solved, score, detail, raw_tail)`. Under this design, **no change to the core hook-dispatch
mechanism is required** — Epic B only needs to write a small CLI wrapper script that:

1. Reads `AO_HOOK_CONTEXT_PATH` (JSON: task id, output paths, agent status, etc. — exactly the
   "task spec, run/task state, produced artifact paths, exit/success status" the brief asked for).
2. Constructs (or reuses) a `GraderContext`-shaped object (`repo_dir` etc.) and calls an existing
   or adapted `Grader.grade(...)`.
3. Writes `{"solved": result.solved, "score": result.score, "detail": result.detail,
   "raw_tail": result.raw_tail}` to `AO_HOOK_RESULT_PATH`.
4. `sys.exit(0 if result.solved else 1)`.
5. The workflow spec declares `"post_hook": {"command": ["python3",
   "scripts/hooks/grade.py"], "on_failure": "ignore"}` (observe-only) or `"fail_task"` (gate on
   grading).

The engine records the verbatim `detail` on `HookOutcome.detail` / `TaskRunState.post_hook_result`
— fully queryable from `RunState` for Epic B's outcome-metrics work, with zero change to
`engine.py`'s hook-dispatch code path. This is the deliberate design intent behind D2/D3 above:
by making the hook boundary "argv command + exit code + optional JSON detail," ANY future
post-hook — grading or otherwise — plugs in as a spec change and a script, never a core-engine
change. **This section is the forward-compat note the brief asked to be explicit about — Epic B's
implementer should start here, not re-derive the hook contract.**

## 8. Config precedence

Hooks are **spec-only** — declared per task (or, non-MVP, per workflow) in the JSON/YAML spec —
and deliberately **not** part of the existing CLI > env var > `.ao/config.yaml` three-layer
precedence chain (ADR-0003) that governs *invocation-scoped runtime settings* like `max_parallel`,
`self_heal`, `isolation_strict`, quota timing. Rationale:

- Every existing "run a command as part of task/workflow execution" surface in this codebase
  (`IntegrationSpec.verify_command`, `ResolverConfig.regenerate[].command`,
  `CircuitBreakerSpec`) is spec-only today; none of them ride the CLI/env/config chain. A hook
  command is declarative *workflow behavior*, not an operator runtime knob — it belongs with its
  peers.
- The precedent for a genuinely orthogonal "operator override, not a workspace setting" concept
  already exists and is explicitly documented as *not* using the config-file layer:
  `cli._resolve_no_isolation`'s docstring: `"deliberately NO config-file layer... this is an
  emergency kill switch, not a workspace-wide setting."` An analogous `--no-hooks` /
  `AO_DISABLE_HOOKS` emergency kill switch is a reasonable future addition on the same model, but
  is **Non-MVP / deferred** here — it is not required for the hook mechanism to function
  correctly, and adding it now would touch `cli.py`, which is outside this epic's narrow change
  boundary (see change-scope table below).

## 9. Non-MVP / deferred (explicitly recorded, not silently dropped)

- Workflow-level default `pre_hook`/`post_hook` applied to every task (mirrors
  `general_instructions`). Validation approach if added later: same fill-in pattern as
  `resolve_effective_agent`.
- `--no-hooks` / `AO_DISABLE_HOOKS` emergency kill switch (mirrors `--no-isolation`).
- Hook-level retry policy independent of `RetryPolicy`.
- Token/cost accounting for a hook that itself invokes an LLM.
- Dashboard/UI surfacing of `pre_hook_result`/`post_hook_result` (the fields are persisted in
  `RunState` today, same as every other per-task field the dashboard doesn't yet render).

## 10. Change-scope boundary (parallel-epics policy)

| File | Change | Boundary |
|---|---|---|
| `src/agent_orchestrator/models.py` | Add `HookSpec`, `HookOutcome`, `HookStatus`, `HookOnFailure`, `DEFAULT_HOOK_TIMEOUT_SECONDS`, `DEFAULT_PRE_HOOK_ON_FAILURE`, `DEFAULT_POST_HOOK_ON_FAILURE`, `resolve_hook_on_failure()`; add `pre_hook`/`post_hook` fields to `TaskSpec`; add `pre_hook_result`/`post_hook_result` to `TaskResult` and `TaskRunState`. | Additive only — no existing field/behavior changed. |
| `src/agent_orchestrator/engine.py` | New private helper(s) `_run_hook` / `_finalize_with_post_hook` near `_run_with_retries`; two call sites inside `_run_with_retries` (pre-hook gate before the attempt loop; post-hook wrap on the `"succeeded"` early return and the final exhausted-attempts return only — cancelled/quota-exhausted returns untouched); two-line mirror in `_settle_completed_task` next to the existing `output_artifact_path` mirror. | Must not touch scheduling, budget, breakers, monitoring, isolation/integration call sites themselves — hooks consume their existing outputs, never restructure them. |
| `src/agent_orchestrator/spec.py` | pydantic-level validation already covered by `HookSpec.command: Field(min_length=1)`; add a `cross_validate` note only if a genuine cross-task rule is needed (none identified for MVP). | No change to isolation/routing/loop cross-validation logic. |
| `specs/workflow.schema.json` | Add `$defs/hook`; reference as `pre_hook`/`post_hook` on the task def. | Additive only. |
| `specs/examples/` | One new example workflow + a tiny hook script demonstrating pre/post hooks end-to-end with `FakeExecutor`-compatible agents. | New files only. |
| `tests/` | New test file(s) for hook dispatch (no-op, pre/post pass/fail, on_failure policies, timeout, malformed result file) + one e2e test exercising the example spec via the CLI. | New files only; must not weaken/skip any existing test. |
| `docs-md/` | This document. | — |

Out of scope for this epic (touched by neither models.py's "frozen for other tickets" regions nor
this table): `cli.py`, `project_config.py`, `scheduler.py`, `breakers.py`, `monitoring.py`,
`isolation/*`, `bench/*` (Epic B's own future territory).

## 11. Early-gate review

Requested from `reviewer` + `architect` before implementation begins — see
`meta/tickets/E-AMSSHX-task-lifecycle-hooks/STATUS.md` for the recorded outcome and any changes
made in response.
