# EPIC: E-j4gno6-token-budget-rate-limit

## Metadata
- Epic ID: `E-j4gno6-token-budget-rate-limit`
- Title: Token Usage Budgeting & Rate Limiting (with exhaustion stop/wait)
- Owner: architect
- Created: 2026-06-18
- Last Updated: 2026-06-18 (epic drafted)
- Status: Draft

## Summary
- Goal: Bound a run's token consumption with two independent, simultaneously-enforced limits — a **total budget** (cap on tokens for the whole run) and a **rate limit** (tokens per configurable window) — using a **hybrid accounting** model (pessimistic pre-run estimate gates admission; real CLI `usage` reconciles after the task runs). When either our configured limit OR the provider's real rate signal (HTTP 429 / retry-after) is hit, detect it, compute when the next quota frees up, and either **stop** (default) or **wait-then-continue** (opt-in), resumably.
- Scope In:
  - **Budget config + schema**: a versioned `budget` block in the workflow spec (total, rate {tokens, window}, on_exhaustion, estimator buffer/output allowance), validated by `workflow.schema.json`; CLI overrides.
  - **Token estimator**: a pluggable estimator interface; default `chars/4` heuristic over resolved input + instruction file sizes + an output allowance, × a pessimistic buffer (default 1.3), all named constants.
  - **BudgetManager / TokenMeter**: a pluggable ABC + default impl, injected into the `Orchestrator` like executor/store. Enforces total + rate-window limits with an injected `clock`; computes "next available" for each trigger; persists counters to `RunState`.
  - **Actuals capture**: `ClaudeCliExecutor` runs `claude` with `--output-format json`, parses the `usage` block (input/output/cache tokens); `TaskResult` gains token fields; 429 / retry-after detection surfaces a structured signal. Fallback to estimate when actuals are unavailable.
  - **Engine integration**: pre-run **gate** (admit/block) before each task; post-run **reconcile** (replace estimate with actuals); **stop vs wait** handling using the injected `sleeper` + `clock`; resume continues accounting without double-charging.
  - **CLI flags**, structured **budget events**, and a **BudgetExhausted/RateLimited** error type.
- Scope Out (non-MVP / explicitly excluded):
  - Cost-in-currency budgeting (USD) and per-model price tables — tokens only this epic (estimator interface leaves room).
  - Cross-run / global (multi-run) budgets or a shared org-wide quota store — scope is **per-run**, persisted to `RunState`.
  - Distributed/concurrent rate coordination across parallel workers (engine stays linear topo iteration; single-process counters).
  - Mid-task streaming token metering / partial-task pre-emption — gate is per-task admission, not intra-task.
  - Token *reduction* strategies (prompt compression, model downgrade) — this epic measures + limits, it does not optimize.
  - Sliding-window sub-second precision beyond the configured window granularity (minute / 10-min / hour buckets only).

## MVP vs Non-MVP split
- **MVP (this epic, all FRs below)**: config+schema, estimator (chars/4 + buffer), BudgetManager total + rate enforcement with injected clock, actuals capture from CLI JSON + 429 detection, engine gate/reconcile/stop/wait/resume, CLI flags, full test plan, docs.
- **Non-MVP (follow-on epics, captured as OPEN_QUESTION / Scope Out)**: USD cost budgeting, multi-run/global quotas, per-model price tables, parallel-worker rate coordination, token-reduction strategies.

## Requirements

