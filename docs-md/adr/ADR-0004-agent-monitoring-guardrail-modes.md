# ADR-0004 — Agent-based monitoring & self-healing: guardrail modes

- Status: **Accepted** (implemented 2026-07-15)
- Date: 2026-07-15
- Deciders: Avadhoot Divekar (user, via `meta/prompts/prompt.md` "Current Ask"), Claude (dev-epic role)
- Related: [`docs-md/lld-agent-monitoring-self-healing.md`](../lld-agent-monitoring-self-healing.md) (full design) · [`docs-md/lld-run-control-routing-breakers.md`](../lld-run-control-routing-breakers.md) §6/§16/§17 (breaker framework this extends) · Epic [`E-XyfjuZ-agent-monitoring-self-healing`](../../meta/tickets/E-XyfjuZ-agent-monitoring-self-healing/EPIC.md)

## Context

The origin ask (`meta/prompts/prompt.md`) requested agent-based monitoring + self-healing for
workflow runs, with an explicit two-mode guardrail split: **recommendation** (the monitor may take a
judgment call and extend limits) vs. **hard limits** (never increase quotas/budgets; may only heal
failures caused by other issues — network errors, broken API responses). It must work within the
existing `ao run`/`ao resume` commands, with no new subcommand.

The repo already has a pluggable circuit-breaker framework (`E-rc7k2v`, `breakers.py`,
`evaluate_breakers`) and an operator-driven, unbounded breaker-extension mechanism (`E-3JTmVu`,
`apply_breaker_extension`, `ao resume --extend-breaker`). The question this ADR resolves: how does an
**automated, in-process** monitor decision plug into that same framework without regressing either
epic's existing, tested guarantees (byte-identical no-op when unused; hard limits stay hard; an
operator's manual extension stays independent of anything automated)?

## Decision

### 1. Guardrail mode is a per-breaker spec field, not a run-level flag
`CircuitBreakerSpec.mode: Literal["hard", "recommend"] = "hard"`. A workflow author opts in
*specific* breakers to recommend-mode; every other breaker (and every breaker declared before this
epic) is untouched. Rejected alternative: a single run-level "guardrail mode" flag governing every
breaker uniformly — rejected because the origin ask's own example ("recommendation" vs "hard limits")
implies different breakers in the same run can carry different risk profiles (e.g. a cost breaker
might be negotiable while a `stop_file` operator kill-switch never should be).

### 2. Built-in hard stops are hard by construction, not by convention
The three re-framed built-in stops (budget exhaustion/unsatisfiable, quota max-wait) and any
provider-429 stop never construct a `CircuitBreakerSpec` — they call `record_trip()` directly and
`break` the run loop before `evaluate_breakers()` is ever reached in the same iteration. This was
verified against the live code (not assumed) before implementation, and is reinforced by a defensive,
`python -O`-immune `raise AssertionError` in the engine's consult helper. Rejected alternative: give
built-ins a `mode` field defaulted to `"hard"` and gate them the same way as declared breakers —
rejected because it would require constructing `CircuitBreakerSpec` objects for the built-ins (a
larger, riskier change to well-tested existing code) for no behavioral gain, since they are already
structurally unreachable by the consult path.

### 3. One `Monitor` ABC with two typed consult methods, not a unified `consult()`
Considered unifying `decide_breaker_trip`/`decide_task_failure` into a single
`consult(ConsultRequest) -> Verdict` (raised during the early-gate architect review). Rejected: the
two consult points have genuinely different decision vocabularies (`extend`/`halt` vs.
`retry`/`accept_failure`) and payloads (`extend_by_seconds` vs. `wait_seconds`); unifying would need
either a discriminated-union type (worse type safety, moves branching from "which method" to "which
branch inside one method" — no net simplification) or a lowest-common-denominator shape (a
dual-purpose nullable "amount" field is more confusing, not less). The DRY concern behind the
suggestion — shared engine-side wrapper logic (bound/cap checks, logging, audit-recording) — is
addressed without unifying the ABC: both `_consult_breaker_trips` and `_consult_task_failure_heal`
follow the identical shape (bound check → cap check → build summary → log consult → call monitor
in a `try/except` → record decision → log decision) without a shared abstraction forcing it.

### 4. Monitor bookkeeping is derived from one audit list, never duplicated (Design Decision D9)
`RunState` gains exactly one new field, `monitor_decisions: list[MonitorDecisionRecord]`. Per-breaker
extension counts, per-task heal-retry counts, and the run-wide call count are all *derived* from this
list on demand (`count_monitor_breaker_extensions`/`count_monitor_heal_retries`/
`count_monitor_calls_made`), mirroring `breakers.py`'s own `_consecutive_failure_streak`/
`_settled_task_active_seconds` pattern. Rejected alternative (the originally-planned design, revised
before any `RunState` code was written): three separate persisted dict/int fields — rejected once the
architect's early-gate review pointed out this codebase's established "derive, don't duplicate
bookkeeping" convention, since a derived count can never drift from its own audit trail.

