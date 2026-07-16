# LLD — Agent-based workflow monitoring & self-healing (guardrail modes)

- Epic: [`E-XyfjuZ-agent-monitoring-self-healing`](../meta/tickets/E-XyfjuZ-agent-monitoring-self-healing/EPIC.md)
- Epic context (requirements, traceability, design-gate outcomes): [`docs-md/ai-epics/E-XyfjuZ-agent-monitoring-self-healing.md`](ai-epics/E-XyfjuZ-agent-monitoring-self-healing.md)
- ADR: [`ADR-0004`](adr/ADR-0004-agent-monitoring-guardrail-modes.md)
- Foundation (extended, not modified): [`lld-run-control-routing-breakers.md`](lld-run-control-routing-breakers.md) §6 (breaker framework), §16 (`run_active_seconds` + operator `--extend-breaker`) — see that doc's new §17 addendum for how the two extension mechanisms relate.
- Status: **Implemented.** This doc describes the shipped design; see the epic context doc's Evidence Log for exact test/coverage numbers.
- Audience: a developer new to the repo should be able to locate and modify each module from this doc alone.
- Date: 2026-07-15 · Owner: dev-epic agent

> Grounding note: every line/behavior cited below was read from the live, shipped code on 2026-07-15
> (`engine.py`, `monitoring.py`, `models.py`, `breakers.py`, `project_config.py`, `cli.py`,
> `specs/workflow.schema.json`).

---

## 0. Problem & scope

A monitoring agent should supervise workflow runs **shallowly** — it never needs to understand a
workflow's own domain logic, only compact, path/id/count-shaped summaries — and can **heal** two
distinct classes of run friction, each gated by its own guardrail:

1. **Recommend-mode circuit breaker trips.** A workflow author opts a *specific* breaker into
   `mode: "recommend"`; when it trips, the monitor may extend its threshold once (bounded) or halt.
   `mode: "hard"` (default) is today's behavior — never consultable, never extendable.
2. **Task-failure self-healing.** Opt-in per run (`monitoring.self_heal`, default off). When a task
   exhausts its `RetryPolicy` and settles `"failed"`, the monitor may request one bounded extra retry
   (e.g. a transient network error) or accept the failure.

Both route through the **existing** `ao run` / `ao resume` commands — no new CLI subcommand, no new
top-level spec concept beyond one field on the existing circuit-breaker spec.

**Explicitly out of scope (Non-MVP)** — do not build without a fresh epic: periodic/mid-task "pulse"
monitoring (only task/breaker **boundary** hooks exist); cross-run learning; spec- or
source-code-mutation healing; notifications (Slack/email/webhook); self-healing for
`timed_out`/`cancelled` task statuses (§5 below — `"failed"` only); any change to the existing,
unbounded, operator-driven `ao resume --extend-breaker` mechanism; full budget/cost integration for
`AgentMonitor` invocations (§8, a documented known limitation, not silently dropped).

---

## 1. Files touched (map of change)

