# HLD — Task Lifecycle Hooks (Epic A)

- Epic: `E-AMSSHX-task-lifecycle-hooks`
- Status: **Reviewed (Rev 2)** — incorporates early-gate `reviewer` + `architect` findings, see §12.
- Author: `dev-epic` agent, 2026-09-21 (Rev 1); revised 2026-09-21 (Rev 2, post early-gate)
- Related: `docs-md/hld-agent-orchestrator.md`, `docs-md/lld-agent-orchestrator.md`,
  `docs-md/task-isolation-hld.md` (AC-15 argv-containment invariant — see §3),
  ADR-0003 (settings precedence), ADR-0007 (parallel execution), `E-Wk9Tz3` (task isolation),
  `E-XyfjuZ` (monitoring/self-healing), `src/agent_orchestrator/bench/graders.py`

## 1. Scope

Engine/DAG-level **pre/post hooks per task**: an operator declares named hook commands at
workflow root; a task references one by name for its `pre_hook`/`post_hook`. The engine runs the
referenced command immediately before a task dispatch begins and immediately after it concludes.
This is explicitly **not** Claude Code's own session hooks (PreToolUse/PostToolUse,
`settings.json`) — it is orchestrator-engine functionality, analogous to
`IntegrationSpec.verify_command` / `RegenerateRule.command`.

Non-goals (this epic, see §9 for the full Non-MVP/deferred list): a workflow-level default hook
applied to every task without an explicit per-task reference; hook-level retry policy independent
of the task's own `RetryPolicy`; token/cost accounting for a hook's own execution (hooks are
**explicitly non-LLM, non-billable** subprocesses — see §7); a CLI/env-level global hook toggle;
an `on_failure` value that skips a task outright (use routers/`branches` for conditional
execution); distinguishing "hook declared but this dispatch never reached it" from "no hook
declared" in `RunState` (both currently read as `None` — a documented limitation, not silently
dropped).

## 2. Why this shape (read the current engine first)

`Orchestrator._run_with_retries` (engine.py) is the ONE place a task's agent executor is actually
invoked, on a worker thread (ADR-0007 D3), once per **dispatch cycle** (`TaskRunState.
dispatch_cycle`, R-21) — a task is redispatched through a *fresh* call to this function on:
self-heal retry (Consult Point B), a T2/T3 conflict-ladder resolve/rerun (`E-Wk9Tz3`), and a
quota/429 requeue. `_run_with_retries` performs **no `RunState` mutation** (NFR-3) — it is a pure
function of its arguments that returns a `TaskResult`; the caller (`_run_and_integrate`, still
worker-thread) then gates isolation/`integrate()` on `result.status == "succeeded"`, and
`_settle_completed_task` (main thread) is the only place that mutates `RunState`.

**Design decision D1 — hooks live entirely inside `_run_with_retries`, with one narrow, explicit
exception (§6).** A `pre_hook` runs once, before the attempt loop; a `post_hook` runs once, after
the attempt loop produces a *terminal* result. Both build and return an ordinary-shaped
`TaskResult` with zero new call sites in `_settle_completed_task`'s mutation logic (beyond a
2-line mirror), isolation, budget, retries, breakers, or self-healing themselves — those all
continue to read only `TaskResult.status`. The one exception, found in early-gate review, is the
T2 conflict-resolver dispatch (§6) — a task's declared hooks must be explicitly suppressed there,
not silently inherited.

**Design decision D2 — hooks are argv commands, not Python callables.** This matches
`IntegrationSpec.verify_command` / `ResolverConfig.regenerate[].command` and the repo's
"Declarative, structured specs... payloads referenced by path, never inlined into the engine"
principle. **Early-gate architect confirmed this is right and landscape-consistent** (Kubernetes
`postStart`/`preStop` exec hooks, Argo Workflows `hooks:`/`onExit`, GitHub Actions `post:` steps
all use argv + exit code) — this was NOT changed by the review.

**Design decision D3 — verdict is the process exit code; a JSON result file is optional, additive
detail, never an override.** Exit `0` = passed, non-zero = failed (mirrors `bench/graders.py`'s
`CommandGrader`). An optional JSON file at `AO_HOOK_RESULT_PATH` is read back (bounded, via the
existing `artifacts.read_control`) and stored as `HookOutcome.detail`/`.score` — see §5 for the
exact read sequencing (a review finding: "no file" and "malformed file" must be distinguished, not
folded into one code path). **Not changed by review** — architect: "don't change argv vs
exit-code."

**Design decision D4 (NEW, Rev 2) — hooks are declared at workflow root, tasks reference them by
name.** This is the one core-shape change from early-gate review. See §3.

## 3. Spec surface (REVISED — Rev 2)

