# TASK: T-Rpt3Wq-results-comparison-report

## Metadata
- Task ID: `T-Rpt3Wq-results-comparison-report`
- Epic ID: `E-9Qk4Zt-agent-benchmark-harness`
- Owner: developer agent
- Created: 2026-07-22 · Last Updated: 2026-07-22
- Status: Done · Estimate: 2.0 days (≤3)

## Requirements Mapping
- FR-6 (results + comparison), NFR-1.

## Description
Implement `bench/results.py` (design §4.6): the `run.json` + `summary.md` writers (called by the runner) and the cross-subject comparison writer (`ao-bench report`). Results are committed to git for preservation; transcripts are NOT committed (pointer only).

- `assemble_run_json(...)` → schema-versioned dict: `bench_run_id`, `suite`, `domain`, `subject{id,type,model,resolved_config}`, `env{ao_version,claude_version,os,git_sha}`, timestamps, `tasks[]` (TaskMetric dicts), `aggregate`.
- `write_summary_md(...)` → human table (`task | category | solved | score | cost($) | wall(s) | tokens | status`) + aggregate footer (`solved/total`, `solve_rate`, `total_cost`, `cost_per_solved`).
- `write_comparison(result_dirs)` → loads per-subject `run.json`s for the **same** suite; builds a per-task × per-subject matrix + per-subject aggregates; writes `benchmarks/results/<date>-<suite>-compare/comparison.{json,md}`. `comparison.md` = side-by-side subject table + per-task solved/cost matrix.

## Acceptance Criteria
1. After a runner run over a fake suite, `benchmarks/results/<date>-<suite>-<subject>/run.json` validates against a documented result schema shape and `summary.md` contains one row per task + an aggregate footer.
2. `write_comparison` over two subjects' result dirs (same suite) → `comparison.json` with a task→subject matrix and per-subject aggregates, and a `comparison.md` side-by-side table; `cost_per_solved` present per subject (None when solved==0).
3. Given result dirs for two **different** suites, `write_comparison` refuses (clear error), does not emit a misleading comparison.
4. A missing subject `run.json` → that subject shown as "not run" in the comparison, no crash.
5. `run.json` stores each task's `workspace` as a path pointer; no transcript content is copied into committed files. `mypy`/`ruff` clean; no core edit.

## Risks
- Divergent suite/task sets across subjects → key the matrix by union of task ids, mark missing cells explicitly.

## Dependencies
- T-Run5Tz (produces the metrics the writers serialize).

## Pseudocode / Algorithm
```text
assemble_run_json(...): {schema_version, bench_run_id, suite, domain, subject, env, started/ended, tasks[], aggregate}
write_comparison(dirs): runs=[load run.json]; assert same suite; matrix[task][subject]={solved,score,cost,wall}
  per_subject={sid: aggregate}; write comparison.json + side-by-side comparison.md
```

## Schemas / Interface Notes
- Interface: `assemble_run_json(...)`, `write_summary_md(...)`, `write_comparison(result_dirs)`.
- Data schema: `run.json` (design §4.6/§6) + `comparison.json`.
- Artifacts: writes committed files under `benchmarks/results/**`.

## Handoff Boundary
- Upstream: T-Run5Tz metrics.
- Downstream: T-Cli8Nf wires `ao-bench report` → `write_comparison`; T-Fx6Dp0 commits real result dirs.

## Artifacts
- Docs/comments: this folder. Large outputs: committed `run.json`/`summary.md`/`comparison.*` (compact).
