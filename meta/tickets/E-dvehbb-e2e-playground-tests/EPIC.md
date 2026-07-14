# EPIC: E-dvehbb-e2e-playground-tests

## Metadata
- Epic ID: `E-dvehbb-e2e-playground-tests`
- Title: End-to-End Playground Tests (fixture / deterministic / fuzzy-real-LLM / performance tiers)
- Owner: architect
- Created: 2026-07-01
- Last Updated: 2026-07-01 (Phase-1 MVP delivered & verified)
- Status: MVP complete (Phase 1) — Phase 2 pending

## Summary
- Goal: Prove that AO's latest code correctly drives real, non-trivial multi-agent workflows to completion from the **outermost user boundary (the `ao` CLI)** — exercising the DAG, dynamic task injection, loops, token budgeting, logging, per-task output capture, and CLI flags — using a new `playground/` corpus of tiny, low-burn example workflows. Tests run in four tiers: (1) fixtures, (2) deterministic (paths/names/structure only, always-green CI), (3) fuzzy/real-LLM (opt-in, content not asserted), (4) performance + correctness.
- Scope In:
  - A top-level `playground/` corpus. Phase 1 delivers ONE proven example, `sum-of-array/`, end-to-end (workflow spec + instruction files + a tiny `PROBLEM.md` + deterministic control-file fixtures). Later phases replicate to `sorting/` and `student-data/`.
  - A per-example **workflow shape** that maps the requested agent pipeline (architect design → architect breakdown → design review → per-task {developer → test-writer → reviewer} → final review round) onto AO's real primitives: static DAG spine + `emit_tasks` dynamic fan-out + a `LoopSpec` review round.
  - Four test tiers and six test areas (below), each mapped to at least one deterministic, agent-checkable assertion.
  - A **FakeExecutor foundation** for the always-green tiers plus a **gated real-`ClaudeCliExecutor` tier** behind `@pytest.mark.real_llm` AND `AO_E2E_REAL_LLM=1`, skipped by default so normal CI never burns tokens.
  - Marker/env registration in `pyproject.toml` + a `tests/playground/conftest.py` gate.
  - A design doc at `docs-md/e2e-playground-testing.md` and a post-implementation docs-refresh reconciliation.
- Scope Out:
  - Any change to engine/executor/spec **production** behavior. This epic adds tests, playground assets, docs, and test-only harness/marker config. If a genuine engine gap is found (e.g. CLI cannot drive a FakeExecutor payload), it is filed as a **separate** ticket, not fixed here (see ADR-002 for why pre-seeding avoids that dependency).
  - New executor kinds, new spec fields, or a schema change (the `agents` schema is `additionalProperties:false` and `AgentSpec.executor` is `Literal["claude_cli","fake"]`; the design lives entirely within these).
  - Parallel task execution, remote log shipping, non-CLI transports.
  - Asserting LLM-generated **content** in any always-green tier (only real-LLM correctness tier asserts functional behavior of generated code, and it is opt-in).

## Requirements

