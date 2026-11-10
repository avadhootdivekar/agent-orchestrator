"""Whole-run USD budget across a list of subjects -- `ao-bench campaign` (E-Bt4Xk9
T-Cm9Tb4, ADR-0009 D3 second budget level).

`run_suite` (bench/runner.py, T-Bg2Wq4/T-Pl3Rx7) already enforces a *per-`ao-bench run`*
USD cap (one suite x one subject). This module adds the *whole-run* (all subjects) cap:
`run_campaign(suite_path, subject_paths, ...)` calls `run_suite` once per subject, IN
THE GIVEN ORDER, tracking cumulative ACTUAL spend (each subject's post-run
`aggregate.total_cost_usd`, never an estimate) against a whole-run cap. Once the
cumulative spend already reaches the cap, every remaining subject is recorded
`launched=False`/`status="skipped_budget"` WITHOUT calling `run_suite` at all (no
workspace materialized, no subprocess spawned) -- mirrors `runner.py`'s own
check-before-schedule discipline for the per-task budget, one level up. Each subject
that does launch is capped at ``min(remaining_whole_budget, per_subject_cap)`` so no
single subject can, on its own, blow the whole-run cap (bounded task-level overshoot
from `run_suite`'s own D4 concurrency guard still applies -- documented there, not
re-derived here).

Staged repeats (HLD §6, `--repeats N` / `--no-staged`): stage 1 is the loop above at
`repeat=1`. With `repeats > 1` and `staged=True` (the default) the campaign then loops

    build the comparison over every run dir so far;
    stop if every subject pair's paired-bootstrap CI (score) excludes 0;
    else run repeat k+1 for EVERY launched subject, restricted to the tasks that can still
    move a CI (`select_repeat_tasks`); stop at `repeats`, when no such task exists, or when
    the whole-run cap is spent.

`select_repeat_tasks` re-runs a task iff, over the subjects that ran it, (a) their repeat-mean
scores are not all identical (the subjects DISAGREE), or (b) some subject's own repeats
already differ (observed run-to-run variance), or (c) some subject has no attempted sample
of it yet (every repeat so far was an infra error / budget skip). Tasks every subject scores
identically cannot change a paired difference, so they are not paid for twice. `staged=False`
instead repeats every task for every subject up to `repeats` (the non-adaptive protocol).
Each executed repeat is recorded in `CampaignRecord.stage_log` and the comparison covers
all repeat dirs. The whole-run cap applies across ALL repeats (spend accumulates); the
per-subject cap applies to each `run_suite` call.

Reuses (read-only, no edits to any of these modules):
  - `bench/spec.py`: `load_suite`/`load_subject`.
  - `bench/tiers.py`: `load_tier_config`/`resolve_effective` (single source of truth for
    the CLI-flag > tier-default precedence -- this module does not re-derive it).
  - `bench/runner.py`: `run_suite`, `resolve_result_dir`, `compute_bench_run_id` (the
    last one lets this module PREDICT a not-yet-run subject's result dir -- same
    derivation `run_suite` uses internally -- so a budget-skipped subject can still be
    fed to `build_comparison` and show up as "not run" via that function's own,
    already-built-for-this missing-dir handling).
  - `bench/results.py`: `build_comparison`/`write_comparison` for the N-way comparison.

Also hosts `enforce_tier_enabled`, the disabled-tier gate (ADR-0009: `benchmarks/
tiers.json` `enabled:false`, e.g. the defined-but-infeasible xlarge tier) shared by
`ao-bench run` AND `ao-bench campaign` (`bench/cli.py` imports it for both -- a single
implementation avoids duplicating the same three-line check/message in two commands).

Import direction (SI-1): reads only from sibling `bench/` modules; nothing in core or
in `bench/{spec,runner,tiers,results}.py` imports this module.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from .errors import BenchError, EnvMismatchError
from .results import ComparisonRecord, build_comparison, write_comparison
from .runner import compute_bench_run_id, resolve_result_dir, run_suite
from .spec import SubjectSpec, load_subject, load_suite
from .stats import ci_excludes_zero
from .tiers import TierConfig, load_tier_config, resolve_effective

# ---------------------------------------------------------------------------
# Named constants (CLAUDE.md: no magic literals)
# ---------------------------------------------------------------------------

# 1.1: additive -- repeats_requested/repeats_done/staged/stage_log/stage_stop_reason on the
# campaign and repeat_run_dirs/repeat_cost_usd on each subject.
CAMPAIGN_SCHEMA_VERSION = "1.1"
CAMPAIGN_JSON_FILENAME = "campaign.json"

# `<utc-date>-<suite.id>-campaign` -- mirrors `runner.compute_bench_run_id`'s
# `<date>-<suite>-<subject>` and `results.compute_compare_id`'s `<date>-<suite>-compare`
# naming conventions exactly (same date format, same trailing-suffix idea).
_CAMPAIGN_ID_DATE_FORMAT = "%Y-%m-%d"
CAMPAIGN_DIR_SUFFIX = "campaign"

# `CampaignSubjectRecord.status` values (distinct from `BenchTaskRecord.subject_status`,
# a per-TASK status -- this is a per-SUBJECT status one level up).
STATUS_COMPLETED = "completed"
STATUS_SKIPPED_BUDGET = "skipped_budget"

# `CampaignRecord.stage_stop_reason` values: why the repeat loop ended.
STOP_ALL_DECIDED = "ci_excludes_zero"  # every subject pair's CI excludes 0
STOP_NO_CANDIDATES = "no_disagreement"  # nothing left that could move a CI
STOP_NO_PAIRS = "no_pairs"  # fewer than two subjects ran: nothing to compare
STOP_BUDGET = "budget_exhausted"  # the whole-run cap is spent
STOP_MAX_REPEATS = "max_repeats"  # `repeats` reached with some pair still undecided
STOP_NON_STAGED = "non_staged_complete"  # `staged=False` ran every requested repeat
STOP_NOT_REQUESTED = "single_repeat"  # `repeats == 1`
# T-Ws7Iso: stage 1 showed the subjects did not share one clean environment, so more repeats
# would only buy a comparison `build_comparison` refuses -- stop and report instead.
STOP_ENV_MISMATCH = "environment_mismatch"

FIRST_REPEAT = 1
# Tolerance for "scores are identical" (they are means of floats in [0, 1]).
_SCORE_EPS = 1e-9


def _utc_now() -> datetime:
    return datetime.now(UTC)


def compute_campaign_id(suite_id: str, clock: Callable[[], datetime]) -> str:
    """`<utc-date>-<suite.id>-campaign` -- the campaign's own result-dir name. Exposed
    (not `_`-prefixed) for the same reason `runner.compute_bench_run_id`/
    `results.compute_compare_id` are: so a caller (the CLI, or a test) can predict the
    output dir without duplicating this derivation.
    """
    return f"{clock().strftime(_CAMPAIGN_ID_DATE_FORMAT)}-{suite_id}-{CAMPAIGN_DIR_SUFFIX}"


def enforce_tier_enabled(tier: str, tier_config: TierConfig, enable_xlarge: bool) -> None:
    """Refuse a disabled tier (`benchmarks/tiers.json` `enabled:false`, e.g. the
    defined-but-infeasible xlarge tier) unless *enable_xlarge* is set.

    Shared by both `ao-bench run` and `ao-bench campaign` (this task's own contract:
    "`--enable-xlarge` ... gates ANY disabled tier" -- the flag name is historical/
    literal per the EPIC, kept as specified even though it guards every tier's
    `enabled` bit, not only xlarge's).
    """
    if tier_config.enabled or enable_xlarge:
        return
    raise BenchError(
        f"Tier {tier!r} is disabled in benchmarks/tiers.json (enabled=false); pass "
        "--enable-xlarge to run it anyway (this flag gates ANY disabled tier, not only "
        "xlarge)."
    )


# ---------------------------------------------------------------------------
# campaign.json shape
# ---------------------------------------------------------------------------


class CampaignSubjectRecord(BaseModel):
    """One subject's outcome within a campaign (TASK.md: "per-subject: subject id, run
    dir, aggregate cost, solve rate, status"). `run_dir`/`total_cost_usd`/`solved`/`total`/
    `solve_rate` describe the repeat-1 run (as before); later repeats are listed in
    `repeat_run_dirs` (in repeat order) with their summed spend in `repeat_cost_usd`."""

    subject_id: str
    subject_path: str
    status: str
    launched: bool
    run_dir: str | None = None
    total_cost_usd: float | None = None
    solved: int | None = None
    total: int | None = None
    solve_rate: float | None = None
    repeat_run_dirs: list[str] = []
    repeat_cost_usd: float = 0.0


class StageLogEntry(BaseModel):
    """One executed repeat round: which `repeat` it was, on which `task_ids`, with which
    launched `subjects`, and `reason` (why this round ran and why these tasks)."""

    repeat: int
    task_ids: list[str]
    reason: str
    subjects: list[str] = []


class CampaignRecord(BaseModel):
    """The persisted `campaign.json` shape (TASK.md: "suite, tier, caps, per-subject
    [...]"). `total_spent_usd` covers every repeat."""

    schema_version: str = CAMPAIGN_SCHEMA_VERSION
    campaign_id: str
    suite_id: str
    tier: str
    generated_at: str
    whole_run_cap_usd: float
    per_subject_cap_usd: float
    max_parallel: int
    total_spent_usd: float
    subjects: list[CampaignSubjectRecord]
    comparison_json: str | None = None
    comparison_md: str | None = None
    # Repeats (HLD §6): how many were asked for, how many rounds actually ran (0 = nothing
    # launched at all), whether the adaptive protocol was used, the per-round log and why
    # the loop stopped.
    repeats_requested: int = 1
    repeats_done: int = 1
    staged: bool = True
    stage_log: list[StageLogEntry] = []
    stage_stop_reason: str | None = None
    # T-Ws7Iso: why the comparison is stamped non-isolated (empty = clean, shared env).
    env_problems: list[str] = []


class CampaignResult(BaseModel):
    """In-memory return value of `run_campaign` -- mirrors the role `BenchRunRecord`
    plays for `run_suite` (the CLI reads this straight back rather than re-parsing the
    just-written `campaign.json`)."""

    record: CampaignRecord
    campaign_dir: str
    comparison: ComparisonRecord | None = None


def _persist_campaign_record(campaign_dir: Path, record: CampaignRecord) -> Path:
    campaign_dir.mkdir(parents=True, exist_ok=True)
    path = campaign_dir / CAMPAIGN_JSON_FILENAME
    path.write_text(record.model_dump_json(indent=2))
    return path


# ---------------------------------------------------------------------------
# Staged-repeat decision rule (HLD §6) -- pure functions of a ComparisonRecord
# ---------------------------------------------------------------------------


def undecided_pairs(comparison: ComparisonRecord) -> list[tuple[str, str]]:
    """Subject pairs whose paired-bootstrap CI on the score difference does NOT exclude 0
    (or that share no attempted task yet): the pairs more repeats could still decide."""
    return [
        (e.a, e.b)
        for e in comparison.pairwise
        if e.ci_lo is None or e.ci_hi is None or not ci_excludes_zero(e.ci_lo, e.ci_hi)
    ]


def select_repeat_tasks(comparison: ComparisonRecord) -> dict[str, str]:
    """Tasks worth another repeat -> why (`"disagree"`, `"variance"` or `"infra-retry"`).

    Over the subjects that ran a task (cells present), the task qualifies when
      - `disagree`: the attempted cells' repeat-mean scores are not all identical -- the
        subjects differ on it, so it carries the paired difference;
      - `variance`: some attempted cell's own repeats already differ (`score_sd > 0`) --
        the subject itself is noisy there;
      - `infra-retry`: some subject has NO attempted sample of it yet (every repeat so far
        was an infra error / budget skip), so the pair has no data on this task at all.
    A task all subjects score identically, with no noise and no hole, cannot move a paired
    difference and is left alone. The result is ordered most-informative first (largest
    score spread across subjects, then largest within-subject sd, then task id) -- the order
    of `StageLogEntry.task_ids`; `run_suite` itself always runs in id order.
    """
    scored: list[tuple[float, float, str, str]] = []
    for task_id in comparison.task_ids:
        cells = [c for c in comparison.matrix.get(task_id, {}).values() if c is not None]
        attempted = [c for c in cells if c.n > 0]
        spread = (
            max(c.score_mean for c in attempted) - min(c.score_mean for c in attempted)
            if attempted
            else 0.0
        )
        max_sd = max((c.score_sd for c in attempted), default=0.0)
        if spread > _SCORE_EPS:
            reason = "disagree"
        elif max_sd > _SCORE_EPS:
            reason = "variance"
        elif len(attempted) < len(cells):
            reason = "infra-retry"
        else:
            continue
        scored.append((spread, max_sd, task_id, reason))
    scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
    return {task_id: reason for _, _, task_id, reason in scored}


def _describe_round(pairs: list[tuple[str, str]], reasons: dict[str, str]) -> str:
    counts: dict[str, int] = {}
    for reason in reasons.values():
        counts[reason] = counts.get(reason, 0) + 1
    why = ", ".join(f"{n} {reason}" for reason, n in sorted(counts.items()))
    pair_list = "; ".join(f"{a} vs {b}" for a, b in pairs)
    return (
        f"{len(pairs)} pair(s) with a CI that includes 0 ({pair_list}); re-running"
        f" {len(reasons)} task(s) ({why})"
    )


@dataclass
class _Budget:
    """Whole-run USD accounting shared by stage 1 and every later repeat round."""

    cap: float
    spent: float = 0.0

    @property
    def remaining(self) -> float:
        return self.cap - self.spent


@dataclass
class _Launched:
    """A subject that launched at repeat 1 -- the only ones later repeats run for."""

    record: CampaignSubjectRecord
    spec: SubjectSpec
    path: str


def run_campaign(
    suite_path: str | Path,
    subject_paths: Sequence[str | Path],
    *,
    max_parallel: int | None = None,
    cost_budget_usd: float | None = None,
    run_budget_usd: float | None = None,
    out_dir: str | Path | None = None,
    force: bool = False,
    enable_xlarge: bool = False,
    clock: Callable[[], datetime] | None = None,
    parity: str | Path | None = None,
    repeats: int = 1,
    staged: bool = True,
    allow_contaminated_env: bool = False,
) -> CampaignResult:
    """Run *suite* against every subject in *subject_paths*, IN THE GIVEN ORDER,
    enforcing a whole-run USD cap across all of them (ADR-0009 D3).

    Parameters
    ----------
    suite_path:
        Path to a `suite.json`/`.yaml` (loaded once via `bench.spec.load_suite`; its
        `tier` field supplies every default below).
    subject_paths:
        One or more `subject.json`/`.yaml` paths, run in this exact order. Must be
        non-empty.
    max_parallel:
        Per-subject `run_suite(max_parallel=...)` override; `None` resolves via
        `bench.tiers.resolve_effective` (CLI value > the suite's tier
        `default_max_parallel` > the loader's own builtin fallback) -- same precedence
        `ao-bench run` uses, not re-derived here.
    cost_budget_usd:
        PER-SUBJECT cap override (same flag/semantics as `ao-bench run --cost-budget-
        usd`); `None` resolves to the suite's tier `cost_budget_usd_per_subject` via the
        same `resolve_effective` call as `max_parallel` above. The cap actually passed
        to each subject's `run_suite` call is this value further bounded by the
        remaining whole-run headroom (see below).
    run_budget_usd:
        WHOLE-RUN cap override; `None` resolves to the suite's tier
        `cost_budget_usd_per_run`.
    out_dir, force:
        Threaded through to every `run_suite` call unchanged (same meaning as on
        `ao-bench run`); `force` also re-runs the campaign's own comparison/campaign.json
        write, not just the underlying task work.
    enable_xlarge:
        Allow a disabled tier (`enforce_tier_enabled`).
    allow_contaminated_env:
        Threaded to every `run_suite` call (T-Ws7Iso; see there). Whatever its value, a
        campaign whose subjects turn out not to share one clean environment stops its
        repeat rounds (`STOP_ENV_MISMATCH`) and writes a comparison stamped `env_mixed`
        rather than failing after the spend.
    clock:
        Injectable UTC clock (CLAUDE.md determinism rule); also threaded into every
        `run_suite` call so a same-day campaign resume lands on the same `bench_run_id`s
        the first pass used.
    parity:
        Parity bundle applied to EVERY subject and every repeat (HLD §2b).
    repeats:
        Maximum number of repeats per (subject x task), >= 1 (HLD §6). `1` (default) is the
        single-pass campaign, unchanged. See the module docstring for the staged protocol.
    staged:
        `True` (default): repeats 2..N run only while some pair's CI still includes 0 and
        only on the tasks `select_repeat_tasks` picks. `False`: every repeat runs every task
        for every subject (non-adaptive).

    Algorithm (TASK.md Pseudocode / Algorithm, ADR-0009 D3): before launching subject k,
    if cumulative ACTUAL spend from already-completed subjects THIS call >= the whole
    cap, subject k (and every subject after it) is recorded `launched=False`/
    `status="skipped_budget"` with `run_dir=None` -- `run_suite` is never called for it,
    so no workspace is materialized and no subprocess is spawned. Otherwise it launches
    with `cost_budget_usd = min(remaining_whole_budget, per_subject_cap)`. Later repeats
    apply the same rule per subject to the same running total.

    Resumable: since `run_suite` itself is resumable (same-day `bench_run_id` skips
    already-recorded tasks), calling `run_campaign` again the same day after a crash
    replays quickly through already-completed subjects (their `aggregate.total_cost_usd`
    is read back, not re-earned) and continues from where it left off -- no separate
    campaign-level "already ran" bookkeeping is needed. The staged decision is a pure
    function of the run dirs on disk, so a resumed campaign picks the same repeat tasks.

    Raises
    ------
    BenchError
        *subject_paths* is empty, `repeats < 1`, the suite's tier is disabled and
        `enable_xlarge` is not set, or any per-subject `load_subject`/`run_suite` call
        raises (fail-fast, same convention as `run_suite` itself: a malformed spec is a
        usage error, not a per-subject-isolated concern).
    SpecValidationError
        *suite_path*, or any subject in *subject_paths*, fails to load/validate.
    """
    if not subject_paths:
        raise BenchError("ao-bench campaign requires at least one --subject")
    if repeats < 1:
        raise BenchError(f"--repeats must be >= 1, got {repeats}")

    resolved_clock = clock or _utc_now
    suite = load_suite(suite_path)
    tier_config = load_tier_config(suite.tier)
    enforce_tier_enabled(suite.tier, tier_config, enable_xlarge)

    whole_cap = (
        run_budget_usd if run_budget_usd is not None else tier_config.cost_budget_usd_per_run
    )
    effective = resolve_effective(
        tier_config, cli_max_parallel=max_parallel, cli_cost_budget=cost_budget_usd
    )
    per_subject_cap_base = float(effective["cost_budget_usd"])
    effective_max_parallel = int(effective["max_parallel"])

    budget = _Budget(cap=whole_cap)
    subject_records: list[CampaignSubjectRecord] = []
    launched: list[_Launched] = []
    comparison_dirs: list[Path] = []

    for subject_path in subject_paths:
        subject_spec = load_subject(subject_path)
        predicted_bench_run_id = compute_bench_run_id(suite, subject_spec, resolved_clock)
        predicted_dir = resolve_result_dir(predicted_bench_run_id, out_dir=out_dir)
        comparison_dirs.append(predicted_dir)

        if budget.remaining <= 0:
            subject_records.append(
                CampaignSubjectRecord(
                    subject_id=subject_spec.id,
                    subject_path=str(subject_path),
                    status=STATUS_SKIPPED_BUDGET,
                    launched=False,
                )
            )
            continue

        per_subject_cap = min(per_subject_cap_base, budget.remaining)
        run_record = run_suite(
            suite_path,
            subject_path,
            out_dir=out_dir,
            force=force,
            cost_budget_usd=per_subject_cap,
            max_parallel=effective_max_parallel,
            clock=resolved_clock,
            parity=parity,
            repeat=FIRST_REPEAT,
            allow_contaminated_env=allow_contaminated_env,
        )
        budget.spent += run_record.aggregate.total_cost_usd
        subject_record = CampaignSubjectRecord(
            subject_id=subject_spec.id,
            subject_path=str(subject_path),
            status=STATUS_COMPLETED,
            launched=True,
            run_dir=str(predicted_dir),
            total_cost_usd=run_record.aggregate.total_cost_usd,
            solved=run_record.aggregate.solved,
            total=run_record.aggregate.total,
            solve_rate=run_record.aggregate.solve_rate,
        )
        subject_records.append(subject_record)
        launched.append(_Launched(record=subject_record, spec=subject_spec, path=str(subject_path)))

    all_tasks = sorted(t.id for t in suite.tasks if t.enabled)
    stage_log: list[StageLogEntry] = []
    repeats_done = 0
    stop_reason: str | None = STOP_NOT_REQUESTED if repeats == 1 else None
    if launched:
        repeats_done = FIRST_REPEAT
        stage_log.append(
            StageLogEntry(
                repeat=FIRST_REPEAT,
                task_ids=all_tasks,
                reason="stage 1: every subject, every task",
                subjects=[ls.record.subject_id for ls in launched],
            )
        )

    def _run_round(repeat: int, task_ids: list[str] | None) -> list[str]:
        """Run repeat *repeat* for every launched subject, cap permitting (`task_ids=None`
        = every task); returns the ids of the subjects that actually ran."""
        ran: list[str] = []
        for ls in launched:
            if budget.remaining <= 0:
                break
            repeat_record = run_suite(
                suite_path,
                ls.path,
                out_dir=out_dir,
                force=force,
                task_filter=task_ids,
                cost_budget_usd=min(per_subject_cap_base, budget.remaining),
                max_parallel=effective_max_parallel,
                clock=resolved_clock,
                parity=parity,
                repeat=repeat,
                allow_contaminated_env=allow_contaminated_env,
            )
            cost = repeat_record.aggregate.total_cost_usd
            budget.spent += cost
            ls.record.repeat_cost_usd += cost
            repeat_dir = resolve_result_dir(
                compute_bench_run_id(suite, ls.spec, resolved_clock, repeat), out_dir=out_dir
            )
            ls.record.repeat_run_dirs.append(str(repeat_dir))
            comparison_dirs.append(repeat_dir)
            ran.append(ls.record.subject_id)
        return ran

    if launched and repeats > 1:
        stop_reason = _run_repeat_rounds(
            all_tasks=all_tasks,
            repeats=repeats,
            staged=staged,
            comparison_dirs=comparison_dirs,
            budget=budget,
            run_round=_run_round,
            stage_log=stage_log,
        )
        repeats_done = max(e.repeat for e in stage_log)
    spent = budget.spent

    campaign_id = compute_campaign_id(suite.id, resolved_clock)
    campaign_dir = resolve_result_dir(campaign_id, out_dir=out_dir)

    comparison: ComparisonRecord | None = None
    comparison_json_path: Path | None = None
    comparison_md_path: Path | None = None
    if any(r.launched for r in subject_records):
        # Feeds EVERY subject's (real-or-predicted) dir in -- `build_comparison`
        # already treats a dir with no `run.json` as "not run" (design intent: a
        # missing subject.run.json -> shown as not run), which is exactly what a
        # budget-skipped subject's never-materialized predicted dir looks like. This
        # reuses that existing machinery rather than re-deriving a parallel notion of
        # "didn't run" here. Repeat dirs of the same subject are aggregated per task.
        try:
            comparison = build_comparison(comparison_dirs)
        except EnvMismatchError:
            # T-Ws7Iso: the runs are already paid for -- keep them, but stamp the report
            # NON-ISOLATED (env_mixed + env_problems) instead of failing the campaign.
            comparison = build_comparison(comparison_dirs, allow_mixed=True)
        comparison_json_path, comparison_md_path = write_comparison(comparison, campaign_dir)
    # else: nothing launched at all (e.g. the whole-run cap was already exhausted
    # before subject 1) -- no comparison to build; campaign.json alone still records
    # every subject as skipped_budget below.

    record = CampaignRecord(
        campaign_id=campaign_id,
        suite_id=suite.id,
        tier=suite.tier,
        generated_at=resolved_clock().isoformat(),
        whole_run_cap_usd=whole_cap,
        per_subject_cap_usd=per_subject_cap_base,
        max_parallel=effective_max_parallel,
        total_spent_usd=spent,
        subjects=subject_records,
        comparison_json=str(comparison_json_path) if comparison_json_path else None,
        comparison_md=str(comparison_md_path) if comparison_md_path else None,
        repeats_requested=repeats,
        repeats_done=repeats_done,
        staged=staged,
        stage_log=stage_log,
        stage_stop_reason=stop_reason,
        env_problems=comparison.env_problems if comparison is not None else [],
    )
    _persist_campaign_record(campaign_dir, record)

    return CampaignResult(record=record, campaign_dir=str(campaign_dir), comparison=comparison)


def _run_repeat_rounds(
    *,
    all_tasks: list[str],
    repeats: int,
    staged: bool,
    comparison_dirs: list[Path],
    budget: _Budget,
    run_round: Callable[[int, list[str] | None], list[str]],
    stage_log: list[StageLogEntry],
) -> str:
    """Drive repeats 2..*repeats* (see the module docstring) and return the stop reason.

    *comparison_dirs* is read live (`run_round` appends each round's dirs to it), so every
    decision sees all repeat dirs run so far; *stage_log* gets one entry per executed round.
    """
    for repeat in range(FIRST_REPEAT + 1, repeats + 1):
        if budget.remaining <= 0:
            return STOP_BUDGET
        if staged:
            try:
                comparison = build_comparison(list(comparison_dirs))
            except EnvMismatchError:
                return STOP_ENV_MISMATCH
            if not comparison.pairwise:
                return STOP_NO_PAIRS
            pairs = undecided_pairs(comparison)
            if not pairs:
                return STOP_ALL_DECIDED
            reasons = select_repeat_tasks(comparison)
            if not reasons:
                return STOP_NO_CANDIDATES
            task_ids = list(reasons)  # most informative first (select_repeat_tasks' order)
            reason = _describe_round(pairs, reasons)
            ran = run_round(repeat, sorted(task_ids))
        else:
            task_ids = all_tasks
            reason = "non-staged: every subject, every task"
            ran = run_round(repeat, None)
        if not ran:  # the cap ran out before any subject could launch this round
            return STOP_BUDGET
        stage_log.append(
            StageLogEntry(repeat=repeat, task_ids=task_ids, reason=reason, subjects=ran)
        )
    return STOP_MAX_REPEATS if staged else STOP_NON_STAGED


__all__ = [
    "CAMPAIGN_JSON_FILENAME",
    "CampaignRecord",
    "CampaignResult",
    "CampaignSubjectRecord",
    "STATUS_COMPLETED",
    "STATUS_SKIPPED_BUDGET",
    "STOP_ALL_DECIDED",
    "STOP_BUDGET",
    "STOP_MAX_REPEATS",
    "STOP_NON_STAGED",
    "STOP_NOT_REQUESTED",
    "STOP_NO_CANDIDATES",
    "STOP_NO_PAIRS",
    "StageLogEntry",
    "compute_campaign_id",
    "enforce_tier_enabled",
    "run_campaign",
    "select_repeat_tasks",
    "undecided_pairs",
]
