# Epic: E-XyfjuZ-agent-monitoring-self-healing

## Metadata
- Epic ID: `E-XyfjuZ-agent-monitoring-self-healing`
- Title: Agent-based workflow monitoring & self-healing (guardrail modes)
- Owner: dev-epic agent
- Created: 2026-07-14
- Last Updated: 2026-07-15
- Status: Done
- Origin ask: [`meta/prompts/prompt.md`](../../meta/prompts/prompt.md) "Current Ask" section — already vetted as worthwhile by the main thread; worth is not re-litigated here.
- Foundation / prior art: [`E-rc7k2v-run-control-routing-breakers`](../../meta/tickets/E-rc7k2v-run-control-routing-breakers/EPIC.md) (breaker framework, Done) and [`E-3JTmVu-breaker-resume-extend`](../../meta/tickets/E-3JTmVu-breaker-resume-extend/EPIC.md) (`apply_breaker_extension`, `breaker_overrides`, Done) — this epic is an additive follow-on that REUSES both mechanisms; neither prior epic is reopened or modified beyond what this epic's own scope requires.

## Goal
Add agent-based monitoring + self-healing for workflow runs, driven entirely by the existing
`ao run` / `ao resume` commands (no new subcommand). The monitor supervises at a shallow level
(compact, path/id/count summaries only — never transcripts or artifact payload content) and can
heal two distinct classes of run friction:

1. **Recommend-mode circuit breaker trips** — when a workflow author has explicitly marked a
   breaker `mode: "recommend"` (opt-in per breaker; default remains `"hard"` = today's behavior,
   byte-identical), the monitor may extend the breaker's threshold once (bounded) and let the run
   continue, or halt.
