# E2E Playground Testing — Design (E-dvehbb-e2e-playground-tests)

> Status: Draft · Owner: architect · Created: 2026-07-01
> Epic: `meta/tickets/E-dvehbb-e2e-playground-tests/EPIC.md`
> Related behavior: `docs-md/logging-dynamic-workflows-hld.md`, `docs-md/token-budgeting-hld.md`

This is the architecture package for adding **end-to-end playground tests** that prove AO's
latest code drives real multi-agent workflows to completion from the outermost user boundary
(the `ao` CLI). It is design + tickets only; no production/test code is written by this task.

Sections: 1 Requirements · 2 Scope · 3 Assumption Log · 4 Standards Survey · 5 Solution
Landscape (Build/Buy/Hybrid) · 6 Orchestration-Testing Landscape & Competitor Analysis ·
7 HLD · 8 Four-Tier Test Strategy · 9 ADR Log · 10 Block Diagram · 11 Spec/Data-Schema
Diagram · 12 Sequence Diagrams · 13 Spec Schema (control files) · 14 Interface/Harness
Contracts · 15 Trigger/Event Schema · 16 Deployment/Upgrade · 17 Developer/Operator
Experience · 18 Test Strategy Detail · 19 Area→Assertion Traceability Matrix · 20 Design
Artifacts Checklist · 21 Execution Readiness Gate · 22 Sprint Plan · 23
Risks/Dependencies/Open Questions · 24 Handoffs & Ownership · 25 Post-Implementation
Docs-Refresh Ticket.

---

## 1. Requirements (consolidated)

**Goal.** A `playground/` corpus of tiny, low-burn example workflows that the AO test suite
runs end-to-end through the `ao` CLI, proving the DAG, dynamic task injection, loops, token
budgeting, logging, per-task output capture, and CLI flags all work on the latest code.

Functional (FR-1..FR-9) and non-functional (NFR-1..NFR-6) requirements are enumerated
authoritatively in `EPIC.md`. Summary:

- FR-1 `playground/<example>/` corpus (Phase-1 = `sum-of-array/`).
- FR-2 per-example workflow shape mapping the requested agent pipeline onto AO primitives.
- FR-3..FR-6 four test tiers (fixture, deterministic, fuzzy/real-LLM, performance+correctness).
- FR-7 six test areas, each with ≥1 deterministic assertion.
- FR-8 marker + env gating for the real-LLM tier.
- FR-9 phased delivery; Phase-1 independently shippable and always green.
- NFR-1 no production `src/` change; NFR-2 determinism; NFR-3 zero default token burn;
  NFR-4 outermost boundary (CLI via `CliRunner`); NFR-5 repo-learning conformance;
  NFR-6 light ticket folders.

## 2. Scope

**In:** playground assets, four test tiers, marker/env gating, shared test harness, this
design doc, and a post-implementation docs-refresh.

**Out:** engine/executor/spec behavior changes, new executor kinds, schema changes, parallel
execution, non-CLI transports, and any always-green assertion on LLM-generated content.

**Boundary rule:** if an engine gap blocks a test (e.g. the CLI cannot drive a FakeExecutor
payload), it is filed as a **separate** ticket — this epic works entirely within shipped
behavior via control-file pre-seeding (ADR-002).

## 3. Assumption Log

```
ASSUMPTION: The CLI's DispatchExecutor constructs a payload-less FakeExecutor().
  Risk: If a future change lets the CLI inject fake payloads, pre-seeding becomes
        redundant (not wrong). Mitigation: verified against executors/__init__.py at
        design time; a fixture-tier note re-checks on drift.
ASSUMPTION: The playground problems are trivial and deliberately low-burn (sum/sort/queries).
  Risk: Real-LLM tier still costs some tokens. Mitigation: opt-in double gate; tiny inputs.
ASSUMPTION: For the MVP example, the breakdown emits exactly ONE task chain (N=1).
  Risk: Real agents may emit N>1, breaking static integrate/loop wiring. Mitigation:
        architect-breakdown.md pins the manifest; real tier asserts completion only; OQ-2
        tracks the multi-chain aggregator pattern.
ASSUMPTION: HeuristicTokenEstimator uses file sizes only and is fully deterministic given
        fixed fixture sizes. Risk: estimator formula change. Mitigation: Area-2 recomputes
        from the live EstimatorConfig; a formula change flips the test loudly (intended).
ASSUMPTION: team_size = 2 for sprint math. Risk: staffing differs. Mitigation: capacity math
        is shown and rescalable.
ASSUMPTION: `claude` CLI availability + auth exist in the gated environment.
  Risk: absent binary. Mitigation: requires_claude() skip (non-fatal).
```

## 4. Standards Survey (practical)