### Functional
- FR-1 (Playground corpus): A top-level `playground/<example>/` directory holds each example's `PROBLEM.md`, `workflow.json`, `reposet.json`, a fake-executor agents file, a claude-cli agents file, `instructions/*.md`, and `fixtures/*` (deterministic-tier control files + expected-structure manifests). Phase-1 example is `sum-of-array/`.
- FR-2 (Workflow shape): Each example workflow encodes the requested pipeline using a **static spine** (`architect-design → design-review → architect-breakdown → integrate → review-round-loop → done`) where `architect-breakdown` uses `emit_tasks` to inject a fixed-shape per-task chain (`impl → test-write → task-review`) with **unique artifact paths**, and the final review round is a `LoopSpec` over `[bugfix, final-review]` gated by a `{"continue": bool}` verdict. (See design doc §7 HLD / ADR-001.)
- FR-3 (Fixture tier): Deterministic control files (`tasks-manifest.json`, gate verdict files) and expected-structure manifests (`expected_paths.json`, `expected_events.json`) live as versioned fixtures; a fixture-tier test validates every example's specs against `specs/*.schema.json` and loads/validates the fixtures.
- FR-4 (Deterministic tier): For each example, a test drives `ao run` (and `validate`/`status`/`resume`) through `typer.testing.CliRunner` against the **fake** agents file, with control files pre-seeded, and asserts ONLY file names / paths / directory structure / `run.log` structure & event order / DAG order / status.json fields / token-accounting math. Never asserts LLM content. Always green in CI, zero token burn.
- FR-5 (Real-LLM fuzzy tier): The SAME workflow specs run against the **claude-cli** agents file under `@pytest.mark.real_llm` + `AO_E2E_REAL_LLM=1`; skipped by default. Inputs may vary; only structure/exit/paths/completion are asserted, never content. Phase-1 delivers the harness skeleton + one gated smoke test for `sum-of-array`.
- FR-6 (Performance + correctness tier): An always-green performance test bounds FakeExecutor wall-clock per example; an opt-in (same gate as FR-5) correctness test runs the generated code to assert functional correctness (e.g. the produced sum function actually sums).
- FR-7 (Six areas covered): The deterministic tier asserts at least one check per area: (1) workflow/DAG/dependencies, (2) token computation/assumptions, (3) dynamic inputs/dependency specification, (4) logging, (5) per-task agent-output capture, (6) CLI + flags exercised e2e. (Traceability matrix in design doc §19.)
- FR-8 (Gating mechanism): `pyproject.toml` registers markers (`real_llm`, `e2e`, `perf`); `tests/playground/conftest.py` skips `real_llm` items unless `AO_E2E_REAL_LLM=1`, and selects the fake vs claude-cli agents file per tier.
- FR-9 (Phased delivery): Phase 1 (`sum-of-array` + deterministic tier + gated harness skeleton) is independently shippable and always green. Phases 2–3 replicate to `sorting`/`student-data` and add fuzzy + performance tiers without touching Phase-1 assets.

### Non-Functional
- NFR-1 (No production behavior change): This epic writes tests, playground assets, docs, and test-config only. Any code change to `src/` requires a separate ticket + explicit approval.
- NFR-2 (Determinism): The always-green tiers use FakeExecutor + fixed workspace (`AO_WORKSPACE_ROOT=tmp_path`) + pre-seeded control files; run twice → byte-identical expanded task ids, topo order, and asserted structure. No wall-clock or LLM nondeterminism in asserted values.
- NFR-3 (Zero default token burn): Default `pytest -q` collects but skips every `real_llm` test; the fake tiers never spawn a subprocess (FakeExecutor). CI stays green offline.
- NFR-4 (Outermost boundary): Every deterministic e2e assertion is driven through the `ao` CLI via `CliRunner`, never by calling `Orchestrator(...)` directly. (Engine-API tests already exist and are not duplicated here.)
- NFR-5 (Repo-learning conformance): All authored specs honor the recorded constraints — workflow/task/loop `id` matches `^[a-z0-9][a-z0-9-_]*$`; agents entries use only `executor`/`command_template`/`prompt_template`/`context_window`; injected/cloned tasks use unique artifact paths so `build_dag` inferred edges never form spurious cycles; local tooling is `uv run`.
- NFR-6 (Light ticket folders): Ticket dirs stay markdown-first; the playground corpus lives under `playground/` (product asset, intentionally committed), and any bulky generated run output goes under root `output/`, linked — not embedded.

## Key Design Facts (authoritative — see design doc for full derivation)

These are the load-bearing facts every task must honor. Full rationale in
`docs-md/e2e-playground-testing.md`.

