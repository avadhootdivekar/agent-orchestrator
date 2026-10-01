# TASK: T-l7t6TT-workflow-snapshot

## Metadata
- Task ID: `T-l7t6TT-workflow-snapshot`
- Epic ID: `E-k3AMEr-run-graph-canvas`
- Owner: `developer` (Dev A)
- Created: `2026-09-27`
- Last Updated: `2026-09-27`
- Status: `Done` (all 11 acceptance criteria verified; Gate G1 reviewer + tester sign-off both PASS — 2 reviewer SHOULD-FIX findings resolved post-gate, see `STATUS.md`)
- Estimate: `12 focus hours (1.5 days)`

## Requirements Mapping
- Requirement IDs: FR-2, D-2, NFR-1, NFR-5, NFR-7 · HLD §8.2 · ADR-0017 D2

## Description
Make a run's **static** dependency structure reconstructible from its own run directory, however
the run was launched:
- At every run-session start, append a `SpecSession` to `RunState.spec_sessions`. This is the
  source of truth.
- Write `<run_dir>/workflow.snapshot.<sha12>.json` **only if it does not already exist**
  (write-once, never overwritten).

The static spec is the live `WorkflowSpec` with every `state.injected_tasks` id filtered out.

**Insertion point** (corrected by the developer review): in `Orchestrator.run`
(`engine.py:~656`), place the call **immediately after**
`state = run_state or self._runstate.new_run(workflow)` (`~680`) and **before** the existing
`self._runstate.save(state)` (`~681`, the run's first save).

## Acceptance Criteria
1. **Models and constants.** `models.py` gains:
   - `SpecSession{session: int, started_at: str, spec_sha256: str}`
   - `RunState.spec_sessions: list[SpecSession] = []`
   - `WorkflowSnapshot{schema_version: int = 1, run_id: str, spec_sha256: str, written_at: str, workflow: WorkflowSpec}`

   `runstate.py` gains the named constants `WORKFLOW_SNAPSHOT_PREFIX`, `WORKFLOW_SNAPSHOT_SUFFIX`,
   `WORKFLOW_SNAPSHOT_SHA_CHARS = 12`, `WORKFLOW_SNAPSHOT_SCHEMA_VERSION = 1`, and
   `WORKFLOW_SNAPSHOT_MAX_BYTES = 20_000_000`.
2. **Canonical sha.** `canonical_spec_json(static)` =
   `json.dumps(static.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))`. The sha256
   of two equal specs built in different key orders is identical (test).
3. **Fresh run.** Given `ao run` via `CliRunner` on a static workflow with the fake executor, then:
   - `state.spec_sessions` has exactly 1 entry with `session == 1`
   - exactly one file `workflow.snapshot.<sha[:12]>.json` exists in the run dir
   - its `spec_sha256` equals the session's sha
   - its `workflow.tasks` ids equal the spec's task ids in order
4. **Injected excluded.** Given an emit workflow that injected `d,e`, when the run is resumed, then
   the resumed session's snapshot `workflow.tasks` contains **no** `d`/`e`, and its sha equals
   session 1's sha (same static spec, so no new file).
5. **Resume, unchanged spec.** `spec_sessions` has 2 entries with the same sha, there is still
   exactly 1 snapshot file, and its mtime is unchanged (no rewrite).
6. **Resume, changed spec.** Resuming with an edited static spec means `spec_sessions[1].spec_sha256`
   differs, **two** snapshot files exist, the first is byte-identical to before, and a
   `run.spec_changed_on_resume` warning is logged (asserted via `caplog`).
7. **Tolerant load.** `load_workflow_snapshot_at(run_dir, sha)` returns `None`, with a warning
   logged and never an exception, for each of:
   - a missing file
   - a file larger than `WORKFLOW_SNAPSHOT_MAX_BYTES`, **checked with `stat()` before reading**
     (test with a sparse file or a monkeypatched stat)
   - invalid JSON
   - a schema-invalid body
   - a body `spec_sha256` that differs from the requested sha
8. **Failure never fails the run.** With the snapshot write monkeypatched to raise `OSError`, the run
   still completes `succeeded`, a `run.snapshot_failed` warning is logged, and `spec_sessions` still
   records the session.
9. **Backward compatibility.** A pre-epic `state.json` loads with `spec_sessions == []`.
10. **The shared helper** `load_workflow_snapshot_at(run_dir: Path, sha: str)` is a module-level
    function in `runstate.py`. `RunStateStore.load_workflow_snapshot(run_id, sha)` delegates to it,
    and the dashboard (T-AsQ77e) reuses it. There is one parser and one size cap (DRY).
11. The full `pytest -q`, `ruff`, and `mypy src` pass, with no regression versus baseline.

## Risks
- Placement error: the snapshot is skipped for resumed runs. Mitigated by AC-4/5/6, which use real resume.
- Disk usage: one extra file per distinct spec per run, the size of a spec file. This is acceptable
  and documented (retention = run dir).

## Dependencies
- None. Can be done in parallel with T-AZzgT8 (both touch `models.py`, but in different classes, so
  rebase trivially). Blocks `T-M4qboy`.

## Pseudocode / Algorithm
See HLD §8.2, which is verbatim and authoritative. Summary:
```text
record_spec_session(state, workflow):
  static = workflow minus injected ids; sha = sha256(canonical_spec_json(static))
  prev = last session or None; append SpecSession(len+1, now, sha)
  path = run_dir / f"workflow.snapshot.{sha[:12]}.json"
  if not path.exists(): atomic_write(path, WorkflowSnapshot(...))
  if prev and prev.sha != sha: warn run.spec_changed_on_resume
engine: try record_spec_session(state, workflow) except OSError: warn run.snapshot_failed   # before first save
```

## Schemas / Interface Notes
- Interface: `RunStateStore.record_spec_session`, `RunStateStore.load_workflow_snapshot`,
  `runstate.load_workflow_snapshot_at`, `runstate.canonical_spec_json` (HLD §14.1).
- Spec / data schema: HLD §13.1 (file) and §13.2 (`spec_sessions`).
- Triggers / events: log events `run.snapshot_failed` and `run.spec_changed_on_resume` (HLD §15).
- Artifacts: `<workspace>/.orchestrator/runs/<run_id>/workflow.snapshot.<sha12>.json`.

## Handoff Boundary
- Upstream: none.
- Downstream: `T-M4qboy` (builder input), `T-AsQ77e` (loads via the shared helper). **Gate G1.**

## Artifacts
- Docs/comments: `meta/tickets/E-k3AMEr-run-graph-canvas/T-l7t6TT-workflow-snapshot/`
- Large outputs: N/A
