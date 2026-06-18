# EPIC: E-v0f0c9-logging-dynamic-workflows

## Metadata
- Epic ID: `E-v0f0c9-logging-dynamic-workflows`
- Title: Structured Logging & Traceability + Dynamic Workflow Support
- Owner: architect
- Created: 2026-06-18
- Last Updated: 2026-06-18 (T-17v8sr done; epic closed)
- Status: Done

## Summary
- Goal: Give operators full run-time observability (structured JSON logs, captured agent output, live status artifact) and let workflows grow at run time — discover tasks dynamically, run iterative dev↔review loops, and run post-development review/audit cycles — while preserving the engine's deterministic / idempotent / resumable / NFR-1 guarantees.
- Scope In:
  - **Area 1 — Logging & Traceability**: stdlib `JSONFormatter`, per-run `run.log` (JSON lines), per-task captured agent stdout/stderr artifacts, `status.json` snapshot updated on every task transition, `ao status` reading the snapshot.
  - **Area 2 — Dynamic Workflows**: `emit_tasks` dynamic task injection (engine re-expands the DAG mid-run), a `LoopSpec` construct for iterative loops (2b) and post-dev review/audit cycles (2c), and the `RunState` extensions needed to keep both resumable.
- Scope Out:
  - Distributed/remote log shipping, log rotation, OpenTelemetry/metrics exporters.
  - Parallel task execution (engine remains linear topo iteration).
  - Nested loops (a loop body that itself contains another `LoopSpec`) — single-level loops only in this epic.
  - Streaming live tail of agent output to console (output is captured to files; console keeps structured log lines).
  - Conditional branching beyond the loop gate (no general `if/else` DAG edges).

## Requirements

### Feature Area 1 — Structured Logging & Traceability
- FR-1: A user can read the latest run status at any time from a machine- and human-readable `status.json` snapshot, refreshed after every task state transition.
- FR-2: Engine and executors emit structured JSON log records (one JSON object per line) through Python stdlib `logging` + a custom `JSONFormatter` (no new third-party dependency).
- FR-3: Each run writes a per-run log file at `.orchestrator/runs/<run_id>/run.log` in addition to console logging.
- FR-4: An executor run's stdout/stderr is captured to per-task artifact files under `.orchestrator/runs/<run_id>/<task_id>/` so downstream agents/auditors can read them by path.
- FR-5: `TaskResult` carries the path(s) to captured output so the engine can record them in `RunState` without reading their contents (NFR-1 preserved).
- FR-6: `ao status <run_id>` reads `status.json` (falling back to `state.json`) and prints a status table.

### Feature Area 2 — Dynamic Workflow Support
- FR-7 (2a): A task with `emit_tasks: true` writes a `task_manifest_path` JSON file containing a list of `TaskSpec` objects; on success the engine validates, merges them into the live `WorkflowSpec`, rebuilds the DAG, re-runs cycle detection, and recomputes the remaining topo order from the injection point.
- FR-8 (2a): Injected tasks are recorded in `RunState` so that resume reconstructs the expanded workflow without re-running the emitter.
- FR-9 (2b): A `LoopSpec` workflow-level construct repeats an ordered group of body tasks as a unit; a designated gate task's JSON output (`gate_field`) decides continue/stop; a hard `max_iterations` cap always bounds the loop.
- FR-10 (2b): Each loop iteration clones the body tasks with a deterministic iteration suffix (`__iterN`), injects them via the same mechanism as FR-7, and chains iterations so iteration N+1 depends on iteration N.
- FR-11 (2c): Post-development review/audit cycles are expressed with the same `LoopSpec` (a body of review→remediate tasks gated by a review verdict artifact); static downstream tasks may declare `depends_on: [<loop_id>]`.
- FR-12: Loops and dynamic injection are resumable and deterministic — a re-run from spec + `RunState` reproduces the same expanded graph.

### Non-Functional Requirements
- NFR-1 (preserved): The engine never reads payload artifact contents. The only files it reads are machine-written **control** files: the existing `output_manifest`, the new `task_manifest_path`, and loop `gate_output_path`. These are explicitly classified as control-plane reads (same exception class as `read_manifest`).
- NFR-2: Deterministic — fixed clock + fixed iteration suffixing make injected graphs and log timestamps reproducible in tests.
- NFR-3: Idempotent / resumable — injected tasks and loop iterations recorded in `RunState`; replays skip completed work via existing `should_skip`.
- NFR-4: No new mandatory third-party dependency (stdlib `logging` + pydantic v2 only).
- NFR-5: All new spec fields are reflected in `specs/workflow.schema.json` (which is `additionalProperties: false`).
- NFR-6: Safety — injected/cloned task ids are namespaced and de-duplicated; a re-injection cannot silently overwrite an existing task; cycle detection runs after every injection.

