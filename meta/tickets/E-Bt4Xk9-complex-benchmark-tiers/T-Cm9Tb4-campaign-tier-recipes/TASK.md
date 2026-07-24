# TASK: T-Cm9Tb4-campaign-tier-recipes

## Metadata
- Task ID: `T-Cm9Tb4-campaign-tier-recipes`
- Epic ID: `E-Bt4Xk9-complex-benchmark-tiers`
- Owner: developer agent
- Created: 2026-07-22
- Last Updated: 2026-07-22
- Status: Done
- Estimate: 2.0 days

## Requirements Mapping
- FR-9 (whole-run USD cap via `ao-bench campaign`; per-tier make recipes; `--enable-xlarge` gate)

## Description
Add the **whole-run** budget level and the ergonomic per-tier entry points. `ao-bench campaign --suite S --subject J1 --subject J2 ...` runs the suite against a list of subjects sequentially, tracks cumulative spend across subjects against the tier's `cost_budget_usd_per_run`, stops launching further subjects once the whole-run cap is reached (recording them as budget-skipped), then writes the cross-subject comparison. Each per-subject `run_suite` is called with a per-subject cap = `min(remaining_whole_budget, tier.cost_budget_usd_per_subject)`. Add `make bench-medium`/`bench-large` recipes and an `--enable-xlarge` gate that refuses a disabled tier without the flag.

## File ownership (exclusive)
- `src/agent_orchestrator/bench/campaign.py` — NEW: `run_campaign(suite, subjects, ...)` orchestration.
- `src/agent_orchestrator/bench/cli.py` — add the `campaign` subcommand + `--enable-xlarge`; refuse a disabled tier. (Sequenced AFTER T-Pl3Rx7/T-Bg2Wq4 cli edits — same file.)
- `Makefile` — add `bench-medium`, `bench-large` (+ `bench-campaign`) recipes + `BENCH_*` knobs. (No existing target edited.)
- (read-only) `bench/runner.py` (`run_suite`, `resolve_result_dir`), `bench/results.py` (`build_comparison`/`write_comparison`), `bench/tiers.py`.

## Inputs / Outputs
- Inputs: a suite, a list of subject configs, the tier (from the suite), CLI overrides.
- Outputs: N per-subject `run.json`s + a `comparison.{json,md}`; a campaign summary of spend vs the whole-run cap.

## Acceptance Criteria
1. **Given** a `tier: small` suite + 3 fake subjects each scripted to cost $4/run and the tier whole-run cap $10 **When** `ao-bench campaign` runs **Then** the first 2 subjects run (cumulative $8 < $10), the 3rd is recorded/reported as budget-skipped (cumulative would exceed), and the comparison covers the subjects that ran.
2. Each per-subject run is capped at `min(remaining_whole_budget, tier.cost_budget_usd_per_subject)` — asserted (a subject cannot individually exceed the per-model cap, and cannot push the campaign over the whole-run cap by more than the bounded per-task overshoot).
3. **Given** an `xlarge` (disabled) suite **When** `ao-bench campaign`/`run` runs without `--enable-xlarge` **Then** it refuses with a clear message (exit 1); **with** `--enable-xlarge` it proceeds.
4. `make bench-medium` runs `dev-medium` against the medium subject set; `make bench-large` runs `swe-verified-mini` against the large subject set (both via `campaign`), overridable via `BENCH_*` knobs; `make -n` (dry-run) shows the correct invocations.
5. Campaign is **resumable**: re-running skips subjects whose `run.json` is already complete for the day (reuses `run_suite`'s own resume), and recomputes cumulative spend from existing runs.
6. Campaign exit code: 0 on clean/completed-or-budget-capped; 1 on a usage/spec error. Budget-skipping a subject is NOT a failure.

## Risks
- cli.py is a shared hot file (T-Bg2Wq4 `--cost-budget-usd`, T-Pl3Rx7 `--max-parallel`, this task `campaign`/`--enable-xlarge`). Sequence: T-Bg2Wq4 → T-Pl3Rx7 → T-Cm9Tb4. Keep additions additive (new command, new options), no edits to existing `run`/`report`/`list` bodies beyond adding options.
- Whole-run accounting must read each subject's actual `aggregate.total_cost_usd` (post-run), not the estimate, before deciding the next subject.

## Pseudocode / Algorithm
```text
# campaign.py
def run_campaign(suite_path, subject_paths, *, max_parallel=None, whole_budget=None, enable_xlarge=False, out_dir=None):
    suite = load_suite(suite_path); tier = load_tier_config(suite.tier)
    if not tier.enabled and not enable_xlarge: raise BenchError(f"tier {suite.tier!r} disabled; pass --enable-xlarge")
    whole_cap = whole_budget if whole_budget is not None else tier.cost_budget_usd_per_run
    spent = _sum_existing_campaign_spend(suite, subject_paths, out_dir)   # resume
    records, skipped = [], []
    for sj in subject_paths:
        remaining = whole_cap - spent
        if remaining <= 0: skipped.append(sj); continue
        per_subject_cap = min(remaining, tier.cost_budget_usd_per_subject)
        rec = run_suite(suite_path, sj, cost_budget_usd=per_subject_cap, max_parallel=max_parallel, out_dir=out_dir)
        spent += rec.aggregate.total_cost_usd
        records.append(rec)
    cmp = build_comparison([resolve_result_dir(r.bench_run_id, out_dir=out_dir) for r in records])
    write_comparison(cmp, ...)
    return CampaignResult(records=records, skipped_subjects=skipped, total_spent=spent, whole_cap=whole_cap)
```

## Schemas / Interface Notes
- CLI: `ao-bench campaign --suite S (--subject J)... [--max-parallel N] [--cost-budget-usd U] [--enable-xlarge] [--out-dir D]`.
- `run`/`campaign` both refuse a disabled tier unless `--enable-xlarge`.
- Triggers/events: `bench.campaign.start/subject.skip_budget/campaign.end`. Artifacts: per-subject `run.json` + `comparison.*` (committed).
- Makefile knobs: `BENCH_MEDIUM_SUITE`, `BENCH_MEDIUM_SUBJECTS`, `BENCH_LARGE_SUITE`, `BENCH_LARGE_SUBJECTS`.

## Handoff Boundary
- Upstream: T-Bg2Wq4 (per-run cap), T-Pl3Rx7 (max_parallel), T-Tr1Km8 (whole-run cap + xlarge flag), T-Md7Vc3/T-Sw5Hd9 (the suites the recipes target).
- Downstream: T-Ts0Xn5 (campaign tests), T-Dc1Yg7 (docs the recipes), the PLAN runs.

## Artifacts
- Docs/comments: this folder. Large outputs: none.