| File | Change |
|---|---|
| `specs/workflow.schema.json` | `circuitBreaker.properties.mode` enum (`"hard"`\|`"recommend"`, default `"hard"`) |
| `models.py` | `CircuitBreakerSpec.mode`; `MonitorDecisionRecord`; `RunState.monitor_decisions`; `count_monitor_breaker_extensions`/`count_monitor_heal_retries`/`count_monitor_calls_made`; `DEFAULT_MAX_EXTENSIONS_PER_BREAKER`/`DEFAULT_MAX_HEAL_RETRIES_PER_TASK`/`DEFAULT_MAX_MONITOR_CALLS_PER_RUN` |
| `monitoring.py` (**new**) | `Monitor` ABC; `RuleBasedMonitor`; `AgentMonitor`; `BreakerTripSummary`/`BreakerVerdict`/`TaskFailureSummary`/`HealVerdict`; `build_task_failure_summary`; `SAFE_DEFAULT_BREAKER_VERDICT`/`SAFE_DEFAULT_HEAL_VERDICT` |
| `engine.py` | `Orchestrator.__init__` gains `monitor`/`max_extensions_per_breaker`/`max_monitor_calls_per_run`/`self_heal_enabled`/`max_heal_retries_per_task`; Consult Point A wired at the `evaluate_breakers` call site; Consult Point B wired after the budget-reconcile/429 block; `_consult_breaker_trips`/`_consult_task_failure_heal` private methods |
| `project_config.py` | `MonitoringConfig` + `ProjectConfig.monitoring`; `ao init` scaffold gains a commented `monitoring:` block |
| `cli.py` | `_resolve_monitoring_settings`/`_build_monitor`; `--self-heal/--no-self-heal` on `run`/`resume` |
| `breakers.py` | **unchanged** — reused as-is (`apply_breaker_extension`, `record_trip`, `evaluate_breakers`) |
| `spec.py` | **unchanged** — `_MVP_BREAKER_CONDITIONS` already derives from `BREAKER_REGISTRY` (commit 8debeb3); `mode` needs no cross-referential validation (schema enum + Pydantic `Literal` are sufficient, kept in sync by a dedicated drift-guard test, §7) |

**No new runtime dependency.** `AgentMonitor`'s context/verdict reads reuse `artifacts.read_control`
(the same bounded-JSON surface routing/loop verdicts already use).

---

## 2. Guardrail modes (`CircuitBreakerSpec.mode`)

```jsonc
// specs/workflow.schema.json, $defs.circuitBreaker.properties
"mode": {
  "enum": ["hard", "recommend"],
  "default": "hard",
  "description": "'hard' (default): never consultable/extendable. 'recommend': the monitor may extend this breaker's threshold (bounded) or halt when it trips."
}
```

```python
# models.py
class CircuitBreakerSpec(BaseModel):
    ...
    mode: Literal["hard", "recommend"] = "hard"
```

Built-in re-framed stops (`BUILTIN_BUDGET_EXHAUSTED`, `BUILTIN_BUDGET_UNSATISFIABLE`,
`BUILTIN_QUOTA_MAX_WAIT`, and any provider-429 stop) **never construct a `CircuitBreakerSpec` at
all** — each calls `breakers.record_trip()` directly at its own dedicated call site in `engine.py`
and immediately sets `state.status = "failed"; break`, all **before** the run loop ever reaches the
`evaluate_breakers()` call for that iteration. They are unconditionally hard by construction, not by
a flag — verified by tracing every one of the three call sites (budget gate ~L340-407, quota
max-wait ~L515-546, provider-429 ~L639-665) against the `evaluate_breakers` call site (~L816).

A schema-vs-`Literal` drift guard (`tests/test_routing_breaker_models.py::TestGuardrailMode::
test_schema_enum_matches_pydantic_literal_exactly`) introspects
`CircuitBreakerSpec.model_fields["mode"].annotation` via `typing.get_args` and asserts it exactly
equals the schema's `enum` set — the same class of drift the `run_active_seconds`
breaker-condition allowlist bug (fixed in commit 8debeb3) exposed, closed here for `mode` too.

---

## 3. `Monitor` abstraction (`monitoring.py`)

Mirrors the `Breaker`/`Executor`/`BudgetManager` DI style: an ABC plus two implementations, neither
of which the engine constructs directly by name — `cli.py`'s `_build_monitor` does that from
config.

```python
class Monitor(ABC):
    name: str
    def decide_breaker_trip(self, trip: BreakerTripSummary, *, run_id: str) -> BreakerVerdict: ...
    def decide_task_failure(self, summary: TaskFailureSummary, *, run_id: str) -> HealVerdict: ...
```