- **C4 / block diagrams** (§10) for the test topology; **ADRs** (§9) for every load-bearing
  decision; **test pyramid** honored — unit/integration already exist in `tests/`; this epic
  adds the **e2e apex** via the CLI boundary (matching CLAUDE.md's "as outer a boundary as
  possible" mandate).
- **JSON Schema** already governs specs (`specs/*.schema.json`); the fixture tier reuses it
  (jsonschema validation). Control files have documented shapes (§13).
- **Observability** is asserted, not just produced: `run.log` (JSON lines), `status.json`,
  per-task capture dirs — the deterministic tier turns these into machine assertions.
- **Deterministic testing**: fixed workspace (`AO_WORKSPACE_ROOT=tmp_path`), FakeExecutor (no
  subprocess), pre-seeded control files, structure-only assertions — the standard "golden
  path + golden tree" approach.
- **Gating convention**: pytest markers + env flag, the industry-standard way to fence
  expensive/networked tests (mirrors `pytest.mark.slow`, `pytest.mark.integration`).

## 5. Solution Landscape — Build vs Buy vs Hybrid

| Option | What it means here | Verdict |
|--------|--------------------|---------|
| **Buy** | Adopt an external e2e framework (Robot Framework, behave/BDD, cypress-like) | Rejected — the CLI is a Python Typer app; `CliRunner` already exercises the true boundary. External BDD adds indirection + a dependency for no coverage gain. |
| **Build (from scratch)** | Hand-roll a bespoke runner that shells out to `ao` and diffs output | Rejected — reinvents pytest fixtures/parametrization; loses `--cov`, markers, CI integration. |
| **Hybrid (chosen)** | Build a **thin playground harness on pytest + `typer.testing.CliRunner`**, reusing the shipped `FakeExecutor` and the existing `tests/conftest.py` patterns | **Chosen** — minimal new surface, maximal reuse, real boundary, deterministic. |

**Recommendation:** Hybrid. The only "framework" we build is a small `tests/playground/`
harness (`copy_example`, `run_cli`, `agents_for`, fixture loaders, tree/log assertions). The
heavy lifting (parametrization, gating, coverage) is pytest; the boundary is `CliRunner`; the
determinism is `FakeExecutor` + pre-seeded control files.

## 6. Orchestration-Testing Landscape & Competitor Analysis

The relevant comparison is not "which orchestrator is best" but **how orchestration engines
test themselves end-to-end**, and how our fake-vs-real split compares. This directly informs
our tier design.

| Framework | How it e2e-tests workflows | Deterministic "fake" tier | Real-side tier | Notes / user pain |
|-----------|----------------------------|---------------------------|----------------|-------------------|
| **Airflow** | `DagBag` import tests (catch spec errors), example DAGs, `dag.test()` / `airflow tasks test` runs one task locally | Uses `DummyOperator`/`EmptyOperator` + `PythonOperator` stubs; a metadata DB in tests | Full scheduler+executor integration on a test DB/cluster | Import tests are the gold standard for "specs stay valid"; heavy DB fixture; flaky scheduler tests are a known complaint |
| **Prefect** | `prefect_test_harness()` spins an ephemeral SQLite backend so flows run in-process | Tasks run for real but against a throwaway server; mock task results via `MagicMock` | Real API/agents against Prefect Cloud/Server | Harness is loved for local determinism; still needs a server process |
| **Dagster** | `materialize([...])`, `build_op_context()`, `ExecuteInProcess`, **asset checks** | In-process executor + resource mocking; asset checks assert structure | Real IO managers/resources | Structure-asserting "asset checks" are close cousins of our path/tree assertions |
| **Temporal** | `TestWorkflowEnvironment` with **time-skipping**; **replay tests** against recorded histories | Activities mocked; deterministic virtual clock | Real workers against a dev server | Time-skip + replay is the strongest determinism model in the space; heavier than we need |
| **Argo Workflows** | CI e2e against a `kind` k8s cluster; `hera`/manifests | Limited; mostly integration | Full cluster runs | Highest operational burden; slow e2e |
| **GitHub Actions** | `act` runs workflows locally; matrix jobs | Stubbed steps | Real runners | `act` ≈ our "run the real thing locally" idea |
| **n8n / Windmill / Luigi / Step Functions** | Node-level unit tests + sample flows; SFN has a **local emulator** | Varies | Cloud | Sample-flow-as-test is common; few give a clean deterministic content-free assertion story |

**Gap analysis — what they do well / fall short:**
- **Do well:** import/spec-validation tests (Airflow), in-process ephemeral harness (Prefect),
  structure-asserting checks (Dagster), virtual-clock determinism + replay (Temporal).
- **Fall short:** most couple determinism to a DB/server/cluster fixture; few offer a
  *content-free* golden-tree assertion for LLM/nondeterministic steps; the "expensive real
  run" is rarely fenced behind a clean double gate that keeps default CI free.

**Differentiation — what THIS design does:**
- A **zero-dependency deterministic tier**: FakeExecutor spawns no subprocess and needs no
  DB/server/cluster (unlike Airflow/Prefect/Argo). Determinism comes from pre-seeded control
  files + file-size-based token math, not a virtual clock or recorded history.
- **One spec, two tiers**: the same workflow runs deterministic (fake, pre-seeded) and
  real (LLM, generated) — a split few engines formalize.
- **Content-free assertions by construction**: like Dagster asset checks but explicitly
  designed for nondeterministic (LLM) outputs — we assert names/paths/structure/order/log
  shape/token-math, never generated text.

**Positioning statement:**
```
We will:
- Match Airflow in spec/import validation (fixture tier validates every example vs JSON Schema).
- Match Dagster in structure-asserting checks (deterministic tier asserts the golden tree + log shape).
- Beat Prefect/Airflow in setup simplicity (no DB/server/cluster — FakeExecutor + tmp workspace).
- Borrow Temporal's determinism intent (fixed workspace + pre-seeded control files) without its
  virtual-clock/replay machinery (we don't need it for a linear engine).
- Avoid Argo's operational burden (no cluster) and avoid asserting LLM content anywhere the
  default CI runs (double-gated real tier only).
```
This ties directly to spec ergonomics: because AO specs are declarative JSON with control
files on a narrow NFR-1 allow-list, we can pre-seed those files and get determinism for free —
a property Python-DSL engines (Prefect/Dagster) cannot match as cleanly.

## 7. HLD — Architecture of the Playground

### 7.1 Topology
```
playground/                       # committed product asset (like specs/examples/)
  README.md
  sum-of-array/                   # Phase 1 (proven template)
    PROBLEM.md                    # trivial, low-burn problem statement
    workflow.json                 # the spine below (id: sum-of-array)
    reposet.json                  # one repo set: sum-set, workspace_root "."
    agents.fake.json              # every agent -> {"executor":"fake"}
    agents.claude.json            # every agent -> claude_cli (only 4 allowed keys)
    instructions/*.md             # one per agent role; breakdown + final-review PIN control-file contracts
    fixtures/                     # deterministic-tier pre-seed + expected structure
      tasks-manifest.json         # {"tasks":[impl-t1,testwrite-t1,taskreview-t1]}
      final-verdict.json          # {"continue": false}
      final-verdict-iter2.json    # {"continue": false}  (2-round variant)
      expected_paths.json         # golden file/dir tree
      expected_events.json        # golden run.log event set + task.start order
  sorting/                        # Phase 2 (same structure)
  student-data/                   # Phase 2 (same structure)

tests/playground/
  __init__.py
  conftest.py                     # real_llm gate (marker + AO_E2E_REAL_LLM)
  _playground.py                  # copy_example, run_cli, agents_for, loaders, assertions
  test_fixtures.py                # Tier 1
  test_sum_of_array_deterministic.py  # Tier 2 (six areas)
  test_sum_of_array_real_llm.py   # Tier 3 (gated)
  test_performance.py             # Tier 4 perf (Fake, always)
  test_correctness_real.py        # Tier 4 correctness (gated)
```

### 7.2 Per-example workflow shape (the core mapping)

The requested pipeline (architect design → breakdown → design review → per-task
{developer → test-writer → reviewer} → final review round that can spawn bug tasks and loop)
maps onto AO primitives as a **static spine + one `emit_tasks` fan-out + one `LoopSpec`**:

```
architect-design    (architect)   -> output/design.md                              [HLD+LLD+ADR]
design-review       (reviewer)    output/design.md         -> output/design-review.md   depends_on: design
architect-breakdown (architect)   design.md, design-review.md   depends_on: design-review
                    emit_tasks:true  task_manifest_path: output/tasks-manifest.json  outputs: []
    ── emit injects the FIXED single chain (unique paths) ──
    impl-t1         (developer)    depends_on: architect-breakdown  in: design.md            -> output/tasks/t1/impl.md
    testwrite-t1    (test-writer)  depends_on: impl-t1              in: .../impl.md          -> output/tasks/t1/tests.md
    taskreview-t1   (reviewer)     depends_on: testwrite-t1         in: .../impl,.../tests   -> output/tasks/t1/review.md
integrate           (integrator)  depends_on: architect-breakdown  in: output/tasks/t1/review.md  -> output/integrated.md
    ── LoopSpec "review-round": body [bugfix, final-review], gate=final-review ──
    bugfix          (developer)    depends_on: integrate   in: output/integrated.md   -> output/bugfix.md
    final-review    (reviewer)     depends_on: bugfix      in: output/bugfix.md       -> output/final-review.md
                    gate_output_path: output/final-verdict.json  gate_field: continue  max_iterations: 3
done                (integrator)   depends_on: review-round(loop)  in: output/final-review.md  -> output/summary.md
```

**Why this shape (ADR-001):** it demonstrates BOTH dynamic primitives while producing a
well-defined, deterministic DAG using only already-tested engine paths:
- **`emit_tasks` fan-out** models the per-task {developer → test-writer → reviewer} chain. The
  emitted tasks have **unique paths** (`output/tasks/t1/*`) so `build_dag` inferred edges never
  form spurious cycles (repo learning honored).
- **`integrate`** is the ordering anchor: it declares `depends_on: [architect-breakdown]`
  (so it is never scheduled before the spine, even pre-injection) AND declares
  `inputs: [output/tasks/t1/review.md]` (so once the DAG is rebuilt post-injection, an
  inferred edge orders it after the injected chain). This is the crux that lets a **static**
  task run after **dynamic** tasks without knowing their ids at author time.
- **`LoopSpec` review round** models "final reviewer takes another dev/review round." Body
  `[bugfix, final-review]` clones per iteration (`__iterN`); the gate verdict `{"continue":
  bool}` drives continue/stop; `max_iterations` caps it. `emit_tasks` stays on the spine only —
  never inside the loop body — because `_clone_body` sets `emit_tasks:false` on clones.
- **`done`** uses `depends_on: [review-round]` (a loop id), which `dag._resolve_loop_dep`
  resolves to the last body task of the highest materialized iteration (tested path).

### 7.3 The central constraint — CLI drives a payload-less FakeExecutor

`executors/DispatchExecutor.__init__` builds `self._fake = FakeExecutor()` with **no
payloads**. So through the `ao` CLI, a `fake` agent produces only: declared output files
(`fake output for <id>`), `stdout.txt`/`stderr.txt` capture stubs, and a default `succeeded`
result — **no** token actuals, emitted manifest, gate verdict, or output manifest.

**Resolution (ADR-002):** the three engine-read **control files** are machine-written and on
the NFR-1 allow-list, so the **deterministic tier pre-seeds them on disk before `ao run`**:
- `output/tasks-manifest.json` — read by the engine after `architect-breakdown` succeeds → injects the chain.
- `output/final-verdict.json` (+ `__iter2` …) — read by the loop gate per iteration → continue/stop.
- (`output/*-manifest.json` for `output_manifest` dynamic outputs, if an example uses it.)

The FakeExecutor leaves pre-seeded files untouched (it only writes them when payloads are
configured, which the CLI never does), so the engine reads exactly what the fixture placed.
The SAME spec, run in the real-LLM tier, has the real agents **generate** these files (their
instruction files pin the contract). One spec, two tiers.

## 8. Four-Tier Test Strategy

| Tier | Purpose | Executor | Gate | Asserts | CI |
|------|---------|----------|------|---------|----|
| **1 Fixture** | Static correctness of specs+fixtures; catch authoring errors cheaply | none | always | specs validate (cross_validate + JSON Schema); manifest/verdict fixtures well-formed; **expanded DAG acyclic** with expected edges; ids match pattern | always |
| **2 Deterministic** | Prove the engine drives the full workflow correctly, reproducibly | Fake (via CLI) | always | file names/paths/tree; `run.log` structure + `task.start` order; `status.json` fields+origins; **token math** (estimator + budget counters); CLI exit+flags | always green, 0 tokens |
| **3 Fuzzy / real-LLM** | Prove real agents drive the same spec to completion under variation | ClaudeCli (via CLI) | `real_llm` + `AO_E2E_REAL_LLM=1` | exit 0; `status=succeeded`; spine outputs + control files EXIST and parse — **never content** | opt-in, skipped |
| **4 Perf + correctness** | Bound engine speed; spot-check generated code works | Fake (perf) + ClaudeCli (correctness) | perf always; correctness gated | wall-clock < named budget + no excess retries (Fake); `sum([1,2,3])==6` on generated code (real) | perf always; correctness opt-in |

**Fuzzy-input strategy (OQ-1):** Phase-1 uses a small fixed matrix of trivial inputs
(`[]`, `[1,2,3]`, `[-1,1]`) so the real tier varies inputs without a seeded generator;
a seeded generator is deferred. Content is never asserted in tiers 1–3; only tier-4
correctness (gated) executes generated code.

## 9. ADR Log

```
ADR-001: Per-example workflow shape = static spine + emit_tasks fan-out + LoopSpec review round.
  Context: Must map the requested dynamic pipeline (per-task fan-out + final review round)
    onto AO primitives while staying deterministic and using tested engine paths.
  Options: (a) one fully-dynamic graph (all tasks emitted); (b) static spine + one emit + one loop;
    (c) pure static DAG (no dynamics).
  Decision: (b).
  Reason: (a) needs untested nested emit/loop + unknown-id ordering; (c) skips the very
    dynamic features we must test. (b) exercises emit_tasks AND loops with a well-defined DAG.
  Consequences: `integrate` anchor task needed for ordering; MVP fixes N=1 chain (OQ-2 for N>1).

ADR-002: Deterministic tier pre-seeds control files instead of adding a scripted executor.
  Context: CLI's DispatchExecutor builds a payload-less FakeExecutor; it cannot emit task
    manifests / gate verdicts / output manifests / token actuals via the CLI.
  Options: (a) add a new "scripted fake" executor kind + schema enum; (b) pre-seed the
    engine-read control files as fixtures; (c) drive via Python API (violates NFR-4).
  Decision: (b).
  Reason: (a) is a production schema change (agents schema is additionalProperties:false;
    AgentSpec.executor is Literal) — out of scope and riskier; (c) abandons the CLI boundary.
    (b) keeps the fake path pure, needs no code change, and lets one spec serve both tiers.
  Consequences: fixtures must track control-file paths incl. loop iteration suffixes
    (`_gate_path_for_iter`); real tier regenerates the same files live.

ADR-003: Real-LLM tier gated by BOTH a pytest marker and an env flag.
  Context: Must never burn tokens or flake in default CI.
  Decision: `@pytest.mark.real_llm` AND `AO_E2E_REAL_LLM=1` (belt + suspenders).
  Reason: Marker alone can be run accidentally with `-m real_llm`; env alone lacks selection.
    Both together make the default path unambiguously skip.
  Consequences: conftest collection hook auto-skips real_llm items unless the env is set.

ADR-004: playground/ is a committed product asset, not a tests/ fixture dir.
  Context: Where do example specs/instructions live?
  Decision: top-level playground/ (sibling to specs/examples/); tests copy it into tmp_path.
  Reason: examples double as documentation/onboarding and can seed `ao init` templates;
    copying into tmp_path gives per-run isolation without polluting the repo.
  Consequences: harness needs copy_example(); Fake-tier runs write only under tmp_path.
  Amendment (2026-07-01): the REAL-LLM tier must NOT use pytest tmp_path. The spawned
    `claude` subprocess is sandboxed to the repo working directory, and system /tmp is
    outside that allow-list — instruction reads / output writes there are denied. The
    real tier instead uses the `real_llm_workspace` fixture (a per-test, gitignored dir
    under `playground/.tmp/`, inside the allow-list). Executor paths are absolute, so
    only the workspace *location* changes; no `--add-dir` / unrestricted access is needed.

ADR-005: One spec, two agents files select the tier (agents.fake.json / agents.claude.json).
  Context: Avoid forking the workflow per tier.
  Decision: keep a single workflow.json; switch executor via the --agents file.
  Reason: guarantees the two tiers test the identical DAG; the only variables are executor
    and control-file provenance.
  Consequences: agents.claude.json must use only the 4 allowed keys (additionalProperties:false).

ADR-006: Token-accounting assertions target estimator math + budget counters, not actuals.
  Context: FakeExecutor via CLI reports no token actuals; reconcile falls back to estimate.
  Decision: assert consumed_tokens == recomputed HeuristicTokenEstimator sum over tasks,
    and assert budget.charge/reconcile events; assert reconcile actual == charged estimate.
  Reason: file-size-based estimate is fully deterministic; gives a real Area-2 check with no LLM.
  Consequences: Area-2 recomputes with the live EstimatorConfig (defaults + --pessimism-buffer).
```

## 10. Block Diagram (test topology)
```
            ┌──────────────────────────── tests/playground/ ────────────────────────────┐
            │  conftest.py (real_llm gate)   _playground.py (copy_example/run_cli/…)      │
            │                                                                             │
  Tier 1 ── │  test_fixtures.py ── load specs+fixtures ── jsonschema ── build_dag(acyclic)│
  Tier 2 ── │  test_*_deterministic.py ─┐                                                 │
  Tier 4p ─ │  test_performance.py ─────┤ pre-seed control files                          │
            │                           │                                                 │
            └───────────────────────────┼─────────────────────────────────────────────── ┘
                                        │ CliRunner.invoke(app, ["run", …, agents.fake.json])
                                        ▼
        ┌───────────────────── ao CLI (Typer app) ─────────────────────┐
        │  load+cross_validate → Orchestrator.run → DispatchExecutor    │
        │                                            └─ FakeExecutor()   │  (no payloads)
        │  writes: output/*  · .orchestrator/runs/<id>/{state,status.json,run.log,<task>/std*.txt}
        └───────────────────────────────────────────────────────────────┘
                                        ▲
  Tier 3 ── test_*_real_llm.py ─────────┘ CliRunner.invoke(app, ["run", …, agents.claude.json])
  Tier 4c ─ test_correctness_real.py       (gate: real_llm + AO_E2E_REAL_LLM=1) → ClaudeCliExecutor → real `claude`
```

## 11. Spec / Data-Schema Diagram
```
WorkflowSpec(id: sum-of-array)
 ├─ tasks[]: architect-design, design-review, architect-breakdown(emit_tasks),
 │           integrate, bugfix, final-review, done
 ├─ loops[]: review-round { body:[bugfix,final-review], gate_task_id:final-review,
 │                          gate_output_path:output/final-verdict.json, max_iterations:3 }
 └─ triggers[]: [{type: manual}]

RepoSet(sum-set): workspace_root ".", repos:[{id:code, path:".", role:primary}]
Agents(fake):   {architect,reviewer,developer,test-writer,integrator} -> {executor:fake}
Agents(claude): same names -> {executor:claude_cli, command_template, prompt_template, context_window}

Control files (engine-read, NFR-1 allow-list):
  output/tasks-manifest.json  : {"tasks":[TaskSpec, …]}          (emit_tasks source)
  output/final-verdict.json   : {"continue": bool}               (loop gate, iter 1)
  output/final-verdict__iter2.json : {"continue": bool}          (loop gate, iter ≥2)
Run artifacts (engine-written):
  .orchestrator/runs/<run_id>/state.json   : RunState (budget_counters, tasks{origin,…})
  .orchestrator/runs/<run_id>/status.json  : snapshot (counts, current_task, tasks[origin])
  .orchestrator/runs/<run_id>/run.log      : JSON lines (ts,level,logger,msg,event,task_id,…)
  .orchestrator/runs/<run_id>/<task_id>/stdout.txt, stderr.txt
```

## 12. Sequence Diagrams

**12.1 Deterministic happy path (Fake, one review round):**
```
test → copy_example(sum-of-array, tmp) ; seed(tasks-manifest.json, final-verdict.json={"continue":false})
test → CliRunner: ao run --workflow workflow.json --agents agents.fake.json  (AO_WORKSPACE_ROOT=tmp)
 CLI → load+cross_validate → Orchestrator.run
 engine → design → design-review → architect-breakdown(succeeds; no outputs)
 engine → read_task_manifest(output/tasks-manifest.json) → inject impl-t1,testwrite-t1,taskreview-t1 → rebuild DAG
 engine → impl-t1 → testwrite-t1 → taskreview-t1 (Fake writes output files + std*.txt stubs)
 engine → integrate (inferred edge from taskreview-t1 output) → bugfix → final-review
 engine → read_gate(output/final-verdict.json)=false → loop ends → done
 engine → status=succeeded ; write state/status/run.log
test → assert exit 0 ; recovered task.start order == expected ; tree == expected_paths ; std*.txt exist
```

**12.2 Deterministic two-round loop (Fake):**
```
seed final-verdict.json={"continue":true} ; final-verdict__iter2.json={"continue":false}
… final-review (iter1) → read_gate=true → clone [bugfix__iter2, final-review__iter2] (inputs/outputs cleared) → loop.iterate
… final-review__iter2 → read_gate(output/final-verdict__iter2.json)=false → done
test → assert status.json has origin="loop" clones with __iter2 ids ; done ordered last
```

**12.3 Real-LLM path (gated):**
```
AO_E2E_REAL_LLM=1 ; requires_claude()
test → real_llm_workspace (playground/.tmp/ws-*, repo-local + gitignored, in sandbox allow-list)
test → copy_example (NO pre-seed) → ao run --agents agents.claude.json (--permission-mode bypassPermissions)
 engine → architect-breakdown → ClaudeCliExecutor(real claude, cwd=workspace) WRITES output/tasks-manifest.json
 engine → read_task_manifest → inject → … → final-review WRITES output/final-verdict.json → done
test → assert exit 0 ; status=succeeded ; spine outputs + control files EXIST & parse (no content asserts)
```
Note: the real tier uses `real_llm_workspace` (NOT `tmp_path`) — the spawned `claude` is
sandboxed to the repo dir, so system /tmp is unreachable. See §12.5 for the three real-LLM
hardening fixes (turn budget, permissions, agent cwd) that made this path reliably green.

**12.5 Real-LLM hardening (RCA + fixes, 2026-07-02):** getting the gated tier to pass green
against the real `claude` surfaced three distinct, order-revealed failures. Each was diagnosed
from the captured `stdout.txt`/`stderr.txt` (`subtype`, `num_turns`, `permission_denials`,
`terminal_reason`) plus workspace file-presence checks:

| Failure | Root cause (evidence) | Fix |
|---|---|---|
| `architect-design` → `error_max_turns` (`num_turns:6`, `permission_denials:[]`) | `effort:medium` mapped to `--max-turns 5` — too tight for a task that non-deterministically needs 3–6 turns. **Not** a permission issue. | Raised `EFFORT_MAX_TURNS` to `{low:15, medium:30, high:60}` (turns are a loop-breaker; token budget is the real cost guard) + added explicit `AgentSpec.max_turns` override + `ao run --max-turns` flag + `MAX_TURNS`/`AO_MAX_TURNS` plumbing. |
| `taskreview-t1` → declared output `review.md` missing (Bash in `permission_denials`) | reviewer used `acceptEdits`, which silently **denies Bash**, but `reviewer.md` requires running `pytest` — the agent stalls and skips its write. | All playground agents standardized on `--permission-mode bypassPermissions` (permission mode must match what the instruction actually needs). |
| `architect-breakdown` → manifest "not found" (claimed success) | `subprocess.run` set **no `cwd`**, so `claude` inherited the repo-root cwd and the agent's *relative* `output/tasks-manifest.json` write landed at repo root, outside the workspace. | Framework-level, config-driven agent cwd: `AgentSpec.working_dir` → engine resolves under `workspace_root` → `TaskContext.cwd` → executor `subprocess.run(cwd=…)`. Default cwd = workspace root. (Deliberately crosses this epic's original NFR-1 "no `src/` changes" boundary, per user direction — the fix belongs in the framework, not the test.) |

