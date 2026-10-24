"""Result & comparison writers (design doc `docs-md/benchmarking-framework-hld.md` §4.6).

`bench/runner.py` (T-Run5Tz) already owns and persists the machine-readable `run.json`
shape (`BenchRunRecord`/`BenchTaskRecord`, write-temp + atomic rename, one write per
completed task -- see that module's docstring for the accepted deviation). This module
builds ON TOP of that already-landed shape:

  - `load_run(run_dir)`           -- a validating loader that reads a persisted
                                      `run.json` back into a `BenchRunRecord`.
  - `write_summary_md(...)`       -- a human-readable `summary.md` next to `run.json`
                                      (a markdown per-task table + an aggregate footer).
                                      Called by the CLI (`ao-bench run`, T-Cli8Nf) right
                                      after `runner.run_suite` returns.
  - `build_comparison(...)` /
    `write_comparison(...)`       -- cross-subject `comparison.{json,md}` for
                                      `ao-bench report` (T-Cli8Nf): a per-task x
                                      per-subject matrix plus per-subject aggregates and
                                      a "winner" line per axis.

`build_comparison` is also where the discriminating-suites statistics (HLD §2b/§5/§6) live:
several run dirs for the SAME subject are accepted as *repeats* (aggregated per task into
mean/sd/n cells), runs are refused unless they share one parity bundle fingerprint, infra
errors are excluded from the capability (attempted-based) rates and listed in their own
table, and every subject pair gets a paired bootstrap CI + Wilcoxon p over per-task score
differences (`bench/stats.py`). `load_comparison` reads any `comparison.json` back,
including the pre-repeats 1.0 shape.

Results are committed to git for preservation (design §9); every task's `workspace` is
already just a path POINTER (`BenchTaskRecord.workspace`, set by the runner from
`SubjectResult.capture_dir`) -- this module never reads or copies transcript content,
only the already-bounded `run.json` fields.

Import direction (SI-1): reads only from core-adjacent sibling `bench/` modules
(`runner`, `metrics`, `errors`, `stats`); nothing in core or in `bench/{spec,subjects,graders,
metrics,runner}.py` imports this module.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, model_validator

from . import stats
from .envcheck import EnvFingerprint, foreign_entries, merge_fingerprints
from .errors import EnvMismatchError, ResultsError
from .metrics import INFRA_ERROR_STATUS, Aggregate, TaskMetric, aggregate
from .runner import RUN_JSON_FILENAME, BenchRunRecord, BenchTaskRecord
from .subjects import AGENT_SUBJECT_TYPES

# ---------------------------------------------------------------------------
# Named constants (CLAUDE.md: no magic literals)
# ---------------------------------------------------------------------------

SUMMARY_MD_FILENAME = "summary.md"
COMPARISON_JSON_FILENAME = "comparison.json"
COMPARISON_MD_FILENAME = "comparison.md"
# 2.0: repeat-aggregated cells + parity/pairwise/per-family/infra blocks (HLD §6). 1.0 files
# (one run per subject, no stats) still load via `load_comparison` / `ComparisonRecord`.
COMPARISON_SCHEMA_VERSION = "2.0"
LEGACY_COMPARISON_SCHEMA_VERSIONS = frozenset({"1.0"})

# `<date>-<suite.id>-compare` (design §9) -- mirrors runner.py's own
# `<date>-<suite.id>-<subject.id>` bench_run_id convention/date format, kept as a local
# constant rather than importing runner's underscore-prefixed private one.
_COMPARE_ID_DATE_FORMAT = "%Y-%m-%d"
COMPARE_DIR_SUFFIX = "compare"

# `bench_run_id`/compare-dir names start with an ISO date (`compute_bench_run_id`,
# `compute_compare_id`) -- used to best-effort recover a subject id from a run dir that
# has no `run.json` at all (AC4: "missing subject run.json -> shown as not run").
_RUN_DIR_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-(.+)$")
# `compute_bench_run_id(..., repeat=n>1)` appends `-r<n>` -- a missing repeat dir must not
# be mistaken for a separate, never-run subject.
_REPEAT_SUFFIX_RE = re.compile(r"^(.+)-r\d+$")

# Infra heuristic (HLD §1/§5.1): a run that did not solve, cost almost nothing and ended
# quickly looks like an abort rather than a capability miss (the `django-11138` $0.09
# case). Suspects are REPORTED, never excluded -- only `subject_status == "error"` is.
SUSPECT_MAX_COST_USD = 0.15
SUSPECT_MAX_WALL_SECONDS = 180.0
# HLD §6: a campaign with more than this share of infra errors is marked unreliable.
UNRELIABLE_INFRA_SHARE = 0.10

# Bootstrap parameters for the pairwise CIs. Fixed (not user knobs) so a report
# regenerated from the same run dirs is byte-identical.
BOOTSTRAP_N = stats.DEFAULT_N_BOOT
BOOTSTRAP_ALPHA = stats.DEFAULT_ALPHA
BOOTSTRAP_SEED = stats.DEFAULT_SEED
PAIRWISE_METRIC_SCORE = "score"

# Statuses where the subject never made a real attempt: excluded from attempted-based
# (capability) stats. Mirrors `metrics.aggregate`'s own `attempted` definition.
STATUS_SKIPPED_BUDGET = "skipped_budget"
NOT_ATTEMPTED_STATUSES = frozenset({INFRA_ERROR_STATUS, STATUS_SKIPPED_BUDGET})
# `ComparisonTaskCell.subject_status` for a cell whose repeats ended in different statuses.
STATUS_MIXED = "mixed"


def _utc_now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# comparison.json shape
# ---------------------------------------------------------------------------


class ComparisonTaskCell(BaseModel):
    """One (task x subject) cell of the comparison matrix -- the subset of
    `BenchTaskRecord` a side-by-side report needs. Absent entirely (`None` in
    `ComparisonRecord.matrix`) when that subject never ran that task (divergent task
    sets across subjects, or the subject didn't run at all) -- design §4.6 risk:
    "divergent task sets across subjects -> key the matrix by union of task ids, mark
    missing cells explicitly."

    A cell aggregates every repeat of the task for that subject (HLD §6). The original
    fields keep their meaning for a single repeat (verbatim copies of the run record) and
    degrade sensibly for several: `solved` = a strict majority of the attempted repeats
    solved it, `score` = `score_mean`, `cost_usd` = `cost_median`, `wall_clock_seconds` =
    `wall_median`, `subject_status` = the common status or `"mixed"`.

    The new fields are capability statistics over the ATTEMPTED repeats only (infra errors
    and budget skips are not evidence about the subject): `n` is how many repeats that
    is -- `0` means every repeat was an infra error / budget skip, so the cell takes no
    part in rates, roll-ups or the pairwise tests.
    """

    subject_status: str
    solved: bool
    score: float
    cost_usd: float | None
    wall_clock_seconds: float
    score_mean: float = 0.0
    score_sd: float = 0.0
    n: int = 1
    solve_rate: float = 0.0
    cost_median: float | None = None
    wall_median: float = 0.0

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy_cell(cls, data: Any) -> Any:
        """A 1.0 cell (or one built with only the original five fields) is exactly one
        repeat: derive the new statistics from the original values."""
        if not isinstance(data, dict) or "score_mean" in data:
            return data
        upgraded = dict(data)
        attempted = data.get("subject_status") not in NOT_ATTEMPTED_STATUSES
        upgraded["score_mean"] = data.get("score", 0.0)
        upgraded["score_sd"] = 0.0
        upgraded["n"] = 1 if attempted else 0
        upgraded["solve_rate"] = 1.0 if data.get("solved") else 0.0
        upgraded["cost_median"] = data.get("cost_usd")
        upgraded["wall_median"] = data.get("wall_clock_seconds", 0.0)
        return upgraded


class RollupCell(BaseModel):
    """One (family|language x subject) roll-up: mean over the tasks of that group the
    subject attempted, each task counting once (equal weight, HLD §6)."""

    score_mean: float
    solve_rate: float
    n: int


class PairwiseEntry(BaseModel):
    """Paired comparison of two subjects over the tasks both attempted (HLD §6): per-task
    difference of the repeat-mean score `a - b`, its paired-bootstrap 95% CI and the
    Wilcoxon signed-rank p. Statistics are `None` when the pair shares no attempted task.
    """

    a: str
    b: str
    metric: str = PAIRWISE_METRIC_SCORE
    mean_diff: float | None = None
    ci_lo: float | None = None
    ci_hi: float | None = None
    p_wilcoxon: float | None = None
    n_tasks: int = 0


class InfraEntry(BaseModel):
    """Infra picture of one subject. `count` = task runs (repeats included) that ended
    `subject_status == "error"` (excluded from attempted rates); `task_ids` = distinct
    tasks affected. `suspect_task_ids` = tasks with a NON-error run that was unsolved,
    cost < `SUSPECT_MAX_COST_USD` and finished in < `SUSPECT_MAX_WALL_SECONDS` -- it looks
    like an abort but is reported only, never excluded."""

    count: int = 0
    task_ids: list[str] = []
    suspect_task_ids: list[str] = []


class SubjectStats(BaseModel):
    """Per-subject capability stats over ATTEMPTED work (infra errors / budget skips out).

    `solve_rate_attempted` / `score_mean_attempted` average the per-task cell values, so a
    task re-run in later repeats still counts once. `task_runs` counts every record
    (repeats included); `infra_share = infra_errors / task_runs`.
    """

    repeats: int = 1
    task_runs: int = 0
    attempted: int = 0
    infra_errors: int = 0
    infra_share: float = 0.0
    tasks_attempted: int = 0
    solve_rate_attempted: float = 0.0
    score_mean_attempted: float = 0.0
    cost_per_score_point: float | None = None
    cost_median: float | None = None
    wall_median: float | None = None


class EfficiencyRow(BaseModel):
    """Per-subject cost/time efficiency (T-Cb8Rk2). `spend_usd` counts every task run (infra
    errors cost money too); the per-task figures cover attempted runs only."""

    spend_usd: float = 0.0
    runs_attempted: int = 0
    solved_runs: int = 0
    score_mean: float = 0.0
    cost_per_task_mean: float | None = None
    cost_per_task_median: float | None = None
    cost_per_solved: float | None = None
    cost_per_score_point: float | None = None
    wall_per_task_mean: float | None = None
    output_tokens_per_task_mean: float | None = None


class EnvSubjectSummary(BaseModel):
    """Environment-isolation picture of one subject across its repeats (T-Ws7Iso)."""

    # The subject's runs predate T-Ws7Iso (no `env_isolation` on the record).
    legacy: bool = False
    contaminated: bool = False
    # Union of every task's init-event fingerprint (None: no transcript at all).
    fingerprint_digest: str | None = None
    baseline_digest: str | None = None
    user_config_digest: str | None = None
    # Entries the subject's agents loaded that the run's baseline did not.
    foreign: dict[str, list[str]] = {}
    # Sessions of this subject did not all load the same sets (reported, not refused).
    divergent: bool = False
    # Tasks whose transcripts reached for grader-owned assets (excluded from the stats).
    grading_access_task_ids: list[str] = []


class ComparisonRecord(BaseModel):
    """The persisted `comparison.json` shape (design §4.6): a per-task x per-subject
    matrix plus per-subject aggregates, all keyed by the union of task ids / requested
    subjects so a caller never has to guess which cells are legitimately absent.

    Everything beyond `per_subject` is the 2.0 statistics layer and defaults to empty, so
    a hand-built or 1.0 record is still valid (see `load_comparison`).
    """

    schema_version: str = COMPARISON_SCHEMA_VERSION
    suite_id: str
    generated_at: str
    # All subjects considered, in the order their run dirs were given to
    # `build_comparison` (found first, then not-run) -- stable, deterministic column
    # order for `comparison.md`'s tables.
    subjects: list[str]
    not_run: list[str] = []
    task_ids: list[str] = []
    # task_id -> subject_id -> cell (None = that subject never ran that task).
    matrix: dict[str, dict[str, ComparisonTaskCell | None]] = {}
    # subject_id -> aggregate over every repeat of that subject (None = in `not_run`).
    per_subject: dict[str, Aggregate | None] = {}
    # --- 2.0 statistics layer (HLD §2b, §6) -------------------------------------------
    # The one parity bundle fingerprint every loaded run shares (None = no bundle was
    # applied, OR the runs were mixed -- see `parity_mixed` / `parity_by_subject`).
    parity_fingerprint: str | None = None
    parity_mixed: bool = False
    parity_by_subject: dict[str, str | None] = {}
    # subject_id -> number of repeat runs loaded for it.
    repeats_per_subject: dict[str, int] = {}
    subject_stats: dict[str, SubjectStats | None] = {}
    # group -> subject_id -> roll-up (tasks without family/language metadata are left out).
    per_family: dict[str, dict[str, RollupCell]] = {}
    per_language: dict[str, dict[str, RollupCell]] = {}
    infra: dict[str, InfraEntry] = {}
    pairwise: list[PairwiseEntry] = []
    # Subjects whose infra-error share exceeds `UNRELIABLE_INFRA_SHARE` (HLD §6).
    unreliable_subjects: list[str] = []
    # --- environment isolation (T-Ws7Iso) -----------------------------------------------
    env_by_subject: dict[str, EnvSubjectSummary] = {}
    # True when an environment check failed and --allow-mixed let the comparison through;
    # `env_problems` says which.
    env_mixed: bool = False
    env_problems: list[str] = []
    # --- efficiency + probe-group view (T-Cb8Rk2) ----------------------------------------
    efficiency: dict[str, EfficiencyRow] = {}
    # subject_id -> probe group (core/trap/perf) -> mean pass fraction over attempted runs
    # that had that group. Shows WHERE subjects differ (perf is timing-noisy).
    group_scores: dict[str, dict[str, float]] = {}
    # Deterministic, rule-based observations; a write-up adds judgement on top.
    observations: list[str] = []


# ---------------------------------------------------------------------------
# load_run -- validating loader for an already-persisted run.json
# ---------------------------------------------------------------------------


def load_run(run_dir: str | Path) -> BenchRunRecord:
    """Load and validate a persisted `run.json`.

    *run_dir* may be either the directory a `run_suite` call wrote into (the common
    case -- `<results_root>/<bench_run_id>/`) or the `run.json` file itself.

    Raises `ResultsError` if the file is missing, is not valid JSON, or does not match
    `BenchRunRecord`'s schema -- a loud failure beats silently discarding a tampered/
    foreign file (mirrors `runner._load_existing_record`'s own resume-safety rationale;
    this module cannot reuse that private helper directly since it treats "missing" as
    a legitimate `None` resume case, whereas here a missing `run.json` the caller
    explicitly asked to load is always an error).
    """
    p = Path(run_dir)
    json_path = p / RUN_JSON_FILENAME if p.is_dir() else p
    if not json_path.exists():
        raise ResultsError(f"No {RUN_JSON_FILENAME} found at {json_path}")
    try:
        data = json.loads(json_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ResultsError(f"Failed to read {json_path}: {exc}") from exc
    try:
        return BenchRunRecord.model_validate(data)
    except Exception as exc:  # pydantic.ValidationError et al.
        raise ResultsError(
            f"{json_path} does not match the expected run.json schema: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# summary.md
# ---------------------------------------------------------------------------


def _fmt_cost(value: float | None) -> str:
    return "—" if value is None else f"{value:.4f}"


def _fmt_opt_int(value: int | None) -> str:
    return "—" if value is None else str(value)


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _fmt_bool(value: bool) -> str:
    return "yes" if value else "no"


def _render_summary_md(record: BenchRunRecord) -> str:
    """Design §4.6: `task | category | solved | score | cost($) | wall(s) |
    tokens(in/out) | status` + an aggregate footer (`solved/total`, `solve_rate`,
    `total_cost`, `cost_per_solved`) -- extended with a `turns` column per this task's
    own acceptance criteria.
    """
    lines: list[str] = [
        f"# Bench summary — {record.suite_id} × {record.subject.id}",
        "",
        f"- bench_run_id: `{record.bench_run_id}`",
        f"- domain: {record.domain}",
        f"- subject: `{record.subject.id}` (type={record.subject.type}, "
        f"model={record.subject.model or '—'})",
        f"- started_at: {record.started_at}",
        f"- ended_at: {record.ended_at or '—'}",
        "",
        "| Task | Category | Solved | Score | Wall(s) | Cost($) | Tokens(in/out)"
        " | Turns | Status |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for t in sorted(record.tasks, key=lambda t: t.task_id):
        lines.append(
            f"| {t.task_id} | {t.category} | {_fmt_bool(t.solved)} | {t.score:.2f} "
            f"| {t.wall_clock_seconds:.2f} | {_fmt_cost(t.cost_usd)} "
            f"| {_fmt_opt_int(t.input_tokens)}/{_fmt_opt_int(t.output_tokens)} "
            f"| {_fmt_opt_int(t.turns)} | {t.subject_status} |"
        )

    agg = record.aggregate
    cost_note = "" if agg.cost_available else " (partial: some tasks had no recorded cost)"
    lines += [
        "",
        "## Aggregate",
        "",
        f"- solved: {agg.solved}/{agg.total} ({_fmt_pct(agg.solve_rate)})",
        f"- total_cost: ${agg.total_cost_usd:.4f}{cost_note}",
        f"- cost_per_solved: {_fmt_cost(agg.cost_per_solved)}",
        f"- total_wall_clock: {agg.total_wall_clock_seconds:.2f}s",
    ]
    lines += _render_run_env_md(record)
    return "\n".join(lines) + "\n"


def _render_run_env_md(record: BenchRunRecord) -> list[str]:
    """Environment-isolation footer of `summary.md` (T-Ws7Iso); empty for older records."""
    iso = record.env_isolation
    if iso is None:
        return []
    lines = [
        "",
        "## Environment isolation",
        "",
        f"- workspace_root: `{iso.workspace_root}`",
        f"- grading_root: `{iso.grading_root}`",
        f"- env_contaminated: {_fmt_bool(record.env_contaminated)}"
        + (f" ({', '.join(iso.instruction_ancestors)})" if iso.instruction_ancestors else ""),
    ]
    if record.env_baseline is not None:
        lines.append(f"- baseline fingerprint: `{record.env_baseline.digest[:12]}`")
    if record.user_config is not None:
        shown = {k: v for k, v in record.user_config.settings.items() if not k.endswith("_sha256")}
        lines.append(f"- user config: `{record.user_config.digest[:12]}` {shown or ''}".rstrip())
    for t in sorted(record.tasks, key=lambda t: t.task_id):
        if t.env_foreign:
            lines.append(f"- {t.task_id}: foreign entries {t.env_foreign}")
        if t.suspect:
            lines.append(f"- {t.task_id}: **suspect ({t.suspect_reason})** -- excluded from stats")
    return lines


def write_summary_md(
    record_or_run_dir: BenchRunRecord | str | Path,
    run_dir: str | Path | None = None,
) -> Path:
    """Write a human-readable `summary.md` next to `run.json` (design §4.6).

    Two calling conventions (TASK.md T-Rpt3Wq: `write_summary_md(record | run_dir)`):

      - ``write_summary_md(run_dir)`` -- no in-memory record on hand; loads
        `run.json` from *run_dir* via `load_run` first (e.g. a `ao-bench report`-style
        tool regenerating a summary for an already-persisted run).
      - ``write_summary_md(record, run_dir)`` -- the record is already in memory (the
        common case: right after `runner.run_suite` returns, avoiding a redundant
        re-read of the `run.json` the runner just wrote) plus the directory it lives in.

    Returns the path to the written `summary.md`.
    """
    record: BenchRunRecord
    target_dir: Path
    if isinstance(record_or_run_dir, BenchRunRecord):
        if run_dir is None:
            raise ResultsError(
                "write_summary_md(record, run_dir): run_dir is required when passing "
                "an in-memory BenchRunRecord"
            )
        record = record_or_run_dir
        target_dir = Path(run_dir)
    else:
        target_dir = Path(record_or_run_dir)
        record = load_run(target_dir)

    target_dir.mkdir(parents=True, exist_ok=True)
    summary_path = target_dir / SUMMARY_MD_FILENAME
    summary_path.write_text(_render_summary_md(record))
    return summary_path


# ---------------------------------------------------------------------------
# build_comparison
# ---------------------------------------------------------------------------


def compute_compare_id(suite_id: str, clock: Callable[[], datetime]) -> str:
    """`<utc-date>-<suite.id>-compare` (design §9) -- the comparison directory name.
    Exposed (not `_`-prefixed) so `T-Cli8Nf`'s `ao-bench report` can predict/report the
    output dir the same way `runner.compute_bench_run_id` lets `run` do, without
    duplicating the derivation.
    """
    return f"{clock().strftime(_COMPARE_ID_DATE_FORMAT)}-{suite_id}-{COMPARE_DIR_SUFFIX}"


def _derive_subject_id(run_dir: Path, known_suite_id: str | None) -> str:
    """Best-effort subject id for a run dir with no `run.json` at all (AC4: "missing
    subject run.json -> shown as not run"). `bench_run_id`s are
    `<date>-<suite_id>-<subject_id>` (`runner.compute_bench_run_id`) -- with
    *known_suite_id* (learned from any sibling run dir that DID load) the date + suite-
    id prefix can be stripped to recover exactly the subject id; without it, the whole
    date-stripped remainder is used verbatim as a readable-enough label rather than
    guessing where the suite id ends and the subject id begins (both may themselves
    contain hyphens).
    """
    name = run_dir.name
    match = _RUN_DIR_DATE_PREFIX_RE.match(name)
    rest = match.group(1) if match else name
    prefix = f"{known_suite_id}-"
    if known_suite_id and rest.startswith(prefix):
        return rest[len(prefix) :]
    return rest


def is_attempted(task: BenchTaskRecord) -> bool:
    """True iff the record is capability evidence: the subject made a real attempt (not an
    infra error / budget skip) AND the hidden-asset audit did not flag it (T-Ws7Iso: a task
    whose agent reached for grader-owned assets says nothing about capability)."""
    return task.subject_status not in NOT_ATTEMPTED_STATUSES and not task.suspect


def _median_or_none(values: list[float]) -> float | None:
    return stats.median(values) if values else None


def _build_cell(recs: list[BenchTaskRecord]) -> ComparisonTaskCell:
    """Aggregate every repeat of one (task x subject) into a `ComparisonTaskCell`.

    A single record keeps the legacy fields verbatim (byte-compatible with the pre-repeats
    comparison); several are summarised as documented on the model. The statistics cover
    the attempted repeats only.
    """
    attempted = [r for r in recs if is_attempted(r)]
    # Cost/wall medians describe attempted work; an all-infra cell falls back to what it
    # did spend so the column is not blank for a task that ran.
    basis = attempted or recs
    cost_median = _median_or_none([r.cost_usd for r in basis if r.cost_usd is not None])
    wall_median = stats.median([r.wall_clock_seconds for r in basis])
    if attempted:
        scores = [r.score for r in attempted]
        score_mean = stats.mean(scores)
        score_sd = stats.sd(scores)
        solve_rate = stats.mean([1.0 if r.solved else 0.0 for r in attempted])
    else:
        score_mean, score_sd, solve_rate = 0.0, 0.0, 0.0

    if len(recs) == 1:
        only = recs[0]
        return ComparisonTaskCell(
            subject_status=only.subject_status,
            solved=only.solved,
            score=only.score,
            cost_usd=only.cost_usd,
            wall_clock_seconds=only.wall_clock_seconds,
            score_mean=score_mean,
            score_sd=score_sd,
            n=len(attempted),
            solve_rate=solve_rate,
            cost_median=cost_median,
            wall_median=wall_median,
        )
    statuses = {r.subject_status for r in recs}
    return ComparisonTaskCell(
        subject_status=statuses.pop() if len(statuses) == 1 else STATUS_MIXED,
        solved=solve_rate > 0.5,
        score=score_mean,
        cost_usd=cost_median,
        wall_clock_seconds=wall_median,
        score_mean=score_mean,
        score_sd=score_sd,
        n=len(attempted),
        solve_rate=solve_rate,
        cost_median=cost_median,
        wall_median=wall_median,
    )


def _is_suspect(task: BenchTaskRecord) -> bool:
    """HLD §1/§5.1 infra heuristic on a NON-error run: unsolved, cheap and quick."""
    return (
        is_attempted(task)
        and not task.solved
        and task.cost_usd is not None
        and task.cost_usd < SUSPECT_MAX_COST_USD
        and task.wall_clock_seconds < SUSPECT_MAX_WALL_SECONDS
    )


def _subject_stats(
    task_ids: list[str],
    subject_id: str,
    matrix: dict[str, dict[str, ComparisonTaskCell | None]],
    records: list[BenchTaskRecord],
    repeats: int,
) -> SubjectStats:
    cells = [c for tid in task_ids if (c := matrix[tid].get(subject_id)) is not None]
    attempted_cells = [c for c in cells if c.n > 0]
    attempted_recs = [r for r in records if is_attempted(r)]
    infra = sum(1 for r in records if r.subject_status == INFRA_ERROR_STATUS)
    score_sum = sum(r.score for r in attempted_recs)
    costs = [r.cost_usd for r in attempted_recs if r.cost_usd is not None]
    return SubjectStats(
        repeats=repeats,
        task_runs=len(records),
        attempted=len(attempted_recs),
        infra_errors=infra,
        infra_share=(infra / len(records)) if records else 0.0,
        tasks_attempted=len(attempted_cells),
        solve_rate_attempted=(
            stats.mean([c.solve_rate for c in attempted_cells]) if attempted_cells else 0.0
        ),
        score_mean_attempted=(
            stats.mean([c.score_mean for c in attempted_cells]) if attempted_cells else 0.0
        ),
        cost_per_score_point=(sum(costs) / score_sum) if costs and score_sum > 0 else None,
        cost_median=_median_or_none(
            [c.cost_median for c in attempted_cells if c.cost_median is not None]
        ),
        wall_median=_median_or_none([c.wall_median for c in attempted_cells]),
    )


def _rollup(
    group_of_task: dict[str, str | None],
    subjects: list[str],
    matrix: dict[str, dict[str, ComparisonTaskCell | None]],
) -> dict[str, dict[str, RollupCell]]:
    """`group -> subject -> RollupCell`, groups sorted; tasks with no group are skipped and
    a (group, subject) with no attempted task is omitted."""
    groups = sorted({g for g in group_of_task.values() if g is not None})
    out: dict[str, dict[str, RollupCell]] = {}
    for group in groups:
        group_tasks = [tid for tid, g in group_of_task.items() if g == group]
        per_subject: dict[str, RollupCell] = {}
        for sid in subjects:
            cells = [
                c for tid in group_tasks if (c := matrix[tid].get(sid)) is not None and c.n > 0
            ]
            if cells:
                per_subject[sid] = RollupCell(
                    score_mean=stats.mean([c.score_mean for c in cells]),
                    solve_rate=stats.mean([c.solve_rate for c in cells]),
                    n=len(cells),
                )
        out[group] = per_subject
    return out


def _pairwise(
    task_ids: list[str],
    ran_subjects: list[str],
    matrix: dict[str, dict[str, ComparisonTaskCell | None]],
) -> list[PairwiseEntry]:
    """One `PairwiseEntry` per ordered pair of subjects that ran (paired per task on the
    repeat-mean score, over tasks BOTH attempted)."""
    entries: list[PairwiseEntry] = []
    for i, a in enumerate(ran_subjects):
        for b in ran_subjects[i + 1 :]:
            diffs: list[float] = []
            for tid in task_ids:
                ca, cb = matrix[tid].get(a), matrix[tid].get(b)
                if ca is not None and cb is not None and ca.n > 0 and cb.n > 0:
                    diffs.append(ca.score_mean - cb.score_mean)
            if not diffs:
                entries.append(PairwiseEntry(a=a, b=b))
                continue
            mean_diff, lo, hi = stats.paired_bootstrap_ci(
                diffs, n_boot=BOOTSTRAP_N, alpha=BOOTSTRAP_ALPHA, seed=BOOTSTRAP_SEED
            )
            _, p_value = stats.wilcoxon_signed_rank(diffs)
            entries.append(
                PairwiseEntry(
                    a=a,
                    b=b,
                    mean_diff=mean_diff,
                    ci_lo=lo,
                    ci_hi=hi,
                    p_wilcoxon=p_value,
                    n_tasks=len(diffs),
                )
            )
    return entries


def _mean_or_none(values: list[float]) -> float | None:
    return stats.mean(values) if values else None


def _efficiency(records: list[BenchTaskRecord]) -> EfficiencyRow:
    attempted = [r for r in records if is_attempted(r)]
    costs = [r.cost_usd for r in attempted if r.cost_usd is not None]
    solved = sum(1 for r in attempted if r.solved)
    score_sum = sum(r.score for r in attempted)
    spend = sum(r.cost_usd or 0.0 for r in records)
    out_tokens = [float(r.output_tokens) for r in attempted if r.output_tokens is not None]
    return EfficiencyRow(
        spend_usd=spend,
        runs_attempted=len(attempted),
        solved_runs=solved,
        score_mean=stats.mean([r.score for r in attempted]) if attempted else 0.0,
        cost_per_task_mean=_mean_or_none(costs),
        cost_per_task_median=_median_or_none(costs),
        cost_per_solved=(sum(costs) / solved) if costs and solved else None,
        cost_per_score_point=(sum(costs) / score_sum) if costs and score_sum > 0 else None,
        wall_per_task_mean=_mean_or_none([r.wall_clock_seconds for r in attempted]),
        output_tokens_per_task_mean=_mean_or_none(out_tokens),
    )


def _group_scores(records: list[BenchTaskRecord]) -> dict[str, float]:
    """Mean pass fraction per probe group (`grader_detail.score_breakdown`, blackbox grader)."""
    per_group: dict[str, list[float]] = {}
    for r in records:
        if not is_attempted(r):
            continue
        breakdown = r.grader_detail.get("score_breakdown")
        if not isinstance(breakdown, dict):
            continue
        for group, cell in breakdown.items():
            if isinstance(cell, dict) and cell.get("total"):
                per_group.setdefault(group, []).append(float(cell.get("fraction", 0.0)))
    return {g: stats.mean(v) for g, v in sorted(per_group.items())}


# A probe group whose between-subject spread is at least this is called out as discriminating.
OBSERVATION_GROUP_SPREAD = 0.10
# Mean score at/above which a subject is at the suite's ceiling (saturation call-out).
OBSERVATION_CEILING_SCORE = 0.95


def _observations(
    efficiency: dict[str, EfficiencyRow],
    group_scores: dict[str, dict[str, float]],
    pairwise: list[PairwiseEntry],
) -> list[str]:
    """Rule-based observations (no judgement): leaders, cost efficiency, which probe group
    separates the subjects, and which pairwise differences are statistically decided."""
    out: list[str] = []
    ran = {sid: e for sid, e in efficiency.items() if e.runs_attempted}
    if not ran:
        return out
    best = max(ran.items(), key=lambda kv: kv[1].score_mean)
    out.append(f"Highest mean score: {best[0]} ({best[1].score_mean:.3f}).")
    priced = {sid: e.cost_per_score_point for sid, e in ran.items() if e.cost_per_score_point}
    if priced:
        cheapest = min(priced.items(), key=lambda kv: kv[1])
        dearest = max(priced.items(), key=lambda kv: kv[1])
        out.append(
            f"Cheapest per score point: {cheapest[0]} (${cheapest[1]:.3f}); most expensive: "
            f"{dearest[0]} (${dearest[1]:.3f}), {dearest[1] / cheapest[1]:.1f}x."
        )
    groups = sorted({g for gs in group_scores.values() for g in gs})
    for g in groups:
        vals = {sid: gs[g] for sid, gs in group_scores.items() if g in gs}
        if len(vals) >= 2:
            spread = max(vals.values()) - min(vals.values())
            if spread >= OBSERVATION_GROUP_SPREAD:
                lo = min(vals.items(), key=lambda kv: kv[1])
                hi = max(vals.items(), key=lambda kv: kv[1])
                out.append(
                    f"Probe group '{g}' separates subjects by {spread:.2f} "
                    f"({hi[0]} {hi[1]:.2f} vs {lo[0]} {lo[1]:.2f})"
                    + ("; perf is timing-sensitive, weigh it lightly." if g == "perf" else ".")
                )
    decided = [
        e
        for e in pairwise
        if e.ci_lo is not None and e.ci_hi is not None and stats.ci_excludes_zero(e.ci_lo, e.ci_hi)
    ]
    undecided = [e for e in pairwise if e not in decided and e.n_tasks]
    if decided:
        out.append(
            "Pairs whose 95% CI excludes 0: "
            + ", ".join(f"{e.a} vs {e.b} ({e.mean_diff:+.3f}, n={e.n_tasks})" for e in decided)
            + "."
        )
    scores = [e.score_mean for e in ran.values()]
    at_ceiling = len(scores) >= 2 and min(scores) >= OBSERVATION_CEILING_SCORE
    if at_ceiling and max(scores) - min(scores) < OBSERVATION_GROUP_SPREAD:
        out.append(
            f"Every subject scores >= {OBSERVATION_CEILING_SCORE:.2f}: the suite is at its "
            "ceiling and does not separate these subjects on quality -- repeats will not "
            "change that; a harder instrument would. Compare them on cost and time."
        )
    elif undecided:
        out.append(
            f"{len(undecided)} pair(s) not distinguishable at this n (CI straddles 0): add "
            "tasks or repeats before ranking them."
        )
    return out


def _env_summary(runs: list[BenchRunRecord]) -> EnvSubjectSummary:
    """Fold one subject's repeat records into an `EnvSubjectSummary`."""
    if all(r.env_isolation is None for r in runs):
        return EnvSubjectSummary(legacy=True)
    task_fps: list[EnvFingerprint | None] = [t.env_fingerprint for r in runs for t in r.tasks]
    merged = merge_fingerprints(task_fps)
    foreign: dict[str, set[str]] = {}
    for r in runs:
        run_fp = merge_fingerprints(t.env_fingerprint for t in r.tasks)
        for field, names in foreign_entries(run_fp, r.env_baseline).items():
            foreign.setdefault(field, set()).update(names)
    baselines = {r.env_baseline.digest for r in runs if r.env_baseline is not None}
    user_configs = {r.user_config.digest for r in runs if r.user_config is not None}
    return EnvSubjectSummary(
        contaminated=any(r.env_contaminated for r in runs),
        fingerprint_digest=merged.digest if merged is not None else None,
        # Several distinct values across repeats are joined so a mismatch stays visible.
        baseline_digest="+".join(sorted(baselines)) or None,
        user_config_digest="+".join(sorted(user_configs)) or None,
        foreign={f: sorted(v) for f, v in sorted(foreign.items())},
        divergent=merged.divergent if merged is not None else False,
        grading_access_task_ids=sorted({t.task_id for r in runs for t in r.tasks if t.suspect}),
    )


def _env_problems(
    summaries: dict[str, EnvSubjectSummary], subject_types: dict[str, str]
) -> list[str]:
    """Every reason the subjects did not run in one clean, shared environment (T-Ws7Iso)."""
    problems: list[str] = []
    for sid, env in summaries.items():
        if env.contaminated:
            problems.append(
                f"{sid}: workspace root had instruction-file ancestors (env_contaminated)"
            )
        if env.foreign:
            problems.append(f"{sid}: loaded entries absent from the baseline: {env.foreign}")
    agent_sids = [sid for sid in summaries if subject_types.get(sid) in AGENT_SUBJECT_TYPES]
    legacy = [sid for sid in agent_sids if summaries[sid].legacy]
    if legacy and len(legacy) < len(agent_sids):
        problems.append(
            f"{', '.join(legacy)}: run predates environment fingerprinting while other "
            "subjects were fingerprinted"
        )
    for label, attr in (
        ("init-event fingerprint", "fingerprint_digest"),
        ("baseline", "baseline_digest"),
        ("user-level config", "user_config_digest"),
    ):
        values = {
            sid: getattr(summaries[sid], attr)
            for sid in agent_sids
            if getattr(summaries[sid], attr) is not None
        }
        if len(set(values.values())) > 1:
            problems.append(f"subjects differ in {label}: {values}")
    return problems


def build_comparison(
    run_dirs: Sequence[str | Path],
    *,
    allow_mixed: bool = False,
    clock: Callable[[], datetime] | None = None,
) -> ComparisonRecord:
    """Build a cross-subject `ComparisonRecord` over *run_dirs* (design §4.6, HLD §6).

    Each entry is a result dir (as `run_suite`/`resolve_result_dir` produce). Several dirs
    for the SAME subject id are *repeats* of one (suite x subject) -- distinct `repeat`
    values, identical run-level `config_fingerprint` -- and are aggregated per task
    (`ComparisonTaskCell`: mean/sd/n of the score, solve rate, cost/wall medians). Two dirs
    with the same subject AND the same `repeat` collapse (last wins), as before.

    Refuses (raises `ResultsError`) to compare:

      - runs from different suite ids or different `schema_version`s -- comparing them
        would silently mix incompatible task sets/shapes;
      - runs whose `parity_fingerprint` differs (HLD §2b: not apples-to-apples), unless
        `allow_mixed=True`, which stamps the record `parity_mixed=True` instead;
      - two runs for the SAME subject id whose run-level `config_fingerprint` differs,
        unless `allow_mixed=True` -- e.g. two different-day re-runs of one subject with
        different overrides; the later one wins once explicitly allowed;
      - (T-Ws7Iso) runs whose ENVIRONMENT differs or is unclean: an `env_contaminated` run,
        a subject whose agents loaded skills/agents/commands/MCP servers/plugins absent
        from its run's baseline probe, agent subjects whose init-event fingerprint,
        baseline or user-level config digests differ, or a pre-T-Ws7Iso agent run mixed
        with fingerprinted ones -- unless `allow_mixed=True`, which stamps `env_mixed=True`
        and lists `env_problems`. Tasks flagged by the hidden-asset audit (`suspect`) are
        never refused; they are excluded from every capability statistic.

    A run dir with no `run.json` at all is never an error: that subject is recorded in
    `ComparisonRecord.not_run` and shown as "not run" everywhere in the report (AC4). A
    single run dir degrades gracefully to a one-subject "comparison" (AC5) rather than
    requiring at least two.

    Raises `ResultsError` if NONE of *run_dirs* has a `run.json` -- there would be
    nothing to determine even the suite id from.
    """
    resolved_clock = clock or _utc_now
    dirs = [Path(d) for d in run_dirs]

    # subject -> repeat number -> record (same (subject, repeat) twice: last wins).
    loaded: dict[str, dict[int, BenchRunRecord]] = {}
    fingerprints: dict[str, str] = {}
    parity_by_subject: dict[str, str | None] = {}
    ordered_subjects: list[str] = []
    not_run: list[str] = []
    missing_dirs: list[Path] = []
    known_suite_id: str | None = None
    known_schema_version: str | None = None
    reference_parity: str | None = None
    parity_mixed = False

    for d in dirs:
        if not (d / RUN_JSON_FILENAME).exists():
            missing_dirs.append(d)
            continue

        record = load_run(d)
        if known_suite_id is None:
            known_suite_id = record.suite_id
            known_schema_version = record.schema_version
            reference_parity = record.parity_fingerprint
        elif record.suite_id != known_suite_id:
            raise ResultsError(
                f"Refusing to compare runs from different suites: {known_suite_id!r} "
                f"(seen first) vs {record.suite_id!r} ({d})"
            )
        elif record.schema_version != known_schema_version:
            raise ResultsError(
                f"Refusing to compare runs with different schema_version: "
                f"{known_schema_version!r} (seen first) vs {record.schema_version!r} ({d})"
            )

        if record.parity_fingerprint != reference_parity:
            if not allow_mixed:
                raise ResultsError(
                    f"Refusing to compare runs with different parity_fingerprint (not "
                    f"apples-to-apples, HLD §2b; pass allow_mixed=True / --allow-mixed to "
                    f"override and stamp the report non-parity): {reference_parity!r} "
                    f"(seen first) vs {record.parity_fingerprint!r} ({d})"
                )
            parity_mixed = True

        subject_id = record.subject.id
        if (
            subject_id in fingerprints
            and fingerprints[subject_id] != record.config_fingerprint
            and not allow_mixed
        ):
            raise ResultsError(
                f"Refusing to compare two runs for subject {subject_id!r} with "
                f"different config_fingerprint (pass allow_mixed=True to override): "
                f"{fingerprints[subject_id]!r} vs {record.config_fingerprint!r} ({d})"
            )
        if subject_id not in loaded:
            ordered_subjects.append(subject_id)
            loaded[subject_id] = {}
        loaded[subject_id][record.repeat] = record  # last-wins per (subject, repeat)
        fingerprints[subject_id] = record.config_fingerprint
        parity_by_subject[subject_id] = record.parity_fingerprint

    if known_suite_id is None:
        raise ResultsError(
            "No valid run.json found in any of the given run dirs "
            f"({[str(d) for d in dirs]}); cannot determine the suite being compared."
        )

    for d in missing_dirs:
        subject_id = _derive_subject_id(d, known_suite_id)
        repeat_match = _REPEAT_SUFFIX_RE.match(subject_id)
        if repeat_match and repeat_match.group(1) in loaded:
            continue  # a repeat dir of a subject that did run, not a separate subject
        if subject_id in ordered_subjects:
            continue
        ordered_subjects.append(subject_id)
        not_run.append(subject_id)

    ran_subjects = [sid for sid in ordered_subjects if sid in loaded]
    records_of: dict[str, list[BenchTaskRecord]] = {
        sid: [t for rep in sorted(loaded[sid]) for t in loaded[sid][rep].tasks]
        for sid in ran_subjects
    }
    task_ids = sorted({t.task_id for recs in records_of.values() for t in recs})

    matrix: dict[str, dict[str, ComparisonTaskCell | None]] = {tid: {} for tid in task_ids}
    family_of: dict[str, str | None] = {}
    language_of: dict[str, str | None] = {}
    for subject_id in ordered_subjects:
        by_task: dict[str, list[BenchTaskRecord]] = {}
        for t in records_of.get(subject_id, []):
            by_task.setdefault(t.task_id, []).append(t)
            family_of.setdefault(t.task_id, t.family)
            language_of.setdefault(t.task_id, t.language)
        for task_id in task_ids:
            recs = by_task.get(task_id)
            matrix[task_id][subject_id] = None if not recs else _build_cell(recs)

    per_subject: dict[str, Aggregate | None] = {}
    for subject_id in ordered_subjects:
        if subject_id not in loaded:
            per_subject[subject_id] = None
        elif len(loaded[subject_id]) == 1:
            # One run: its own persisted aggregate, untouched (byte-compatible).
            per_subject[subject_id] = next(iter(loaded[subject_id].values())).aggregate
        else:
            # Repeats: totals (cost, wall, counts) over every repeat's task runs.
            per_subject[subject_id] = aggregate(cast("list[TaskMetric]", records_of[subject_id]))

    subject_stats: dict[str, SubjectStats | None] = {
        sid: (
            _subject_stats(task_ids, sid, matrix, records_of[sid], len(loaded[sid]))
            if sid in loaded
            else None
        )
        for sid in ordered_subjects
    }
    infra: dict[str, InfraEntry] = {}
    for sid in ran_subjects:
        recs = records_of[sid]
        infra[sid] = InfraEntry(
            count=sum(1 for r in recs if r.subject_status == INFRA_ERROR_STATUS),
            task_ids=sorted({r.task_id for r in recs if r.subject_status == INFRA_ERROR_STATUS}),
            suspect_task_ids=sorted({r.task_id for r in recs if _is_suspect(r)}),
        )
    unreliable = [
        sid
        for sid in ran_subjects
        if (st := subject_stats[sid]) is not None and st.infra_share > UNRELIABLE_INFRA_SHARE
    ]

    efficiency = {sid: _efficiency(records_of[sid]) for sid in ran_subjects}
    group_scores = {sid: _group_scores(records_of[sid]) for sid in ran_subjects}
    pairwise = _pairwise(task_ids, ran_subjects, matrix)

    env_by_subject = {sid: _env_summary(list(loaded[sid].values())) for sid in ran_subjects}
    subject_types = {sid: next(iter(loaded[sid].values())).subject.type for sid in ran_subjects}
    env_problems = _env_problems(env_by_subject, subject_types)
    if env_problems and not allow_mixed:
        raise EnvMismatchError(
            "Refusing to compare runs whose environments differ or leaked (T-Ws7Iso; pass "
            "allow_mixed=True / --allow-mixed to override and stamp the report): "
            + "; ".join(env_problems)
        )

    return ComparisonRecord(
        suite_id=known_suite_id,
        generated_at=resolved_clock().isoformat(),
        subjects=ordered_subjects,
        not_run=not_run,
        task_ids=task_ids,
        matrix=matrix,
        per_subject=per_subject,
        parity_fingerprint=None if parity_mixed else reference_parity,
        parity_mixed=parity_mixed,
        parity_by_subject=parity_by_subject,
        repeats_per_subject={sid: len(loaded[sid]) for sid in ran_subjects},
        subject_stats=subject_stats,
        per_family=_rollup(family_of, ran_subjects, matrix),
        per_language=_rollup(language_of, ran_subjects, matrix),
        infra=infra,
        pairwise=pairwise,
        unreliable_subjects=unreliable,
        efficiency=efficiency,
        group_scores=group_scores,
        observations=_observations(efficiency, group_scores, pairwise),
        env_by_subject=env_by_subject,
        env_mixed=bool(env_problems),
        env_problems=env_problems,
    )


# ---------------------------------------------------------------------------
# comparison.md
# ---------------------------------------------------------------------------


def _fmt_matrix_cell(cell: ComparisonTaskCell | None) -> str:
    if cell is None:
        return "—"
    if cell.n > 1:
        # Repeats: "k/n" solved, then the median cost -- the single-repeat shape below is
        # kept verbatim for the common r=1 case.
        return f"{round(cell.solve_rate * cell.n)}/{cell.n} / {_fmt_cost(cell.cost_median)}"
    return f"{_fmt_bool(cell.solved)} / {_fmt_cost(cell.cost_usd)}"


def _best(candidates: list[tuple[float, str]], *, minimize: bool) -> tuple[float, str] | None:
    """Pick the best `(value, subject_id)` candidate; ties break on subject_id
    ascending for a deterministic winner line (CLAUDE.md determinism rule)."""
    if not candidates:
        return None
    key: Callable[[tuple[float, str]], tuple[float, str]] = (
        (lambda c: (c[0], c[1])) if minimize else (lambda c: (-c[0], c[1]))
    )
    return min(candidates, key=key)


def _tie_count(candidates: list[tuple[float, str]], value: float) -> int:
    """Count of *candidates* sharing the winning *value* (W5: a winner line must
    disclose when >1 subject shares that value, not silently hide it behind `_best`'s
    deterministic subject-id tiebreak).
    """
    return sum(1 for v, _ in candidates if v == value)


def _attempted_rates(
    comparison: ComparisonRecord, subject_id: str, agg: Aggregate
) -> tuple[float, float] | None:
    """`(solve_rate, mean_score)` over ATTEMPTED work for *subject_id*, or `None` when it
    made no real attempt at all (nothing to rank on).

    Prefers the comparison's own `subject_stats` (task-level, repeat-aware). A record
    without it (hand-built / 1.0) falls back to the `Aggregate`: its `*_attempted` fields,
    or -- when those were never recorded (`attempted == 0` with no infra errors, i.e. a
    pre-seam aggregate) -- the all-task rates, which are then the attempted ones by
    definition.
    """
    st = comparison.subject_stats.get(subject_id)
    if st is not None:
        return (st.solve_rate_attempted, st.score_mean_attempted) if st.attempted > 0 else None
    if agg.attempted == 0 and agg.infra_errors == 0:
        return (agg.solve_rate, agg.mean_score)
    if agg.attempted == 0:
        return None
    return (agg.solve_rate_attempted, agg.mean_score_attempted)


def render_winners(comparison: ComparisonRecord) -> list[str]:
    """A "winner" line per axis (solve rate, mean score, total cost, cost per solved,
    wall-clock) -- TASK.md T-Rpt3Wq bullet (c). Only subjects that actually ran are
    candidates; an axis with no eligible candidate (e.g. every subject not run, or nobody
    solved anything so every `cost_per_solved` is `None`) renders "n/a" rather than
    raising.

    Solve rate and mean score rank on ATTEMPTED work (infra errors / budget skips
    excluded, HLD §5.1) with each task counted once however many repeats it had (HLD §6);
    a subject that never made a real attempt is not a candidate on those two axes.

    Not `_`-prefixed: `bench/cli.py`'s `ao-bench report` (T-Cli8Nf) reuses this directly
    to print the same winner lines to stdout, rather than re-deriving them or scraping
    the just-written `comparison.md` back off disk.
    """
    ran = {sid: agg for sid, agg in comparison.per_subject.items() if agg is not None}
    if not ran:
        return ["- no subjects were run; nothing to compare."]

    rates = {sid: _attempted_rates(comparison, sid, agg) for sid, agg in ran.items()}
    # A subject with no attempted work at all (every task an infra error / budget skip) says
    # nothing on ANY axis -- not even "cheapest" -- so it is no candidate on any of them.
    eligible = {sid: agg for sid, agg in ran.items() if rates[sid] is not None}
    solve_candidates = [(r[0], sid) for sid, r in rates.items() if r is not None]
    score_candidates = [(r[1], sid) for sid, r in rates.items() if r is not None]
    cost_candidates = [(agg.total_cost_usd, sid) for sid, agg in eligible.items()]
    cost_per_solved_candidates = [
        (agg.cost_per_solved, sid)
        for sid, agg in eligible.items()
        if agg.cost_per_solved is not None
    ]
    wall_candidates = [(agg.total_wall_clock_seconds, sid) for sid, agg in eligible.items()]

    best_solve = _best(solve_candidates, minimize=False)
    best_score = _best(score_candidates, minimize=False)
    best_cost = _best(cost_candidates, minimize=True)
    best_cost_per_solved = _best(cost_per_solved_candidates, minimize=True)
    best_wall = _best(wall_candidates, minimize=True)

    def _line(
        label: str,
        best: tuple[float, str] | None,
        fmt: Callable[[float], str],
        candidates: list[tuple[float, str]],
    ) -> str:
        if best is None:
            return f"- {label}: n/a"
        others = _tie_count(candidates, best[0]) - 1
        # W5: disclose a tie rather than letting the subject-id tiebreak in `_best`
        # silently present one winner as sole when others share the same value.
        tie_note = f", tied with {others} other{'s' if others != 1 else ''}" if others > 0 else ""
        return f"- {label}: {best[1]} ({fmt(best[0])}{tie_note})"

    return [
        _line(
            "Highest solve rate",
            best_solve,
            lambda v: f"{_fmt_pct(v)} of attempted",
            solve_candidates,
        ),
        _line(
            "Highest mean score",
            best_score,
            lambda v: f"{v:.3f} over attempted",
            score_candidates,
        ),
        _line("Lowest total cost", best_cost, lambda v: f"${v:.4f}", cost_candidates),
        _line(
            "Lowest cost per solved",
            best_cost_per_solved,
            lambda v: f"${v:.4f}",
            cost_per_solved_candidates,
        ),
        _line("Fastest total wall-clock", best_wall, lambda v: f"{v:.2f}s", wall_candidates),
    ]


def _fmt_opt_float(value: float | None, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _fmt_p(value: float | None) -> str:
    if value is None:
        return "—"
    return "<0.001" if value < 0.001 else f"{value:.3f}"


def _fmt_rollup_cell(cell: RollupCell | None) -> str:
    if cell is None:
        return "—"
    return f"{cell.score_mean:.2f} / {_fmt_pct(cell.solve_rate)} (n={cell.n})"


def _render_parity_md(comparison: ComparisonRecord) -> list[str]:
    lines = ["", "## Parity", ""]
    if comparison.parity_mixed:
        lines.append(
            "- **NON-PARITY comparison**: the runs used different parity bundles"
            " (--allow-mixed), so a difference may reflect instructions/skills/limits"
            " rather than the subjects themselves (HLD §2b)."
        )
        for sid, fp in comparison.parity_by_subject.items():
            lines.append(f"- {sid}: {f'`{fp}`' if fp else 'none'}")
    elif comparison.parity_fingerprint is not None:
        lines.append(
            f"- parity_fingerprint: `{comparison.parity_fingerprint}` (shared by every run)"
        )
    else:
        lines.append(
            "- no parity bundle applied: subjects ran under their own instructions/skills/"
            "limits, so a difference may not isolate orchestration (HLD §2b)."
        )
    if comparison.repeats_per_subject:
        per = ", ".join(f"{sid}={n}" for sid, n in comparison.repeats_per_subject.items())
        lines.append(f"- repeats per subject: {per}")
    return lines


def _render_efficiency_md(comparison: ComparisonRecord) -> list[str]:
    lines = ["", "## Cost & efficiency", ""]
    if not comparison.efficiency:
        lines.append("_No efficiency data on this comparison._")
        return lines
    lines += [
        "Per-task figures cover attempted runs (repeats included); spend counts every run.",
        "",
        "| Subject | Spend($) | Runs | Solved | Mean score | $/task (mean) | $/task (median)"
        " | $/solved | $/score-pt | Wall/task(s) | Out tokens/task |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for sid, e in comparison.efficiency.items():
        out_tok = e.output_tokens_per_task_mean
        tokens = "—" if out_tok is None else f"{out_tok:.0f}"
        lines.append(
            f"| {sid} | {e.spend_usd:.4f} | {e.runs_attempted} | {e.solved_runs} "
            f"| {e.score_mean:.3f} | {_fmt_cost(e.cost_per_task_mean)} "
            f"| {_fmt_cost(e.cost_per_task_median)} | {_fmt_cost(e.cost_per_solved)} "
            f"| {_fmt_cost(e.cost_per_score_point)} | {_fmt_opt_float(e.wall_per_task_mean, 1)} "
            f"| {tokens} |"
        )
    if comparison.group_scores:
        groups = sorted({g for gs in comparison.group_scores.values() for g in gs})
        lines += [
            "",
            "Mean pass fraction per probe group (attempted runs):",
            "",
            "| Subject | " + " | ".join(groups) + " |",
            "| --- | " + " | ".join("---" for _ in groups) + " |",
        ]
        for sid, gs in comparison.group_scores.items():
            cells = [f"{gs[g]:.3f}" if g in gs else "—" for g in groups]
            lines.append(f"| {sid} | " + " | ".join(cells) + " |")
    if comparison.observations:
        lines += ["", "### Observations (rule-based)", ""]
        lines += [f"- {o}" for o in comparison.observations]
    return lines


def _render_env_md(comparison: ComparisonRecord) -> list[str]:
    lines = ["", "## Environment isolation", ""]
    if comparison.env_mixed:
        lines.append(
            "- **NON-ISOLATED comparison** (--allow-mixed): the subjects did not run in one"
            " clean, shared environment, so a difference may reflect what the agents loaded"
            " rather than the subjects themselves:"
        )
        lines += [f"  - {p}" for p in comparison.env_problems]
    if not comparison.env_by_subject:
        lines.append("_No environment data on this comparison._")
        return lines
    if comparison.env_mixed:
        lines.append("")
    lines += [
        "| Subject | Fingerprint | Baseline | User config | Contaminated | Foreign"
        " | Grading-access tasks (excluded) |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]

    def _short(value: str | None) -> str:
        return f"`{value[:12]}`" if value else "—"

    for sid, env in comparison.env_by_subject.items():
        if env.legacy:
            lines.append(f"| {sid} | not fingerprinted (pre-T-Ws7Iso) | — | — | ? | ? | — |")
            continue
        foreign = "; ".join(f"{f}: {', '.join(v)}" for f, v in env.foreign.items()) or "—"
        lines.append(
            f"| {sid} | {_short(env.fingerprint_digest)}{' (divergent)' if env.divergent else ''}"
            f" | {_short(env.baseline_digest)} | {_short(env.user_config_digest)}"
            f" | {_fmt_bool(env.contaminated)} | {foreign}"
            f" | {', '.join(env.grading_access_task_ids) or '—'} |"
        )
    return lines


def _not_run_label(comparison: ComparisonRecord, subject_id: str) -> str:
    """`"<id> (not run)"` for a subject with no run, plain `<id>` for one that ran but has
    no stats block (a 1.0 comparison read back via `load_comparison`)."""
    return (
        f"{subject_id} (not run)" if comparison.per_subject.get(subject_id) is None else subject_id
    )


def _render_attempted_md(comparison: ComparisonRecord) -> list[str]:
    lines = [
        "",
        "## Attempted-based rates (infra excluded)",
        "",
        "Rates average per-task values over the tasks a subject actually attempted"
        " (infra errors and budget skips are not capability evidence); each task counts once"
        " however many repeats it had.",
        "",
        "| Subject | Task runs | Attempted | Infra errors | Solve rate | Mean score"
        " | Cost/score-pt($) | Median cost/task($) | Median wall/task(s) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for sid in comparison.subjects:
        st = comparison.subject_stats.get(sid)
        if st is None:
            lines.append(f"| {_not_run_label(comparison, sid)} | — | — | — | — | — | — | — | — |")
            continue
        lines.append(
            f"| {sid} | {st.task_runs} | {st.attempted} | {st.infra_errors} "
            f"| {_fmt_pct(st.solve_rate_attempted)} | {st.score_mean_attempted:.3f} "
            f"| {_fmt_cost(st.cost_per_score_point)} | {_fmt_cost(st.cost_median)} "
            f"| {_fmt_opt_float(st.wall_median, 2)} |"
        )
    return lines


def _render_pairwise_md(comparison: ComparisonRecord) -> list[str]:
    lines = [
        "",
        "## Pairwise (paired bootstrap 95% CI, Wilcoxon p)",
        "",
        "Per-task difference of the repeat-mean score, A minus B, over tasks both attempted;"
        " `CI excludes 0` is the staged-repeat stopping rule (HLD §6). Reported, not gated.",
        "",
        "| A | B | Metric | Mean diff (A-B) | 95% CI | Wilcoxon p | Tasks | CI excludes 0 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    if not comparison.pairwise:
        lines.append("| — | — | — | — | — | — | — | — |")
    for e in comparison.pairwise:
        if e.mean_diff is None or e.ci_lo is None or e.ci_hi is None:
            lines.append(f"| {e.a} | {e.b} | {e.metric} | — | — | — | 0 | — |")
            continue
        lines.append(
            f"| {e.a} | {e.b} | {e.metric} | {e.mean_diff:+.3f} "
            f"| [{e.ci_lo:.3f}, {e.ci_hi:.3f}] | {_fmt_p(e.p_wilcoxon)} | {e.n_tasks} "
            f"| {_fmt_bool(stats.ci_excludes_zero(e.ci_lo, e.ci_hi))} |"
        )
    return lines


def _render_rollup_md(
    title: str,
    label: str,
    plural: str,
    rollup: dict[str, dict[str, RollupCell]],
    subjects: list[str],
) -> list[str]:
    lines = ["", f"## {title}", ""]
    if not rollup:
        lines.append(f"_No {label} metadata on this suite's tasks._")
        return lines
    lines.append(
        "Cells: mean score / solve rate (n = tasks attempted)."
        f" {plural.capitalize()} weigh equally."
    )
    lines.append("")
    lines.append(f"| {label.title()} | " + " | ".join(subjects) + " |")
    lines.append("| --- | " + " | ".join("---" for _ in subjects) + " |")
    for group, per_subject in rollup.items():
        cells = [_fmt_rollup_cell(per_subject.get(sid)) for sid in subjects]
        lines.append(f"| {group} | " + " | ".join(cells) + " |")
    return lines


def _render_infra_md(comparison: ComparisonRecord) -> list[str]:
    lines = [
        "",
        "## Infra errors / suspects",
        "",
        f"Infra errors (`subject_status == error`) are excluded from the rates above."
        f" Suspects are unsolved non-error runs under ${SUSPECT_MAX_COST_USD:.2f} and"
        f" {SUSPECT_MAX_WALL_SECONDS:.0f}s -- they look like aborts but stay IN the rates.",
        "",
        "| Subject | Infra errors | Share | Infra tasks | Suspect tasks |",
        "| --- | --- | --- | --- | --- |",
    ]
    for sid in comparison.subjects:
        entry = comparison.infra.get(sid)
        st = comparison.subject_stats.get(sid)
        if entry is None or st is None:
            lines.append(f"| {_not_run_label(comparison, sid)} | — | — | — | — |")
            continue
        lines.append(
            f"| {sid} | {entry.count} | {_fmt_pct(st.infra_share)} "
            f"| {', '.join(entry.task_ids) or '—'} | {', '.join(entry.suspect_task_ids) or '—'} |"
        )
    return lines


def _render_comparison_md(comparison: ComparisonRecord) -> str:
    """Design §4.6: side-by-side subject table + per-task solved/cost matrix, plus a
    "winner" line per axis (this task's own acceptance criteria), then the HLD §6
    statistics sections (parity, attempted rates, pairwise CIs, family/language roll-ups,
    infra) after the original three tables, which keep their shape."""
    lines: list[str] = [f"# Bench comparison — {comparison.suite_id}", ""]
    lines.append(f"- generated_at: {comparison.generated_at}")
    if comparison.not_run:
        lines.append(f"- not run: {', '.join(comparison.not_run)}")
    if comparison.unreliable_subjects:
        lines += [
            "",
            f"> **UNRELIABLE**: infra errors exceed {UNRELIABLE_INFRA_SHARE * 100:.0f}% of task"
            f" runs for {', '.join(comparison.unreliable_subjects)} -- treat the rankings below"
            " with caution.",
        ]
    lines += [
        "",
        "## Per-subject",
        "",
        "| Subject | Solved/Total | Solve Rate | Total Cost($) | Cost/Solved($) | Total Wall(s) |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for sid in comparison.subjects:
        agg = comparison.per_subject.get(sid)
        if agg is None:
            lines.append(f"| {sid} (not run) | — | — | — | — | — |")
        else:
            lines.append(
                f"| {sid} | {agg.solved}/{agg.total} | {_fmt_pct(agg.solve_rate)} "
                f"| {agg.total_cost_usd:.4f} | {_fmt_cost(agg.cost_per_solved)} "
                f"| {agg.total_wall_clock_seconds:.2f} |"
            )

    lines += ["", "## Per-task (solved / cost)", ""]
    lines.append("| Task | " + " | ".join(comparison.subjects) + " |")
    lines.append("| --- | " + " | ".join("---" for _ in comparison.subjects) + " |")
    for task_id in comparison.task_ids:
        row_cells = [
            _fmt_matrix_cell(comparison.matrix.get(task_id, {}).get(sid))
            for sid in comparison.subjects
        ]
        lines.append(f"| {task_id} | " + " | ".join(row_cells) + " |")

    lines += ["", "## Winners", ""]
    lines += render_winners(comparison)

    lines += _render_efficiency_md(comparison)
    lines += _render_parity_md(comparison)
    lines += _render_env_md(comparison)
    lines += _render_attempted_md(comparison)
    lines += _render_pairwise_md(comparison)
    lines += _render_rollup_md(
        "Per family", "family", "families", comparison.per_family, comparison.subjects
    )
    lines += _render_rollup_md(
        "Per language", "language", "languages", comparison.per_language, comparison.subjects
    )
    lines += _render_infra_md(comparison)
    return "\n".join(lines) + "\n"


def write_comparison(comparison: ComparisonRecord, out_dir: str | Path) -> tuple[Path, Path]:
    """Write `comparison.json` + `comparison.md` into *out_dir* (design §4.6/§9 --
    typically `benchmarks/results/<date>-<suite>-compare/`, resolved by the caller via
    `runner.resolve_result_dir(compute_compare_id(...), out_dir=results_root)`).

    Returns `(comparison_json_path, comparison_md_path)`.
    """
    target_dir = Path(out_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    json_path = target_dir / COMPARISON_JSON_FILENAME
    md_path = target_dir / COMPARISON_MD_FILENAME
    json_path.write_text(comparison.model_dump_json(indent=2))
    md_path.write_text(_render_comparison_md(comparison))
    return json_path, md_path


def load_comparison(path: str | Path) -> ComparisonRecord:
    """Read a `comparison.json` back into a `ComparisonRecord` (*path* is the file or the
    directory holding it).

    Understands the current 2.0 shape and the pre-repeats 1.0 one: a 1.0 file loads with
    its cells upgraded to single-repeat cells (see `ComparisonTaskCell`) and an empty
    statistics layer, keeping `schema_version == "1.0"` so nobody mistakes it for a
    comparison that had pairwise stats computed. Any other version, unreadable JSON or a
    shape mismatch raises `ResultsError`.
    """
    p = Path(path)
    json_path = p / COMPARISON_JSON_FILENAME if p.is_dir() else p
    if not json_path.exists():
        raise ResultsError(f"No {COMPARISON_JSON_FILENAME} found at {json_path}")
    try:
        data = json.loads(json_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ResultsError(f"Failed to read {json_path}: {exc}") from exc
    version = data.get("schema_version") if isinstance(data, dict) else None
    if version != COMPARISON_SCHEMA_VERSION and version not in LEGACY_COMPARISON_SCHEMA_VERSIONS:
        raise ResultsError(
            f"{json_path} has unsupported comparison schema_version {version!r} (supported:"
            f" {COMPARISON_SCHEMA_VERSION!r}, legacy {sorted(LEGACY_COMPARISON_SCHEMA_VERSIONS)})"
        )
    try:
        return ComparisonRecord.model_validate(data)
    except Exception as exc:  # pydantic.ValidationError et al.
        raise ResultsError(
            f"{json_path} does not match the expected comparison.json schema: {exc}"
        ) from exc
