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
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from .errors import BenchError
from .results import ComparisonRecord, build_comparison, write_comparison
from .runner import compute_bench_run_id, resolve_result_dir, run_suite
from .spec import load_subject, load_suite
from .tiers import TierConfig, load_tier_config, resolve_effective

# ---------------------------------------------------------------------------
# Named constants (CLAUDE.md: no magic literals)
# ---------------------------------------------------------------------------

CAMPAIGN_SCHEMA_VERSION = "1.0"
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
    dir, aggregate cost, solve rate, status")."""

    subject_id: str
    subject_path: str
    status: str
    launched: bool
    run_dir: str | None = None
    total_cost_usd: float | None = None
    solved: int | None = None
    total: int | None = None
    solve_rate: float | None = None


class CampaignRecord(BaseModel):
    """The persisted `campaign.json` shape (TASK.md: "suite, tier, caps, per-subject
    [...]")."""

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
    clock:
        Injectable UTC clock (CLAUDE.md determinism rule); also threaded into every
        `run_suite` call so a same-day campaign resume lands on the same `bench_run_id`s
        the first pass used.

    Algorithm (TASK.md Pseudocode / Algorithm, ADR-0009 D3): before launching subject k,
    if cumulative ACTUAL spend from already-completed subjects THIS call >= the whole
    cap, subject k (and every subject after it) is recorded `launched=False`/
    `status="skipped_budget"` with `run_dir=None` -- `run_suite` is never called for it,
    so no workspace is materialized and no subprocess is spawned. Otherwise it launches
    with `cost_budget_usd = min(remaining_whole_budget, per_subject_cap)`.

    Resumable: since `run_suite` itself is resumable (same-day `bench_run_id` skips
    already-recorded tasks), calling `run_campaign` again the same day after a crash
    replays quickly through already-completed subjects (their `aggregate.total_cost_usd`
    is read back, not re-earned) and continues from where it left off -- no separate
    campaign-level "already ran" bookkeeping is needed.

    Raises
    ------
    BenchError
        *subject_paths* is empty, the suite's tier is disabled and `enable_xlarge` is
        not set, or any per-subject `load_subject`/`run_suite` call raises (fail-fast,
        same convention as `run_suite` itself: a malformed spec is a usage error, not a
        per-subject-isolated concern).
    SpecValidationError
        *suite_path*, or any subject in *subject_paths*, fails to load/validate.
    """
    if not subject_paths:
        raise BenchError("ao-bench campaign requires at least one --subject")

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

    spent = 0.0
    subject_records: list[CampaignSubjectRecord] = []
    comparison_dirs: list[Path] = []

    for subject_path in subject_paths:
        subject_spec = load_subject(subject_path)
        predicted_bench_run_id = compute_bench_run_id(suite, subject_spec, resolved_clock)
        predicted_dir = resolve_result_dir(predicted_bench_run_id, out_dir=out_dir)
        comparison_dirs.append(predicted_dir)

        remaining = whole_cap - spent
        if remaining <= 0:
            subject_records.append(
                CampaignSubjectRecord(
                    subject_id=subject_spec.id,
                    subject_path=str(subject_path),
                    status=STATUS_SKIPPED_BUDGET,
                    launched=False,
                )
            )
            continue

        per_subject_cap = min(per_subject_cap_base, remaining)
        run_record = run_suite(
            suite_path,
            subject_path,
            out_dir=out_dir,
            force=force,
            cost_budget_usd=per_subject_cap,
            max_parallel=effective_max_parallel,
            clock=resolved_clock,
        )
        spent += run_record.aggregate.total_cost_usd
        subject_records.append(
            CampaignSubjectRecord(
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
        )

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
        # "didn't run" here.
        comparison = build_comparison(comparison_dirs)
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
    )
    _persist_campaign_record(campaign_dir, record)

    return CampaignResult(record=record, campaign_dir=str(campaign_dir), comparison=comparison)


__all__ = [
    "CAMPAIGN_JSON_FILENAME",
    "CampaignRecord",
    "CampaignResult",
    "CampaignSubjectRecord",
    "STATUS_COMPLETED",
    "STATUS_SKIPPED_BUDGET",
    "compute_campaign_id",
    "enforce_tier_enabled",
    "run_campaign",
]