Validation: full real-LLM suite `3 passed in 578s` (`PYTEST_EXIT=0`); manifest now lands inside
the workspace; fast suite `387 passed` (+4 cwd/max-turns unit+integration tests); `mypy` clean.

**12.4 Failure/edge — cycle & resume (Area 1 & 6):**
```
cyclic variant: ao run cyclic.json → CycleError → nonzero exit ; output ~ "cycle"
resume: force a task fail (behavior via a failing variant) → ao run exits 1 →
        ao resume --run-id <id> → prepare_resume skips succeeded, re-runs failed → succeeded
```

## 13. Control-File Schemas (machine-written; engine reads by path)

```json
// output/tasks-manifest.json  (emit_tasks source; read by read_task_manifest)
{ "tasks": [
  { "id": "impl-t1", "agent": "developer", "instruction": "instructions/developer.md",
    "depends_on": ["architect-breakdown"], "inputs": ["output/design.md"],
    "outputs": ["output/tasks/t1/impl.md"] },
  { "id": "testwrite-t1", "agent": "test-writer", "instruction": "instructions/test-writer.md",
    "depends_on": ["impl-t1"], "inputs": ["output/tasks/t1/impl.md"],
    "outputs": ["output/tasks/t1/tests.md"] },
  { "id": "taskreview-t1", "agent": "reviewer", "instruction": "instructions/reviewer.md",
    "depends_on": ["testwrite-t1"], "inputs": ["output/tasks/t1/impl.md","output/tasks/t1/tests.md"],
    "outputs": ["output/tasks/t1/review.md"] }
] }
```
```json
// output/final-verdict.json (+ output/final-verdict__iter2.json …)  read by read_gate
{ "continue": false }
```
Both conform to shapes already implemented (`read_task_manifest`, `read_gate`); no schema
change. Each injected `TaskSpec` validates against the same task rules as `workflow.schema.json`.

