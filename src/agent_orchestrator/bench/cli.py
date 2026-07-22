"""`ao-bench` CLI -- `validate` (T-Sc4Hm2) plus `run`/`report`/`list` (T-Cli8Nf).

Registered as the standalone `ao-bench` console script (`pyproject.toml`
`[project.scripts]`, ADR-0008 D4) -- a SEPARATE entry point from `ao`, so the core `ao`
command surface/tests stay byte-untouched and `bench/` never loads on a normal `ao run`
(SI-1). Every command lazy-imports its heavy dependencies (`spec`/`runner`/`results`)
inside the function body, mirroring `validate`'s own existing pattern and core
`cli.py`'s own convention -- `import agent_orchestrator.bench.cli` alone stays cheap.
"""

from __future__ import annotations

import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from ..errors import SpecValidationError
from .errors import BenchError
from .spec import load_subject, load_suite

if TYPE_CHECKING:
    from .results import ComparisonRecord
    from .runner import BenchRunRecord

app = typer.Typer(name="ao-bench", help="Benchmark harness CLI for agent-orchestrator subjects.")

# Exit codes (design doc §7 / this task's own AC): 0 ok, 1 usage/spec error, 2 a run
# completed but at least one task's SUBJECT (not grader-verdict) status indicates a
# harness-level failure -- named, not magic literals, and documented here once for
# every command that uses them.
EXIT_OK = 0
EXIT_USAGE_ERROR = 1
EXIT_RUN_HAD_FAILURES = 2

# `SubjectResult.status` values that mean "the subject itself did not complete
# normally" (bench/subjects.py's own Literal) -- distinct from `solved=False`, which is
# a legitimate benchmark data point (the subject ran fine, it just didn't solve the
# task) and must never flip the CLI's exit code. AC2: "a suite where the fake subject
# fails a task -> exit non-zero" means exactly this (e.g. `scripted_effect: "fail"`).
_HARNESS_FAILURE_STATUSES = frozenset({"failed", "timed_out", "error"})


@app.callback()
def main() -> None:
    """Benchmark harness CLI for agent-orchestrator subjects.

    An explicit callback (even a no-op one) is required so Typer builds a proper
    multi-command group from the start -- without it, Typer collapses a Typer app
    that has exactly one registered command so it can be invoked without naming that
    command (e.g. `ao-bench --suite s.json` instead of `ao-bench validate --suite
    s.json`). `validate` is the only command today, but T-Cli8Nf adds `run`/`report`/
    `list` alongside it, so the `ao-bench <command> ...` invocation form (design doc
    §7) must be stable from this first command onward.
    """


# Bounded probe timeout (ASSUMPTION A1) -- named, not a magic literal. `claude --version`
# is a near-instant local check; a few seconds is generous headroom without letting a
# hung/misbehaving binary stall `ao-bench validate`.
_CLAUDE_PROBE_TIMEOUT_SECONDS = 5