## Interface Contracts

Authoritative signatures for everything this epic adds. These are the frozen
contracts each task implements against; the per-task `TASK.md` may not diverge
from them without updating this section. (Deep rationale: HLD §3, §6.)

### New module `src/agent_orchestrator/logging_setup.py` (T-pd2vu2)
```python
class JSONFormatter(logging.Formatter):
    """Render a LogRecord as one JSON object per line.
    Fields: ts (ISO-8601 UTC), level, logger, event, msg,
            run_id?, task_id?, attempt?, exit_code?, plus any record.extra."""
    def format(self, record: logging.LogRecord) -> str: ...

def attach_run_handler(run_id: str, log_path: str) -> logging.Handler:
    """Attach a per-run FileHandler(log_path, JSONFormatter) + console StreamHandler
    to the package root logger. Idempotent per run_id. Returns the file handler."""

def detach_run_handler(run_id: str) -> None:
    """Remove + close the handler attached for run_id. Safe if absent (no leak)."""

def get_run_logger(run_id: str, task_id: str | None = None) -> logging.LoggerAdapter:
    """LoggerAdapter that injects run_id/task_id into every record's extra."""
```
- Console + file share one `JSONFormatter` instance; handler lifecycle is owned
  by `Orchestrator.run()` in a `try/finally` (attach at start, detach in finally).

### `artifacts.py` — control-read helpers (T-17av6o, T-sfdybw)
```python
def read_task_manifest(store: ArtifactStore, path: str) -> list[TaskSpec]:
    """Read {"tasks":[<TaskSpec>...]}; raise ValueError on missing/invalid/wrong-shape.
    The ONLY new payload-content read for 2a; mirrors read_manifest scope (NFR-1)."""

def read_gate(store: ArtifactStore, path: str, field: str = "continue") -> bool:
    """Read {"<field>": bool, ...}; raise ValueError if file missing, field absent,
    or value is non-bool. The ONLY new content read for 2b/2c."""
```
- Both join `read_manifest` on the **control-file allow-list** (ADR-004). No
  other module may read artifact content. A grep-able audit gate enforces this.

### `executors/base.py` + executors (T-f0xkdw)
- Contract unchanged signature (`execute(ctx) -> TaskResult`), new obligations:
  - Executor writes captured `stdout.txt` / `stderr.txt` into `ctx.output_dir`
    (creating it; empty files still written when output is empty).
  - Sets `TaskResult.output_artifact_path = ctx.output_dir`.
  - `FakeExecutor` honors this too (writes stub capture files) so integration
    tests exercise the same path.
- `TaskContext` carries two additional fields added for Area 2 (not in the
  original EPIC interface table):
  - `task_manifest_path: str | None` — resolved path for an `emit_tasks` task
    to write its task manifest JSON. None when `task.emit_tasks` is False.
  - `gate_output_path: str | None` — resolved, iteration-suffixed path for a
    loop gate task to write its verdict. None when the task is not a gate.
  Both are paths only (NFR-1 safe); the engine sets them in `_run_with_retries`
  before constructing `TaskContext`.

### `runstate.py` (T-1gsn0l)
```python
def write_status(self, state: RunState) -> None:
    """Derive status.json from RunState and atomic-write it next to state.json.
    Called INSIDE save() so the two can never diverge (ADR-002, R4 mitigation)."""
```

### `engine.py` private helpers (T-17av6o, T-sfdybw)
```python
def _inject(self, new: list[TaskSpec], workflow, state, origin: str) -> None
def _clone_body(self, loop: LoopSpec, iter_n: int) -> list[TaskSpec]
def _should_continue_loop(self, loop: LoopSpec) -> bool   # wraps read_gate
def _recompute_order(self, graph, done: set[str]) -> tuple[list[str], int]
```

### `errors.py` — new structured errors
```python
class InjectionError(OrchestratorError): ...  # duplicate id / bad merge (2a)
class LoopError(OrchestratorError): ...        # loop config / runaway (2b/2c)
class GateError(OrchestratorError): ...        # gate file/field invalid
```

### CLI contract — `ao status` (T-1gsn0l)
- **Current** (`cli.py:201`): loads `state.json`, requires the full
  `--workflow/--reposets/--agents` triplet just to resolve the workspace.
- **Target**: prefer `status.json`, fall back to `state.json`; accept
  `--workspace <root>` (or `AO_WORKSPACE_ROOT`) so the spec triplet is **not**
  required to inspect a run. Exit `0` if found, `1` if no run dir.
- Output: existing table + `current_task` and per-task `origin` columns.

## Control-File Schemas (machine-written, on the NFR-1 allow-list)