## 14. Interface / Harness Contracts (test-only)

```python
# tests/playground/_playground.py
REPO_ROOT: Path                                   # agent-orchestrator root
def discover_examples() -> list[str]              # sorted names under playground/ with a workflow.json
def copy_example(name: str, tmp_path: Path) -> Path   # copytree(playground/<name>, tmp_path, dirs_exist_ok=True)
def run_cli(args: list[str], tmp_path: Path)          # CliRunner().invoke(app, args, env={"AO_WORKSPACE_ROOT": str(tmp_path)})
def agents_for(tier: str) -> str                  # "fake"->agents.fake.json, "real"->agents.claude.json
def seed_control_files(tmp_path: Path, *, rounds: int = 1) -> None  # copy fixtures/*.json to <tmp>/output/(suffixed paths)
def load_expected(name: str) -> dict              # fixtures/expected_paths.json + expected_events.json merged
def expanded_workflow(name: str) -> WorkflowSpec  # spine + manifest tasks merged (fixture-tier acyclicity)
def assert_tree(root: Path, expected_paths: list[str]) -> None      # every path exists & non-empty
def runlog(tmp_path: Path) -> Path                # .orchestrator/runs/<run_id>/run.log (run_id from CLI output)
def read_state(tmp_path: Path) -> dict ; read_status(tmp_path: Path) -> dict
def requires_claude() -> None                     # pytest.skip if shutil.which("claude") is None

# tests/playground/conftest.py
def pytest_collection_modifyitems(config, items): # skip real_llm items unless AO_E2E_REAL_LLM=1

# pyproject.toml
[tool.pytest.ini_options]
markers = ["real_llm: opt-in real ClaudeCliExecutor tier (needs AO_E2E_REAL_LLM=1)",
           "e2e: end-to-end via the ao CLI", "perf: performance envelope checks"]
```
**Errors / edge cases:** `run_id` extracted via `re.search(r"Run:\s+(\S+)", result.output)`
(existing pattern); `seed_control_files` MUST place iter-≥2 verdicts at the
`__iterN`-suffixed path per `engine._gate_path_for_iter`; `requires_claude()` skips (not
fails) when the binary is absent; the gate hook only touches items carrying the `real_llm`
keyword. **Idempotency:** deterministic runs are pure over `tmp_path`; re-running yields
identical asserted values (structure/paths/ordered-event-names/consumed_tokens).