### Functional
- FR-1: A workflow spec can declare a `budget` block with a **total token cap**; the engine refuses to admit a task whose pre-run estimate would push cumulative consumed-or-estimated tokens past the cap.
- FR-2: A workflow spec can declare a **rate limit** as `{ tokens, window }` where `window ∈ {minute, ten_minutes, hour}` (or an explicit seconds value); the engine refuses to admit a task whose estimate would exceed the tokens allotted to the current window.
- FR-3: Both limits (FR-1 total + FR-2 rate) are enforced **simultaneously and independently**; a task is admitted only if it passes both. Precedence on block: report the **first** limit that blocks (total checked before rate), but both reasons are computed and logged.
- FR-4: **Hybrid accounting** — before a task runs, charge a **pessimistic estimate** (`(chars/4 over resolved inputs + instruction file bytes) + output_allowance`, all × `pessimism_buffer`, default 1.3); after the task runs, **reconcile**: subtract the estimate and add the **actual** token count. Re-running/resuming a completed task must **not double-charge**.
- FR-5: Actual token counts come from the `claude` CLI invoked with `--output-format json`; the executor parses the `usage` block (`input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`). When actuals are unavailable (FakeExecutor, non-JSON output, timeout, parse failure), the estimate is **kept as the charge** (documented fallback).
- FR-6: When a limit is hit (our configured total/rate OR a provider 429), the engine **detects** it and **computes when the next quota becomes available** (total: never within this run → stop; rate window: the window reset instant; provider 429: the `retry-after` / reset time the CLI surfaces).
- FR-7: A configurable `on_exhaustion` flag selects **stop** (default) or **wait**. `stop` ends the run cleanly with a `BudgetExhausted`/`RateLimited` terminal state and a resumable `RunState`. `wait` sleeps (via the injected `sleeper`) until the computed next-available instant, then continues from the same task.
- FR-8: **Provider 429 in scope** — the executor detects the provider's real rate-limit signal (HTTP 429 / retry-after header / rate-limit error text surfaced by the `claude` CLI) and reports its reset time; this is a **separate trigger** from our configured limit but feeds the **same** stop/wait handler.
- FR-9: Budget consumption + rate-window counters are **persisted to `RunState`** via `RunStateStore` so a resumed run continues accounting correctly; rate windows are tracked **per-run**.
- FR-10: Budget config is overridable from the **CLI** (e.g. `--budget-total`, `--rate-tokens`, `--rate-window`, `--on-exhaustion`), with precedence CLI > spec `budget` block > unset (no limit).
- FR-11: Structured budget **events** are emitted via the existing per-run logger: `budget.charge`, `budget.reconcile`, `budget.gate_block`, `budget.exhausted`, `budget.wait`, `budget.resume`, `budget.provider_429`.

### Non-Functional
- NFR-1 (preserved): The engine never reads payload artifact contents. The estimator needs **file sizes** (`os.stat`/`store.size()`), not contents — adding a `size(path)` to the artifact store is **not** a content read. No estimator or meter reads artifact bytes.
- NFR-2: Deterministic — rate-window math, "next available" computation, and wait use an **injected clock + injected sleeper** so tests assert exact instants with a fixed clock (CLAUDE.md mandates fixed clocks/seeds).
- NFR-3: Idempotent / resumable — re-running or resuming must not double-charge already-completed tasks; counters live in `RunState` and a reconciled task records its final charge so replay skips it.
- NFR-4: No new mandatory third-party dependency (stdlib + pydantic v2; estimator uses `len()`/`os.stat`; JSON parse via stdlib `json`).
- NFR-5: All new spec fields appear in `specs/workflow.schema.json` (which is `additionalProperties: false`); the `budget` block round-trips through load → validate → re-serialize.
- NFR-6: **Pluggable** — `BudgetManager` and `TokenEstimator` sit behind ABCs and are injected into the `Orchestrator` exactly like `Executor`/`ArtifactStore`/`RunStateStore`, so each is unit-testable in isolation and swappable.
- NFR-7: No magic literals — `1.3` (pessimism buffer), `4` (chars-per-token), the default output allowance, and window-to-seconds mappings are **named, configurable constants**.