1. **CLI → payload-less FakeExecutor.** `executors/DispatchExecutor` constructs a
   plain `FakeExecutor()` (no `emit_payloads` / `gate_payloads` / `manifest_payloads`
   / `token_outputs`). Therefore, driven through the CLI, a `fake` agent produces
   ONLY: declared output files (`fake output for <id>`), `stdout.txt`/`stderr.txt`
   capture stubs under `.orchestrator/runs/<run_id>/<task_id>/`, and a default
   `succeeded` result with **no** token actuals, **no** emitted task manifest,
   **no** gate verdict, **no** output manifest.
2. **Control-file pre-seeding (ADR-002).** The engine reads three machine-written
   **control** files by path (NFR-1 allow-list): `task_manifest_path` (emit_tasks),
   `gate_output_path` (loop verdict, iteration-suffixed), and `output_manifest`.
   Because the CLI's FakeExecutor leaves these untouched, the **deterministic tier
   pre-seeds them as fixtures on disk before invoking the CLI**. The SAME workflow
   spec, run in the real-LLM tier, has the real agents *generate* those files. One
   spec, two tiers.
3. **Token math is deterministic without actuals.** FakeExecutor via CLI reports no
   actuals, so the budget reconcile falls back to the estimate. `HeuristicTokenEstimator`
   computes from **file sizes only** (`instruction + inputs + dynamic_inputs`,
   `chars_per_token`, `output_allowance_tokens`, `pessimism_buffer`). With fixed
   fixture sizes, `state.budget_counters.consumed_tokens` is exactly recomputable →
   asserted deterministically (Area 2).
4. **Unique paths in the injected chain.** `build_dag` infers edges from matching
   input/output paths. Injected chain tasks and loop-body clones MUST use unique
   paths (loop clones already have inputs/outputs cleared by `engine._clone_body`).
   The emit fixture assigns `output/tasks/t1/{impl,tests,review}.md`.
5. **Ordering after dynamic injection.** A static `integrate` task anchors after the
   spine via explicit `depends_on: ["architect-breakdown"]` AND consumes the fixed
   emitted terminal output (`output/tasks/t1/review.md`) via an inferred edge, so it
   is correctly ordered after the injected chain once the DAG is rebuilt. The loop
   (`review-round`) and `done` (`depends_on: ["review-round"]`) follow.

## Test Tiers (design doc §8)

| Tier | Executor | Gate | Asserts | CI |
|------|----------|------|---------|----|
| 1 — Fixture | n/a | always | example specs validate vs schema; fixtures load/validate | always |
| 2 — Deterministic / reproducible | Fake (via CLI) | always | paths / names / dir tree / run.log structure+order / status.json / token math / CLI exit+flags | always green |
| 3 — Fuzzy / nondeterministic (real-LLM) | ClaudeCli (via CLI) | `real_llm` + `AO_E2E_REAL_LLM=1` | structure / exit / completion only — NOT content | opt-in, skipped |
| 4 — Performance + correctness | Fake (perf, always) + ClaudeCli (correctness, gated) | perf always; correctness gated | wall-clock envelope (Fake); functional correctness of generated code (real) | perf always; correctness opt-in |

## Six Areas → Deterministic Assertion (design doc §19 matrix)

| # | Area | Deterministic assertion (Fake, via CLI) |
|---|------|-----------------------------------------|
| 1 | Workflow / DAG / dependencies | topo order recovered from `run.log` `task.start` events == expected; all declared+injected outputs exist; cyclic variant → nonzero exit + "cycle" |
| 2 | Token computation / assumptions | run with `--budget-total` + `--pessimism-buffer`; `state.json` `budget_counters.consumed_tokens` == recomputed estimator sum (file sizes); `budget.charge`/`budget.reconcile` events present; actuals-absent → estimate-fallback path |
| 3 | Dynamic inputs / dependency spec | injected chain ids present in `status.json` with `origin="injected"`; loop clones present with `origin="loop"` and `__iterN` ids; `done` ordered after final iteration |
| 4 | Logging | `run.log` exists; every line valid JSON with `ts/level/logger/msg`; event set ⊇ {run.start, task.start, task.end, run.end, task.injected, loop.iterate}; per-task `task_id` present |
| 5 | Per-task agent-output capture | `.orchestrator/runs/<run_id>/<task_id>/stdout.txt`+`stderr.txt` exist for every executed task (incl. injected + loop clones); `state.json` `output_artifact_path` set |
| 6 | CLI + flags e2e | `validate` (ok+fail), `run` (exit 0/1), `resume` (fail→resume), `status --workspace`, budget flags accepted, `--rate-window invalid` → nonzero + ERROR |