## 15. Trigger / Event Schema

- **Workflow trigger:** all playground workflows use the default `[{ "type": "manual" }]`
  trigger (no cron/event) — runs are invoked explicitly by tests via `ao run`. Cron/event
  triggers are out of scope for the playground (they belong to scheduler tests).
- **Log events asserted (Area 4):** the engine emits (via `logging_setup`) the event set
  `{run.start, task.start, task.end, run.end, task.skip, task.fail, task.injected,
  loop.iterate, budget.charge, budget.reconcile, budget.gate_block}`. The deterministic tier
  asserts the presence of the core subset `{run.start, task.start, task.end, run.end,
  task.injected, loop.iterate}` and, when a budget is set, `{budget.charge, budget.reconcile}`.
  `expected_events.json` pins the exact `task.start` order.

## 16. Deployment / Upgrade Considerations

- **No runtime deployment** — this is test + asset material. "Deploy" = the tests run in CI
  (`uv run pytest -q`) and stay green offline (NFR-3).
- **Upgrade safety:** because assertions are structure/path/order/token-math (not content),
  routine LLM/model changes don't break the always-green tiers. Engine changes that alter the
  golden tree, event set, or token formula will flip specific tier-2 assertions — a **feature**
  (regression detection), reconciled via the docs-refresh (T-5bh03j).
