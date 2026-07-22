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

Results are committed to git for preservation (design §9); every task's `workspace` is
already just a path POINTER (`BenchTaskRecord.workspace`, set by the runner from
`SubjectResult.capture_dir`) -- this module never reads or copies transcript content,
only the already-bounded `run.json` fields.

Import direction (SI-1): reads only from core-adjacent sibling `bench/` modules
(`runner`, `metrics`, `errors`); nothing in core or in `bench/{spec,subjects,graders,
metrics,runner}.py` imports this module.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from .errors import ResultsError
from .metrics import Aggregate
from .runner import RUN_JSON_FILENAME, BenchRunRecord

# ---------------------------------------------------------------------------
# Named constants (CLAUDE.md: no magic literals)
# ---------------------------------------------------------------------------

SUMMARY_MD_FILENAME = "summary.md"
COMPARISON_JSON_FILENAME = "comparison.json"
COMPARISON_MD_FILENAME = "comparison.md"
COMPARISON_SCHEMA_VERSION = "1.0"

# `<date>-<suite.id>-compare` (design §9) -- mirrors runner.py's own
# `<date>-<suite.id>-<subject.id>` bench_run_id convention/date format, kept as a local
# constant rather than importing runner's underscore-prefixed private one.
_COMPARE_ID_DATE_FORMAT = "%Y-%m-%d"
COMPARE_DIR_SUFFIX = "compare"

# `bench_run_id`/compare-dir names start with an ISO date (`compute_bench_run_id`,
# `compute_compare_id`) -- used to best-effort recover a subject id from a run dir that
# has no `run.json` at all (AC4: "missing subject run.json -> shown as not run").
_RUN_DIR_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-(.+)$")


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
    """

    subject_status: str
    solved: bool
    score: float
    cost_usd: float | None
    wall_clock_seconds: float


class ComparisonRecord(BaseModel):
    """The persisted `comparison.json` shape (design §4.6): a per-task x per-subject
    matrix plus per-subject aggregates, all keyed by the union of task ids / requested
    subjects so a caller never has to guess which cells are legitimately absent.
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
    # subject_id -> aggregate (None = that subject is in `not_run`).
    per_subject: dict[str, Aggregate | None] = {}


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
    return "\n".join(lines) + "\n"


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


def build_comparison(
    run_dirs: Sequence[str | Path],
    *,
    allow_mixed: bool = False,
    clock: Callable[[], datetime] | None = None,
) -> ComparisonRecord:
    """Build a cross-subject `ComparisonRecord` over *run_dirs* (design §4.6).

    Each entry is a result dir (as `run_suite`/`resolve_result_dir` produce). Refuses
    (raises `ResultsError`) to compare:

      - runs from different suite ids or different `schema_version`s -- comparing them
        would silently mix incompatible task sets/shapes;
      - two runs for the SAME subject id whose run-level `config_fingerprint` differs,
        unless `allow_mixed=True` -- e.g. two different-day re-runs of one subject with
        different overrides; the later one wins once explicitly allowed.

    A run dir with no `run.json` at all is never an error: that subject is recorded in
    `ComparisonRecord.not_run` and shown as "not run" everywhere in the report (AC4). A
    single run dir degrades gracefully to a one-subject "comparison" (AC5) rather than
    requiring at least two.

    Raises `ResultsError` if NONE of *run_dirs* has a `run.json` -- there would be
    nothing to determine even the suite id from.
    """
    resolved_clock = clock or _utc_now
    dirs = [Path(d) for d in run_dirs]

    loaded: dict[str, BenchRunRecord] = {}
    fingerprints: dict[str, str] = {}
    ordered_subjects: list[str] = []
    not_run: list[str] = []
    missing_dirs: list[Path] = []
    known_suite_id: str | None = None
    known_schema_version: str | None = None

    for d in dirs:
        if not (d / RUN_JSON_FILENAME).exists():
            missing_dirs.append(d)
            continue

        record = load_run(d)
        if known_suite_id is None:
            known_suite_id = record.suite_id
            known_schema_version = record.schema_version
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
        loaded[subject_id] = record  # last-wins for an explicitly-allowed mixed dup
        fingerprints[subject_id] = record.config_fingerprint

    if known_suite_id is None:
        raise ResultsError(
            "No valid run.json found in any of the given run dirs "
            f"({[str(d) for d in dirs]}); cannot determine the suite being compared."
        )

    for d in missing_dirs:
        subject_id = _derive_subject_id(d, known_suite_id)
        ordered_subjects.append(subject_id)
        not_run.append(subject_id)

    task_ids = sorted({t.task_id for record in loaded.values() for t in record.tasks})

    matrix: dict[str, dict[str, ComparisonTaskCell | None]] = {tid: {} for tid in task_ids}
    for subject_id in ordered_subjects:
        subject_record = loaded.get(subject_id)
        by_task = {t.task_id: t for t in subject_record.tasks} if subject_record is not None else {}
        for task_id in task_ids:
            t = by_task.get(task_id)
            matrix[task_id][subject_id] = (
                None
                if t is None
                else ComparisonTaskCell(
                    subject_status=t.subject_status,
                    solved=t.solved,
                    score=t.score,
                    cost_usd=t.cost_usd,
                    wall_clock_seconds=t.wall_clock_seconds,
                )
            )

    per_subject: dict[str, Aggregate | None] = {
        subject_id: (loaded[subject_id].aggregate if subject_id in loaded else None)
        for subject_id in ordered_subjects
    }

    return ComparisonRecord(
        suite_id=known_suite_id,
        generated_at=resolved_clock().isoformat(),
        subjects=ordered_subjects,
        not_run=not_run,
        task_ids=task_ids,
        matrix=matrix,
        per_subject=per_subject,
    )


