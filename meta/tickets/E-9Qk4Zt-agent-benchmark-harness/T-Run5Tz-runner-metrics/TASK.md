# TASK: T-Run5Tz-runner-metrics

## Metadata
- Task ID: `T-Run5Tz-runner-metrics`
- Epic ID: `E-9Qk4Zt-agent-benchmark-harness`
- Owner: developer agent
- Created: 2026-07-22 · Last Updated: 2026-07-22
- Status: Draft · Estimate: 3.0 days (≤3)

## Requirements Mapping
- FR-5 (runner), NFR-2 (safe/bounded/deterministic/reproducible), NFR-1.

## Description
Implement `bench/runner.py::run_suite(suite, subject_spec, opts) -> RunResult` (design §4.5): the deterministic, resumable, bounded orchestration loop that ties subjects (T-Sbj9Ka) + graders (T-Grd7Vx) + metrics (T-Grd7Vx) together and persists per-task results incrementally. Emits structured log events (design §8) via the reused `logging_setup`.

Behavior:
- `bench_run_id = "<utc-date>-<suite.id>-<subject.id>"` (stable → reproducible dir name).
- Iterate `sorted(suite.tasks, key=id)` (deterministic order).
- Resume: load existing `run.json` metrics; skip tasks already present unless `--force`.
- Per task: fresh workspace under `playground/.tmp/bench/<bench_run_id>/<subject>/<task>` (path-guarded); copy fixture → `repo/`; compute `config_fingerprint = sha256(canonical(task)+canonical(subject)+ao_version)`; `Subject.run` → `Grader.grade` → `build_task_metric`; **write `run.json` after each task** (always-valid JSON → resumable).
- Bound: per-task `timeout_seconds` (task or `opts.default_timeout`); budget/max-turns passed into `RunContext`.
- One task raising `BenchError` → recorded as `subject_status="error"`, run continues.

## Acceptance Criteria
1. Given a 3-task fake suite + `FakeSubject`+`PytestGrader`, When `run_suite`, Then all 3 run once in id-sorted order; `run.json` lists 3 metrics; `bench.task.end` logged per task with solved/cost/wall.
2. Re-running the same suite/subject → all 3 skipped (idempotent), `run.json` unchanged; with `force=True` → all 3 re-run.
3. Interrupt after task 2 (simulate) → `run.json` holds 2 valid metrics; a resume runs only task 3.
4. A task whose subject raises → `subject_status="error"` recorded, remaining tasks still run (no all-or-nothing).
5. A fixture/workspace path escaping `playground/.tmp/bench` (`..`/symlink) → `SubjectError`/`BenchError`, task not run against an out-of-sandbox dir.
6. `config_fingerprint` is stable across identical (task,subject,ao-version) and changes when any of them changes.
7. Committed fixtures are never mutated (only `ws/repo` copies); workspaces persist after the run (audit; no auto-delete — learnings §18). `mypy`/`ruff` clean; no core edit.

## Risks
- Persist-after-each-task must keep `run.json` always-valid (write to temp + atomic rename) so an interrupt never corrupts resume state.

## Dependencies
- T-Sbj9Ka (subjects), T-Grd7Vx (graders + metrics).

## Pseudocode / Algorithm
```text
run_suite(suite, subject, opts):  # design §4.5 full pseudocode
  metrics = load_existing(result_dir/run.json)
  for task in sorted(suite.tasks, key=id):
    if task.id in metrics and not opts.force: continue
    ws = fresh_workspace(playground/.tmp/bench/<run_id>/<subject>/<task>)   # path-guarded
    copytree(task.fixture, ws/repo)
    try: sr = SUBJECT_REGISTRY[subject.type](subject).run(task, ctx)
         gr = GRADER_REGISTRY[task.grader.type]().grade(task.grader, ctx)
    except BenchError as e: sr=SubjectResult("error",...); gr=GradeResult(False,0.0,...,str(e))
    metrics[task.id] = build_task_metric(...); atomic_write_json(result_dir/run.json, ...)
  write_summary_md(...)   # (writer body lands in T-Rpt3Wq; runner calls it)
```

## Schemas / Interface Notes
- Interface: `run_suite(suite,subject_spec,opts)->RunResult`; `RunOptions{force,budget_total,max_turns,default_timeout}`.
- Triggers/events: `bench.run.start|task.start|task.end|run.end` (design §8).
- Artifacts: writes `benchmarks/results/<bench_run_id>/run.json` (committed) + workspaces under `playground/.tmp/bench` (gitignored).

## Handoff Boundary
- Upstream: T-Sbj9Ka/T-Grd7Vx.
- Downstream: T-Rpt3Wq owns the `run.json`/`summary.md` writer bodies + comparison; T-Cli8Nf wires `ao-bench run` to `run_suite`.

## Artifacts
- Docs/comments: this folder. Large outputs: committed `run.json` (small); transcripts stay gitignored (pointer only).
