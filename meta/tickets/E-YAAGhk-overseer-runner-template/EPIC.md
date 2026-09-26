# EPIC: E-YAAGhk-overseer-runner-template

## Metadata
- Epic ID: `E-YAAGhk-overseer-runner-template`
- Title: `overseer-runner` builtin template: recursive wave decomposition, periodic overseer checkpoints (alignment / loops / progress), and budget-staged graceful degradation to a usable deliverable
- Owner: architect (design) → dev-epic (decomposition + delivery)
- Created: 2026-09-26
- Last Updated: 2026-09-26
- Status: In Progress (design complete Rev 2; implementation started — T-pYt478 done, 14 tasks
  remain; execution log: `docs-md/ai-epics/overseer-runner-template.md`)

## Summary
- Goal: a generic, reusable `ao new overseer-runner` template. It takes one open-ended `prompt.md`
  (possibly several distinct asks), decomposes it at run time into bounded **waves** of emitted
  sub-DAGs, and closes every wave with an **overseer checkpoint**. The checkpoint checks alignment
  with the original ask, loops/cycles (A→B→B→A, A→B→C→D×2), and real progress versus rework, then
  emits the next wave, a stabilization wave, a human hold, or the close-out tail. At 80/90/95% of a
  fixed `run_budget_usd` (projected, latched) the overseer moves explore → converge → stabilize →
  closeout, so the deliverable ends **usable** (buildable/actionable, code or not) rather than
  half-finished. A hard `run_cost_usd` breaker at 100% is only the backstop.
- Scope In: the new builtin at `src/agent_orchestrator/templates/builtin/overseer-runner/`
  (`template.yaml`, `workflow.json.tmpl`, `overseer-contract.md.tmpl`, `overseer-config.json.tmpl`,
  `prompt.md.tmpl`, `tools/overseer_tool.py`, `instructions/*.md`, `README.md`); **one small engine
  correctness fix (G5, `engine.py::_settle` ordering)**; tests (tool unit, engine unit, template
  assets, CliRunner e2e); `docs-md/overseer-runner-hld.md`; ADR-0016; a post-implementation docs
  refresh.
- Scope Out (user decisions, not re-litigated): independent child `ao run`s; any new engine
  cadence/drain/pause primitive; Monitor ABC changes; mid-run grading (ADR-0015 D2 stands);
  `routed-runner` behavior changes (it benefits passively from G5); dashboard UI work; `E-Grpp0X`
  (the checker makes this template independent of it).

## Design
- HLD+LLD (sections 1–25): [`docs-md/overseer-runner-hld.md`](../../../docs-md/overseer-runner-hld.md)
- ADR: [`docs-md/adr/ADR-0016-overseer-runner-cadence-and-budget-governance.md`](../../../docs-md/adr/ADR-0016-overseer-runner-cadence-and-budget-governance.md)
- G5 reproduction: [`output/E-YAAGhk-overseer-runner-template/repro_emit_lost_on_breaker_trip.py`](../../../output/E-YAAGhk-overseer-runner-template/repro_emit_lost_on_breaker_trip.py)

## Requirements
Full table with verification methods: design doc §1.2. Summary:

### MVP
- FR-1 Builtin template renders and passes `ao validate` (`ao new overseer-runner`).
- FR-2 Intake → immutable, hash-locked charter (asks, acceptance criteria, per-ask usable bar); emits wave 1 + `ck-01`.
- FR-3 Wave/checkpoint recursion: each `ck-K` emits exactly one of {wave+`ck-(K+1)`, hold, close-out tail}, mechanically checked.
- FR-4 Cadence: wave size ≤ min(`wave_size`, time cap from `wave_max_minutes`, budget cap).
- FR-5 Engine-enforced per-unit breadcrumbs folded into an idempotent, tool-owned `ledger.jsonl`.
- FR-6 Deterministic loop/rework/progress signals (period_repeat, mirror_flipflop, content_oscillation, wave_signature_repeat, stall, attempt_cap, repeated_failure, ask_starvation, blocked_units, prompt_changed); every signal must be answered.
- FR-7 Alignment: every unit cites charter ask ids; the charter hash is locked.
- FR-8 Budget stages from projected spend, latched, restricting allowed decisions.
- FR-9 Graceful degradation: stabilize-only work in `stabilize`; forced close-out tail in `closeout`/`must_close`.
- FR-10 Hard `run_cost_usd` backstop at 100% (`mode: "hard"`).
- FR-11 Zero-cost, re-armable human hold via the next checkpoint's pre-hook.
- FR-12 Honest close-out report (done / usable / not done / how to continue) and final verify report.
- FR-13 Engine fix G5: emission is persisted before breaker evaluation (never lost on a trip or crash).
- FR-14 README (params, breaker rationale, stages, hold, gaps, isolation).
- FR-16 Budget override file re-bases stages. It is honored only with a matching engine-recorded `--extend-breaker` (promoted to MVP in Rev 2).
- FR-17 Live smoke run (real `claude_cli`, ≤ $25) with evidence under `output/` (promoted to MVP in Rev 2; the only NFR-8 validation).
- FR-18 Unit budget gate (`ov-unit-gate` pre-hook on every wave unit) re-arms budget/fan-out containment at $0, because breakers latch (Rev 2).
- FR-19 Operator close-out on demand (`overseer_tool.py request-closeout`): pending units are skipped at $0 on resume, and the next checkpoint is forced to close out. It covers the post-backstop close-out and "stop now but leave it usable" (Rev 2).
### MVP-Should (below the sprint cut line)
- FR-15 Nested depth-2 sub-DAGs via `expand` units + fixed `<unit>--done` sub-aggregator.
### Non-MVP (deferred, reasons in the design doc §1.2)
- NFR-X1 preemptive time checkpoints · NFR-X2 child runs · NFR-X3 per-ask repo routing ·
  NFR-X4 engine `drain` action · NFR-X5 Monitor pulse · NFR-X6 first-class paused status ·
  NFR-X7 legacy re-inject on resume · NFR-X8 embedding similarity · NFR-X9 agents.recommended asset ·
  NFR-X10 `wave_signature_repeat` detector · NFR-X11 tamper-proof (vs tamper-evident) governance.