- **CI wiring:** default job runs tiers 1, 2, 4-perf. An optional manual/scheduled job sets
  `AO_E2E_REAL_LLM=1` (with `claude` auth) to run tiers 3 + 4-correctness. Add
  `-m "not real_llm"` to the default job for explicitness.
- **Coverage:** the playground tests raise e2e coverage of `cli.py`/`engine.py` boundary paths;
  keep the repo's ≥80% gate.

## 17. Developer / Operator Experience

The framework's users are workflow authors + operators; the playground is also their
best on-ramp:
- **Ergonomic spec:** the `sum-of-array` workflow is a copy-paste template for a real
  design→build→review pipeline; `PROBLEM.md` + `instructions/*` show the intended authoring style.
- **Fast local iteration:** the deterministic tier runs in milliseconds (no subprocess), so
  authors get instant feedback on spec validity + DAG shape via `uv run pytest -q tests/playground`.
- **Diagnosable failures:** a broken spec fails the fixture tier with a schema/DAG message
  before any run; a broken run shows the recovered `task.start` order vs expected and the exact
  missing path — pointing at the offending task.
- **Clear real-run switch:** `AO_E2E_REAL_LLM=1 uv run pytest -m real_llm` is the one documented
  command to see the real thing drive a workflow end-to-end (README).