## Locked design decisions (from the user — honored)
- **Accounting = HYBRID**: pre-run estimate (chars/4 of resolved inputs + instruction bytes + output allowance, × 1.3) gates admission; reconcile against CLI `--output-format json` `usage` actuals after the task; fall back to estimate when actuals unavailable.
- **Scope = PER-RUN, persisted to RunState**: consumed total + rate-window counters live in `RunState`, persisted by `RunStateStore`; rate windows per-run.
- **Provider 429 = IN SCOPE (Both)**: detect provider 429 / retry-after in addition to our configured limits; two separate triggers feed one stop/wait handler.
- **Default action = STOP on exhaustion**; wait is opt-in.
- **Config location**: a new top-level `budget` block in the **workflow spec** (sibling of `defaults`), overridable by CLI flags. (Decision ADR-BUD-001 in the HLD.)

## Interface Contracts (frozen surfaces — see HLD §LLD for full pseudocode)
```python
# models.py — new config + counter models (pydantic v2)
class RateLimit(BaseModel):
    tokens: int                       # tokens allotted per window
    window: Literal["minute","ten_minutes","hour"] | None = None
    window_seconds: int | None = None # explicit override; exactly one of window/window_seconds

class EstimatorConfig(BaseModel):
    chars_per_token: int = 4
    pessimism_buffer: float = 1.3
    output_allowance_tokens: int = 1000

class BudgetSpec(BaseModel):
    total_tokens: int | None = None             # None => no total cap
    rate: RateLimit | None = None               # None => no rate cap
    on_exhaustion: Literal["stop","wait"] = "stop"
    estimator: EstimatorConfig = EstimatorConfig()

class BudgetCounters(BaseModel):              # persisted inside RunState (FR-9, NFR-3)
    consumed_tokens: int = 0                   # reconciled total (actuals + fallbacks)
    window_start_epoch: float | None = None    # current rate-window start (clock epoch secs)
    window_consumed_tokens: int = 0
    charged_estimate: dict[str, int] = {}      # task_id -> estimate currently charged (un-reconciled)
    reconciled_tasks: list[str] = []           # task_ids whose actuals are final (no double-charge)

# TaskResult — new token fields (FR-5)
class TaskResult(BaseModel):
    ...
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    actuals_available: bool = False            # False => engine keeps the estimate (fallback)
    provider_rate_limited: bool = False        # 429 detected (FR-8)
    provider_retry_after_epoch: float | None = None  # reset instant the CLI surfaced

# estimator.py
class TokenEstimator(ABC):
    @abstractmethod
    def estimate(self, ctx: TaskContext, cfg: EstimatorConfig) -> int: ...
class HeuristicTokenEstimator(TokenEstimator): ...   # default chars/4 + buffer

# budget.py
class BudgetDecision(BaseModel):
    admit: bool
    blocked_by: Literal["total","rate"] | None = None
    next_available_epoch: float | None = None   # when the blocking quota frees (None for total)

class BudgetManager(ABC):
    @abstractmethod
    def gate(self, task_id: str, estimate: int, counters: BudgetCounters) -> BudgetDecision: ...
    @abstractmethod
    def charge_estimate(self, task_id: str, estimate: int, counters: BudgetCounters) -> None: ...
    @abstractmethod
    def reconcile(self, task_id: str, actual: int, counters: BudgetCounters) -> None: ...
    @abstractmethod
    def on_provider_429(self, retry_after_epoch: float | None, counters: BudgetCounters) -> BudgetDecision: ...
class DefaultBudgetManager(BudgetManager):
    def __init__(self, spec: BudgetSpec, clock: Callable[[], datetime]): ...

# errors.py
class BudgetExhausted(OrchestratorError): ...   # total cap hit; carries blocked_by, next_available
class RateLimited(OrchestratorError): ...       # rate window or provider 429; carries next_available_epoch
```

## Control / signal surfaces (no new artifact-content reads — NFR-1 preserved)
| Surface | Producer | Consumer | Notes |
|--------|----------|----------|-------|
| `claude --output-format json` `usage` block | provider CLI (stdout) | `ClaudeCliExecutor` | parsed by executor only; engine reads `TaskResult` fields, not stdout |
| 429 / retry-after | provider CLI (stderr / exit / JSON error) | `ClaudeCliExecutor` | surfaced as `TaskResult.provider_rate_limited` + reset epoch |
| `BudgetCounters` | `BudgetManager` | persisted in `RunState` via `RunStateStore` | per-run, drives resume continuance |
| file **sizes** for estimate | `ArtifactStore.size(path)` (new) | `HeuristicTokenEstimator` | `os.stat` size, **not** content (NFR-1 safe) |