**DTOs are shallow by construction** (NFR-1-style): `BreakerTripSummary` carries
`breaker_id`/`condition`/`action`/`detail`/`prior_extensions`; `TaskFailureSummary` carries
`task_id`/`attempt_count`/`terminal_reason`/`errors`/`stderr_tail`/`exit_code`/`prior_heal_retries`.
`prior_extensions`/`prior_heal_retries` are populated by the ENGINE (from
`count_monitor_breaker_extensions`/`count_monitor_heal_retries`, §6) so a stateless `Monitor` can
still implement "once, then stop" policies without keeping its own mutable state.

### 3.1 `RuleBasedMonitor` — default, deterministic, zero-cost
- Breaker policy: `prior_extensions == 0` → `extend` (with `extend_by_seconds=None`, i.e. "extend by
  the breaker's own original threshold amount" — the caller passes this straight into the existing
  `apply_breaker_extension(..., extend_by_same=True)`); otherwise → `halt`.
- Failure policy: builds one lowercased text blob from `terminal_reason + errors + stderr_tail` and
  regex-searches `DEFAULT_TRANSIENT_PATTERNS` (network/timeout/connection-reset/5xx/JSON-decode) plus
  any `monitoring.transient_patterns` config additions (never a replacement). `prior_heal_retries ==
  0` and a match → `retry` (`wait_seconds`, default 30.0); otherwise → `accept_failure`.
- No I/O, no clock, no randomness — every call is a pure function of its input DTO.

### 3.2 `AgentMonitor` — the "agent-based" headline
Invokes a named `agents.json` agent through the **existing** `Executor`/`TaskContext` machinery —
the identical contract every regular workflow task uses. Per consult:
1. Writes a **baked Python string constant** instruction (`_BREAKER_TRIP_INSTRUCTION` /
   `_TASK_FAILURE_INSTRUCTION` — never a `specs/examples/*.md` path: `pyproject.toml`'s wheel
   packaging, `packages = ["src/agent_orchestrator"]`, does not ship `specs/`, so a repo-relative
   instruction path would silently break for any `uv tool install`ed `ao` binary) + the compact
   context JSON to `.orchestrator/runs/<run_id>/monitor/call-<n>-<subject>/{instruction.md,
   context.json}`.
2. Builds a manually-constructed `TaskContext` (no `TaskSpec`/DAG involvement — `repo_paths={}`,
   `cwd=agent.working_dir or workspace_root`) and calls `executor.execute(ctx)` — exactly **one**
   call, no internal retry/quota-wait loop, so a slow/quota-exhausted/failing monitor invocation
   returns immediately rather than hanging.
3. Reads back `verdict.json` via the shared, bounded `artifacts.read_control` reader.
4. ANY of: a non-`"succeeded"` result, an executor exception, a missing/oversized/malformed verdict
   file, or a verdict failing strict shape validation (unknown `decision`, wrong field types) →
   returns `SAFE_DEFAULT_BREAKER_VERDICT` (`halt`) / `SAFE_DEFAULT_HEAL_VERDICT` (`accept_failure`)
   — never raises.

**Known limitation (documented, not silently absorbed):** `AgentMonitor`'s `executor.execute()` call
is out-of-band relative to `Orchestrator._run_with_retries`'s budget-gate/reconcile wrapper — this is
exactly what makes it safe to call synchronously without corrupting `state.tasks`/breaker/usage math
(verified: `Executor.execute()` is fully decoupled from `RunState`, which only the engine's own
dispatch loop mutates — proven by
`tests/test_monitoring_breaker_consult.py::TestConsultNeverPollutesRunState`), but it ALSO means a
real agent-based monitor's token/dollar cost is not gated by the run's token budget nor counted in
`run_cost_usd`/`budget_counters`. Mitigated by `max_monitor_calls_per_run` (bounds invocation
*count*); full budget-system integration for monitor calls is a follow-on epic if real usage shows
it's needed.