# ---------------------------------------------------------------------------
# comparison.md
# ---------------------------------------------------------------------------


def _fmt_matrix_cell(cell: ComparisonTaskCell | None) -> str:
    if cell is None:
        return "—"
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


def render_winners(comparison: ComparisonRecord) -> list[str]:
    """A "winner" line per axis (solve_rate, total cost, cost per solved, wall-clock) --
    TASK.md T-Rpt3Wq bullet (c). Only subjects that actually ran are candidates; an axis
    with no eligible candidate (e.g. every subject not run, or nobody solved anything so
    every `cost_per_solved` is `None`) renders "n/a" rather than raising.

    Not `_`-prefixed: `bench/cli.py`'s `ao-bench report` (T-Cli8Nf) reuses this directly
    to print the same winner lines to stdout, rather than re-deriving them or scraping
    the just-written `comparison.md` back off disk.
    """
    ran = {sid: agg for sid, agg in comparison.per_subject.items() if agg is not None}
    if not ran:
        return ["- no subjects were run; nothing to compare."]

    best_solve = _best([(agg.solve_rate, sid) for sid, agg in ran.items()], minimize=False)
    best_cost = _best([(agg.total_cost_usd, sid) for sid, agg in ran.items()], minimize=True)
    best_cost_per_solved = _best(
        [(agg.cost_per_solved, sid) for sid, agg in ran.items() if agg.cost_per_solved is not None],
        minimize=True,
    )
    best_wall = _best(
        [(agg.total_wall_clock_seconds, sid) for sid, agg in ran.items()], minimize=True
    )

    def _line(label: str, best: tuple[float, str] | None, fmt: Callable[[float], str]) -> str:
        return f"- {label}: n/a" if best is None else f"- {label}: {best[1]} ({fmt(best[0])})"

    return [
        _line("Highest solve rate", best_solve, _fmt_pct),
        _line("Lowest total cost", best_cost, lambda v: f"${v:.4f}"),
        _line("Lowest cost per solved", best_cost_per_solved, lambda v: f"${v:.4f}"),
        _line("Fastest total wall-clock", best_wall, lambda v: f"{v:.2f}s"),
    ]


def _render_comparison_md(comparison: ComparisonRecord) -> str:
    """Design §4.6: side-by-side subject table + per-task solved/cost matrix, plus a
    "winner" line per axis (this task's own acceptance criteria)."""
    lines: list[str] = [f"# Bench comparison — {comparison.suite_id}", ""]
    lines.append(f"- generated_at: {comparison.generated_at}")
    if comparison.not_run:
        lines.append(f"- not run: {', '.join(comparison.not_run)}")
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