2. **Task-failure self-healing** — when a task exhausts its `RetryPolicy` and settles `"failed"`
   (opt-in via `monitoring.self_heal: true`, default `false`), the monitor may request one bounded
   extra retry (e.g. for a transient network/API error) or accept the failure (today's behavior).

**Hard limits are never consultable or extendable** — the three built-in re-framed stops
(`BUILTIN_QUOTA_MAX_WAIT`, `BUILTIN_BUDGET_EXHAUSTED`, `BUILTIN_BUDGET_UNSATISFIABLE`) and any
provider-429 stop always hard-halt exactly as today; this is enforced structurally (they never
flow through the new consult path at all — see Design Decision D1) plus a defensive assertion.

## Acceptance Criteria (testable)
1. `CircuitBreakerSpec.mode: Literal["hard","recommend"] = "hard"` — an unset/`"hard"` breaker
   produces byte-identical behavior to pre-epic code (proven by re-running
   `tests/test_stop_reframe_parity.py` + `tests/test_resume_replay.py` unmodified, green).
2. A workflow with ONLY `mode: "recommend"` breakers tripping at one boundary consults the
   Monitor per breaker in declared order; `extend` (from every consulted breaker) applies
   `apply_breaker_extension` and the run continues; any `halt` (explicit, or bound/cap exhausted)
   halts exactly like today.
3. A boundary where ANY tripped breaker is `mode: "hard"` (mixed with recommend or not) halts
   without ever calling the Monitor (proven by a monitor double that raises if invoked).
4. `max_extensions_per_breaker` (default 1) is enforced in engine code, not by monitor
   self-restraint — a monitor double that always answers `extend` still gets forced to `halt`
   once the bound is hit.
5. Task-failure self-heal is OFF by default; enabling `monitoring.self_heal: true` (or
   `--self-heal` / `AO_SELF_HEAL`) lets a `"failed"`-after-retries task be re-queued once
   (bounded by `max_heal_retries_per_task`, default 1) when the Monitor says `retry`; heal
   retries do not decrement `RetryPolicy.max_attempts` accounting.
6. `RuleBasedMonitor` (default) is deterministic and zero-cost: extend-once-then-halt for
   breakers; retry-once-if-transient-pattern-else-accept_failure for task failures.
7. `AgentMonitor` invokes a named `agents.json` agent through the existing `Executor`/
   `TaskContext` machinery, expects a strict JSON verdict file, and falls back to the safe
   default (halt / accept_failure) on ANY error, timeout, non-`succeeded` executor result, or
   verdict that fails strict validation.
8. All new `RunState` fields (`monitor_decisions`, `monitor_breaker_extensions`,
   `monitor_heal_retries`, `monitor_calls_made`) default such that an old `state.json` (predating
   this epic) loads and resumes without modification.
9. `evaluate_breakers`'s byte-identical no-op guarantee (no `clock()` call, no behavior change)//
   is preserved for workflows with no `circuit_breakers` — proven by the existing stepping-clock
   tests staying green, unmodified.
10. `uv run pytest -q` stays fully green; exact before/after counts recorded in Evidence Log;
    `ruff check .` / `ruff format --check .` / `uv run mypy src` introduce zero new findings.

## Design Decisions (locked before implementation — early-gate review targets these)

### D1 — Built-in hard stops structurally never reach the consult path
Traced every one of the three re-framed builtin stop sites in `engine.py` (budget-gate
unsatisfiable/exhausted ~L340-407, quota max-wait ~L515-546, provider-429 stop ~L639-665): each
calls `record_trip()` directly and does `state.status="failed"; failed=True; break` **before**
the loop iteration ever reaches the `evaluate_breakers(...)` call (~L781, further down, after
task-outcome handling). Consult Point A only ever triggers off breakers newly appearing in
`state.tripped_breakers` **after an `evaluate_breakers()` call**, and only for ids present in
`workflow.circuit_breakers` (never a bare `builtin.*` id — which the id pattern
`^[a-z0-9][a-z0-9-_]*$` cannot even represent, since it disallows the `.` in `builtin.xxx`).
Net: built-ins cannot reach the consult path by construction. A defensive `assert` inside the new
consult helper additionally guards against a future refactor accidentally wiring a hard breaker in
(belt-and-suspenders per the brief's "enforce in code, not convention").

### D2 — `evaluate_breakers()` signature/behavior is 100% unchanged
Determining "which specs newly tripped this boundary" (needed to decide hard-vs-recommend) is
done at the **call site** in `engine.py` by diffing `state.tripped_breakers` ids before/after the
existing call, then mapping surviving new ids back to `workflow.circuit_breakers` (preserving
declared order). This avoids touching `evaluate_breakers`'s return type or internals at all —
preserving AC9 and every existing test that calls `evaluate_breakers(...)` directly and asserts on
its `Action | None` return value (`tests/test_breaker_extension.py`,
`tests/test_mvp_breaker_conditions.py`, `tests/test_engine_breakers.py`).

### D3 — Consult Point A resolution is all-or-nothing per boundary
When N recommend-mode breakers trip at the same boundary, each is consulted (bound/cap-exhausted
ones are auto-`halt` without a monitor call); if **every** decision is `extend`, all extensions are
applied and the run continues; if **any** decision is `halt`, **no** extension is applied and the
run halts exactly like today. Rationale: avoids a "half-extended" state that would be confusing to
audit and biases toward safety (any dissent stops the run).

### D4 — Consult Point B fires before any of the failed attempt's bookkeeping is written
Placed immediately after the existing budget-reconcile/429 block (~engine.py L687) and **before**
`ts.attempts = result.attempts` / `ts.ended_at = ...` (~L689-690) — mirroring exactly where the
quota-exhaustion and provider-429 routes already short-circuit on `result.*` fields before touching
`TaskRunState`. A successfully healed task's transient failure therefore never sets
`ts.status="failed"`, never calls `evaluate_breakers` for that attempt, and never touches
`ts.started_at` (single-writer guard, E-3JTmVu, already in place and untouched) — `run_active_seconds`
correctly counts the heal wait as in-task time, and `task_failures`/`consecutive_failures` breakers
never see a failure that was transparently healed. Scoped strictly to `result.status == "failed"`
(not `timed_out`/`cancelled` — a hung task or an explicit cancel is not "self-healed" in MVP; this
is a deliberate, documented boundary).

### D5 — Compact failure summary reuses existing `TaskResult` fields; one bounded, guarded extra read
No new field exists on `TaskResult` for "terminal_reason"/"errors" — reusing `result.error` (already
an executor-produced, ≤500-char bounded string) as both, rather than inventing new engine-side
`result.json` re-parsing. The brief's explicit "stderr tail ≤2000 chars" requirement is met by ONE
additional bounded, `try/except`-guarded read of `stderr.txt` from `result.output_artifact_path`
(never full `transcript.jsonl`, never artifact/instruction content) — this is the single new file
read this epic introduces into the engine's vicinity, scoped to `monitoring.py`, capped, and
non-fatal on any I/O error (falls back to `""`). Flagged explicitly for early-gate review as the one
NFR-1-adjacent judgment call in this design.

### D6 — Monitor instruction templates are baked Python string constants, not repo files
`AgentMonitor` writes its own instruction file (baked constant text) alongside the generated
context JSON into `.orchestrator/runs/<run_id>/monitor/call-<n>-<subject>/` every consult — never
depends on a `specs/examples/instructions/*.md` path resolving under an arbitrary `workspace_root`.
Reason: `pyproject.toml`'s wheel packaging (`packages = ["src/agent_orchestrator"]`) does **not**
ship `specs/` — a repo-relative instruction path would silently break for any `uv tool install`ed
`ao` binary the moment `workspace_root` isn't the repo itself. This mirrors the existing
context/verdict file convention (also run-scoped, always resolvable).

### D7 — `self_heal` CLI/env precedence is monotonic-enable, not full 3-layer override
`--self-heal` / `AO_SELF_HEAL` / config `monitoring.self_heal: true` each independently can turn
self-heal ON; there is no CLI escape hatch to force it OFF when config enables it (config editing
covers that case). Chosen to avoid exploding flag count for a rarely-adjusted, inherently-safe
(bounded to 1 extra retry by default) opt-in — explicitly flagged as a scope simplification versus
the full CLI>env>config>default chain used for `model`/`effort`/etc. (ADR-0003 style). The
`monitor` (which Monitor impl), bounds, and `transient_patterns` are config-file-only (no CLI/env)
per the brief's "don't explode flag count."

### D8 — `max_monitor_calls_per_run` is shared across both consult points, enforced centrally
One counter (`RunState.monitor_calls_made`), checked in engine code (never inside a `Monitor`
implementation) before every actual monitor invocation, for both breaker-trip and task-failure
consults. Cap-exhausted is treated identically to "monitor error/timeout" (decision 4 of the
brief) — falls back to the safe default, logs a distinct `monitor.cap_exceeded` event (not
`monitor.consult`/`monitor.decision`, since no real consult happened).

## Requirements (MVP / Non-MVP / Stretch, Functional / Non-functional)

### MVP — Functional
| ID | Requirement | Acceptance criteria | Verification |
|---|---|---|---|
| FR-1 | `CircuitBreakerSpec.mode: "hard"\|"recommend"` (default `"hard"`) + schema enum | AC1 | `tests/test_routing_breaker_models.py` additions, schema round-trip test |
| FR-2 | Consult Point A: breaker-trip guardrail consult at the `evaluate_breakers` boundary | AC2, AC3, AC4 | `tests/test_monitoring_breaker_consult.py` (new) |
| FR-3 | Consult Point B: opt-in task-failure self-heal | AC5 | `tests/test_monitoring_self_heal.py` (new) |
| FR-4 | `Monitor` ABC + `RuleBasedMonitor` + `AgentMonitor` | AC6, AC7 | `tests/test_monitoring.py` (new) |
| FR-5 | `monitoring:` config section + minimal CLI/env knob | AC5 | `tests/test_project_config.py` additions, CliRunner e2e |
| FR-6 | Observability: `monitor.consult`/`monitor.decision`/`monitor.cap_exceeded` events + `RunState.monitor_decisions` + counters | AC8 | log-capture assertions in engine integration tests |
| FR-7 | Validation: schema/model `mode` enum kept in sync (derived, not hand-copied) | — | schema-vs-Literal introspection test |
| FR-8 | No new CLI subcommand; works within `ao run`/`ao resume` | — | CliRunner e2e tests use existing `run`/`resume` only |

### MVP — Non-functional
| ID | Requirement | Verification |
|---|---|---|
| NFR-1 | Hard limits/builtins never extendable/consultable (D1) | dedicated test: mixed hard+recommend trip never invokes a monitor double |
| NFR-2 | Monitor failure/timeout/invalid verdict/cap-exhaustion never regresses run safety vs. today | invalid-verdict + cap-exhausted tests assert fallback equals pre-epic behavior |
| NFR-3 | Determinism: fixed/stepping clocks, injected sleeper, `FakeExecutor`, no real `claude` in required tests | all new tests follow this; optional `real_llm` marker stays opt-in |
| NFR-4 | Backward-compat: every new persisted field defaulted | old-fixture resume test (mirrors E-3JTmVu's own NFR-5 test style) |
| NFR-5 | `evaluate_breakers` byte-identical no-op guarantee preserved (AC9) | existing stepping-clock tests re-run unmodified |
| NFR-6 | Narrow change scope: `models.py`, `breakers.py` (reuse only, no signature change), `spec.py`, `project_config.py`, `cli.py`, new `monitoring.py`, `engine.py` (two additive hook sites) | diff review; no `dag.py`/`runstate.py`/`artifacts.py`/`budget.py` changes |
| NFR-7 | Compact summaries bounded, never transcripts/payload (D5) | unit test asserting summary excludes transcript/artifact content, stderr capped at 2000 chars |

### Non-MVP (deferred — explicitly out of scope this epic)
- Periodic/mid-task "pulse" monitoring (only task/breaker **boundary** hooks are in scope).
- Cross-run learning / persisted monitor memory across separate runs.
- Spec- or source-code-mutation healing (monitor editing `workflow.json` or repo source).
- Notifications (Slack/email/webhook) on monitor decisions.
- Self-healing for `timed_out`/`cancelled` task statuses (D4 — `"failed"` only).
- Any change to `ao resume --extend-breaker`'s existing (operator-driven, unbounded) mechanism.
- **AgentMonitor cost/budget integration** (early-gate architect finding): a real agent-based
  monitor invocation's token/dollar cost is not gated by the run's token budget nor counted in
  `run_cost_usd`/`budget_counters` — `AgentMonitor` calls `executor.execute()` out-of-band
  relative to `Orchestrator._run_with_retries`'s budget wrapper. Mitigated in MVP by
  `max_monitor_calls_per_run` (bounds invocation count, not spend); full integration would require
  threading the budget manager into the consult path, a larger cross-cutting change deferred to a
  follow-on epic if real usage shows it's needed.

### Stretch (nice-to-have, not required to ship)
- `real_llm`-marked opt-in test actually spawning `claude` for `AgentMonitor` (AO_E2E_REAL_LLM=1).
- `ao status` trailer line summarizing monitor decisions (mirrors the existing tripped-breakers trailer).

## Traceability (requirement → task)
| Requirement | Task |
|---|---|
| FR-1, FR-7 | `T-mYMiPK-guardrail-mode-schema-model` |
| FR-4, NFR-3, NFR-7 | `T-h2XLxe-monitor-abstraction` |
| FR-2, FR-6 (breaker side), NFR-1, NFR-5 | `T-TdildW-breaker-consult-engine` |
| FR-3, FR-6 (heal side) | `T-yjtdAq-self-heal-engine` |
| FR-5, FR-8 | `T-QyNnf5-config-cli-wiring` |
| All FR/NFR (verification) | `T-Wx8vUq-tests-e2e-monitoring` |
| Docs/ADR/examples/learnings/gates | `T-H8Mmog-docs-adr-examples-review` |

## Decomposition (tasks)
| Task | Description | Est. |
|------|-------------|------|
| `T-mYMiPK-guardrail-mode-schema-model` | `CircuitBreakerSpec.mode` + schema enum + schema/Literal sync test | 0.5 day |
| `T-h2XLxe-monitor-abstraction` | New `monitoring.py`: `Monitor` ABC, `RuleBasedMonitor`, `AgentMonitor`, DTOs, baked instruction templates | 1.5 day |
| `T-TdildW-breaker-consult-engine` | `Orchestrator` DI params, Consult Point A wiring, `RunState` fields, events | 1 day |
| `T-yjtdAq-self-heal-engine` | Consult Point B wiring, failure-summary builder (bounded stderr read), events | 1 day |
| `T-QyNnf5-config-cli-wiring` | `MonitoringConfig`, `_resolve_monitoring_settings`, `_build_monitor`, `run`/`resume` wiring | 0.5 day |
| `T-Wx8vUq-tests-e2e-monitoring` | Full test matrix (unit/integration/CliRunner) per Acceptance Criteria | 1.5 day |
| `T-H8Mmog-docs-adr-examples-review` | LLD doc, ADR-0004, doc cross-ref grep-fix, example spec, learnings, reviewer + tester late gate | 1 day |

Total ≈ 7 dev-days — single-sprint sized; sequenced Wave 1 (mYMiPK) → Wave 2 (h2XLxe) → Wave 3
(TdildW, yjtdAq parallel-safe, both depend on h2XLxe) → Wave 4 (QyNnf5) → Wave 5 (Wx8vUq) → Wave 6
(H8Mmog).

## Early gate (design review) — status: DONE, design revised
Ran `reviewer` and `architect` passes in parallel (design-level, before any code existed) on
Design Decisions D1-D8. Both returned **no showstoppers, proceed** — full findings below, with
each disposition (adopted / documented / rejected-with-reason) recorded.

### Reviewer findings
- **Warning 1 (adopted)**: `executors/claude_cli.py` already slices the tail of `stderr.txt`
  into `TaskResult.error` (<=500 chars) — a separate ≤2000-char re-read of the same file from
  `monitoring.py` is redundant, not just risky. **D5 revised**: `TaskFailureSummary.stderr_tail`
  now derives from `TaskResult.error` (re-capped at `STDERR_TAIL_MAX_CHARS`) — **zero new
  file-content reads anywhere in this epic**, strictly stronger NFR-1 compliance than originally
  planned. `monitoring.py`'s `_read_stderr_tail` function was removed entirely;
  `build_task_failure_summary` simplified accordingly. Tests updated
  (`tests/test_monitoring.py::TestBuildTaskFailureSummary`).
- **Warning 2 (documented, no code change)**: D4's heal-invisible-to-breakers tradeoff is
  intentional and sound (verified: `TaskFailuresBreaker`/`ConsecutiveFailuresBreaker` gate purely
  on `ts.status`, which a healed retry never sets to `"failed"`), but a systemic transient issue
  would heal every failure and never trip a count-based breaker. Will be called out explicitly as
  a documented tradeoff in the LLD/ADR (`T-H8Mmog`), not changed — self-heal is opt-in and bounded
  to 1 retry/task by default, so the blast radius of "invisible" healing is small.
- **Warning 3 (adopted, deferred to T-QyNnf5)**: D7's monotonic-enable-only precedence is
  asymmetric with every other setting's CLI>env>config>default chain (no way to force
  `self_heal` OFF via CLI/env when config enables it). **D7 revised**: `T-QyNnf5` will implement a
  proper tri-state `--self-heal/--no-self-heal` (`bool | None`, default `None`) CLI flag +
  tri-state `AO_SELF_HEAL` env var (unset/`1`/`0`), with CLI > env > config > default precedence
  in BOTH directions — not a monotonic OR.
- Notes: D1/D2/D3/D6/D8 all independently re-verified against the live code and confirmed sound.
  Suggested naming the 500/2000-char bounds as constants (already done:
  `STDERR_TAIL_MAX_CHARS`/`DEFAULT_MONITOR_TIMEOUT_SECONDS` in `monitoring.py`) and documenting
  whether the shared `max_monitor_calls_per_run` cap is intentional (yes — D8 already states this
  explicitly; reaffirmed).

### Architect findings
- **Warning 1 (confirmed already-satisfied by design, test obligation added)**: `AgentMonitor`
  must call `executor.execute()` out-of-band and never touch `state.tasks`/`injected_tasks` —
  verified `Executor.execute()` is fully decoupled from `RunState` (only `engine.py`'s own
  dispatch loop mutates `state.tasks`). `monitoring.py`'s `AgentMonitor`/`Monitor` ABC never
  receives or touches a `RunState` reference at all — structurally satisfied. Action: `T-Wx8vUq`
  must add an explicit engine-integration test proving a consult leaves `state.tasks`/
  `injected_tasks` byte-identical before/after.
- **Warning 2 (documented as a known MVP limitation)**: a real `AgentMonitor` invocation's
  token/dollar cost is NOT gated by the run's token budget nor counted in
  `run_cost_usd`/`budget_counters` (the call is out-of-band relative to
  `Orchestrator._run_with_retries`'s budget-gate/reconcile wrapper — the same decoupling that
  makes Warning 1 safe also causes this). Mitigated in MVP by `max_monitor_calls_per_run`
  (bounds invocation *count*, not spend). Explicitly added to the Non-MVP list below ("AgentMonitor
  cost/budget integration") rather than silently absorbed into MVP scope. Confirmed the "hang"
  concern does NOT apply: `AgentMonitor` makes exactly one `executor.execute()` call per consult,
  no internal retry/quota-wait loop, so a quota-exhausted/slow monitor call returns immediately
  (falls back to the safe default) — documented explicitly in `monitoring.py`'s module docstring.
- **Warning 3 (confirmed already-satisfied by planned engine design)**: no `self._runstate.save()`
  may happen between `evaluate_breakers()` recording a trip and the consult resolving (else a
  half-consulted state could hit disk). Re-confirmed the planned `T-TdildW` pseudocode already
  has exactly this property (the only `save()` calls are AFTER the consult outcome is known) —
  will add an explicit code comment + regression test when `T-TdildW` lands.
- **Note 4 (considered, REJECTED, reasoning recorded)**: unify `decide_breaker_trip`/
  `decide_task_failure` into one `consult(ConsultRequest) -> Verdict` method. Rejected: the two
  consult points have genuinely different decision vocabularies (extend/halt vs.
  retry/accept_failure) and different verdict payloads (`extend_by_seconds` vs. `wait_seconds`);
  unifying would require either a discriminated-union type (worse type safety, pushes the
  branching from "which method" to "which branch inside one method" — no net simplification) or a
  lowest-common-denominator shape (a dual-purpose nullable "amount" field is more confusing, not
  less). The DRY concern this note is really after (shared engine-side wrapper: cap-checking,
  logging, audit-recording) is addressed WITHOUT unifying the ABC — `T-TdildW`/`T-yjtdAq` will
  share a private bounds/cap-check helper at the engine layer. Kept the two-method ABC as already
  implemented and tested.
- **Note 5 (adopted — design improvement)**: derive `monitor_breaker_extensions`/
  `monitor_heal_retries`/`monitor_calls_made` from `RunState.monitor_decisions` (the audit list)
  rather than persisting them as three separate dict/int fields — matches this codebase's own
  "derive, don't duplicate bookkeeping" convention (`_consecutive_failure_streak`,
  `_settled_task_active_seconds`). **New Design Decision D9** (below) formalizes this: RunState
  gains exactly ONE new field (`monitor_decisions`), not four. Trivially cheap (list bounded by
  `max_monitor_calls_per_run`, default 10) and eliminates a whole class of counter/audit-log drift
  bug. Adopted for `T-TdildW`/`T-yjtdAq` (not yet implemented at review time, so zero rework cost).
- **Note 6 (documented)**: monitor-driven and operator-driven (`ao resume --extend-breaker`)
  extensions both write additively to the same shared `RunState.breaker_overrides` — intentional
  (single source of truth for the effective threshold regardless of who extended it), but means an
  operator's old→new confirmation line reflects a monitor-inflated baseline if a monitor already
  extended first. Will note this explicitly in the LLD. The `extend_by_seconds` field name (native
  unit varies by breaker condition) matches `apply_breaker_extension`'s own existing, documented
  parameter name — kept for consistency rather than renamed.
- **Note 7 (documented)**: D4's `timed_out`/`cancelled` exclusion should be surfaced loudly in
  user-facing docs since transient API stalls often surface AS timeouts — will make this explicit
  in the LLD (`T-H8Mmog`).

### D9 (new) — monitor bookkeeping is derived from `RunState.monitor_decisions`, never duplicated
`RunState` gains exactly one new field: `monitor_decisions: list[MonitorDecisionRecord] = []`
(persisted, NFR-5-defaulted). `monitor_breaker_extensions[breaker_id]`, `monitor_heal_retries
[task_id]`, and `monitor_calls_made` are NOT separate persisted fields — they are computed on
demand by pure helper functions in `models.py` (mirroring `compute_run_usage_totals`'s style):
counting matching entries in `monitor_decisions` by `consult_point`/`subject_id`/`decision`. This
supersedes the original plan (in D2/D3's pseudocode sketches) of three separate dict/int fields —
adopted from the architect's early-gate Note 5 before any RunState code was written, so this is a
net simplification with zero rework cost.

## Evidence Log

### 2026-07-14 — Iteration 1 (context read, baseline captured, design locked)
- Read (in full): `meta/prompts/prompt.md`, `docs-md/lld-run-control-routing-breakers.md`,
  `src/agent_orchestrator/breakers.py`, `src/agent_orchestrator/engine.py` (all 1375 lines),
  `docs-md/ai-epics/E-3JTmVu-breaker-resume-extend.md`, `meta/instructions/memories/README.md`,
  `meta/learning-compact.md`, `models.py`, `project_config.py`, `cli.py`, `spec.py`,
  `specs/workflow.schema.json`, `errors.py`, `runstate.py`, `executors/{base,fake,__init__}.py`,
  `executors/claude_cli.py`, `logging_setup.py`, `meta/tickets/README.md` + templates,
  `docs-md/adr/ADR-0003-settings-precedence-policy.md`, plus existing breaker test files
  (`test_breaker_extension.py`, `test_resume_extend_breaker_cli.py`,
  `test_mvp_breaker_conditions.py`) to confirm test-authoring conventions before writing new ones.
- Baseline captured (measured, not assumed): `uv run pytest -q` → **686 passed, 3 skipped** (4.85s
  fast run / 9.62s with coverage). Coverage: **TOTAL 91%** (2542 stmts, 217 missed) — matches the
  brief's stated baseline exactly. `uv run ruff check .` → **2 pre-existing errors**
  (`tests/test_e2e_cli.py:270`, unrelated F841). `uv run ruff format --check .` → clean (63 files).
  `uv run mypy src` → **4 pre-existing errors**, all `_version.py`.
- Locked design decisions D1-D8 above by tracing exact engine.py code paths (confirmed builtin
  stops `break` before ever reaching `evaluate_breakers` in the same iteration; confirmed the
  `builtin.*` id pattern cannot pass the existing `^[a-z0-9][a-z0-9-_]*$` schema pattern; confirmed
  `TaskResult` has no `terminal_reason` field; confirmed wheel packaging excludes `specs/`).
- Ticket tree created under `meta/tickets/E-XyfjuZ-agent-monitoring-self-healing/` (this epic + 7
  tasks), mirroring this document.

### 2026-07-15 — Iteration 2 (early gate run, design revised, T-mYMiPK + T-h2XLxe implemented)
- Spawned `reviewer` + `architect` subagents in parallel for the early-gate design review (before
  any code existed); both returned **no showstoppers, proceed** with actionable findings — see
  the "Early gate" section above for the full findings + dispositions (D5 revised/simplified, D7
  revised to a proper tri-state CLI override, D9 added, one alternative — unified `consult()`
  method — considered and rejected with recorded reasoning).
- `T-mYMiPK-guardrail-mode-schema-model`: implemented. `CircuitBreakerSpec.mode` +
  schema enum + 7 new tests (`tests/test_routing_breaker_models.py::TestGuardrailMode`,
  including a schema-vs-Pydantic-Literal drift guard). `uv run pytest -q` → 693 passed, 3 skipped
  (686 baseline + 7, zero regressions). Ruff/mypy clean on touched files.
  Ticket marked Done.
- `T-h2XLxe-monitor-abstraction`: implemented. New `src/agent_orchestrator/monitoring.py`
  (`Monitor` ABC, `RuleBasedMonitor`, `AgentMonitor`, DTOs, baked instruction templates,
  `SAFE_DEFAULT_BREAKER_VERDICT`/`SAFE_DEFAULT_HEAL_VERDICT`). Revised per the reviewer's D5
  finding mid-implementation (before tests were finalized) to eliminate the `stderr.txt` file
  read entirely. 42 new tests in `tests/test_monitoring.py` (module shape, `RuleBasedMonitor`
  breaker/failure policies incl. transient-pattern coverage, `AgentMonitor` via a local
  test-double `Executor` covering valid/invalid/missing-verdict/non-succeeded/raising-executor
  paths, `build_task_failure_summary`). `uv run pytest -q` → 735 passed, 3 skipped (693 + 42,
  zero regressions). Ruff/mypy clean.
- Full-suite baseline after both tasks: `uv run pytest -q` → **735 passed, 3 skipped** (baseline
  686 + 49 new tests, zero regressions, zero skips added).

### 2026-07-15 — Iteration 3 (T-TdildW/T-yjtdAq/T-QyNnf5/T-Wx8vUq/T-H8Mmog implemented, late gates run)
- `T-TdildW`, `T-yjtdAq`, `T-QyNnf5`, `T-Wx8vUq` all implemented and marked Done — see their own
  ticket STATUS.md files for per-task evidence. Full suite progression:
  740 (T-TdildW) → 762 (T-yjtdAq) → 778 (T-QyNnf5) → 783 (T-Wx8vUq's 2 coverage-gap-closing
  additions plus the load_workflow-layer mode-validation test).
- Coverage audit (T-Wx8vUq) found and closed 2 real gaps: `AgentMonitor.decide_task_failure`'s
  missing-verdict fallback had no direct test, and a cancel-during-self-heal-wait +
  `_consult_breaker_trips`'s hard-mode defense-in-depth assertion were both unreachable by the
  existing tests. `monitoring.py` now at 100% line coverage.
- Docs (T-H8Mmog): `docs-md/lld-agent-monitoring-self-healing.md` (full LLD),
  `docs-md/adr/ADR-0004-agent-monitoring-guardrail-modes.md`, cross-reference sweep across all 7
  `docs-md/*.md` files that mention circuit breakers/monitoring (only 2 needed additions — the
  top-level HLD's feature-doc index and a new §17 addendum in
  `lld-run-control-routing-breakers.md` explaining how the two extension mechanisms relate; no
  actively-contradicted claims found), `specs/examples/workflow-monitoring.json` (validated via
  `ao validate`), 3 new entries in `meta/learnings.md` + `meta/learning-compact.md`.
- **Late-gate `tester` run (independent, real CLI)**: verified end-to-end via `uv run ao resume`
  in a scratch workspace — (1) a `mode: "recommend"` `task_failures` breaker tripped, was
  consulted (`monitor.consult`/`monitor.decision` events, monitor="rules"), extended
  1.0→2.0 (`breaker.extend` event, `breaker_overrides={"fails-cap-a": 2.0}`,
  `tripped_breakers=[]` un-latched), and the run completed `status="succeeded"`; (2) the same
  breaker declared `mode: "hard"` tripped and halted immediately with **zero** `monitor.consult`
  events and `monitor_decisions=[]` — proving the byte-identical-by-default guarantee holds
  through the real CLI, not just unit tests; (3) `ao status` correctly surfaces run
  status/tasks/tripped-breakers. Full tester report on file; no discrepancies found.

### 2026-07-15 — Iteration 4 (late-gate reviewer findings incorporated, epic closed out)
- **Late-gate `reviewer` run (full diff)** returned: 1 Critical (empirically verified via a
  standalone repro script, not speculative), 2 Warnings, 3 Suggestions. Full report on file.
  Disposition:
  - **Critical #1 (FIXED)**: a self-heal retry silently discarded the pre-heal cycle's REAL
    cost/token actuals — `engine.py`'s settle-time `ts.cumulative_* = result.* or 0` used
    plain assignment, and the heal-retry branch `continue`d before ever reaching that
    assignment for the failed cycle, so only the LAST cycle's actuals ever survived. This
    silently weakens `TaskCostUsdBreaker`/`RunCostUsdBreaker` (E-9h3m7k) in exactly the
    recovery scenario self-heal is meant to cover, and reintroduces the exact bug class
    E-9h3m7k fixed for retries *within* one `_run_with_retries` call, one level up (across
    heal-triggered redispatches). Fixed: accumulate (`+=`) the about-to-be-discarded
    result's actuals into `ts.cumulative_*` in the heal-retry branch before the `continue`,
    and changed the final settle-time assignment from `=` to `+=` so it adds onto rather
    than clobbers any prior heal-cycle contribution (verified safe for every other caller:
    this line runs at most once per dispatch outside self-heal, and `prepare_resume` hands
    any re-dispatched task a fresh, zeroed `TaskRunState`). 2 new regression tests added to
    `tests/test_monitoring_self_heal.py`
    (`TestSelfHealAccumulatesActualsAcrossHealedCycles`): one asserting the summed
    cumulative fields across a failed-then-healed-then-succeeded cycle, one proving
    `TaskCostUsdBreaker` now correctly trips across a healed retry (the exact scenario the
    breaker's own docstring describes, just via a heal-triggered redispatch instead of an
    internal retry).
  - **Warning #1 (FIXED)**: `_resolve_run_settings`/`_resolve_monitoring_settings` had
    verbatim-duplicated project-config load-and-swallow logic. Extracted a shared
    `_load_project_config_or_none()` helper in `cli.py`; both resolvers now call it.
  - **Warning #2 (accepted as expected, not a defect)**: ticket/status rollup staleness was
    correctly identified as deliberately deferred (the epic doc's own "Next actions" said so
    at the time) — addressed now, in this same iteration, per the reviewer's own suggested
    next step.
  - **Suggestion #1 (FIXED, cheap)**: `_consult_breaker_trips`'s `all(...)` over an empty
    list would be vacuously `True` (wrongly returning `"extend"`) for a hypothetical future
    caller with an empty `newly_tripped_specs` — added an explicit `if not
    newly_tripped_specs: return "halt"` guard (currently unreachable via the sole existing
    call site, which only invokes with a non-empty list — documented as such, left
    uncovered by design rather than adding a test for genuinely dead code).
  - **Suggestion #2 (accepted, no action)**: minor harmless redundancy
    (`AgentMonitor._consult`'s `exists()` check before `read_control`) — not worth a change.
  - **Suggestion #3 (accepted, no action)**: the two consult-method shape duplication is
    ADR-0004 Decision 3's own deliberate, documented tradeoff — re-confirmed correct, no
    change.
- Post-fix verification: `uv run pytest -q --cov=agent_orchestrator --cov-report=term-missing`
  → **785 passed, 3 skipped**; coverage **TOTAL 92%** (2824 stmts, 217 missed) — `monitoring.py`
  still 100%. `uv run ruff check .` → 2 pre-existing errors (unchanged baseline). `uv run ruff
  format --check .` → 68 files clean. `uv run mypy src` → 4 pre-existing errors (unchanged
  baseline, `_version.py`). Zero regressions from the fixes.

## Risks & Blockers
- None. D5's original risk (bounded `stderr.txt` read) was fully resolved by the early-gate
  review (design is STRICTER on NFR-1 than originally planned). The late-gate reviewer's one
  Critical finding (cumulative-actuals discard across a healed retry) is fixed and regression-
  tested. Late-gate tester evidence confirms the shipped code matches the design end-to-end via
  the real CLI.

## Next actions
1. None — epic complete. All 7 tasks Done, early + late gates run with findings incorporated,
   full suite green, tickets synced. See the final completion handoff for exact artifact paths.