## Task List
- [x] `T-oh5gl5-budget-config-schema` — Budget config models (`BudgetSpec`/`RateLimit`/`EstimatorConfig`/`BudgetCounters`) + `workflow.schema.json` `budget` block + cross-validation + `RunState` counter field. **Done 2026-06-18**
- [x] `T-n7hmwj-token-estimator` — `TokenEstimator` ABC + `HeuristicTokenEstimator` (chars/4 + buffer + output allowance) + `ArtifactStore.size()`. **Done 2026-06-18**
- [x] `T-7kp8iv-budget-manager-meter` — `BudgetManager` ABC + `DefaultBudgetManager`: total + rate-window enforcement, next-available computation, provider-429 handler, injected clock. **Done 2026-06-18**
- [x] `T-1m9744-executor-actuals-429` — `claude_cli` `--output-format json` + `usage` parse + `TaskResult` token fields + 429/retry-after detection; FakeExecutor injected actuals. **Done 2026-06-18**
- [x] `T-algywf-engine-budget-integration` — Orchestrator gate + reconcile + stop/wait (injected sleeper/clock) + resume continuance + budget events + `BudgetExhausted`/`RateLimited` errors. **Done 2026-06-18**
- [x] `T-xefapr-cli-budget-flags` — `ao run`/`ao resume` budget flags with CLI > spec precedence; wire `BudgetManager`/estimator into the Orchestrator construction. **Done 2026-06-18**
- [x] `T-67kiia-tests-budget` — Unit (estimator, total+rate windows, fixed-clock wait, 429 parse, double-charge guard) + integration (engine stops; engine waits then continues; resume continuance) with FakeExecutor + fixed clock/sleeper; coverage gate. **Done 2026-06-18**
- [x] `T-5igs6g-docs-refresh-budget` — Reconcile `docs-md/token-budgeting-hld.md` + index with shipped behavior; resolve open questions; add ADRs. **Done 2026-06-18**

## Dependency Order
```
T-oh5gl5 (config + schema + RunState counters)
   ├─> T-n7hmwj (estimator; needs EstimatorConfig + store.size)
   └─> T-7kp8iv (BudgetManager; needs BudgetSpec + BudgetCounters)
T-1m9744 (executor actuals + 429; needs TaskResult token fields from T-oh5gl5)
        │
T-n7hmwj + T-7kp8iv + T-1m9744 ──> T-algywf (engine: gate/reconcile/stop/wait/resume)
                                        └─> T-xefapr (CLI flags + wiring)
all implementation tasks ───────────────> T-67kiia (tests + integration)
T-67kiia (green) ───────────────────────> T-5igs6g (docs-refresh)
```
- `T-n7hmwj`, `T-7kp8iv`, `T-1m9744` can proceed in parallel after `T-oh5gl5` (they share only the models from `T-oh5gl5`).
- `T-algywf` is the integration join point; `T-xefapr` follows it.
- `T-67kiia` covers all features; `T-5igs6g` closes the epic.