### 5. Monitor-driven extension reuses the operator-driven mechanism; bounds stay independent
A recommend-mode consult's `extend` decision calls the *exact same* `apply_breaker_extension()`
function `ao resume --extend-breaker` (E-3JTmVu) uses — same `breaker_overrides` write, same
un-latch semantics, same `breaker.extend` event. The monitor-driven bound
(`max_extensions_per_breaker`, engine-enforced, derived from `monitor_decisions`) is tracked entirely
separately from the operator path (which remains unbounded, by design — an operator's explicit
judgment call is not something the framework should second-guess with a cap). Rejected alternative:
a single shared extension-count budget across both — rejected because it would let automated
extensions silently eat into an operator's remaining manual headroom (or vice versa), which is
surprising and hard to reason about operationally.

### 6. `self_heal` CLI precedence is the full tri-state chain, not monotonic-enable
Revised during the early-gate reviewer pass (before any CLI code existed): `--self-heal` /
`--no-self-heal` / `AO_SELF_HEAL` / `monitoring.self_heal` follow the same CLI > env > config >
default precedence as every other runtime setting (ADR-0003), in **both** directions — an operator
can force self-heal off via CLI/env even when config enables it. The initially-considered
monotonic-enable-only design (any layer saying "on" wins, no way to force "off") was rejected as an
unjustified asymmetry with every other setting, especially since self-heal changes observable failure
behavior. Every other monitoring knob (`monitor` selection, all three bounds, `transient_patterns`)
stays config-file-only (no CLI/env) — unlike `self_heal`, these are not commonly toggled per-invocation.

### 7. Self-heal is opt-in, scoped to `status == "failed"`, and invisible to failure-counting breakers by design
Self-heal defaults off (`monitoring.self_heal: false`). It never applies to `timed_out`/`cancelled`
statuses — a cancel is an explicit operator act that must never be silently overridden, and a
timed-out (still-running-when-the-clock-ran-out) task is a different risk class than a task that
cleanly returned failed. A consequence, flagged by the early-gate reviewer and accepted rather than
engineered around: a healed-and-succeeded task never sets `ts.status = "failed"`, so it is invisible
to `task_failures`/`consecutive_failures` breakers — a systemic transient issue would heal every
individual failure and never trip a count-based breaker built to catch exactly that pattern. Accepted
because self-heal is opt-in and bounded to 1 retry/task by default, keeping the blast radius small; a
future breaker condition on cumulative heal-retry count is a reasonable non-MVP follow-on.

### 8. `AgentMonitor` cost/budget integration is explicitly deferred
`AgentMonitor`'s `executor.execute()` call is out-of-band relative to the engine's budget-gate/
reconcile wrapper — the same decoupling that makes it safe to call without touching `RunState.tasks`
also means its real token/dollar cost is not gated by the run's budget nor counted in `run_cost_usd`.
Mitigated by `max_monitor_calls_per_run` (bounds invocation count, not spend); full integration would
require threading the budget manager into the consult path, judged too large a change for this MVP
without evidence it's needed in practice.

## Alternatives considered (epic-level)

- **A run-level guardrail flag instead of per-breaker `mode`** — see Decision 1.
- **Unified `consult()` method on the `Monitor` ABC** — see Decision 3.
- **Three separate persisted RunState counters instead of one derived audit list** — see Decision 4.
- **A shared extension-count budget between monitor and operator paths** — see Decision 5.
- **Monotonic-enable-only `self_heal` CLI precedence** — see Decision 6.
- **Extending self-heal to `timed_out` tasks** — rejected for MVP; the risk profile of a still-running,
  clock-expired task differs enough from a cleanly-returned failure to warrant a separate epic if
  ever pursued.

## Consequences

- Every pre-epic workflow spec, and every breaker that never declares `mode`, is byte-identical —
  proven by re-running the full pre-existing regression suite (`test_stop_reframe_parity.py`,
  `test_resume_replay.py`, and the whole breaker/engine/budget test corpus) unmodified.
- `RuleBasedMonitor` (default) adds zero runtime cost or nondeterminism to any run that doesn't
  declare `mode: "recommend"` or enable `self_heal`.
- `breakers.py` and `spec.py` required **zero** changes — the guardrail-mode feature is additive at
  the `engine.py`/`models.py`/`monitoring.py` layer only, honoring the epic's narrow-change-scope
  mandate.
- Full details, exact hook line references, and the complete edge-case/test matrix live in
  [`lld-agent-monitoring-self-healing.md`](../lld-agent-monitoring-self-healing.md).