def _probe_claude_cli() -> None:
    """Best-effort probe of `claude --version` for `claude_cli` subjects (ASSUMPTION A1).

    Never raises and never fails validation on its own: a missing/broken `claude`
    binary should not block spec-level `ao-bench validate` in environments (e.g. CI)
    where only FakeSubject is ever exercised (design doc §13). Prints a WARNING to
    stderr instead so a real bare-claude run surfaces the problem early without making
    the environment probe part of validate's pass/fail contract.
    """
    try:
        result = subprocess.run(  # noqa: S603 -- fixed argv, no shell, bounded timeout
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=_CLAUDE_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        typer.echo(f"WARNING: could not probe `claude --version`: {exc}", err=True)
        return
    if result.returncode != 0:
        typer.echo(f"WARNING: `claude --version` exited {result.returncode}", err=True)
    else:
        typer.echo(f"claude CLI: {result.stdout.strip()}")


@app.command()
def validate(
    suite: str | None = typer.Option(
        None, "--suite", help="Path to a suite.json/.yaml to validate."
    ),
    subject: str | None = typer.Option(
        None, "--subject", help="Path to a subject.json/.yaml to validate."
    ),
) -> None:
    """Validate a benchmark suite and/or subject spec."""
    if suite is None and subject is None:
        typer.echo("ERROR: pass --suite and/or --subject", err=True)
        raise typer.Exit(1)

    try:
        if suite is not None:
            loaded_suite = load_suite(suite)
            typer.echo(f"suite {loaded_suite.id!r}: {len(loaded_suite.tasks)} task(s)")
        if subject is not None:
            loaded_subject = load_subject(subject)
            typer.echo(f"subject {loaded_subject.id!r}: type={loaded_subject.type!r}")
            if loaded_subject.type == "claude_cli":
                _probe_claude_cli()
    except (SpecValidationError, BenchError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo("OK")


def _print_run_summary(record: BenchRunRecord, result_dir: Path, summary_path: Path) -> None:
    agg = record.aggregate
    cost_per_solved = "—" if agg.cost_per_solved is None else f"${agg.cost_per_solved:.4f}"
    typer.echo(f"Result dir: {result_dir}")
    typer.echo(f"Summary:    {summary_path}")
    typer.echo(
        f"solved {agg.solved}/{agg.total} ({agg.solve_rate * 100:.1f}%) "
        f"total_cost=${agg.total_cost_usd:.4f} cost_per_solved={cost_per_solved} "
        f"total_wall={agg.total_wall_clock_seconds:.2f}s"
    )


@app.command()
def run(
    suite: str = typer.Option(..., "--suite", help="Path to a suite.json/.yaml to run."),
    subject: str = typer.Option(..., "--subject", help="Path to a subject.json/.yaml to run."),
    out_dir: str | None = typer.Option(
        None,
        "--out-dir",
        help="Override the results root (default: benchmarks/results/, committed).",
    ),
    force: bool = typer.Option(
        False, "--force", help="Re-run every considered task even if already recorded."
    ),
    task: list[str] | None = typer.Option(
        None,
        "--task",
        help="Restrict this run to one task id (repeatable). Default: every task in the suite.",
    ),
    budget_total: int | None = typer.Option(
        None, "--budget-total", help="Run-level token budget override, threaded to each task."
    ),
    max_turns: int | None = typer.Option(
        None, "--max-turns", help="Run-level max-turns override, threaded to each task."
    ),
    timeout: int | None = typer.Option(
        None,
        "--timeout",
        help="Run-level default per-task timeout in seconds (a task's own timeout_seconds,"
        " then the suite's defaults.timeout_seconds, still win over this).",
    ),
) -> None:
    """Run a benchmark suite against a subject; writes run.json + summary.md.

    Exit 0 on a clean run; exit 2 if any task's SUBJECT status (not its grader verdict --
    an unsolved-but-cleanly-run task is a normal benchmark outcome, not a failure) was
    failed/timed_out/error; exit 1 on a usage or spec-loading error.
    """
    from .results import write_summary_md
    from .runner import resolve_result_dir, run_suite

    try:
        record = run_suite(
            suite,
            subject,
            out_dir=out_dir,
            force=force,
            task_filter=task or None,
            budget_total=budget_total,
            max_turns=max_turns,
            default_timeout=timeout,
        )
    except (SpecValidationError, BenchError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(EXIT_USAGE_ERROR) from exc

    if record.ended_at is None:
        # runner.run_suite's own documented in-memory-only case: --task matched no
        # suite task, so nothing ran and nothing was persisted.
        typer.echo("No tasks ran (--task matched nothing in the suite); nothing written.")
        raise typer.Exit(EXIT_OK)

    result_dir = resolve_result_dir(record.bench_run_id, out_dir=out_dir)
    summary_path = write_summary_md(record, result_dir)
    _print_run_summary(record, result_dir, summary_path)

    any_harness_failure = any(t.subject_status in _HARNESS_FAILURE_STATUSES for t in record.tasks)
    raise typer.Exit(EXIT_RUN_HAD_FAILURES if any_harness_failure else EXIT_OK)


# `<date>-<suite_id>-<subject_id>` (runner.compute_bench_run_id) -- suite_id is a known,
# literal string here (the caller's own `--suite`), so this only needs to recover the
# subject id, exactly mirroring `results._derive_subject_id`'s own approach.
def _discover_latest_run_dirs(results_root: Path, suite_id: str) -> list[Path]:
    """Auto-discover the LATEST result dir per subject for *suite_id* under
    *results_root* (T-Cli8Nf design note: "auto-discover latest per subject for a
    suite -- keep simple"). Directory names sort lexicographically the same as their
    embedded ISO date, so a single forward sorted pass -- keeping the last match per
    subject id -- is exactly "latest wins", no date parsing required. The compare
    directory itself (`<date>-<suite_id>-compare`) is excluded -- it is a report
    OUTPUT, never a subject to compare.
    """
    from .results import COMPARE_DIR_SUFFIX

    if not results_root.is_dir():
        return []
    pattern = re.compile(rf"^\d{{4}}-\d{{2}}-\d{{2}}-{re.escape(suite_id)}-(.+)$")
    latest_by_subject: dict[str, Path] = {}
    for entry in sorted(results_root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        match = pattern.match(entry.name)
        if not match:
            continue
        subject_id = match.group(1)
        if subject_id == COMPARE_DIR_SUFFIX:
            continue
        latest_by_subject[subject_id] = entry  # later (sorted) match overwrites -> latest date
    return sorted(latest_by_subject.values(), key=lambda p: p.name)


@app.command()
def report(
    run_dir: list[str] | None = typer.Option(
        None,
        "--run-dir",
        help="Result dir(s) to compare (repeatable; give 2+ for a real comparison,"
        " 1 degrades gracefully to a single-subject report).",
    ),
    results_root: str | None = typer.Option(
        None,
        "--results-root",
        help="Auto-discover the latest run dir per subject for --suite under this root"
        " (used instead of one or more --run-dir).",
    ),
    suite: str | None = typer.Option(
        None, "--suite", help="Suite id to auto-discover under --results-root."
    ),
    out_dir: str | None = typer.Option(
        None,
        "--out-dir",
        help="Override the results root the comparison dir is written under"
        " (default: --results-root, or benchmarks/results/ if neither is given).",
    ),
    allow_mixed: bool = typer.Option(
        False,
        "--allow-mixed",
        help="Allow comparing two runs for the same subject id with different"
        " config_fingerprint (default: refused).",
    ),
) -> None:
    """Compare result dirs for the same suite; writes comparison.json + comparison.md."""
    from .results import build_comparison, compute_compare_id, render_winners, write_comparison
    from .runner import resolve_result_dir

    if run_dir:
        dirs = [Path(d) for d in run_dir]
    elif results_root is not None and suite is not None:
        dirs = _discover_latest_run_dirs(Path(results_root), suite)
        if not dirs:
            typer.echo(
                f"ERROR: no result dirs found for suite {suite!r} under {results_root}", err=True
            )
            raise typer.Exit(EXIT_USAGE_ERROR)
    else:
        typer.echo(
            "ERROR: pass one or more --run-dir, or both --results-root and --suite", err=True
        )
        raise typer.Exit(EXIT_USAGE_ERROR)

    try:
        # `build_comparison` only ever raises `ResultsError` (a `BenchError` subclass,
        # bench/errors.py) -- caught via its base for the same reason `validate` above
        # catches `BenchError` broadly.
        comparison: ComparisonRecord = build_comparison(dirs, allow_mixed=allow_mixed)
    except BenchError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(EXIT_USAGE_ERROR) from exc

    def _wall_clock() -> datetime:
        return datetime.now(UTC)

    compare_id = compute_compare_id(comparison.suite_id, _wall_clock)
    compare_root = out_dir if out_dir is not None else results_root
    compare_dir = resolve_result_dir(compare_id, out_dir=compare_root)
    json_path, md_path = write_comparison(comparison, compare_dir)

    typer.echo(f"Comparison: {json_path}")
    typer.echo(f"            {md_path}")
    for line in render_winners(comparison):
        typer.echo(line)


@app.command(name="list")
def list_cmd(
    suite: str = typer.Option(..., "--suite", help="Path to a suite.json/.yaml to list."),
) -> None:
    """List a suite's tasks (id, category, grader type, timeout, tags)."""
    try:
        loaded_suite = load_suite(suite)
    except SpecValidationError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(EXIT_USAGE_ERROR) from exc

    typer.echo(f"{'Task':<30} {'Category':<10} {'Grader':<15} {'Timeout(s)':<11} Tags")
    for t in sorted(loaded_suite.tasks, key=lambda t: t.id):
        timeout_display = t.timeout_seconds if t.timeout_seconds is not None else "—"
        typer.echo(
            f"{t.id:<30} {t.category:<10} {t.grader.type:<15} {timeout_display!s:<11} "
            f"{','.join(t.tags)}"
        )