## Risks and Dependencies
- R1: **Estimate accuracy** — chars/4 + 1.3 can badly mis-predict (cache reads, large outputs), causing premature blocks or overshoot. Mitigation: hybrid reconcile corrects cumulative drift after each task; buffer + output allowance are configurable; log estimate-vs-actual deltas for tuning.
- R2: **Double-charge on resume** — the gate charges an estimate, the run dies before reconcile, resume re-runs the task. Mitigation: `BudgetCounters.charged_estimate[task_id]` + `reconciled_tasks`; on resume, a task that was charged-but-not-reconciled has its stale estimate **reversed** before re-gating (idempotency invariant, NFR-3).
- R3: **Window-boundary races / clock skew** — rate-window math must be reproducible. Mitigation: injected clock everywhere; window keyed by `window_start_epoch` recorded in `RunState`; explicit window-roll logic with unit tests at boundaries.
- R4: **Provider 429 format drift** — the `claude` CLI's 429 / retry-after surface may vary by version. Mitigation: isolate parsing in one executor function with documented fallbacks (no reset time → use a configurable default backoff); OPEN_QUESTION below.
- R5: **NFR-1 erosion** — estimator must not read artifact content. Mitigation: estimator uses `store.size()` (stat only); epic-close grep audit confirms no new `.read_text()` on artifact paths outside the existing control allow-list.
- R6: **wait blocking the engine indefinitely** — a `wait` that sleeps for a very long provider reset could hang an unattended run. Mitigation: `cancel_fn` is checked around/after the wait; document a max-wait OPEN_QUESTION.

## Assumptions (ASSUMPTION log)
- ASSUMPTION: `claude --output-format json` emits a `usage` object with `input_tokens`/`output_tokens` (and cache fields). Risk: field names/shape differ across CLI versions → actuals silently unparsed. Mitigation: tolerant parser + fallback-to-estimate + unit tests with recorded fixtures; verify against the installed CLI in `T-1m9744`.
- ASSUMPTION: provider 429 reaches us via the `claude` CLI exit/stderr/JSON-error (we do not call the HTTP API directly). Risk: CLI swallows/retries 429 internally. Mitigation: detect both an explicit 429 signal and a generic rate-limit error string; if neither surfaces, only our configured limits apply.
- ASSUMPTION: cache tokens count toward consumption for budgeting. Risk: over-counting cache reads. Mitigation: `EstimatorConfig`/manager treat `consumed = input + output + cache_creation + cache_read` by default; an OPEN_QUESTION flags whether cache_read should be discounted.

## Open Questions (OPEN_QUESTION)
- OPEN_QUESTION: Exact `usage` JSON shape from the installed `claude` CLI version — confirm field names in `T-1m9744` against a live `--output-format json` run before freezing the parser.
- OPEN_QUESTION: Should `cache_read_input_tokens` be discounted (e.g. ×0.1) when charging consumption? Default: count fully; revisit with real data.
- OPEN_QUESTION: Max wait cap for `on_exhaustion=wait` (avoid an unbounded sleep on a long provider reset). Proposal: optional `max_wait_seconds` in `BudgetSpec`; default unbounded but `cancel_fn`-interruptible.
- OPEN_QUESTION: Rate-window model — fixed tumbling windows (simpler, chosen default) vs sliding window. MVP ships tumbling (keyed by `window_start_epoch`); sliding is Scope Out.

## Test Strategy (summary; full plan in T-67kiia + HLD §Test)
- Unit: estimator (chars/4 + buffer + output allowance, zero-size files, missing-input handling), `DefaultBudgetManager` total-cap block, rate-window block, window roll-over at fixed-clock boundaries, next-available math, provider-429 handler, double-charge guard (charge→reconcile, charge→reverse-on-resume).
- Integration (FakeExecutor + injected token outputs + fixed clock/sleeper): engine **stops** at total cap; engine **stops** at rate cap; engine **waits** then continues across a window roll; provider-429 **wait** then continue; **resume continuance** (a stopped run resumes and finishes with correct cumulative tokens and no double-charge).
- Gates: `ruff`/`mypy`/`pytest` green; schema round-trip for the `budget` block; ≥80% coverage on new modules; NFR-1 grep audit.

## Links
- Design doc (HLD + LLD): `docs-md/token-budgeting-hld.md`
- Landscape note: `output/E-j4gno6-token-budget-rate-limit/landscape-note.md`
- Sprint plan: this EPIC + per-task `TASK.md` files under `ad/tickets/E-j4gno6-token-budget-rate-limit/`
- Output artifacts: `output/E-j4gno6-token-budget-rate-limit/` (code lands in `src/agent_orchestrator/`)
