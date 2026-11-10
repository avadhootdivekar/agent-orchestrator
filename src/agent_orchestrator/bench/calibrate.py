"""`ao-bench calibrate` (HLD §7 step 2): is every task in the discriminating band?

Runs the anchor subject(s) over a suite at `repeats` repeats (reusing `runner.run_suite`,
one call per subject x repeat, each repeat in its own `-r<n>` result dir), pools the
pass/fail outcomes of every ATTEMPTED run (infra errors and budget skips are not evidence)
per task across subjects x repeats, and compares that pooled pass rate with the band
(default 0.2-0.8, inclusive -- HLD G1: "pooled pass rate in the 20-80 % band"):

  - `in`      -- discriminating; keep;
  - `above`   -- too easy (saturated);
  - `below`   -- too hard (nobody gets it);
  - `no_data` -- every run of it was an infra error / budget skip; nothing to judge.

Out-of-band tasks get a one-line suggestion (text only -- regeneration/retirement are other
verbs): a task with a `difficulty` vector (Tier 1, generated) is `raise difficulty` /
`lower difficulty`; a task without one (Tier 2, fixed) can only be `retire`d (HLD §7.2).

With `--write` each calibrated task's `calibration` record
(`{date, subjects, repeats, pooled_pass_rate, verdict}`) is stored in `suite.json`
(`json.dumps(indent=2)`, non-ASCII preserved; a suite whose hand formatting differs from that
is re-indented once). A `no_data` task keeps whatever record it already had. The rewritten
file is re-validated through `load_suite` and rolled back if it no longer loads.

Lazy imports: `bench/cli.py` imports this module at start-up for `calibrate_command`, so the
heavy bench modules (`runner`, `spec`, `results`) are imported inside the functions that need
them, keeping `ao-bench --help` and the SI-1 import isolation cheap.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import typer
from pydantic import BaseModel

# Named constants (CLAUDE.md: no magic literals).
DEFAULT_BAND = (0.2, 0.8)
DEFAULT_REPEATS = 2
CALIBRATION_DATE_FORMAT = "%Y-%m-%d"
POOLED_RATE_DIGITS = 4
JSON_INDENT = 2
_SUITE_YAML_SUFFIXES = frozenset({".yaml", ".yml"})

VERDICT_IN = "in"
VERDICT_ABOVE = "above"
VERDICT_BELOW = "below"
VERDICT_NO_DATA = "no_data"

SUGGEST_KEEP = "keep"
SUGGEST_RAISE = "raise difficulty"
SUGGEST_LOWER = "lower difficulty"
SUGGEST_RETIRE = "retire"
SUGGEST_RERUN = "rerun (no attempted runs)"


class TaskCalibration(BaseModel):
    """One task's calibration outcome: the pooled pass rate and what to do about it."""

    task_id: str
    family: str | None = None
    # Attempted subject x repeat runs pooled / how many of them solved the task / how many
    # runs were set aside as infra errors or budget skips.
    attempts: int
    passes: int
    excluded: int
    pooled_pass_rate: float | None
    verdict: str
    suggestion: str


class CalibrationReport(BaseModel):
    suite_id: str
    date: str
    subjects: list[str]
    repeats: int
    band: tuple[float, float]
    tasks: list[TaskCalibration]
    run_dirs: list[str] = []


def _utc_now() -> datetime:
    return datetime.now(UTC)


def parse_band(text: str) -> tuple[float, float]:
    """`"0.2,0.8"` -> `(0.2, 0.8)`; `ValueError` unless `0 <= lo < hi <= 1`."""
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 2:
        raise ValueError(f"--band must be 'LOW,HIGH' (two numbers), got {text!r}")
    try:
        lo, hi = float(parts[0]), float(parts[1])
    except ValueError as exc:
        raise ValueError(f"--band must be 'LOW,HIGH' (two numbers), got {text!r}") from exc
    if not 0.0 <= lo < hi <= 1.0:
        raise ValueError(f"--band needs 0 <= LOW < HIGH <= 1, got {lo},{hi}")
    return lo, hi