These are the only files the engine reads for *content*. Each is machine-written
by an agent/executor, validated on read, and produces a structured error on
violation (no silent skips).

| File | Path | Schema | Reader |
|------|------|--------|--------|
| Output manifest (existing) | `task.output_manifest` | `{"artifacts": [str, ...]}` | `read_manifest` |
| Task manifest (2a) | `task.task_manifest_path` | `{"tasks": [ <TaskSpec>, ... ]}` | `read_task_manifest` |
| Loop gate verdict (2b/2c) | `loop.gate_output_path` (suffixed per iter) | `{"continue": bool, ...optional ...}` | `read_gate` |
| Status snapshot (FR-1) | `.orchestrator/runs/<run_id>/status.json` | see below | `ao status` |

`status.json` shape (derived from `RunState`, never authored by hand):
```json
{ "run_id": "...", "workflow_id": "...", "status": "running|succeeded|failed|cancelled",
  "updated_at": "ISO-8601", "current_task": "<first running/pending or null>",
  "counts": {"succeeded": 0, "failed": 0, "running": 0, "pending": 0, "skipped": 0},
  "tasks": [ {"id": "...", "status": "...", "attempts": 0,
              "origin": "static|injected|loop", "output_artifact_path": "..."} ] }
```

## Test Fixtures & Harness

New shared harness lands in `tests/conftest.py` (none exists today) so every
test in the epic builds on the same deterministic primitives.

### Shared `conftest.py` fixtures
| Fixture | Provides |
|---------|----------|
| `fixed_clock` | `lambda: datetime(2026,1,1,tzinfo=UTC)` → reproducible `run_id`, timestamps, `__iterN` ids (NFR-2). |
| `workspace(tmp_path)` | A clean workspace root + `AO_WORKSPACE_ROOT`. |
| `store(workspace)` | `LocalFsArtifactStore(workspace)`. |
| `rs_store(workspace, store, fixed_clock)` | `RunStateStore(..., clock=fixed_clock)`. |
| `make_workflow` | Factory: build a `WorkflowSpec` from a compact dict (tasks/loops) for unit tests without spec files. |
| `read_jsonl(path)` | Parse `run.log`, asserting every line is valid JSON; returns list of records. |
| `read_status(run_dir)` | Load + return `status.json` as a dict. |

### `FakeExecutor` extensions (T-f0xkdw / T-5isej3)
Extend the existing `tests`-facing `FakeExecutor` (it already supports
`behaviors`, `write_outputs`, `manifest_payloads`) with:
- `emit_payloads: dict[task_id, dict]` → writes `{"tasks":[...]}` to the task's
  `task_manifest_path` on success (drives 2a).
- `gate_payloads: dict[task_id, list[bool]]` → on the K-th invocation of a gate
  task, writes `{"continue": <bool>}` to its (suffixed) `gate_output_path`,
  letting a test script “continue, continue, stop” to exercise N iterations.
- Always writes stub `stdout.txt`/`stderr.txt` into `ctx.output_dir` so capture
  assertions and downstream-consumes-output paths are covered.

### Spec fixtures (added under `specs/examples/`)
- `workflow-dynamic.json` — one `emit_tasks` emitter + a static consumer of an
  injected output (golden case for 2a + resume).
- `workflow-loop.json` — a `dev → review` body with a gate task and
  `max_iterations`, plus a static `finalize` task `depends_on:[<loop_id>]`
  (golden case for 2b/2c). Both validate against `workflow.schema.json`.

### Interface-contract tests (one per public surface above)
Each contract in **Interface Contracts** gets a focused unit test asserting its
happy path **and** its named error (e.g. `read_task_manifest` on a non-dict, on
a missing `tasks`, on an invalid `TaskSpec`; `read_gate` on missing field /
non-bool; `_inject` on duplicate id → `InjectionError`).

## Audit / Quality Gates

Gates are enforced at three layers; a task is **not** done until its row passes.

### Per-task gate (Definition of Done for every implementation task)
- `ruff check .` and `ruff format --check .` clean.
- `mypy .` clean on touched modules.
- `pytest -q` green (full suite, not just new tests — see Regression Verification).
- Any new/changed spec field present in `specs/workflow.schema.json` with
  `additionalProperties:false` still honored, and `python -m agent_orchestrator.validate`
  validates all existing + new example specs.

### Runtime control-plane gates (engine-enforced, each with a test)
| Gate | Triggered by | Failure mode |
|------|--------------|--------------|
| Manifest-shape gate | `read_task_manifest` | emitter task → `failed`, run fails, `ValueError` logged |
| Duplicate-id gate (NFR-6) | `_inject` | `InjectionError`, run fails |
| Cycle gate (R2) | `build_dag` after each injection | `CycleError` naming nodes |
| Gate-field gate | `read_gate` | `GateError`, run fails |
| `max_iterations` gate | loop controller | loop stops cleanly even if gate says continue |
| Emit/loop-field cross-validation | `spec.cross_validate` | `SpecValidationError` at load |

