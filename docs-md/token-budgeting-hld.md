# Token Usage Budgeting & Rate Limiting — HLD + LLD

> Epic: `E-j4gno6-token-budget-rate-limit` · Author: architect · Date: 2026-06-18 · Status: Design
> Related: [`hld-agent-orchestrator.md`](hld-agent-orchestrator.md), [`lld-agent-orchestrator.md`](lld-agent-orchestrator.md), [`logging-dynamic-workflows-hld.md`](logging-dynamic-workflows-hld.md)
> Landscape note: [`../output/E-j4gno6-token-budget-rate-limit/landscape-note.md`](../output/E-j4gno6-token-budget-rate-limit/landscape-note.md)

## 1. Problem & Goals

Bound a run's token consumption with **two independent, simultaneously-enforced** limits:

1. **Total budget** — a cap on total tokens for the whole run.
2. **Rate limit** — tokens per configurable window (per minute / per 10 min / per hour).

When either limit (or the provider's real 429) is hit, detect it, compute when the next
quota frees, and either **stop** (default) or **wait-then-continue** (opt-in), resumably.

Accounting is **hybrid**: a pessimistic pre-run *estimate* gates admission; after the
task runs we *reconcile* against the real token counts the `claude` CLI reports.

### Locked decisions (from product owner)
- Accounting = **HYBRID** (estimate gates; actuals reconcile; estimate is the fallback).
- Scope = **PER-RUN**, persisted to `RunState` (rate windows per-run).
- Provider **429 in scope** — a second trigger feeding the same stop/wait handler.
- Default = **STOP**; wait is opt-in.

### Non-Functional invariants preserved
- **NFR-1** (no artifact-content reads): the estimator uses file **sizes** (`os.stat`), not contents.
- **Determinism** (NFR-2): injected `clock` + injected `sleeper` make all time math reproducible.
- **Idempotent/resumable** (NFR-3): counters in `RunState`; no double-charge on resume.
- **Pluggable** (NFR-6): `BudgetManager` + `TokenEstimator` are injected ABCs.
- **No magic literals** (NFR-7): `1.3`, `4`, `1000`, window seconds are named constants.

## 2. High-Level Design

```
                         WorkflowSpec.budget (BudgetSpec)            CLI overrides
                                   │                                     │
                                   └──────────── effective BudgetSpec ───┘
                                                       │
   Orchestrator.run() loop (linear topo order)        ▼
   ┌───────────────────────────────────────────────────────────────────────┐
   │  for tid in order:                                                      │
   │     resolve paths ─► estimate = TokenEstimator.estimate(ctx, cfg)       │  (chars/4 + allowance) × 1.3
   │                                                                         │
   │     ┌── GATE ──────────────────────────────────────────────┐           │
   │     │  decision = BudgetManager.gate(tid, estimate, counters)│          │
   │     │     admit?  ── no ─► _handle_exhaustion(stop | wait) ──┼─► sleep   │  (injected sleeper+clock)
   │     │       │ yes                                            │           │
   │     │  BudgetManager.charge_estimate(tid, estimate)         │           │  emit budget.charge
   │     └────────────────────────────────────────────────────────┘         │
   │                         │                                               │
   │     result = _run_with_retries(...)  ─► ClaudeCliExecutor               │
   │                         │              (--output-format json → usage)   │
   │     ┌── RECONCILE ───────────────────────────────────────────┐         │
   │     │  if result.provider_rate_limited: reverse + 429 handler │         │  emit budget.provider_429
   │     │  else: actual = sum(usage) or estimate (fallback)       │         │
   │     │        BudgetManager.reconcile(tid, actual, counters)   │         │  emit budget.reconcile
   │     └─────────────────────────────────────────────────────────┘        │
   │     RunStateStore.save(state)   # counters persisted (per-run)          │
   └───────────────────────────────────────────────────────────────────────┘
```

Two triggers, one handler:

```
  our configured TOTAL cap  ─┐
  our configured RATE cap   ─┼─►  BudgetDecision(admit=False, blocked_by, next_available_epoch)
  provider 429 / retry-after ─┘                         │
                                                        ▼
                                       on_exhaustion == "stop"  → end run, RunState resumable
                                       on_exhaustion == "wait"  → sleeper(next_available - now) → re-gate
```

### Components
| Component | New? | Responsibility |
|-----------|------|----------------|
| `models.BudgetSpec / RateLimit / EstimatorConfig / BudgetCounters` | new | config + persisted counters |
| `estimator.TokenEstimator` (ABC) + `HeuristicTokenEstimator` | new | pre-run estimate from file sizes |
| `budget.BudgetManager` (ABC) + `DefaultBudgetManager` | new | total + rate enforcement, next-available, 429 handling — **pure** |
| `executors.claude_cli` (`--output-format json` + `usage` parse + 429) | changed | actuals + provider rate signal |
| `models.TaskResult` token fields | changed | carry actuals + 429 from executor to engine |
| `engine.Orchestrator` (gate/reconcile/stop/wait/resume) | changed | orchestrate the meter; own persistence, sleeping, events |
| `runstate.RunState.budget_counters` | changed | per-run persistence |
| `cli` budget flags | changed | overrides + wiring |
| `errors.BudgetExhausted / RateLimited` | new | structured terminal signals |

### Separation of concerns
- `DefaultBudgetManager` is **pure**: it mutates an in-memory `BudgetCounters`, uses an injected clock, and never sleeps, persists, or logs. The **engine** owns sleeping (injected `sleeper`), persistence (`RunStateStore.save`), and event logging. This keeps the manager trivially unit-testable and swappable (NFR-6).

## 3. LLD — module by module

### 3.1 `models.py` (T-oh5gl5)

**Purpose**: shared config + persisted counters + TaskResult token fields.
**Inputs**: parsed spec / CLI. **Outputs**: typed models. **Dependencies**: pydantic v2.

```python
WINDOW_SECONDS = {"minute": 60, "ten_minutes": 600, "hour": 3600}
DEFAULT_CHARS_PER_TOKEN = 4
DEFAULT_PESSIMISM_BUFFER = 1.3
DEFAULT_OUTPUT_ALLOWANCE_TOKENS = 1000
DEFAULT_429_BACKOFF_SECONDS = 60

class RateLimit(BaseModel):
    tokens: int
    window: Literal["minute","ten_minutes","hour"] | None = None
    window_seconds: int | None = None
    def seconds(self) -> int:
        return self.window_seconds if self.window_seconds is not None else WINDOW_SECONDS[self.window]

class EstimatorConfig(BaseModel):
    chars_per_token: int = DEFAULT_CHARS_PER_TOKEN
    pessimism_buffer: float = DEFAULT_PESSIMISM_BUFFER
    output_allowance_tokens: int = DEFAULT_OUTPUT_ALLOWANCE_TOKENS

class BudgetSpec(BaseModel):
    total_tokens: int | None = None
    rate: RateLimit | None = None
    on_exhaustion: Literal["stop","wait"] = "stop"
    estimator: EstimatorConfig = EstimatorConfig()

class BudgetCounters(BaseModel):       # persisted inside RunState
    consumed_tokens: int = 0
    window_start_epoch: float | None = None
    window_consumed_tokens: int = 0
    charged_estimate: dict[str, int] = {}
    reconciled_tasks: list[str] = []
```
`WorkflowSpec` gains `budget: BudgetSpec | None = None`. `RunState` gains
`budget_counters: BudgetCounters = BudgetCounters()` (default → old states load).
`TaskResult` gains the token + 429 fields (see §3.4).

**Edge cases**: both/neither of `window`/`window_seconds` → cross-validation error;
`pessimism_buffer < 1.0` → error; old `RunState` without `budget_counters` → default.

### 3.2 `estimator.py` (T-n7hmwj)

**Purpose**: pessimistic pre-run estimate. **Inputs**: `TaskContext` (paths only) + `EstimatorConfig`.
**Outputs**: `int` token estimate. **Dependencies**: `ArtifactStore.size()`.

```python
class TokenEstimator(ABC):
    @abstractmethod
    def estimate(self, ctx: TaskContext, cfg: EstimatorConfig) -> int: ...

class HeuristicTokenEstimator(TokenEstimator):
    def __init__(self, store: ArtifactStore): self._store = store
    def estimate(self, ctx, cfg) -> int:
        b = self._store.size(ctx.instruction_path)
        for p in (*ctx.input_paths, *ctx.dynamic_input_paths):
            b += self._store.size(p)              # os.stat, 0 if missing — NFR-1 safe
        raw = b / cfg.chars_per_token + cfg.output_allowance_tokens
        return math.ceil(raw * cfg.pessimism_buffer)
```
`ArtifactStore.size(path) -> int` is new: `os.stat(resolve(path)).st_size`, `0` if missing.

**Edge cases**: missing file → 0; directory input → top-level stat only (documented
limitation; recursive sizing is a follow-on item); multi-byte UTF-8 inflates (pessimistic, OK).

### 3.3 `budget.py` (T-7kp8iv)

**Purpose**: pure meter. **Inputs**: `BudgetSpec`, injected `clock`, a `BudgetCounters`.
**Outputs**: `BudgetDecision`; mutated counters. **Dependencies**: none (no IO).

```python
class BudgetDecision(BaseModel):
    admit: bool
    blocked_by: Literal["total","rate"] | None = None
    next_available_epoch: float | None = None

class BudgetManager(ABC):
    def gate(self, task_id, estimate, counters) -> BudgetDecision: ...
    def charge_estimate(self, task_id, estimate, counters) -> None: ...
    def reconcile(self, task_id, actual, counters) -> None: ...
    def reverse_estimate(self, task_id, counters) -> None: ...
    def on_provider_429(self, retry_after_epoch, counters) -> BudgetDecision: ...
```
`DefaultBudgetManager(spec, clock)` — see pseudocode in `T-7kp8iv-budget-manager-meter/TASK.md`.
Key invariants:
- **Precedence**: total checked before rate (FR-3); both reasons logged by the engine.
- **Tumbling window** keyed by `window_start_epoch`; rolls when `now >= start + seconds`.
- **Reconcile is idempotent** (`reconciled_tasks` membership) → no double-charge (NFR-3).
- **Unsatisfiable detection**: if `estimate > rate.tokens` (single task can never fit a
  window) or `estimate > total_tokens`, the manager/engine must **stop** rather than wait forever.

**Edge cases**: no limits → always admit; window boundary exact equality rolls; 429 with no
retry-after → `now + DEFAULT_429_BACKOFF_SECONDS`; double reconcile → no-op.

### 3.4 `executors/claude_cli.py` + `TaskResult` (T-1m9744)

**Purpose**: real actuals + provider 429. **Inputs**: subprocess stdout/stderr/returncode.
**Outputs**: `TaskResult` token fields. **Dependencies**: stdlib `json`.

`TaskResult` additions:
```python
input_tokens: int | None = None
output_tokens: int | None = None
cache_creation_input_tokens: int | None = None
cache_read_input_tokens: int | None = None
actuals_available: bool = False
provider_rate_limited: bool = False
provider_retry_after_epoch: float | None = None
```
- Append `--output-format json` (once, not if already present).
- `parse_usage_and_429(stdout, stderr, returncode, now_epoch)` (pure) parses the `usage`
  block and detects 429. **Fallback**: any parse failure → `actuals_available=False`
  → engine keeps the estimate (FR-5).

> Shape confirmed and fixture frozen at `tests/fixtures/claude_usage.json` (T-1m9744). Top-level
> cost field is `total_cost_usd`; `usage` block fields match exactly as listed in §3.4.

**Edge cases**: non-JSON stdout; missing `usage`; timeout (status `timed_out`, no actuals);
429 with / without retry-after; flag already present.

### 3.5 `engine.py` integration (T-algywf)

**Purpose**: orchestrate the meter. See full pseudocode in `T-algywf-.../TASK.md` §Pseudocode.
**Integration points** (verified against current `engine.py`):
- **Gate**: in the `run()` loop just before `_run_with_retries`, once inputs/paths are resolved.
- **Reconcile / 429 route**: immediately after `_run_with_retries` returns the `TaskResult`.
- **Stop**: log `budget.exhausted`, leave the current task `pending` (un-charged), mark the
  run terminal, break — `RunState` stays resumable.
- **Wait**: `self._sleeper(next_available - clock().timestamp())`, check `cancel_fn`, re-gate the
  same task (cursor not advanced).
- **Resume double-charge guard**: at run start, any `tid` in `charged_estimate` but not in
  `reconciled_tasks` that will re-run is `reverse_estimate`d first (R2 / NFR-3).

When `budget_manager is None` the engine path is byte-identical to today (opt-in).

**Edge cases**: estimate alone exceeds window/total (unsatisfiable → stop, never infinite wait);
cancel during wait → `cancelled`; actuals unavailable → estimate kept; provider-429 mid-run.

### 3.6 `cli.py` (T-xefapr) & `errors.py`
- CLI flags merge over `wf.budget` per-field (CLI > spec); build `DefaultBudgetManager` +
  `HeuristicTokenEstimator`, share one `clock` with `RunStateStore`. No budget anywhere →
  `budget_manager=None`.
- `errors.py`: `BudgetExhausted(OrchestratorError)`, `RateLimited(OrchestratorError)`.

### 3.7 Deviations from original design

Minor behavioural differences between the pseudocode/design above and the shipped code:

1. **`_is_unsatisfiable` guard** (`engine.py`): the engine adds an `_is_unsatisfiable()` check
   that detects when a single task's estimate exceeds the entire window budget or the total
   budget. In this case the engine stops immediately rather than entering an infinite wait
   loop. This guard is not present in the original pseudocode but is a necessary safety
   invariant — without it a single oversized task would spin forever on `on_exhaustion=wait`.

2. **Provider 429 re-run via cursor decrement** (`engine.py`): on a provider 429 with
   `on_exhaustion=wait`, the engine decrements `cursor -= 1` and issues `continue` to re-run
   the same task in the next loop iteration. This achieves the same result as the pseudocode's
   "cursor not advanced" approach — the task is re-gated and re-run without advancing past it
   — but uses Python loop mechanics rather than a separate re-gate inner loop.

3. **`_build_effective_budget` validates `rate_window` against the enum** (`cli.py`): the CLI
   helper validates the `rate_window` string against `WINDOW_SECONDS.keys()` before
   constructing a `RateLimit`, raising a clear `ValueError` with the allowed values. Pydantic's
   `Literal` validation would catch this at model construction time, but the pre-check gives a
   better error message before the model is instantiated.

4. **`BudgetCounters` uses `Field(default_factory=BudgetCounters)`** (`models.py`): `RunState`
   declares `budget_counters: BudgetCounters = Field(default_factory=BudgetCounters)` so that
   old `RunState` JSON files serialised without this field deserialise to a zero-initialized
   `BudgetCounters`. No migration is needed — backward compatibility is automatic.

## 4. Schema additions (`specs/workflow.schema.json`)

Top-level `budget` object (schema is `additionalProperties:false`, so it must be declared):

```jsonc
"budget": {
  "type": "object", "additionalProperties": false,
  "properties": {
    "total_tokens": { "type": "integer", "minimum": 1 },
    "rate": {
      "type": "object", "additionalProperties": false, "required": ["tokens"],
      "properties": {
        "tokens": { "type": "integer", "minimum": 1 },
        "window": { "enum": ["minute","ten_minutes","hour"] },
        "window_seconds": { "type": "integer", "minimum": 1 }
      }
    },
    "on_exhaustion": { "enum": ["stop","wait"], "default": "stop" },
    "estimator": {
      "type": "object", "additionalProperties": false,
      "properties": {
        "chars_per_token": { "type": "integer", "minimum": 1, "default": 4 },
        "pessimism_buffer": { "type": "number", "minimum": 1.0, "default": 1.3 },
        "output_allowance_tokens": { "type": "integer", "minimum": 0, "default": 1000 }
      }
    }
  }
}
```
Cross-validation (`spec.cross_validate`) enforces exactly one of `rate.window`/`rate.window_seconds`.

Example spec block:
```yaml
budget:
  total_tokens: 2000000
  rate: { tokens: 100000, window: minute }
  on_exhaustion: wait
  estimator: { chars_per_token: 4, pessimism_buffer: 1.3, output_allowance_tokens: 1500 }
```

## 5. Sequence — gate / charge / reconcile / wait

```
Engine            Estimator        BudgetManager        Executor(claude)        RunStateStore
  │ resolve paths     │                 │                     │                      │
  │ estimate()  ─────►│                 │                     │                      │
  │ ◄──── estimate ───│                 │                     │                      │
  │ gate(est) ───────────────────────► │                     │                      │
  │ ◄── admit=False, next=T+60 ─────────│   (rate blocked)    │                      │
  │ on_exhaustion=wait → sleeper(60) ───┼─────────────────────┼──────────────────────┤  (injected, deterministic)
  │ gate(est) ───────────────────────► │  (window rolled)     │                      │
  │ ◄── admit=True ─────────────────────│                     │                      │
  │ charge_estimate ─────────────────► │                     │                      │
  │ save(counters) ─────────────────────────────────────────────────────────────► │
  │ execute(ctx) ───────────────────────────────────────────►│                      │
  │ ◄── TaskResult(usage / 429) ─────────────────────────────│                      │
  │ reconcile(actual) ───────────────► │                     │                      │
  │ save(counters) ─────────────────────────────────────────────────────────────► │
```

## 6. Test strategy
See `T-67kiia-tests-budget/TASK.md`. Unit (estimator math, meter total+rate, window roll
at fixed-clock boundary, next-available, 429 parse, double-charge guard) + integration with
`FakeExecutor` + fixed clock/sleeper (stop-total, stop-rate, wait-rate, wait-429, resume
continuance, no-budget regression). Coverage ≥80% on new modules; schema round-trip; NFR-1 grep audit.

## 7. ADR Log

```
ADR-BUD-001: Budget config lives in the workflow spec `budget` block, CLI-overridable.
Context: config could live in workflow spec, defaults, project .ao/config.yaml, or CLI.
Options: (a) workflow `budget` block, (b) workflow `defaults`, (c) project config, (d) CLI-only.
Decision: top-level workflow `budget` block, overridable by CLI (per-field).
Reason: budgets are workflow-scoped (the run's cost profile), belong with the DAG that incurs
  them; CLI override covers ad-hoc/ops tuning. Project config is too coarse; defaults conflate
  retry/timeout with cost policy.
Consequences: schema gains a `budget` block (additionalProperties:false must declare it); CLI
  gains override flags + per-field merge.
```
```
ADR-BUD-002: Hybrid accounting (pessimistic estimate gates; actuals reconcile; estimate = fallback).
Context: pure estimate over/under-charges; pure-actuals can't gate before a task runs.
Options: estimate-only, actuals-only (post-hoc), hybrid.
Decision: hybrid — estimate admits, actuals correct cumulative drift, estimate is the fallback.
Reason: gating needs a pre-run number; reconcile keeps cumulative totals honest; fallback keeps
  FakeExecutor/non-JSON paths working.
Consequences: charged-estimate bookkeeping + reconcile idempotency + resume reverse-charge logic.
```
```
ADR-BUD-003: Tumbling fixed windows (not sliding) for the rate limit.
Context: rate window can be tumbling (reset every N s) or sliding (rolling N s lookback).
Options: tumbling, sliding.
Decision: tumbling, keyed by window_start_epoch in RunState.
Reason: simpler, deterministic, cheap to persist per-run; matches "per minute/10-min/hour" intent.
Consequences: bursty allowance at window edges; sliding window is Scope Out / follow-up.
```
```
ADR-BUD-004: STOP leaves the blocking task `pending` & un-charged; run ends resumable (terminal status).
Context: when stopping on exhaustion, what state does the blocking task hold?
Options: (a) mark task failed, (b) leave task pending un-charged + run terminal, (c) new task status.
Decision: leave the task pending and un-charged; mark the run non-success with a budget reason
  (reuse `failed` + structured BudgetExhausted/RateLimited; minimal enum change).
Reason: resume must re-gate and run the task cleanly with no double-charge; pending+un-charged is
  the natural idempotent state.
Consequences: resume reverses any stale charge; status carries a budget reason for observability.
```
```
ADR-BUD-005: Provider 429 is a SECOND trigger into the SAME stop/wait handler.
Context: provider 429 is independent of our configured limits but needs identical handling.
Options: (a) separate retry mechanism, (b) reuse the budget exhaustion handler.
Decision: executor surfaces 429 + retry-after on TaskResult; engine routes it to the same
  _handle_exhaustion (reverse charge → stop or wait-to-reset → re-run).
Reason: one code path for "quota unavailable, here's when it returns"; avoids divergent retry logic.
Consequences: executor must detect 429 + reset time; engine reverses the charge before re-running.
```

## 8. Deployment / upgrade / dev-experience
- **Opt-in & backward compatible**: no `budget` block and no CLI flags → zero behavior change;
  old `RunState` files load (default `budget_counters`). No migration.
- **Determinism for ops & tests**: injected clock + sleeper; `budget.*` structured events make
  exhaustion/wait diagnosable from `run.log`.
- **Dev/operator experience**: spec is one small block; CLI flags for quick caps; logs name the
  blocking limit and the next-available instant; estimate-vs-actual deltas logged for tuning the
  buffer.

## 9. Open questions (resolved in T-1m9744 / T-5igs6g)

- RESOLVED: The real `claude --output-format json` output shape is confirmed and frozen in
  `tests/fixtures/claude_usage.json`. The top-level cost field is `total_cost_usd` (not
  `cost_usd`). The `usage` block fields are exactly:
  `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`.
  The parser in `parse_usage_and_429` targets this shape.

- RESOLVED: `cache_read_input_tokens` are counted fully (not discounted). Cache reads still
  consume tokens in the provider's quota, so full counting is conservative and correct. This
  can be revisited with real usage data in a follow-on epic.

- RESOLVED: `on_exhaustion=wait` uses an unbounded sleep that is interruptible by
  `cancel_fn()` (checked after sleep returns). A `max_wait_seconds` field can be added to
  `BudgetSpec` in a follow-on epic if operators need a hard cap. Current behavior: wait is
  `cancel_fn()`-interruptible with no upper time bound.

- OPEN (follow-on): Recursive sizing for directory inputs in the estimator (MVP: top-level
  stat only, documented limitation).

---

## 10. Accurate usage metrics addendum (E-9h3m7k, 2026-07-10)

This epic (`E-9h3m7k-accurate-usage-metrics`) is separate from — and downstream of —
the token budgeting/rate-limiting work above. It fixed three confirmed gaps in ACTUAL
(not estimated) usage accounting:

1. **`TaskResult.cost_usd`** — the real `total_cost_usd` field (confirmed §9 above,
   frozen in `tests/fixtures/claude_usage.json`) was never extracted; only the four
   `usage.*` token fields were. `parse_usage_and_429` now also returns `cost_usd`.

2. **Cross-attempt discard bug** — `engine._run_with_retries`'s retry loop
   *overwrote* `last_result` on every attempt; a task that failed on attempt 1 (real
   tokens/cost spent) and succeeded on attempt 2 only ever reported attempt 2's
   numbers. The loop now sums `input_tokens`/`output_tokens`/`cache_*_tokens`/`cost_usd`
   across every attempt and returns the CUMULATIVE total as the task's `TaskResult` —
   this is also what `_sum_actuals` feeds into `BudgetManager.reconcile`, so budget
   reconciliation is now correctly charged for retried tasks too (previously
   under-charged by the discarded attempts' tokens).

3. **No run-level rollup** — `TaskRunState` gained
   `cumulative_input_tokens`/`cumulative_output_tokens`/`cumulative_cache_creation_input_tokens`/
   `cumulative_cache_read_input_tokens`/`cumulative_cost_usd`, mirrored from the (now
   cumulative) `TaskResult` once a task settles. `models.compute_run_usage_totals(state)`
   is a pure function summing these across every task — deliberately NOT a
   separately-mutated `RunState` counter (avoids the double-charge-on-resume failure
   mode that `BudgetCounters.reconciled_tasks` exists to guard against elsewhere).
   `ao run`/`ao status` (`cli.py`) print per-task cost/tokens and a run-total line
   using this function; `status.json` carries a `usage_totals` block for the same data.

4. **Actual-cost circuit breakers** — `breakers.py` gained `task_cost_usd` (any single
   task's cumulative cost ≥ threshold) and `run_cost_usd` (run-wide cumulative cost ≥
   threshold), registered in `BREAKER_REGISTRY` and `spec._MVP_BREAKER_CONDITIONS`
   alongside the six existing MVP conditions. These are distinct from the schema's
   reserved `projected_cost_exceeds` name (still unimplemented — that one is a
   pre-flight ESTIMATE check tied to `budget.py`/`estimator.py`, not actuals).
   `CircuitBreakerSpec.threshold` widened `int -> float` (schema: `integer` ->
   `number`, `minimum: 1` -> `exclusiveMinimum: 0`) to allow fractional USD thresholds;
   count-based conditions are unaffected (`count >= float_threshold` still works).

Companion change: `TaskContext.output_dir` is now attempt-suffixed — see
`docs-md/logging-dynamic-workflows-hld.md` §11.

Not in scope: token-count breakers (`task_tokens`/`run_tokens` — token *totals* are
already capped via `BudgetSpec.total_tokens`); accumulating usage across a
quota-exhaustion-triggered whole-task re-run (the outer `engine.run()` loop, distinct
from the retry loop fixed here) — quota exhaustion is detected before real work
happens in practice, so this was judged out of scope for this pass.