def verdict_for(rate: float | None, band: tuple[float, float]) -> str:
    """Band verdict of a pooled pass rate (the band is inclusive: `lo <= rate <= hi`)."""
    if rate is None:
        return VERDICT_NO_DATA
    lo, hi = band
    if rate < lo:
        return VERDICT_BELOW
    if rate > hi:
        return VERDICT_ABOVE
    return VERDICT_IN


def suggestion_for(verdict: str, *, has_difficulty_vector: bool) -> str:
    """HLD §7.2: adjust the difficulty vector (Tier 1) or drop the task (Tier 2)."""
    if verdict == VERDICT_IN:
        return SUGGEST_KEEP
    if verdict == VERDICT_NO_DATA:
        return SUGGEST_RERUN
    if not has_difficulty_vector:
        return SUGGEST_RETIRE
    return SUGGEST_RAISE if verdict == VERDICT_ABOVE else SUGGEST_LOWER


def calibrate_suite(
    suite_path: str | Path,
    subject_paths: Sequence[str | Path],
    *,
    repeats: int = DEFAULT_REPEATS,
    band: tuple[float, float] = DEFAULT_BAND,
    out_dir: str | Path | None = None,
    tasks: Sequence[str] | None = None,
    cost_budget_usd: float | None = None,
    max_parallel: int | None = None,
    parity: str | Path | None = None,
    force: bool = False,
    enable_xlarge: bool = False,
    clock: Callable[[], datetime] | None = None,
    allow_contaminated_env: bool = False,
) -> CalibrationReport:
    """Run every subject x repeat over the suite and pool the outcomes per task.

    Caps resolve exactly as for `ao-bench run`/`campaign` (CLI value > the suite's tier
    default, via `bench.tiers.resolve_effective`) and apply per `run_suite` call. *tasks*
    restricts the run to those ids (a retired / `enabled: false` task is never run and never
    reported). Raises `BenchError` for a usage problem (no subject, `repeats < 1`, an unknown
    task id, a disabled tier without *enable_xlarge*) and lets `SpecValidationError` from
    spec loading propagate -- a malformed spec is a usage error, not a calibration result.
    """
    from .campaign import enforce_tier_enabled
    from .errors import BenchError
    from .results import is_attempted
    from .runner import compute_bench_run_id, resolve_result_dir, run_suite
    from .spec import load_subject, load_suite
    from .tiers import load_tier_config, resolve_effective

    if not subject_paths:
        raise BenchError("ao-bench calibrate requires at least one --subject")
    if repeats < 1:
        raise BenchError(f"--repeats must be >= 1, got {repeats}")
    resolved_clock = clock or _utc_now

    suite = load_suite(suite_path)
    tier_config = load_tier_config(suite.tier)
    enforce_tier_enabled(suite.tier, tier_config, enable_xlarge)
    effective = resolve_effective(
        tier_config, cli_max_parallel=max_parallel, cli_cost_budget=cost_budget_usd
    )

    live = [t for t in suite.tasks if t.enabled]
    if tasks:
        unknown = sorted(set(tasks) - {t.id for t in suite.tasks})
        if unknown:
            raise BenchError(f"--task not in suite {suite.id!r}: {', '.join(unknown)}")
        wanted = set(tasks)
        live = [t for t in live if t.id in wanted]
    task_filter = sorted(t.id for t in live)

    subject_ids: list[str] = []
    run_dirs: list[str] = []
    outcomes: dict[str, list[tuple[bool, bool]]] = {t.id: [] for t in live}  # (attempted, solved)

    for subject_path in subject_paths:
        subject_spec = load_subject(subject_path)
        subject_ids.append(subject_spec.id)
        for repeat in range(1, repeats + 1):
            record = run_suite(
                suite_path,
                subject_path,
                out_dir=out_dir,
                force=force,
                task_filter=task_filter,
                cost_budget_usd=float(effective["cost_budget_usd"]),
                max_parallel=int(effective["max_parallel"]),
                clock=resolved_clock,
                parity=parity,
                repeat=repeat,
                allow_contaminated_env=allow_contaminated_env,
            )
            run_dirs.append(
                str(
                    resolve_result_dir(
                        compute_bench_run_id(suite, subject_spec, resolved_clock, repeat),
                        out_dir=out_dir,
                    )
                )
            )
            for t in record.tasks:
                if t.task_id in outcomes:
                    outcomes[t.task_id].append((is_attempted(t), t.solved))

    results: list[TaskCalibration] = []
    for task in live:
        runs = outcomes[task.id]
        attempted = [solved for ok, solved in runs if ok]
        rate = (sum(attempted) / len(attempted)) if attempted else None
        verdict = verdict_for(rate, band)
        results.append(
            TaskCalibration(
                task_id=task.id,
                family=task.family,
                attempts=len(attempted),
                passes=sum(attempted),
                excluded=len(runs) - len(attempted),
                pooled_pass_rate=rate,
                verdict=verdict,
                suggestion=suggestion_for(
                    verdict, has_difficulty_vector=task.difficulty is not None
                ),
            )
        )
    return CalibrationReport(
        suite_id=suite.id,
        date=resolved_clock().strftime(CALIBRATION_DATE_FORMAT),
        subjects=subject_ids,
        repeats=repeats,
        band=band,
        tasks=sorted(results, key=lambda r: r.task_id),
        run_dirs=run_dirs,
    )