- **Opportunity (OQ-3):** playground examples can later seed `ao init` templates and the docs
  walkthrough (`docs-md/guide-epic-walkthrough.md`), unifying tests + onboarding.

## 18. Test Strategy Detail (levels + coverage)

- **Unit-ish (Tier 1):** pure validation of specs/fixtures — fast, no run. Target: 100% of
  examples validate; DAG acyclicity proven.
- **Integration/e2e (Tier 2):** full CLI runs against Fake — the apex of the pyramid; one test
  per area × per example. Target: all six areas green; twice-run determinism.
- **System/real (Tier 3):** opt-in real-LLM completion smoke — not counted in default coverage.
- **Perf/correctness (Tier 4):** always-green perf envelope + opt-in functional correctness.
- **Coverage target:** maintain repo ≥80% line coverage; playground tests are additive and must
  not reduce it. Regression baseline captured at implementation start (see EPIC STATUS).
- **Determinism controls:** `AO_WORKSPACE_ROOT=tmp_path`, FakeExecutor, pre-seeded control
  files, structure-only assertions, token math recomputed from fixture sizes.

## 19. Area → Assertion Traceability Matrix

| # | Area | Tier-2 deterministic assertion | Owning task |
|---|------|--------------------------------|-------------|
| 1 | Workflow / DAG / dependencies | exit 0 + `status=succeeded`; recovered `task.start` order == `expected_events.json`; all `expected_paths` exist non-empty; cyclic variant → nonzero + "cycle" | T-r21p4y (A1); T-ee8hzo (static acyclicity) |
| 2 | Token computation / assumptions | with `--budget-total`+`--pessimism-buffer`: `state.budget_counters.consumed_tokens` == recomputed estimator sum; `budget.charge`/`budget.reconcile` events; reconcile actual == charged estimate (no actuals path) | T-r21p4y (A2) |
| 3 | Dynamic inputs / dependency spec | injected `impl-t1/testwrite-t1/taskreview-t1` in `status.json` with `origin="injected"`; loop clones `origin="loop"` `__iterN`; `done` after final iteration | T-r21p4y (A3) |
| 4 | Logging | `run.log` exists; every line valid JSON w/ `ts/level/logger/msg`; event set ⊇ core subset; per-task `task_id` present | T-r21p4y (A4) |
| 5 | Per-task agent-output capture | `.orchestrator/runs/<id>/<task>/stdout.txt`+`stderr.txt` for every executed task (incl. injected+loop); `state.json` `output_artifact_path` set | T-r21p4y (A5) |
| 6 | CLI + flags e2e | `validate` ok/fail; `run` exit 0/1; `--rate-window invalid` → nonzero+ERROR; `status --workspace`; `resume` fail→resume | T-r21p4y (A6) |

Every area has ≥1 deterministic assertion (FR-7 satisfied). Real tier (T-g7rjh0) and
correctness (T-92o31p) add opt-in completion/functional checks on top.

## 20. Design Artifacts Checklist

**After HLD:** [x] logical topology (§7.1, §10) · [x] component breakdown (§7, §14) ·
[x] integration points (CLI boundary, control files) · [x] plugin/extension strategy
(agents-file executor switch; no engine change).
**After LLD:** [x] harness interfaces/contracts (§14) · [x] control-file schemas (§13) ·
[x] pseudocode per task (task tickets) · [x] edge cases (§21) · [x] ADRs (§9).
**Before sprint planning:** [x] tasks atomic (single ownership) · [x] tasks testable
(Given/When/Then acceptance) · [x] tasks unambiguous (≤3 days each).

## 21. Execution Readiness Gate

- **Can a junior implement without guessing?** Yes — each task ticket has explicit
  inputs/outputs, pseudocode, and acceptance criteria; the workflow shape + control-file
  contracts are fully specified.