## ADR Log (summary; full entries in design doc §9)
- ADR-001: Per-example workflow shape = static spine + `emit_tasks` fan-out + `LoopSpec` review round (vs a single fully-dynamic graph). Chosen for a well-defined, deterministic DAG using only already-tested engine paths.
- ADR-002: Deterministic tier **pre-seeds control files** (task manifest / gate verdicts) rather than adding a scripted executor or changing the schema. Keeps the fake path pure and lets one spec serve both tiers.
- ADR-003: Real-LLM tier gated by **both** a pytest marker and an env flag (belt-and-suspenders); default CI skips.
- ADR-004: `playground/` is a committed product asset (like `specs/examples/`), not a test fixture dir; tests copy it into `tmp_path` per run for isolation.
- ADR-005: One spec, two agents files (`agents.fake.json` / `agents.claude.json`) select the tier; no per-tier workflow forks.
- ADR-006: Token-accounting assertions target the **estimator math + budget counters**, not actuals (FakeExecutor reports none), giving a deterministic Area-2 check.

## Task List
- [x] `T-7592ux-playground-scaffold-gating` — **MVP** — DONE — top-level `playground/` scaffold, reposet/agents wiring, marker+env gating (pyproject + `tests/playground/conftest.py`), README, harness (`copy_example`/`run_cli`/`seed_control_files`).
- [x] `T-1vuzyi-sum-of-array-workflow` — **MVP** — DONE — `sum-of-array/` workflow spec + instruction files + `PROBLEM.md` + deterministic control-file fixtures (`expected_events.json` split into verified one_round/two_round).
- [x] `T-ee8hzo-fixture-tier` — **MVP** — DONE — fixture tier (19 tests): schema-validate example specs + load/validate fixtures + expanded-DAG acyclicity; helpers `load_expected`/`assert_tree`.
- [x] `T-r21p4y-deterministic-tier` — **MVP** — DONE — deterministic e2e tier (20 tests) for `sum-of-array` covering all six areas via `CliRunner` + FakeExecutor; token math recomputed from file sizes.
- [x] `T-g7rjh0-real-llm-gated-harness` — **MVP** — DONE — real-`ClaudeCliExecutor` gated tier (3 tests, skipped unless `AO_E2E_REAL_LLM=1`) + `HANDOFF.md` tier-bridge doc.
- [ ] `T-92o31p-perf-correctness-tier` — non-MVP — performance envelope (Fake, always) + gated correctness spot-check (real).
- [ ] `T-n477z9-sorting-example` — non-MVP — `sorting/` example (workflow + instructions + fixtures + deterministic tests) reusing the harness.
- [ ] `T-94tepb-student-data-example` — non-MVP — `student-data/` example, same shape.
- [ ] `T-5bh03j-docs-refresh` — non-MVP — post-implementation reconciliation of `docs-md/e2e-playground-testing.md` + `playground/README.md` against shipped behavior.