def require_writable_suite(path: Path) -> None:
    """`--write` rewrites the suite file as JSON, so a YAML suite is refused (checked
    up front by the CLI, before any expensive run, and again by `write_calibration`)."""
    from .errors import BenchError

    if path.suffix in _SUITE_YAML_SUFFIXES:
        raise BenchError(f"--write supports JSON suites only (got {path.name}); edit YAML by hand")


def write_calibration(suite_path: str | Path, report: CalibrationReport) -> list[str]:
    """Store each calibrated task's `calibration` record in the suite JSON; returns the ids
    written. `no_data` tasks are skipped (never clobber an earlier real record).

    Raises `BenchError` for a non-JSON suite file; if the rewritten suite no longer passes
    `load_suite` the original text is restored before the error propagates.
    """
    from .spec import load_suite

    path = Path(suite_path)
    require_writable_suite(path)
    original = path.read_text()
    data = json.loads(original)
    by_id = {r.task_id: r for r in report.tasks if r.pooled_pass_rate is not None}
    written: list[str] = []
    for task in data.get("tasks", []):
        result = by_id.get(task.get("id"))
        if result is None or result.pooled_pass_rate is None:
            continue
        task["calibration"] = {
            "date": report.date,
            "subjects": report.subjects,
            "repeats": report.repeats,
            "pooled_pass_rate": round(result.pooled_pass_rate, POOLED_RATE_DIGITS),
            "verdict": result.verdict,
        }
        written.append(task["id"])
    path.write_text(json.dumps(data, indent=JSON_INDENT, ensure_ascii=False) + "\n")
    try:
        load_suite(path)
    except Exception:
        path.write_text(original)
        raise
    return written


def render_calibration_table(report: CalibrationReport) -> list[str]:
    """The human-readable table `ao-bench calibrate` prints (one string per line)."""
    lo, hi = report.band
    lines = [
        f"Calibration: suite={report.suite_id} subjects={', '.join(report.subjects)} "
        f"repeats={report.repeats} band=[{lo:.2f}, {hi:.2f}]",
    ]
    width = max([len("Task"), *(len(r.task_id) for r in report.tasks)])
    fam_width = max([len("Family"), *(len(r.family or "—") for r in report.tasks)])
    lines.append(
        f"{'Task':<{width}}  {'Family':<{fam_width}}  {'Pooled':>6}  {'Passes':>7}  "
        f"{'Verdict':<8}  Suggestion"
    )
    for r in report.tasks:
        pooled = "—" if r.pooled_pass_rate is None else f"{r.pooled_pass_rate:.2f}"
        extra = f" ({r.excluded} infra/skipped run(s) excluded)" if r.excluded else ""
        lines.append(
            f"{r.task_id:<{width}}  {(r.family or '—'):<{fam_width}}  {pooled:>6}  "
            f"{f'{r.passes}/{r.attempts}':>7}  {r.verdict:<8}  {r.suggestion}{extra}"
        )
    counts = {
        v: sum(1 for r in report.tasks if r.verdict == v)
        for v in (VERDICT_IN, VERDICT_ABOVE, VERDICT_BELOW, VERDICT_NO_DATA)
    }
    lines.append(
        f"Summary: {counts[VERDICT_IN]} in band, {counts[VERDICT_ABOVE]} above,"
        f" {counts[VERDICT_BELOW]} below, {counts[VERDICT_NO_DATA]} no data."
    )
    return lines