**Rev 1 put `HookSpec` (the argv command itself) directly on `TaskSpec.pre_hook`/`post_hook`.
Early-gate architect review found this breaks a documented, already-reviewed containment
invariant**: `docs-md/task-isolation-hld.md` and `models.py`'s own AC-15 comment on
`TaskSpec.touches` state explicitly that "Command/argv fields are workflow-root-only, by design"
— `verify_command`, `regenerate[].command`, `resolver_*` all live on `WorkflowSpec.integration`,
**never** on `TaskSpec`, *specifically because* `artifacts.read_task_manifest` constructs
`TaskSpec(**t)` from an **agent-authored** `emit_tasks` JSON manifest with **no field allowlist
and no size cap** — an `emit_tasks` task's own agent output can inject any `TaskSpec` field
verbatim. Putting `HookSpec.command` directly on `TaskSpec` would have handed argv construction to
an agent-written file, the exact hole AC-15 exists to close. `read_task_manifest` also bypasses
JSON-Schema validation entirely (pydantic is the only gate for an installed wheel), so the
proposed `$defs/hook` JSON-Schema addition would have provided zero protection on that path.

**Fix (Rev 2): hook commands are declared once at workflow root, in a named registry — mirroring
the existing `TaskSpec.agent` pattern ("Key into the agents registry", never an inline command).
A task references a hook by name.** An agent-authored `emit_tasks` manifest can then only ever
*reference* a hook name the human workflow author already declared before the run started —
exactly the same trust model `TaskSpec.agent` already has for injected tasks (an injected task
picks which pre-declared agent runs it; it can never invent a new one). This closes the hole by
construction, not by validation.

```jsonc
// WorkflowSpec (workflow root) — new "hooks" registry, mirrors "agents"/"reposets" registries
{
  "version": "1.0",
  "id": "example",
  "repo_set": "main",
  "hooks": {
    "check_disk_space": {
      "command": ["python3", "scripts/hooks/check_disk_space.py"],
      "timeout_seconds": 60,
      "on_failure": "fail_task"      // this hook's OWN default when a task-level
                                      // HookRef.on_failure doesn't override it
    },
    "grade": {
      "type": "command",             // discriminator, reserved for a future non-command
                                      // hook kind (e.g. a built-in grader) — see §9
      "command": ["python3", "scripts/hooks/grade.py"],
      "timeout_seconds": 120
    }
  },
  "tasks": [
    {
      "id": "build",
      "agent": "claude",
      "instruction": "instructions/build.md",
      "pre_hook": { "use": "check_disk_space" },
      "post_hook": { "use": "grade", "on_failure": "ignore" }  // per-USE override
    }
  ]
}
```

`HookSpec` (new, `models.py`) — the workflow-root registry entry (the command itself):

```python
DEFAULT_HOOK_TIMEOUT_SECONDS: int = 120  # matches DEFAULT_REGENERATE_TIMEOUT_SECONDS / bench's
                                          # _DEFAULT_GRADER_TIMEOUT_SECONDS — one bounded-subprocess
                                          # convention, reused, not re-invented (see §5 for the
                                          # DRY trade-off this still makes, recorded not hidden).
HookOnFailure = Literal["ignore", "fail_task"]
DEFAULT_PRE_HOOK_ON_FAILURE: HookOnFailure = "fail_task"   # safe-by-default: a broken
                                                            # precondition blocks dispatch.
DEFAULT_POST_HOOK_ON_FAILURE: HookOnFailure = "ignore"     # safe-by-default: observational
                                                            # unless a workflow author opts in.

class HookSpec(BaseModel):
    type: Literal["command"] = "command"      # discriminator (architect N2): reserved for a
                                               # future non-argv hook kind without a breaking
                                               # schema change. MVP implements "command" only;
                                               # any other value is a schema/pydantic validation
                                               # error, not a silent no-op.
    command: list[str] = Field(min_length=1)
    timeout_seconds: int = Field(default=DEFAULT_HOOK_TIMEOUT_SECONDS, ge=1)
    on_failure: HookOnFailure | None = None   # this hook's own default when a task's HookRef
                                               # doesn't override it; None -> resolve_hook_on_failure
                                               # falls through to the kind-specific default.

class HookRef(BaseModel):
    """Task-level reference to a named `WorkflowSpec.hooks` entry — mirrors `TaskSpec.agent`'s
    'key into a registry' pattern (D4). Keeps argv construction OFF TaskSpec/read_task_manifest
    entirely (AC-15 containment, see above) — an emit_tasks-injected task can only pick an
    ALREADY-DECLARED hook name, never construct new argv."""
    use: str                                  # key into WorkflowSpec.hooks
    on_failure: HookOnFailure | None = None   # per-use override; None -> the referenced
                                               # HookSpec's own on_failure; still None -> the
                                               # kind-specific default (pre="fail_task",
                                               # post="ignore").

def resolve_hook_on_failure(
    ref: HookRef, hook: HookSpec, kind: Literal["pre_hook", "post_hook"]
) -> HookOnFailure:
    """The ONE place a hook's on_failure default is resolved — mirrors resolve_task_isolation /
    resolve_effective_agent's own 'one place' convention. Precedence: ref.on_failure (per-use
    override) > hook.on_failure (the hook's own default) > kind-specific default. This is what
    lets the SAME named hook (e.g. "grade") be wired as observe-only on one task and gating on
    another, without declaring it twice."""
```