### 3.3 Compact failure-summary construction (no new file-content read)
`build_task_failure_summary(result: TaskResult, *, prior_heal_retries)` derives `terminal_reason`,
the sole entry of `errors`, AND `stderr_tail` all from `TaskResult.error` (already an
executor-produced, ≤500-char bounded string — `executors/claude_cli.py` already slices the tail of
the task's own `stderr.txt`/`transcript.jsonl` into this field). **This introduces zero new
file-content reads anywhere in the engine's vicinity** — an early-gate reviewer pass confirmed a
separate re-read of `stderr.txt` would be pure redundancy given the executor already captures and
bounds the same tail; `stderr_tail` keeps its own name/cap (`STDERR_TAIL_MAX_CHARS = 2000`) purely so
the DTO's shape matches the brief's literal "stderr tail ≤2000 chars" contract even though today it
never exceeds `TaskResult.error`'s own tighter bound.

---

## 4. Consult Point A — recommend-mode breaker-trip consult (`engine.py`)

### 4.1 Where it hooks in
At the **existing** `evaluate_breakers` call site (the same task-boundary hook
`lld-run-control-routing-breakers.md` §6.1 documents — after outcome handling + save, before the
next dispatch). `evaluate_breakers()` itself is **100% unchanged** — no new parameter, no new
internal branch, no new `clock()` call. "Which specs newly tripped this boundary" is derived at the
**call site** by diffing `state.tripped_breakers` ids before/after the one, unchanged call:

```python
_before_tripped_ids = {tb.id for tb in state.tripped_breakers}
breaker_action = evaluate_breakers(workflow, state, self._clock, self._store, run_log)  # UNCHANGED
if breaker_action is not None:
    _newly_tripped_ids = {tb.id for tb in state.tripped_breakers if tb.id not in _before_tripped_ids}
    _newly_tripped_specs = [b for b in workflow.circuit_breakers if b.id in _newly_tripped_ids]
    _consultable = (
        len(_newly_tripped_specs) == len(_newly_tripped_ids)
        and bool(_newly_tripped_specs)
        and all(b.mode == "recommend" for b in _newly_tripped_specs)
    )
    _consult_outcome = self._consult_breaker_trips(_newly_tripped_specs, state, run_log) if _consultable else "halt"
    if _consult_outcome == "halt":
        state.status = "failed"; failed = True; self._runstate.save(state); break
    self._runstate.save(state)  # "extend": fall through, exactly as if breaker_action had been None
```

This preserves the function's documented byte-identical no-op guarantee and every existing test that
calls `evaluate_breakers()` directly and asserts on its `Action | None` return
(`tests/test_breaker_extension.py`, `tests/test_mvp_breaker_conditions.py`,
`tests/test_engine_breakers.py` — all pass unmodified).

### 4.2 `_consult_breaker_trips` — all-or-nothing resolution
Per newly-tripped spec, in **declared order** (`workflow.circuit_breakers`'s own order, not set
iteration order):
1. `prior = count_monitor_breaker_extensions(state, spec.id)`. If `prior >= max_extensions_per_breaker`
   → contribute `halt` **without consulting at all** (never even calls the monitor).
2. If `count_monitor_calls_made(state) >= max_monitor_calls_per_run` → log `monitor.cap_exceeded`,
   contribute `halt` without consulting.
3. Otherwise: build a `BreakerTripSummary`, log `monitor.consult`, call
   `self._monitor.decide_breaker_trip(...)` inside a `try/except Exception` (NFR-2: a monitor bug
   must never crash the run — falls back to `SAFE_DEFAULT_BREAKER_VERDICT`), append a
   `MonitorDecisionRecord`, log `monitor.decision`.

**If every resolved decision is `extend`**: apply `apply_breaker_extension()` for each (reusing
E-3JTmVu's exact function — same `breaker_overrides` write, same un-latch of the matching
`TrippedBreaker` record) and return `"extend"`. **If any decision is `halt`** (explicit, or
bound/cap-exhausted): apply **no** extensions at all and return `"halt"` — an all-or-nothing
resolution that avoids a "half-extended" state and biases toward safety (any dissent stops the run).

A defensive check (`if any(b.mode != "recommend" ...): raise AssertionError(...)`, using an explicit
`raise` rather than an `assert` statement so it survives `python -O`) makes the "hard breakers never
reach this method" invariant enforced in code, not just by the caller's filtering — belt-and-suspenders
per the epic's explicit "enforce in code, not convention" requirement, proven reachable-and-correct by
`tests/test_monitoring_breaker_consult.py::TestConsultBreakerTripsDefenseInDepth`.

**Ordering guarantee**: no `self._runstate.save(state)` call happens between `evaluate_breakers()`
recording the trip(s) and the consult resolving — the only saves are after the outcome is known
(the `"halt"` branch's save, or the `"extend"` fallthrough's save) — so a half-consulted state can
never hit disk.

---

## 5. Consult Point B — task-failure self-healing (`engine.py`, opt-in)

### 5.1 Where it hooks in
Immediately after the existing budget-reconcile/429 block, **before** `ts.attempts =
result.attempts` / `ts.ended_at = datetime.now(UTC).isoformat()` — mirroring exactly where the
quota-exhaustion and provider-429 routes already short-circuit on `result.*` fields, before any
`TaskRunState` settle-time mutation:

```python
if self._self_heal_enabled and result.status == "failed":
    _heal_verdict = self._consult_task_failure_heal(tid, result, state, run_log)
    if _heal_verdict is not None and _heal_verdict.decision == "retry":
        self._sleeper(_heal_verdict.wait_seconds)
        if self._cancel_fn():
            state.status = "cancelled"; failed = True; self._runstate.save(state); break
        ts.status = "pending"
        cursor -= 1
        self._runstate.save(state)
        continue
    # else: bound/cap exhausted (None) or accept_failure -> fall through, unmodified.

ts.attempts = result.attempts        # existing code, unchanged
ts.ended_at = datetime.now(UTC).isoformat()   # existing code, unchanged
```

A successfully healed failure therefore **never** sets `ts.status = "failed"`, never reaches
`evaluate_breakers`, and never touches `ts.started_at` (the E-3JTmVu single-writer guard —
`if ts.started_at is None: ts.started_at = ...` — is untouched code; this block only ever resets
`ts.status`, exactly like the pre-existing quota-exhaustion requeue). Proven by
`tests/test_monitoring_self_heal.py::TestStartedAtPreservedAcrossHealRetry` using the same
clock-monkeypatch technique as the E-3JTmVu `run_active_seconds` regression test.

**Deliberately scoped to `result.status == "failed"`** — `timed_out`/`cancelled` are never healed.
This is a documented MVP boundary, not an oversight: an operator-requested cancel must never be
silently overridden, and a hung/timed-out task retried automatically without operator awareness
carries different risk than a clean process failure. Note for authors: a workflow that times out
frequently due to genuinely transient issues should raise `timeout_seconds`, not rely on self-heal
to paper over it — self-heal only ever sees a task that *returned* (successfully exhausted its
attempts with a clean failure), never one still running when the clock ran out.

### 5.2 `_consult_task_failure_heal` — same bound/cap/fallback shape as Consult Point A
`prior = count_monitor_heal_retries(state, tid)`; bound-exhausted or cap-exhausted → `None` (no
consult); otherwise builds the `TaskFailureSummary` (§3.3), logs `monitor.consult`, calls
`self._monitor.decide_task_failure(...)` in a `try/except Exception` (same NFR-2 fallback), appends
a `MonitorDecisionRecord`, logs `monitor.decision`, returns the verdict.

**Heal retries do not consume `RetryPolicy.max_attempts`** — a healed retry re-dispatches through
the full `_run_with_retries` path again, getting its own fresh `RetryPolicy` budget, exactly like the
pre-existing quota-exhaustion requeue already does. Proven by
`tests/test_monitoring_self_heal.py::TestSelfHealTransientRetry::
test_heal_retries_do_not_consume_retry_policy_attempts`.

**Known tradeoff (documented, early-gate reviewer finding):** a healed-and-succeeded task is
invisible to `task_failures`/`consecutive_failures` breakers (they gate purely on `ts.status`, which
a healed retry never sets to `"failed"`). A systemic transient issue (e.g. flaky network on every
task) would heal every individual failure and never trip a count-based breaker built to catch
exactly that pattern — only `monitor_decisions`/`count_monitor_heal_retries` make it observable, not
breaker-actionable. Self-heal is opt-in and bounded to 1 retry/task by default, so the blast radius
is small; a future breaker condition on cumulative heal-retry count is a reasonable non-MVP
follow-on if this proves to matter in practice.

---

## 6. Observability & persisted state (Design Decision D9 — derive, don't duplicate)

`RunState` gains exactly **one** new field:

```python
class MonitorDecisionRecord(BaseModel):
    at: str  # ISO-8601 UTC
    consult_point: Literal["breaker_trip", "task_failure"]
    subject_id: str  # breaker id or task id
    decision: str
    monitor: str  # Monitor.name -- "rules" or the configured agent name
    detail: dict = {}

class RunState(BaseModel):
    ...
    monitor_decisions: list[MonitorDecisionRecord] = []   # NFR-5 defaulted
```

`monitor_breaker_extensions[breaker_id]`, `monitor_heal_retries[task_id]`, and `monitor_calls_made`
are **not** separate persisted fields — they are pure functions of `monitor_decisions`
(`count_monitor_breaker_extensions`/`count_monitor_heal_retries`/`count_monitor_calls_made` in
`models.py`), mirroring this codebase's own "derive, don't duplicate persisted bookkeeping"
convention already used by `breakers.py`'s `_consecutive_failure_streak` and
`_settled_task_active_seconds`. Adopted from an early-gate architect finding *before* any RunState
code was written — a net simplification with zero rework cost, and it eliminates a whole class of
counter/audit-log drift bug (the list is bounded by `max_monitor_calls_per_run`, default 10, so the
linear scan is trivially cheap).

One record is appended **per actual consult** — never for a bound/cap-exhausted skip (nothing to
record; observable instead via the counters staying put, plus a distinct `monitor.cap_exceeded`
event when the cap specifically was the cause). `prepare_resume` needs **zero code changes**: it
never touches `monitor_decisions`, so a persisted count from before a stop is honored unchanged on
resume (proven by
`tests/test_monitoring_breaker_consult.py::TestResumeHonoursPersistedMonitorState` and
`tests/test_monitoring_self_heal.py::TestResumeHonoursPersistedHealState`).

**Structured events** (same `logging_setup`/JSON-lines convention as `breaker.trip`/`breaker.extend`):
`monitor.consult` (before a real call), `monitor.decision` (after — includes the resolved
`decision`), `monitor.cap_exceeded` (bound/cap prevented a consult; distinct from the first two since
no real consult happened). `breaker.extend` (existing, unmodified) still fires for a monitor-driven
extension, since it reuses `apply_breaker_extension` directly.

**Shared-baseline note**: both the monitor-driven and operator-driven (`ao resume --extend-breaker`)
extension paths write additively to the same `RunState.breaker_overrides` (single source of truth
for the effective threshold, regardless of who extended it) — see
[`lld-run-control-routing-breakers.md`](lld-run-control-routing-breakers.md) §17 for the full
relationship between the two mechanisms.

---

## 7. Config & CLI (`project_config.py`, `cli.py`)

```yaml
# .ao/config.yaml
monitoring:
  self_heal: false               # AO_SELF_HEAL / --self-heal / --no-self-heal
  monitor: rules                 # "rules" (default) or an agent name from agents.json
  max_extensions_per_breaker: 1
  max_heal_retries_per_task: 1
  max_monitor_calls_per_run: 10
  heal_wait_seconds: 30
  transient_patterns: []         # ADDED to the built-in patterns, never replacing them
```

Precedence: `self_heal` follows the **same tri-state CLI > env (`AO_SELF_HEAL`) > project config >
built-in default** chain as every other runtime setting (`_resolve_run_settings`, ADR-0003) — an
operator can force it OFF via `--no-self-heal` even when config enables it, and vice versa
(revised from an initially-planned monotonic-enable-only draft per early-gate reviewer feedback,
before any CLI code was written). Every other monitoring knob (`monitor` selection, all three
bounds, `transient_patterns`) is **config-file only** — no CLI/env — per the brief's "config file is
the primary surface; don't explode flag count." Recommend-mode breaker consult (Consult Point A) has
**no** config gate at all: it activates purely from a workflow's own `circuit_breakers[].mode`
declaration, independent of `monitoring.self_heal`.

`_build_monitor(cfg, agent_map, store, executor)`: `"rules"` → `RuleBasedMonitor` seeded from
`cfg.heal_wait_seconds`/`cfg.transient_patterns`; any other value → looked up in `agent_map` and
wrapped in `AgentMonitor`; an unknown name is a clean `typer.Exit(1)` **before any task dispatch**
(mirrors `_load_all`'s existing validation style).

---

## 8. Edge cases (per module — mandatory)

**Guardrail modes**
- Every pre-epic workflow (never declares `mode`) → `"hard"` → byte-identical to today, proven by
  re-running `tests/test_stop_reframe_parity.py`/`tests/test_resume_replay.py` unmodified.
- A boundary where ALL newly-tripped breakers are `mode: "hard"` → halt, monitor never even
  constructed-and-called (`tests/test_monitoring_breaker_consult.py::TestHardModeNeverConsults`).
- A boundary mixing `hard` + `recommend` → halt without consulting either (hard wins) —
  `TestMixedHardAndRecommendNeverConsults`.
- `mode: "bogus"` → rejected by the schema `enum` (and, redundantly, by the Pydantic `Literal`)
  before any cross-referential validation runs.

**Consult Point A**
- `max_extensions_per_breaker` bound exhausted → halt; the monitor is provably never called (a
  monitor double that always answers `extend` is still forced to `halt` —
  `TestMaxExtensionsPerBreakerBound`).
- `max_monitor_calls_per_run` cap exhausted → halt without consulting; `monitor.cap_exceeded` logged
  (`TestMaxMonitorCallsPerRunCap`).
- A monitor that raises → falls back to `halt` (the safe default), never crashes the run
  (`TestMonitorRaisingFallsBackToSafeDefault`).
- Resume with a persisted `monitor_decisions` entry from before the stop → the bound correctly sees
  the pre-existing count, not a fresh zero (`TestResumeHonoursPersistedMonitorState`).
- An `AgentMonitor` consult never adds a key to `state.tasks`/`state.injected_tasks`
  (`TestConsultNeverPollutesRunState`).

**Consult Point B**
- `self_heal_enabled=False` (default) → byte-identical failure behavior
  (`TestSelfHealDisabledByDefault`).
- Transient error, heals once, succeeds → run succeeds, `count_monitor_heal_retries == 1`
  (`TestSelfHealTransientRetry`).
- Transient error, heals once, fails again → bound exhausted, falls through to the normal
  failure/run-fail path exactly as if self-heal were off.
- Non-transient error → `accept_failure` on the FIRST consult (no retry attempted, but the consult
  DID happen) → run fails exactly as today (`TestSelfHealNonTransientError`).
- `timed_out` status → never healed at all, zero consults (`TestSelfHealNeverAppliesToTimeoutOrCancel`).
- A cancel request arriving during the self-heal wait → the run is genuinely cancelled, not silently
  swallowed (`test_cancel_during_self_heal_wait_cancels_the_run`).
- Resume with a persisted heal-retry count already at the bound → zero fresh heal budget
  (`TestResumeHonoursPersistedHealState`).

**Config / CLI**
- Absent `monitoring:` block → `MonitoringConfig()` all-defaults, byte-identical.
- `monitoring.monitor: <unknown-agent>` → clean `ao run`/`ao resume` exit 1, before any dispatch.
- `--no-self-heal` overrides a config `self_heal: true` (and vice versa with `--self-heal` over a
  config/env-set false) — `TestResolveMonitoringSettings` (unit) +
  `TestSelfHealViaCli::test_no_self_heal_flag_overrides_config_enabled_self_heal` (CliRunner).

---

## 9. Test strategy & CLI-layer scope note

Layered per `CLAUDE.md` (unit + engine-API integration + CliRunner E2E; fixed/stepping clocks,
injected sleeper, `FakeExecutor`/local test doubles; both engine-API and CLI —
memory `engine-api-tests-dont-cover-cli`).

| Layer | File(s) |
|---|---|
| Schema/model | `tests/test_routing_breaker_models.py::TestGuardrailMode`, `::TestMonitorDecisionRecordAndDerivedCounters` |
| `monitoring.py` unit | `tests/test_monitoring.py` |
| Engine integration (Consult Point A) | `tests/test_monitoring_breaker_consult.py` |
| Engine integration (Consult Point B) | `tests/test_monitoring_self_heal.py` |
| CLI resolver/builder unit | `tests/test_cli.py::TestResolveMonitoringSettings`, `::TestBuildMonitor` |
| CliRunner E2E | `tests/test_e2e_monitoring_cli.py` |

**CLI-layer scope note (self-heal specifically):** `DispatchExecutor` (used by every real `ao
run`/`ao resume` invocation) always constructs a bare, unconfigurable `FakeExecutor()` for
`executor: "fake"` agents — there is no way to make a "fake" agent fail deterministically through
the unmodified CLI path (a pre-existing, already-known constraint:
`tests/test_cli.py::TestRunCommand::test_failed_run_exits_1`'s own comment documents working around
the identical limitation). `tests/test_e2e_monitoring_cli.py` proves the full CLI-to-engine wiring
on a **genuine** dispatch failure by using the real `claude_cli` executor with a deterministic,
network-free `sh -c "...; exit 1"` `command_template` in place of `claude` (no binary/API key
needed) — this exercises config/flag resolution, transient-pattern classification, and the
retry-requeue mechanics through the real `ao run` command. The "heals AND eventually succeeds" happy
path is proven at the engine-API level (`tests/test_monitoring_self_heal.py`, deterministic via a
scripted test-double executor) — an explicit, disclosed scope boundary, not a silent gap.

---

## 10. Assumption log

```
ASSUMPTION: terminal_reason/errors/stderr_tail all derive from TaskResult.error (no genuinely
  separate stderr capture exists on TaskResult today). Risk: if a future executor change adds a
  richer error taxonomy, this DTO shape may need revisiting. Mitigation: the field names/bounds
  documented here are already forward-compatible with that; only the SOURCE would change.
ASSUMPTION: max_monitor_calls_per_run is shared across both consult points (not two separate caps).
  Risk: a workflow with heavy breaker-trip activity could exhaust the budget before any task
  failures get a chance to be healed, or vice versa. Mitigation: default (10) is generous for
  typical runs; both bounds are config-adjustable per project.
ASSUMPTION: self-heal's `timed_out`/`cancelled` exclusion is the right MVP boundary (not overly
  narrow). Risk: a workflow with frequent legitimate timeouts gets no self-heal coverage for them.
  Mitigation: documented loudly (§5.1) so authors reach for `timeout_seconds` instead; a future
  epic could extend self-heal to timed_out if real usage shows it's needed.
```