### Epic-level audit gate (run once before epic close)
- **NFR-1 boundary audit**: `grep`-based check that artifact-content reads exist
  *only* inside `artifacts.read_manifest|read_task_manifest|read_gate`; any other
  `.read_text()/json.load` on a resolved artifact path fails the audit.
- **Determinism audit**: a dynamic spec + manifest run twice under `fixed_clock`
  yields byte-identical expanded task ids and topo order.
- **Coverage gate**: ≥80% line coverage on every module this epic touches.
- **Schema round-trip**: every example spec loads, re-serializes, and re-validates.

## Regression Verification

> Explicitly required step — runs in addition to new-feature tests, at the end of
> every task and again before epic close. No task is "done" on green new tests
> alone; the pre-existing suite must remain green.

1. **Baseline capture** (once, at epic start): on the merge-base, record
   `pytest -q` pass count and `pytest --cov` totals → store in epic `STATUS.md`
   as the regression baseline. Current baseline command:
   `pytest -q | tail -1` and `coverage report` snapshot.
2. **Per-task regression run** (gate on each `TASK.md`): after the change,
   run the **full** suite + static checks, not only the task's new tests:
   ```bash
   ruff check . && ruff format --check . && mypy . && pytest -q
   ```
   Acceptance: every test that passed at baseline still passes (zero new
   failures, zero new errors); no skips introduced for previously-running tests.
3. **Behavioral non-regression**: the existing E2E in `tests/test_integration.py`
   (example workflow → all outputs present, status table) stays green, proving
   the engine-loop refactor (T-17av6o) is backward-compatible for **static**
   workflows (a static workflow must behave identically to pre-epic).
4. **Spec non-regression**: `python -m agent_orchestrator.validate specs/`
   validates all pre-existing example/self-dev specs unchanged.
5. **Epic-close regression**: re-run steps 2–4 on the integrated branch; compare
   against the baseline recorded in step 1 and attach the diff to epic `STATUS.md`.

## Task List
- [x] `T-pd2vu2-structured-logging` — Done (Area 1).
- [x] `T-f0xkdw-agent-output-capture` — Done (Area 1).
- [x] `T-1gsn0l-run-status-artifact` — Done (Area 1).
- [x] `T-17av6o-dynamic-task-injection` — Done (Area 2).
- [x] `T-sfdybw-loop-construct` — Done (Area 2).
- [x] `T-38jqbk-tests-logging` — Done (Area 1).
- [x] `T-5isej3-tests-dynamic` — Done (Area 2).
- [x] `T-17v8sr-docs-refresh` — Done. HLD reconciled with shipped behavior; open questions resolved; ADR-007 added.

## Dependency Order
```
T-pd2vu2 (logging core) ──┬─> T-f0xkdw (output capture) ──┐
                          └─> T-1gsn0l (status artifact) ──┤
                                                           ├─> T-38jqbk (tests-logging)
T-17av6o (dynamic injection) ──> T-sfdybw (loop construct) ┤
                                                           └─> T-5isej3 (tests-dynamic)
all of the above ────────────────────────────────────────────> T-17v8sr (docs-refresh)
```
- `T-f0xkdw` and `T-1gsn0l` depend on `T-pd2vu2` (shared logging/path conventions).
- `T-sfdybw` depends on `T-17av6o` (loop iterations reuse the injection mechanism).
- Test tasks depend on the features they cover.
- `T-17v8sr` depends on all implementation tasks.

## Risks and Dependencies
- R1: `emit_tasks` injection mutates a previously immutable graph; resume correctness depends on faithfully persisting and replaying injected tasks. Mitigation: persist full injected `TaskSpec`s in `RunState`; rebuild the merged workflow before resuming.
- R2: Loop suffixing + dependency chaining can introduce cycles if a body task depends on a non-body task that depends back on the loop. Mitigation: re-run `build_dag` cycle detection after every injection; reject with structured error.
- R3: NFR-1 erosion — control-file reads must stay narrowly scoped. Mitigation: route all control reads through `artifacts.read_*` helpers; document the allow-list.
- R4: `status.json` and `state.json` can diverge. Mitigation: derive `status.json` purely from `RunState`; write it inside the same save path so they update together.

## Links
- Design doc (HLD): `docs-md/logging-dynamic-workflows-hld.md`
- Sprint plan: this EPIC + per-task `TASK.md` files under `ad/tickets/E-v0f0c9-logging-dynamic-workflows/`
- Output artifacts (if any): `output/...` (none expected; code lands in `src/agent_orchestrator/`)