- **Can an AI agent execute without ambiguity?** Yes — file paths, ids, control-file JSON, and
  the pre-seed procedure are concrete.
- **All interfaces/schemas defined?** Yes — harness contracts (§14), control-file schemas
  (§13), existing engine interfaces reused (no new production surface).
- **All failure scenarios handled?** Yes — enumerated edge cases below, each mapped to a tier.

**Edge cases (mandatory):**
- Empty/malformed spec → fixture tier + Area-6 `validate` (nonzero + ERROR).
- Cyclic dependencies → Area-1 cyclic variant (nonzero + "cycle"); fixture-tier acyclicity of
  the expanded graph.
- Missing input artifact → engine `missing_inputs` path; covered by ordering (integrate runs
  after producer) + a negative variant.
- Failed/retried/cancelled task → Area-6 `resume` (fail→resume); attempt-count check (T-92o31p).
- Duplicate node ids (injected manifest) → engine `InjectionError`; fixture-tier asserts
  manifest ids disjoint from spine.
- Executor/backend failure → real tier `requires_claude()` skip; Fake tier can't fail this way.
- Loop clock/iteration edges → `__iterN` gate path suffixing verified in seed_control_files;
  `max_iterations` cap asserted (loop stops even if verdict says continue).

**Verdict:** READY for implementation. No section is NO. (N=1 multi-chain generalization is a
tracked open question, not a blocker.)

## 22. Sprint Plan

Capacity (ASSUMPTION team_size=2, 40% overhead, <4-yr devs):
```
GrossHoursPerSprint    = 2 * 10 * 8 = 160
NetFocusHoursPerSprint = 160 * 0.60 = 96
CommitmentHoursPerSprint = 96 * 0.70..0.85 = 67..82 hours
```
~6 focus-hours/day → ~11–13 task-days committable per sprint.

**Sprint 1 (MVP, ~8–9 task-days) — independently shippable, always green:**
| Task | Est | Dep | MVP |
|------|-----|-----|-----|
| T-7592ux scaffold + gating | <2d | — | yes |
| T-1vuzyi sum-of-array workflow+fixtures | <3d | T-7592ux | yes |
| T-ee8hzo fixture tier | <2d | T-1vuzyi | yes |
| T-r21p4y deterministic tier (6 areas) | <3d | T-1vuzyi,T-ee8hzo | yes |
| T-g7rjh0 real-LLM gated harness skeleton | <2d | T-1vuzyi,T-r21p4y | yes |

**Sprint 2 (Phase 2/3, ~7 task-days):**
| Task | Est | Dep | MVP |
|------|-----|-----|-----|
| T-92o31p perf + correctness | <2d | T-r21p4y,T-g7rjh0 | no |
| T-n477z9 sorting example | <3d | T-r21p4y,T-ee8hzo | no |
| T-94tepb student-data example | <3d | T-r21p4y,T-ee8hzo | no |
| T-5bh03j docs-refresh | <2d | all | no |

Rationale for two sprints: the MVP boundary (always-green Phase-1) must be proven before
replication; splitting there de-risks token/flake exposure and gives a shippable increment.

## 23. Risks / Dependencies / Open Questions

Risks R1–R5 and open questions OQ-1–OQ-3 are enumerated in `EPIC.md`. Highlights:
- **R1** (CLI can't drive Fake payloads) → mitigated by ADR-002 pre-seeding; escalate a
  separate engine ticket only if insufficient.
- **R2** (shared paths → spurious cycles) → unique per-task paths + fixture-tier acyclicity.
- **R3** (real flake/burn) → double gate; content never asserted; low-burn problems.
- **R4** (real agent emits off-shape manifest) → pinned instruction contract; real tier asserts
  completion only; OQ-2 for multi-chain.
- **OQ-1** fuzzy inputs = fixed small matrix (Phase 1) vs seeded generator (deferred).
- **OQ-2** N>1 chains need an injected aggregator so `final-review` need not know chain count.
- **OQ-3** playground as `ao init` templates / docs walkthrough (future).

## 24. Handoffs & Ownership

| Task | Owner role | Hands to |
|------|-----------|----------|
| T-7592ux | developer | T-1vuzyi, T-ee8hzo, T-r21p4y (harness + gate) |
| T-1vuzyi | developer | T-ee8hzo, T-r21p4y, T-g7rjh0 (spec + fixtures + agents files) |
| T-ee8hzo | tester | T-r21p4y (assertion helpers) |
| T-r21p4y | tester | T-g7rjh0, T-92o31p, T-n477z9, T-94tepb (run-step shape) |
| T-g7rjh0 | tester | T-92o31p (real run + gate) — see its `HANDOFF.md` |
| T-92o31p | tester | — |
| T-n477z9 / T-94tepb | developer | T-5bh03j (examples to document) |
| T-5bh03j | architect | epic close |

## 25. Post-Implementation Docs-Refresh Ticket

`T-5bh03j-docs-refresh` (§22, non-MVP, depends on all) reconciles this design and
`playground/README.md` with shipped behavior: fix any drift in the tier table / area matrix /
workflow-shape diagram, link each of the six areas to its concrete test function, record ADR
deltas (e.g. if pre-seeding changed, if OQ-2 was resolved, if an engine ticket was filed),
verify the README `uv run` commands, and roll the epic `EPIC.md`/`STATUS.md` to Done in sync.
The epic is marked Done only after docs are verified against the code with green tests.
```
Definition of Done (epic): tiers 1,2,4-perf green in default CI (0 tokens); tier 3 + 4-correctness
green under AO_E2E_REAL_LLM=1 at least once; six-area matrix all linked to real tests; docs
reconciled; no src/ change (or engine gaps filed as separate tickets).
```