`TaskSpec.pre_hook: HookRef | None = None`, `TaskSpec.post_hook: HookRef | None = None`.
`WorkflowSpec.hooks: dict[str, HookSpec] = {}`.

**New cross-validation rule** (`spec.py::cross_validate`, was "no change" in Rev 1 — corrected):
every `TaskSpec.pre_hook.use`/`post_hook.use` value must be a key in `WorkflowSpec.hooks`, else a
`SpecValidationError` naming the task id and the unresolved hook name — same shape as the existing
"unknown agent id" check `cross_validate` already performs for `TaskSpec.agent`.

Workflow-level default hooks applied to every task without an explicit per-task `HookRef` are
**Non-MVP** (§9) — the registry above already gets most of the value (one command definition,
reused by name) without that additional fill-in mechanism.

## 4. Result shape

```python
HookStatus = Literal["passed", "failed", "error", "timed_out"]

class HookOutcome(BaseModel):
    kind: Literal["pre_hook", "post_hook"]
    hook_name: str            # WorkflowSpec.hooks key that ran (D4) — traceability back to the
                               # registry entry, since a TaskResult alone no longer names the
                               # command.
    status: HookStatus
    exit_code: int | None = None
    duration_ms: int | None = None
    score: float | None = None  # Rev 2 (architect N2/landscape survey — Dagster's typed
                                 # AssetCheckResult precedent): promoted out of `detail` to a
                                 # typed, queryable field since Epic B's outcome metrics will
                                 # want it directly, not buried in an untyped dict. Populated
                                 # from the optional result file's "score" key when present and
                                 # numeric; None otherwise. NEVER influences `status` (D3 still
                                 # holds — exit code alone decides pass/fail).
    detail: dict = {}         # remaining verbatim JSON the hook wrote to AO_HOOK_RESULT_PATH
                               # (informational only); {} when no result file was written, OR
                               # when read_control raised (see `error` to distinguish the two).
    error: str | None = None  # engine-side note: timeout, spawn failure, or a result file that
                               # WAS written but failed read_control's bounded-JSON-object checks
                               # (missing/oversized/invalid-JSON/non-object). Distinguishing "no
                               # file" (error=None, detail={}) from "bad file" (error set,
                               # detail={}) is a review-flagged requirement — see §5.
```

`TaskResult` gains `pre_hook_result: HookOutcome | None = None`,
`post_hook_result: HookOutcome | None = None` (both `None` for a task with no declared hooks —
byte-identical serialization for every pre-epic run). `TaskRunState` mirrors both fields the same
way `output_artifact_path` is already mirrored in `_settle_completed_task` — **and, like that
field, reflects only the MOST RECENT dispatch cycle**, not a history across self-heal/T2/T3
redispatches. This is not a new gap introduced by hooks (`output_artifact_path` already works this
way); full per-cycle history remains on disk regardless (`<cycle-N>/pre_hook/result.json` etc.,
§5), so nothing is lost, only not duplicated into `RunState`. Recorded explicitly here because
early-gate review raised it and it is worth being deliberate about, not silent.

## 5. Execution mechanics