### Non-functional
- NFR-1 path confinement · NFR-2 determinism (injectable clock) · NFR-3 idempotent prep/ledger ·
  NFR-4 fail-closed · NFR-5 stdlib-only, py ≥3.11, no `{{ ident }}` in the tool · NFR-6 bounded
  inputs · NFR-7 backward compatibility except G5 · NFR-8 overseer overhead ≤ ~10% ·
  NFR-9 legibility from ledger + checkpoint artifacts.

## Task List
Team: 4 developers × 2 sprints (capacity math in design doc §22). Every task has a single owner, and
a `reviewer`-agent review step is part of each task's ACs.

Sprint 1 (128 h of 144 h committed)
- [x] `T-kD6L76-design-package` — HLD/LLD, ADR-0016, tickets, Phase-4 consultations (architect). Done.
- [x] `T-pYt478-emit-settle-atomicity` — G5 engine fix + regression tests + NFR-2 allowlist. **Own PR to `main` first** (3 d). **Done**: implemented, independently re-verified, and reviewed (approve with nits, addressed) on branch `fix/emit-settle-atomicity` (commit `3692eac`, off `main`@`8c13320`, local/unpushed pending user confirmation to open the PR). Full suite 3983 passed/8 skipped/0 failed; ruff/mypy clean.
- [ ] `T-ABDjSj-tool-state-ledger-budget` — tool M1: config, hook context, `state.json` loader, hash-chained ledger, path history, budget stages + override, cadence, hold gate, charter-lock verify, unit gate (3 d).
- [ ] `T-C6uQJW-tool-loop-progress-detectors` — tool M2: §8.3 detectors + progress metrics; content_oscillation is trim-first (3 d).
- [ ] `T-eGXqXH-template-scaffold` — `template.yaml`, `workflow.json.tmpl`, `overseer-config.json.tmpl`, `prompt.md.tmpl`, hooks, breakers, assets tests (2 d).
- [ ] `T-ltBLUY-contract-and-readme` — `overseer-contract.md.tmpl` (shapes + OV-R1..R16) + `README.md` (2 d).
- [ ] `T-5ZzAZp-agent-instructions` — 8 MVP instruction files (3 d).

Sprint 2 (120 h above the cut + 16 h below, of 144 h committed)
- [ ] `T-HPJcc6-tool-structural-checkers` — tool M3a: R1–R10, R15, charter lock, `--dry-run` (3 d).
- [ ] `T-tAKBBB-tool-semantic-checkers` — tool M3b: R11–R14, R16, R13c, verdict/deferral checks, ledger events (3 d).
- [ ] `T-WruPiv-e2e-harness-core-scenarios` — scripted-executor harness + e2e (a)–(d) (3 d).
- [ ] `T-vmI0jI-e2e-failure-scenarios` — e2e (e)–(h): backstop+G5+unit gate, signal response, cancel, `max_parallel=2` (3 d).
- [ ] `T-3FlD46-security-review-hardening` — dev-security code review of tool/hooks/checkers + fixes (1 d).
- [ ] `T-23yMMB-live-smoke-run` — FR-17 real-LLM smoke, ≤ $25 (1 d).
- [ ] `T-gbccdr-docs-refresh` — post-implementation reconciliation of `docs-md/` and the skill (1 d).
- — cut line —
- [ ] `T-zLHc7Q-nested-expander-subdag` — FR-15 (2 d).

## Risks and Dependencies
- G5 is an engine behavior change (the settle ordering). It is intended and small, but
  `test_mvp_breaker_conditions` and `test_resume_replay` are known to change (NFR-2 allowlist in the
  same commit). About 10 more suites need inspection. It ships as its own PR first.
- Breakers latch, so after any trip a plain `ao resume` has no engine-side wall. This is covered for
  this template by the FR-18 unit gate. An engine-wide fix is a follow-up candidate (ADR-0016 D7).
- Governance artifacts are tamper-evident, not tamper-proof (no inter-task trust boundary; NFR-X11).
- The LLM overseer may disregard stages. This is mitigated mechanically (R11/R14/R16) plus the hard
  backstop.
- In-flight overshoot past 100% at `max_parallel > 1` is bounded by ≈P × unit cost (documented).
- Detector false positives are mitigated by severities and `accept` with a rationale.
- The hold renders as a `failed` task (NFR-X6), which is documented.
- `keep_existing` instructions can drift from the per-run contract. Mitigations: the "contract
  wins" clause and an `instructions-version` header.
- Dependency: `python3` ≥ 3.11 on the hook PATH (`python_bin` param).
- Related but not required: `E-Grpp0X-injected-task-dag-validation-gap`.

## Links
- Design doc: `docs-md/overseer-runner-hld.md`
- Sprint plan: design doc §22
- Output artifacts: `output/E-YAAGhk-overseer-runner-template/`