def calibrate_command(
    suite: str = typer.Option(..., "--suite", help="Suite to calibrate."),
    subject: list[str] = typer.Option(
        ..., "--subject", help="Anchor subject(s) (path to subject.json/.yaml), repeatable."
    ),
    repeats: int = typer.Option(
        DEFAULT_REPEATS,
        "--repeats",
        min=1,
        help="Repeats per (subject x task) pooled into each task's pass rate (HLD §7: 2).",
    ),
    band: str = typer.Option(
        f"{DEFAULT_BAND[0]},{DEFAULT_BAND[1]}",
        "--band",
        help="Discriminating band 'LOW,HIGH' (inclusive) for the pooled pass rate.",
    ),
    out_dir: str | None = typer.Option(
        None,
        "--out-dir",
        help="Override the results root the per-run dirs are written under"
        " (default: benchmarks/results/, committed).",
    ),
    write: bool = typer.Option(
        False,
        "--write",
        help="Store each calibrated task's `calibration` record"
        " {date, subjects, repeats, pooled_pass_rate, verdict} in the suite.json.",
    ),
    task: list[str] | None = typer.Option(
        None,
        "--task",
        help="Calibrate only this task id (repeatable). Default: every enabled task.",
    ),
    cost_budget_usd: float | None = typer.Option(
        None,
        "--cost-budget-usd",
        help="Per-run USD cap, same meaning as `ao-bench run --cost-budget-usd`"
        " (default: the suite's tier cost_budget_usd_per_subject).",
    ),
    max_parallel: int | None = typer.Option(
        None,
        "--max-parallel",
        help="Task-level parallelism per run (default: the suite's tier default).",
    ),
    parity: str | None = typer.Option(
        None, "--parity", help="Parity bundle applied to every subject (HLD §2b)."
    ),
    force: bool = typer.Option(
        False, "--force", help="Re-run tasks even if a same-day result already records them."
    ),
    enable_xlarge: bool = typer.Option(
        False,
        "--enable-xlarge",
        help="Allow a suite whose tier is disabled in benchmarks/tiers.json.",
    ),
    allow_contaminated_env: bool = typer.Option(
        False,
        "--allow-contaminated-env",
        help="Run agent subjects even if the bench workspace root has an instruction-file"
        " ancestor (T-Ws7Iso); the runs are stamped env_contaminated.",
    ),
) -> None:
    """Calibrate a suite's tasks against anchor subjects (pooled pass-rate band check).

    Runs each --subject for --repeats repeats, pools the attempted outcomes per task and
    prints whether each task sits in the --band, with a raise/lower-difficulty or retire
    suggestion for the ones outside it. Exit 0 whatever the verdicts (calibration is a
    report, not a gate); exit 1 on a usage or spec error.
    """
    from .errors import BenchError, SpecValidationError

    try:
        parsed_band = parse_band(band)
        if write:
            require_writable_suite(Path(suite))
        report = calibrate_suite(
            suite,
            subject,
            repeats=repeats,
            band=parsed_band,
            out_dir=out_dir,
            tasks=task or None,
            cost_budget_usd=cost_budget_usd,
            max_parallel=max_parallel,
            parity=parity,
            force=force,
            enable_xlarge=enable_xlarge,
            allow_contaminated_env=allow_contaminated_env,
        )
        written = write_calibration(suite, report) if write else []
    except (ValueError, SpecValidationError, BenchError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1) from exc

    for line in render_calibration_table(report):
        typer.echo(line)
    if write:
        typer.echo(f"Wrote calibration records for {len(written)} task(s) to {suite}")
    else:
        typer.echo("Dry run: pass --write to store calibration records in the suite.")