**New module, not engine.py (Rev 2, architect suggestion #10):** the subprocess-running mechanics
live in a new `src/agent_orchestrator/hooks.py` (plain functions, not a class — no `self` needed)
— `run_hook(hook: HookSpec, *, kind, hook_name, run_id, task_id, cycle, capture_dir,
context_fields: dict, env_overlay: dict[str, str], cwd: str) -> HookOutcome`. `engine.py` gets two
thin call sites (`_run_with_retries`'s pre-hook check, and `_finalize_with_post_hook`) that build
`context_fields` from already-resolved paths and call into `hooks.run_hook`. This keeps engine.py
(already ~4000 lines, flagged by both reviews as a maintainability concern) from growing further
for a self-contained, single-responsibility chunk of logic.

**Capture layout** — reuses the SAME per-cycle `output_dir` local variable
`_run_with_retries` already computes (both its flat cycle-1 shape and nested `cycle-<n>/` shape
for cycle ≥ 2 — a review finding: don't reinvent this path logic, reuse the existing variable so
both shapes are handled automatically): `<output_dir>/pre_hook/{context.json, result.json?,
stdout.txt, stderr.txt}` and `<output_dir>/post_hook/{...}`, siblings of `attempt-<n>/`.

**`context.json`** (engine-written, paths only — NFR-1, absolute paths throughout — a review
finding: don't leave any path implicitly relative to a worktree `cwd` the hook script might not
share): `version: 1`, `run_id`, `task_id`, `hook_kind`, `hook_name`, `dispatch_cycle`,
`instruction_path`, `general_instruction_paths`, `input_paths`, `output_paths`,
`dynamic_input_paths`, `repo_paths`, `output_dir`; `post_hook` adds `status`, `attempts`,
`exit_code`, `error`, `output_artifact_path` from the just-produced agent `TaskResult`.

**Subprocess env**: inherits `os.environ` merged with the same `env_overlay` the agent executor
received, plus `AO_RUN_ID`/`AO_TASK_ID` (same names `isolation/integrator.py::_base_env` already
uses — reused, not reinvented, per review), `AO_HOOK_KIND`, `AO_HOOK_NAME`, `AO_DISPATCH_CYCLE`,
`AO_HOOK_CONTEXT_PATH`, `AO_HOOK_RESULT_PATH`. `cwd` = the same resolved agent working directory.
`stdin=subprocess.DEVNULL` (review finding — matches `isolation/resolvers.py`'s and the
executor's own convention; `isolation/integrator.py`'s verify-command spawn is the one place in
this codebase that omits it, and this hook mechanism follows the majority, not that outlier).

**Captured output is bounded**: `stdout.txt`/`stderr.txt` are truncated to
`HOOK_CAPTURE_CAP_BYTES` (mirrors `isolation/integrator.py`'s `VERIFY_CAPTURE_CAP_BYTES`
precedent, same order of magnitude) before being written to disk — a review finding: an
unbounded `capture_output=True` on a runaway hook is a real disk/memory-exhaustion surface, not
hypothetical, given the timeout alone can still be many seconds of output.

**Result-file read sequencing (review finding — Rev 1 under-specified this)**: the engine calls
`artifact_store.exists(result_path)` FIRST. Not present → `HookOutcome.detail={}`, `error=None`
(a hook choosing not to write one is normal). Present → `artifacts.read_control` is called and
any `ControlFileError` is caught and surfaces as `HookOutcome.error` (detail stays `{}`) — this is
the genuinely-malformed case FR-6 wants surfaced, and it is now structurally distinct from "no
file," not folded into one blanket `try/except`.

**`hooks.run_hook` never raises** (mirrors `bench/graders.py::_run_command`'s "never raises"
contract — the SAME idiom, deliberately reused a 5th time in this codebase; see the DRY note
below). A timeout, missing binary, or malformed `result.json` all degrade to a `HookOutcome` with
`status`/`error` set — never an uncaught exception reaching the worker thread's
`ThreadPoolExecutor` future.

**DRY note, recorded not hidden (review Warning #4):** this is the *fifth* independent
bounded-subprocess-with-timeout implementation in this codebase (`isolation/git.py`,
`isolation/integrator.py` ×2, `isolation/resolvers.py`, `bench/graders.py::_run_command`, now
`hooks.py::run_hook`). Extracting a single shared primitive would touch all of those files, which
is out of this epic's narrow change-scope boundary (§10) and is pre-existing repo debt, not
something this epic introduces. Deliberately deferred, not silently repeated — a future
cross-cutting cleanup epic is the right place to consolidate all five.

**True no-op path (locked-in requirement, unaffected by the registry redesign)**: the pre-hook
check is `if task.pre_hook is None: ...` before the attempt loop; the post-hook path's first
statement is `if task.post_hook is None: return result`. When neither field is set, zero
hook-related work happens — no dict, no dir, no `WorkflowSpec.hooks` lookup, no subprocess call.
Proven by a `Mock`-patched `hooks.run_hook` call-count assertion (§8).

## 6. Failure semantics (REVISED — Rev 2, three rows added/corrected from early-gate review)

| Question | Decision | Rationale |
|---|---|---|
| Pre-hook fails, `on_failure="fail_task"` (default) | Task dispatch aborts. Returns `TaskResult(status="failed", attempts=0, error="pre_hook '<name>' failed: exit=<code> ...<bounded stderr tail>", pre_hook_result=outcome)` **before the executor is ever invoked** — zero agent spend. | Safe-by-default. `attempts=0` is a value this field has never carried before in this codebase (no `ge=1` constraint, so it's valid) — flagged for implementation to grep-check nothing downstream assumes `attempts >= 1` (e.g. dashboard "attempt N/M" display). |
| Pre-hook fails, `on_failure="ignore"` | Logged + recorded in `pre_hook_result`; the attempt loop runs normally. | Opt-in escape hatch for a purely advisory pre-hook. |
| Post-hook fails, `on_failure="ignore"` (default) | Agent's own `TaskResult.status` is never overwritten. | Grading/observability must not silently start failing pipelines the day it's turned on. |
| Post-hook fails, `on_failure="fail_task"` | A `"succeeded"` result is downgraded to `"failed"`, with `error` populated as `"post_hook '<name>' failed: exit=<code> ...<bounded stderr tail>"` — **not just the bare literal `"post_hook_failed"`** (Rev 1 wording). An already-`"failed"`/`"timed_out"` result is left as-is beyond recording `post_hook_result` on it (a hook can only make a result *stricter*, never *laxer*). | **Corrected by review**: `build_task_failure_summary` (the self-heal consult's own input, `monitoring.py`) derives `terminal_reason`/`errors[0]`/`stderr_tail` entirely from `result.error`/`exit_code` — a bare marker string with no detail would hand a self-heal monitor an empty failure summary. The hook's own exit code and a bounded stderr tail MUST be folded into `result.error` for this to be useful, not just present. |
| **T2 conflict-resolver dispatch (`mode == "resolve"`)** — **NEW row, review BLOCKING finding** | `_prepare_resolver_dispatch`'s existing `task.model_copy(update={...})` call (which already substitutes `agent`/`instruction`/`inputs` for the resolver) additionally sets `pre_hook=None, post_hook=None` on the copy. Hooks **do not fire** for a resolver dispatch. | A resolver dispatch runs a *different* agent doing conflict resolution, not the task's real work, and by design never produces `task.outputs` (AC-6/AC-7) — a task's `post_hook` (e.g. a grading hook checking `task.outputs`) firing against it would grade a merge resolution as if it were the task's own output, and a `fail_task` policy could stall the T2 ladder. Both early-gate reviews independently flagged this exact interaction. |
| T3 rerun dispatch (`mode == "rerun"`) | **Unchanged** — hooks fire normally. No suppression needed here. | A rerun redispatches the ORIGINAL agent on a fresh integration base doing the task's real work again — a genuine re-execution, not a substitution. This is the one case where "falls out of D1 for free" (a fresh `_run_with_retries` call = a fresh dispatch cycle = hooks refire) is exactly correct, confirmed by both reviews. |
| Isolated task, `fail_task` post-hook downgrades `succeeded → failed` — **integration consequence, NEW row, review finding** | Because the post-hook runs inside `_run_with_retries` (before `_run_and_integrate`'s `result.status != "succeeded"` gate at the isolate/integrate call site), the downgrade means `integrate()` is **never called** for that cycle — the isolated worktree's already-completed, correct changes are **not landed** onto the integration branch for this cycle. | This is the SAME gate an ordinary agent failure already goes through (byte-identical mechanism, D1 holds) — but it is a real, non-obvious consequence an operator turning on a `fail_task` grading gate needs to know explicitly, not infer. Stated here as a locked-in, deliberate consequence of the design, not left implicit. |
| Are hooks retried under the task's own `RetryPolicy`? | **No.** Fires exactly once per **dispatch cycle**. | Idempotency (CLAUDE.md design principle) — a side-effecting pre-hook must not silently re-run N times for N agent retries. |
| Self-heal (Consult Point B) eligibility for a hook-downgraded failure | Automatic, no special-casing — self-heal keys off `result.status == "failed"` and now has a real (non-empty) error summary to reason about (row above). An operator who does NOT want a grading failure to be heal-retried should leave the hook's `on_failure="ignore"` (the default) or leave `self_heal_enabled=False` (already the engine default). | Consistent, not a gap — but explicitly a judgment call an operator makes via existing, already-documented switches, not a new one this epic invents. |
| Cancelled run (`cancel_fn()` true) | Post-hook does **not** fire — with one narrow, pre-existing race acknowledged: cancellation is polled only at the top of each attempt iteration, so a cancel arriving mid-final-attempt can still reach the wrapped "exhausted attempts" return. This is a pre-existing engine characteristic (not introduced by this epic) and not claimed to be airtight. | Nothing meaningful completed in the common case; the narrow race is inherited, not created. |
| Quota-exhaustion early return | Post-hook does **not** fire. | Outer-loop "not done yet, will be retried" signal, not a terminal dispatch outcome. |
| Missing declared `task.outputs` (R-2 gate) | Post-hook fires **before** that gate. | Keeps hook dispatch independent of isolation/integration internals. A grading hook that needs outputs to exist checks for them itself via `context.json`'s paths and treats a missing path as a failed grade. |
| Budget/cost accounting for a hook's own execution | **None, and explicitly not "true zero" on the ledger for a pre-hook-blocked cycle** — review finding: when `result.actuals_available is False` (true for a pre-hook block, since the executor never ran), `reconcile()`'s existing fallback keeps the pre-dispatch *estimate* as the charged "actual" rather than reconciling to zero. This is pre-existing fallback behavior for any non-actuals-available failure, not a regression this epic introduces, but "zero agent spend" (this doc, §1) means "the executor was never invoked," not "the budget ledger shows exactly zero" — those are different claims and only the first is guaranteed. | Documented so the distinction is explicit rather than assumed. |

## 7. Forward-compatibility for Epic B (grading post-hook) — REVISED, Rev 2

**Rev 1 overclaimed this section** ("no change to the core hook-dispatch mechanism is required").
Early-gate architect review verified this against the real code and found real gaps. Corrected
below — Epic B's implementer should read THIS version, not re-derive it, and should not start from
Rev 1's framing.

**What a dispatch-scoped hook (this epic's mechanism) does and does not observe** — stated
plainly, since this is exactly what Rev 1 glossed over:

- **Fires for**: every task that actually reaches `_run_with_retries` and produces a terminal
  succeeded/failed/timed_out result on its own dispatch (the common case for a task that runs at
  all).
- **Does NOT fire for**: a task skipped via `skip_if_outputs_exist` (default `True` — the common
  case on any re-run of a workflow); a task resumed as already-succeeded; a T2 resolver-mode
  dispatch whose conflict no longer materializes (`_run_and_integrate`'s `materialized=False`
  short-circuit builds a synthetic success `TaskResult` without ever calling
  `_run_with_retries`); or a task whose dispatch raises an uncaught exception before
  `_run_with_retries` returns (there is no top-level `try`/`except` around the whole function).
- **Consequence for Epic B**: a grading post-hook under THIS mechanism produces a grade only for
  tasks that were freshly (re-)dispatched this run — not a complete per-task outcome ledger across
  skip/resume. If Epic B's outcome-metrics work needs a grade for every task regardless of
  skip/resume, it needs an *additional*, differently-scoped trigger point (a
  "post-settlement" hook, fired once per task from `_settle_completed_task` after the
  isolation/integration gate resolves, covering skip/resume/T2-synthetic-success too) — **this is
  a real, additional trigger point Epic B is expected to add, not something this epic's mechanism
  already provides.** Recorded here explicitly so Epic B scopes for it up front instead of
  discovering the gap mid-epic.

**Hooks are explicitly non-LLM, non-billable subprocesses (Rev 2 boundary, architect finding)**:
a hook that itself calls an LLM (e.g., an LLM-based grader) is **out of scope** for this
mechanism — its cost/tokens are invisible to `reconcile()`, `TaskRunState.cumulative_*`, and the
`task_cost_usd`/`run_cost_usd` breakers (a silent under-report of exactly the invariant
`E-9h3m7k` exists to prevent), and it would bypass `AgentSpec` resolution,
`forced_disallowed_tools`, and quota/429 handling entirely. **If Epic B's grading needs an
LLM-based judge, it should route through agent dispatch / the existing `Monitor` mechanism**
(which already dispatches through the engine's own executor and reads a verdict back via
`read_control`), not through this hook mechanism. A `bench/graders.py`-style deterministic grader
(pytest exit code, file assertions, a scripted check) is squarely what this hook mechanism is
for.

**What DOES still hold from Rev 1, unchanged**: the interface shape itself (argv command + exit
code + optional JSON detail file, now with a typed `score` field per §4) is the right *contract*
for a deterministic grading hook. Epic B's script still only needs to:

1. Read `AO_HOOK_CONTEXT_PATH` (task id, output paths, agent status, etc.).
2. Call an existing/adapted `Grader.grade(...)` (or equivalent deterministic check).
3. Write `{"score": result.score, "detail": {"solved": result.solved, ...}}` to
   `AO_HOOK_RESULT_PATH`.
4. `sys.exit(0 if result.solved else 1)`.
5. Reference the hook by name from whichever task(s) it should grade:
   `"post_hook": {"use": "grade", "on_failure": "ignore"}` (observe-only, the pattern to start
   with) or `"fail_task"` (gate on grading) — decided per task-use, not globally, since the same
   named `"grade"` hook can be wired differently per task.

**Reserved result-file keys (Rev 2, cheap now, expensive to add after scripts exist in the
wild)**: `version` (int, already in `context.json`, mirrored here), `score` (typed on
`HookOutcome`, see §4), `detail` (free-form). Deliberately **not** reserving `cost_usd`/
`input_tokens`/`output_tokens` — that would contradict the non-billable boundary just stated and
invite exactly the wall this section now names explicitly.

## 8. Config precedence

Unchanged from Rev 1, **confirmed correct by early-gate architect review** ("well-argued...
hooks are workflow structure, not an invocation-scoped runtime knob... if you adopt the
workflow-root registry [which Rev 2 does], this argument gets stronger, not weaker"). Hooks are
**spec-only** (declared in `WorkflowSpec.hooks`, referenced per task) — not part of the CLI > env
var > `.ao/config.yaml` three-layer precedence chain (ADR-0003) that governs invocation-scoped
runtime settings. Every existing "run a command as part of task/workflow execution" surface in
this codebase (`verify_command`, `regenerate[].command`) is spec-only today. A future
`--no-hooks`/`AO_DISABLE_HOOKS` emergency kill switch (mirroring `cli._resolve_no_isolation`'s
explicitly-no-config-layer precedent) remains Non-MVP/deferred (§9) — not required for correctness,
and `cli.py` stays outside this epic's change boundary.

## 9. Non-MVP / deferred (explicitly recorded, not silently dropped) — Rev 2 additions marked

- Workflow-level default `pre_hook`/`post_hook` applied to every task without an explicit
  per-task `HookRef` (the named registry already gets most of the reuse value).
- `--no-hooks` / `AO_DISABLE_HOOKS` emergency kill switch.
- Hook-level retry policy independent of `RetryPolicy`.
- **(Rev 2)** Token/cost accounting for a hook that itself invokes an LLM — explicitly out of
  scope, not merely deferred; see §7's non-billable boundary. If ever built, it should route
  through agent dispatch (`Monitor`-shaped), not this mechanism.
- **(Rev 2)** An `on_failure` value that skips the task outright (distinct from failing it) — use
  routers/`branches` for conditional execution instead; adding a `"skipped"` `TaskResult.status`
  member is a non-trivial, rippling change (`models.py:719` today has no such member) and is not
  justified by this epic's scope.
- **(Rev 2)** Distinguishing, in `RunState`, "hook declared but this dispatch cycle never reached
  it" (skipped/resumed/T2-suppressed) from "no hook declared" — both currently read as `None` on
  `TaskRunState`. Cheap to add later (a `HookStatus` member + a write from
  `_prepare_and_maybe_dispatch`'s skip path), but that path is main-thread/outside `_run_with_
  retries` and therefore outside D1's narrow boundary — deferred rather than expanding this
  epic's touch surface.
- **(Rev 2)** A "post-settlement" hook trigger point (fires once per task after
  skip/resume/integration resolves, not just on fresh dispatch) — named in §7 as what Epic B is
  expected to add for complete outcome coverage; not built here.
- **(Rev 2)** Consolidating this codebase's five independent bounded-subprocess-with-timeout
  implementations into one shared primitive (§5 DRY note) — cross-cutting, out of this epic's
  narrow scope.
- Dashboard/UI surfacing of `pre_hook_result`/`post_hook_result`.

## 10. Change-scope boundary (parallel-epics policy) — REVISED, Rev 2

| File | Change | Boundary |
|---|---|---|
| `src/agent_orchestrator/models.py` | Add `HookSpec`, `HookRef`, `HookOutcome`, `HookStatus`, `HookOnFailure`, `DEFAULT_HOOK_TIMEOUT_SECONDS`, `DEFAULT_PRE_HOOK_ON_FAILURE`, `DEFAULT_POST_HOOK_ON_FAILURE`, `resolve_hook_on_failure()`; add `hooks: dict[str, HookSpec] = {}` to `WorkflowSpec`; add `pre_hook`/`post_hook: HookRef | None` to `TaskSpec`; add `pre_hook_result`/`post_hook_result` to `TaskResult` and `TaskRunState`. | Additive only. |
| **`src/agent_orchestrator/hooks.py` (NEW, Rev 2)** | New module: `run_hook(...) -> HookOutcome` + the bounded-subprocess/capture/result-file mechanics (§5). | New file — no existing module's logic moved out of it. |
| `src/agent_orchestrator/engine.py` | Two thin call sites in `_run_with_retries` (pre-hook gate before the attempt loop; post-hook wrap via `_finalize_with_post_hook` on the `"succeeded"` early return and the final exhausted-attempts return only) calling into `hooks.run_hook`; one **additional, review-required** change in `_prepare_resolver_dispatch` (clear `pre_hook`/`post_hook` on the resolver's `task.model_copy(...)`, §6); two-line mirror in `_settle_completed_task`. | Must not touch scheduling, budget, breakers, monitoring, the isolation/integration call sites' own logic, or any OTHER early-return point in `_run_with_retries` beyond the two named. |
| `src/agent_orchestrator/spec.py` | **(Rev 2, was "no change" in Rev 1 — corrected)** New `cross_validate` rule: every `pre_hook.use`/`post_hook.use` must resolve to a `WorkflowSpec.hooks` key. `HookSpec`/`HookRef` field-level validation is pydantic's job, not this file's. | No change to isolation/routing/loop cross-validation logic beyond the one new rule. |
| `specs/workflow.schema.json` | Add `$defs/hook` (the command) and `$defs/hookRef` (the `{use, on_failure}` task-level reference); add `"hooks"` to the workflow-root `properties`; reference `hookRef` from the task def's `pre_hook`/`post_hook`. | Additive only. |
| `specs/examples/` | One new example workflow + tiny hook scripts. | New files only. |
| `tests/` | New test file(s) — see T-jI3P4p, now including the T2-resolver-suppression case and the self-heal-summary-population case both reviews flagged. | New files only; no weakening of existing tests. |
| `docs-md/` | This document. | — |

**(Rev 2) Sequencing note, architect finding**: `_settle_completed_task` and `TaskResult`'s token
fields are also edited by the sibling cost/caching epic in this same branch thread. This epic
(Epic A) should land and merge first; the cost/caching epic should rebase onto it, since both
touch the same small region of `_settle_completed_task` and `TaskResult`.

Out of scope: `cli.py`, `project_config.py`, `scheduler.py`, `breakers.py`, `monitoring.py`,
`isolation/*` (beyond the one named `_prepare_resolver_dispatch` line), `bench/*`.

## 11. Early-gate review — outcome

Both `reviewer` and `architect` returned **"approve with changes"** (not "needs rework") on
2026-09-21. Full transcripts referenced from
`meta/tickets/E-AMSSHX-task-lifecycle-hooks/STATUS.md`. Summary of what changed as a direct
result, all incorporated above:

- **BLOCKING (architect)**: `TaskSpec`-level `HookSpec` broke the AC-15 argv-containment
  invariant → **fixed**: hooks moved to a workflow-root named registry (D4, §3).
  `spec.py::cross_validate` now needs one new rule (§10, corrected from Rev 1).
- **BLOCKING (reviewer + architect, same interaction from two angles)**: T2 conflict-resolver
  dispatch silently inherited a task's hooks → **fixed**: `_prepare_resolver_dispatch` explicitly
  clears both on the resolver's task copy (§6 new row, §10).
  T3 rerun dispatch is explicitly confirmed unaffected (hooks fire normally, no change needed).
- **BLOCKING (architect)**: §7's Epic B forward-compat claim overstated what the mechanism
  observes → **corrected**: §7 rewritten to state exactly what fires/doesn't, the non-LLM/
  non-billable boundary, and the additional "post-settlement hook" trigger point Epic B is
  expected to add.
- **BLOCKING (architect)**: succeeded→failed downgrade's second-order effects (empty self-heal
  summary, integration skip) were unmodelled → **fixed**: §6 now requires the hook's exit
  code/stderr tail to be folded into `result.error` (not a bare marker string), and states the
  integration-skip consequence as a deliberate, documented row.
- **Warnings/suggestions (reviewer)**, all incorporated: result-file exists-first read sequencing
  (§5), DRY trade-off recorded not hidden (§5), budget-reconcile-not-quite-zero caveat (§6),
  cancellation race caveat (§6), `attempts=0` novel-value check (§6), benchmark-is-observational
  reaffirmed (§8/T-jI3P4p unchanged), `hooks.py` extraction (§5/§10).
- **Non-blocking (architect)**, incorporated: `type` discriminator on `HookSpec` for future
  extensibility (§3), `version` field on context/result files (§5), `score` promoted to a typed
  `HookOutcome` field (§4), `stdin=DEVNULL` + bounded capture + absolute paths + reused
  `AO_RUN_ID`/`AO_TASK_ID` naming (§5), capture-layout dual-shape reuse (§5), `on_failure` cannot
  express "skip task" recorded as non-goal (§9), cost-rule-never-reconciled stated explicitly
  (§6/§7).
- **Landscape survey (architect)**: k8s/Argo/GHA precedent for argv+exit-code confirmed D2/D3;
  Airflow/Dagster/Prefect/Temporal precedent noted that most mature orchestrators keep hooks
  *observational* and introduce a separate "check" concept for gating (Dagster asset checks) —
  this design's `on_failure: fail_task` is confirmed as a **deliberate, reasoned divergence**
  (avoids introducing a new DAG node type for MVP), recorded here rather than left implicit.

No item from either review was left unaddressed; none required a full rework of D1-D3 (the core
"lives inside `_run_with_retries`, argv command, exit-code verdict" shape survived intact).