## Dependency Order
```
T-7592ux (scaffold + gating)
   └─> T-1vuzyi (sum-of-array workflow + fixtures)
          ├─> T-ee8hzo (fixture tier) ─┐
          │                            ├─> T-r21p4y (deterministic tier, 6 areas)  [MVP boundary]
          └────────────────────────────┘        │
   T-1vuzyi + T-r21p4y ─> T-g7rjh0 (real-llm gated harness skeleton)  [MVP boundary]
   T-r21p4y ─> T-92o31p (perf + correctness)
   T-r21p4y ─> T-n477z9 (sorting)   ;   T-r21p4y ─> T-94tepb (student-data)
   all implementation ─> T-5bh03j (docs-refresh)
```
- MVP set = {T-7592ux, T-1vuzyi, T-ee8hzo, T-r21p4y, T-g7rjh0}; shippable + always green.
- Phase 2 = {T-92o31p, T-n477z9, T-94tepb}. Phase 3 close-out = {T-5bh03j}.

## Sprint Capacity (documented per architect standard)
- Sprint length: 2 weeks (5-day weeks). Overhead 40%. Team profile: developers <4 yrs.
- ASSUMPTION: team_size = 2 for planning math.
  - `GrossHoursPerSprint = 2 * 10 * 8 = 160`
  - `NetFocusHoursPerSprint = 160 * 0.60 = 96`
  - `CommitmentHoursPerSprint = 96 * (0.70..0.85) = 67..82 hours`
- Task days below assume ~6 focus-hours/day. MVP (T-7592ux..T-g7rjh0) ≈ 8–9 task-days ≈ fits Sprint 1 for a 2-dev team with buffer. Phase 2/3 ≈ 6–7 task-days ≈ Sprint 2.
- Rationale for 2 sprints: MVP must be independently shippable & always green (Phase 1 gate) before replication; splitting on the MVP boundary de-risks token/flake exposure early.

## Risks and Dependencies
- R1 (CLI cannot drive FakeExecutor payloads): the crux constraint. Mitigation: control-file pre-seeding (ADR-002). If pre-seeding proves insufficient for any area, file a **separate** engine ticket (a `--executor fake --fake-script <file>` hook), do NOT change engine here.
- R2 (Spurious DAG cycles from shared paths): injected/cloned tasks reuse artifact paths → inferred-edge cycles. Mitigation: unique per-task paths (`output/tasks/t1/...`); loop clones already clear inputs/outputs; a fixture-tier test asserts the built DAG is acyclic with the expected edges.
- R3 (Real-LLM flakiness / token burn): Mitigation: double gate (marker + env), content never asserted, trivial low-burn problems, agents-file switch keeps the default path fake-only.
- R4 (Real agent emits a differently-shaped manifest): the static `integrate`/loop wiring assumes the fixed emitted chain (`t1`, fixed paths). Mitigation: `architect-breakdown.md` pins the exact manifest contract; real tier asserts only completion/structure. OPEN_QUESTION: multi-chain generalization via an injected aggregator task (deferred).
- R5 (Determinism drift): timestamps, run_ids embed a clock. Mitigation: assert structure/keys/paths/counts, not timestamp values; recompute token math from fixture sizes; never assert content.

## Open Questions
- OQ-1: Should the fuzzy tier vary inputs via a seeded generator or a small fixed matrix of trivial arrays? (Design doc §8 proposes a fixed small matrix for Phase 1; seeded generator deferred.)
- OQ-2: Multi-chain breakdown (N>1 injected chains) needs an injected aggregator so `final-review` need not know chain count statically. Deferred to a follow-on; MVP fixes N=1.
- OQ-3: Should `playground/` examples double as `ao init` templates / docs walkthroughs? (Design doc §17 notes the DX opportunity; out of scope for MVP.)

## Links
- Design doc: `docs-md/e2e-playground-testing.md`
- Sprint plan: this `EPIC.md` + per-task `TASK.md` files under `meta/tickets/E-dvehbb-e2e-playground-tests/`.
- Reference epic (style + Area-1/2 behavior): `meta/tickets/E-v0f0c9-logging-dynamic-workflows/EPIC.md`.
- Output artifacts (if any): `output/...` (none expected; playground assets live under `playground/`).
