# TASK: T-Grd7Vx-grader-registry

## Metadata
- Task ID: `T-Grd7Vx-grader-registry`
- Epic ID: `E-9Qk4Zt-agent-benchmark-harness`
- Owner: developer agent
- Created: 2026-07-22 · Last Updated: 2026-07-22
- Status: Draft · Estimate: 2.0 days (≤3)

## Requirements Mapping
- FR-3 (graders), FR-4 (metrics), NFR-1.

## Description
Implement the `Grader` ABC + `GRADER_REGISTRY` and the MVP graders (design §4.3), plus `metrics.py` (design §4.4). Register grader `type`s so `T-Sc4Hm2`'s `load_suite` validation now resolves against real graders.

Graders (each → `GradeResult{solved,score,detail,raw_tail}`):
- `PytestGrader` — run `cfg.command` (default `uv run pytest -q --tb=no`) in `repo/cfg.cwd`; **exit code is the source of truth** for `solved` (rc==0 ∧ tests>0 ∧ 0 failed); `score`=pass-rate parsed from the summary line (enrich only, A3); optional `pass_threshold` overrides `solved`.
- `CommandGrader` — run `cfg.command`; `solved = (rc==0)`; enables non-dev domains later.
- `FileAssertionGrader` — evaluate `cfg.assertions` (`exists`/`contains`/`equals_file`) against `repo/`; `score`=fraction passing.
- `FakeGrader` — returns a scripted verdict (test-only).

`metrics.py`: `build_task_metric(subject_id, task, SubjectResult, GradeResult, fingerprint) -> TaskMetric`; `aggregate([TaskMetric]) -> Aggregate` (solve_rate, total_cost_usd, total_wall_clock, mean_score, `cost_per_solved`) handling `cost_usd is None` (exclude, flag `cost_available:false`) and `solved==0` (`cost_per_solved=None`).

## Acceptance Criteria
1. `PytestGrader` on a fixture whose tests all pass (rc 0) → `solved=True, score=1.0`; on a fixture with 1 of 4 failing → `solved=False, score=0.75`; on a fixture with 0 tests collected → `solved=False`. (unit, using real tiny fixtures + a real pytest run)
2. `PytestGrader` with a `pass_threshold=0.5` and score 0.75 → `solved=True`; threshold 0.9 → `solved=False`.
3. `CommandGrader` with `command="true"` → solved; `"false"` → not solved. Grader command timeout/missing → `solved=False, score=0.0`, `detail` records it, no exception raised.
4. `FileAssertionGrader`: `exists`+`contains` mix → `score` = fraction passing; all pass → `solved=True`.
5. `aggregate`: with one `cost_usd=None` metric the cost sum excludes it and flags `cost_available:false`; with `solved==0`, `cost_per_solved is None` (no ZeroDivision).
6. Unknown grader type → `load_suite` (T-Sc4Hm2) now rejects via the real registry; `mypy`/`ruff` clean; no core edit.

## Risks
- pytest summary format drift → parse defensively; exit-code remains the pass source (A3).

## Dependencies
- T-Sc4Hm2 (models/registries/errors).

## Pseudocode / Algorithm
```text
PytestGrader.grade: rc,out=run(cmd,cwd,timeout); passed,failed,total=parse_summary(out)
  solved = rc==0 and total>0 and failed==0; score = passed/total if total else (1.0 if rc==0 else 0.0)
  if cfg.pass_threshold: solved = score >= cfg.pass_threshold
aggregate: solved=count(solved); cost=sum(m.cost_usd for m if not None)
  cost_per_solved = cost/solved if solved else None
```

## Schemas / Interface Notes
- Interface: `Grader.grade(cfg,ctx)->GradeResult`; `GRADER_REGISTRY[type]`. Models: `GraderConfig`,`GradeResult`,`TaskMetric`,`Aggregate` (design §6).
- Triggers/events: N/A. Artifacts: reads `repo/` in the workspace; writes nothing.

## Handoff Boundary
- Upstream: T-Sc4Hm2 models.
- Downstream: T-Run5Tz calls `grade` + `build_task_metric`; T-Rpt3Wq consumes `TaskMetric`/`Aggregate`.

## Artifacts
- Docs/comments: this folder. Large outputs: none.
