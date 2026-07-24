"""`ao-bench` CLI -- `validate` (T-Sc4Hm2), `run`/`report`/`list` (T-Cli8Nf), plus
`campaign` (T-Cm9Tb4, whole-run USD cap across a list of subjects -- see
`bench/campaign.py`, which owns the actual orchestration; this module stays a thin CLI
wrapper around it, same convention as `run`'s own thin wrapper around `runner.run_suite`).

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
from . import (
    swebench_grader,  # noqa: F401 -- registers "swebench" grader (T-Sg6Jf2)
    swebench_provider,  # noqa: F401 -- registers "swebench" workspace provider (T-Sw5Hd9)
)
from .errors import BenchError
from .spec import load_subject, load_suite
from .swebench_import import import_swebench_command

if TYPE_CHECKING:
    from .campaign import CampaignResult
    from .results import ComparisonRecord
    from .runner import BenchRunRecord

app = typer.Typer(name="ao-bench", help="Benchmark harness CLI for agent-orchestrator subjects.")
app.command(name="import-swebench")(import_swebench_command)  # T-Sw5Hd9

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
# `"skipped_budget"` (ADR-0009 D3, T-Bg2Wq4) is DELIBERATELY excluded here: a run that
# stopped scheduling because it hit its USD cost cap is a clean, resumable, expected
# outcome (raise the cap and re-run to finish it), never a harness failure.
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
    cost_budget_usd: float | None = typer.Option(
        None,
        "--cost-budget-usd",
        help="Hard USD cost cap for this run (ADR-0009 D3), distinct from --budget-total"
        " (a token budget threaded to `ao run`, not enforced by the harness itself)."
        " Default: the suite's tier default from benchmarks/tiers.json"
        " (cost_budget_usd_per_subject -- small=$5, medium=$50, large=$100). Tasks past"
        " the cap are recorded subject_status=skipped_budget rather than run; re-running"
        " with a higher cap re-attempts exactly those tasks.",
    ),
    max_parallel: int | None = typer.Option(
        None,
        "--max-parallel",
        help="Bounded task-level parallelism (ADR-0009 D4): run up to this many tasks"
        " concurrently via a thread pool. Default: the suite's tier default from"
        " benchmarks/tiers.json (default_max_parallel -- small=1/serial, medium=4,"
        " large=3). 1 (or any value <=1) is strictly serial, byte-identical to the"
        " original single-task-at-a-time behavior.",
    ),
    enable_xlarge: bool = typer.Option(
        False,
        "--enable-xlarge",
        help="Allow running a suite whose resolved tier is disabled in"
        " benchmarks/tiers.json (enabled=false, e.g. xlarge). Gates ANY disabled tier,"
        " not only xlarge -- the flag name is historical/literal per the epic.",
    ),
) -> None:
    """Run a benchmark suite against a subject; writes run.json + summary.md.

    Exit 0 on a clean run (a run that stopped early on its USD budget is still exit 0 --
    `skipped_budget` is a clean, resumable outcome, not a failure); exit 2 if any task's
    SUBJECT status (not its grader verdict -- an unsolved-but-cleanly-run task is a
    normal benchmark outcome, not a failure) was failed/timed_out/error; exit 1 on a
    usage or spec-loading error (including a disabled tier without --enable-xlarge).
    """
    from .campaign import enforce_tier_enabled
    from .results import write_summary_md
    from .runner import resolve_result_dir, run_suite
    from .tiers import load_tier_config, resolve_effective

    try:
        loaded_suite = load_suite(suite)
        # Resolved once (T-Pl3Rx7's own forward note: "reuse that same load, do not
        # load twice") and reused for every tier-derived default below.
        tier_config = load_tier_config(loaded_suite.tier)
        enforce_tier_enabled(loaded_suite.tier, tier_config, enable_xlarge)
        # Precedence (ADR-0009 D3): an explicit --cost-budget-usd always wins; absent
        # that, the suite's OWN tier (bench/spec.py's `BenchSuite.tier`, default
        # "small") supplies its per-subject default cap via bench/tiers.json.
        # `is not None` (not `or`) so an explicit `--cost-budget-usd 0` -- a real,
        # maximally-restrictive cap -- is never mistaken for "not given".
        effective_cost_budget_usd = (
            cost_budget_usd
            if cost_budget_usd is not None
            else tier_config.cost_budget_usd_per_subject
        )
        if cost_budget_usd is None:
            # Reviewer W2: a tier-derived cap applies even with no flag (a behavior
            # change vs the pre-tier, uncapped CLI) -- say so up front, never silently.
            typer.echo(
                f"Cost cap: ${effective_cost_budget_usd:.2f}/subject"
                f" (tier '{loaded_suite.tier}' default; override with --cost-budget-usd)"
            )
        # Same CLI > tier-default > builtin-fallback precedence, via the single
        # `resolve_effective` helper bench/tiers.py already provides (T-Pl3Rx7's own
        # forward note: reuse it, don't re-derive).
        effective_max_parallel = int(
            resolve_effective(tier_config, cli_max_parallel=max_parallel)["max_parallel"]
        )
        record = run_suite(
            suite,
            subject,
            out_dir=out_dir,
            force=force,
            task_filter=task or None,
            budget_total=budget_total,
            max_turns=max_turns,
            default_timeout=timeout,
            cost_budget_usd=effective_cost_budget_usd,
            max_parallel=effective_max_parallel,
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


def _print_campaign_summary(result: CampaignResult) -> None:
    record = result.record
    typer.echo(f"Campaign dir: {result.campaign_dir}")
    typer.echo(
        f"Suite: {record.suite_id} (tier={record.tier}) "
        f"whole_run_cap=${record.whole_run_cap_usd:.4f} "
        f"per_subject_cap=${record.per_subject_cap_usd:.4f} "
        f"max_parallel={record.max_parallel}"
    )
    for sr in record.subjects:
        if sr.launched:
            solve_rate = "—" if sr.solve_rate is None else f"{sr.solve_rate * 100:.1f}%"
            cost = "—" if sr.total_cost_usd is None else f"${sr.total_cost_usd:.4f}"
            typer.echo(
                f"  {sr.subject_id}: {sr.status} solved={sr.solved}/{sr.total} "
                f"({solve_rate}) cost={cost} run_dir={sr.run_dir}"
            )
        else:
            typer.echo(f"  {sr.subject_id}: {sr.status} (not launched)")
    typer.echo(f"Total spent: ${record.total_spent_usd:.4f} / ${record.whole_run_cap_usd:.4f}")
    if record.comparison_json is not None:
        typer.echo(f"Comparison: {record.comparison_json}")
        typer.echo(f"            {record.comparison_md}")


@app.command()
def campaign(
    suite: str = typer.Option(..., "--suite", help="Path to a suite.json/.yaml to run."),
    subject: list[str] = typer.Option(
        ...,
        "--subject",
        help="Path to a subject.json/.yaml to include (repeatable; run IN THE GIVEN"
        " ORDER against --suite). At least one required.",
    ),
    max_parallel: int | None = typer.Option(
        None,
        "--max-parallel",
        help="Per-subject task-level parallelism, same meaning/precedence as `ao-bench"
        " run --max-parallel` (default: the suite's tier default_max_parallel).",
    ),
    cost_budget_usd: float | None = typer.Option(
        None,
        "--cost-budget-usd",
        help="Per-subject USD cost cap override, same meaning as `ao-bench run"
        " --cost-budget-usd` (default: the suite's tier cost_budget_usd_per_subject)."
        " The cap actually applied to a given subject is this value further bounded by"
        " the remaining whole-run headroom (ADR-0009 D3) -- see --run-budget-usd.",
    ),
    run_budget_usd: float | None = typer.Option(
        None,
        "--run-budget-usd",
        help="Whole-CAMPAIGN USD cost cap across every --subject (ADR-0009 D3)."
        " Default: the suite's tier cost_budget_usd_per_run. Once cumulative actual"
        " spend from completed subjects reaches this cap, every remaining subject is"
        " recorded status=skipped_budget without being launched.",
    ),
    out_dir: str | None = typer.Option(
        None,
        "--out-dir",
        help="Override the results root (default: benchmarks/results/, committed).",
    ),
    force: bool = typer.Option(
        False, "--force", help="Re-run every considered task even if already recorded."
    ),
    enable_xlarge: bool = typer.Option(
        False,
        "--enable-xlarge",
        help="Allow running a suite whose resolved tier is disabled in"
        " benchmarks/tiers.json (enabled=false, e.g. xlarge). Gates ANY disabled tier,"
        " not only xlarge -- the flag name is historical/literal per the epic.",
    ),
) -> None:
    """Run a suite against a LIST of subjects, enforcing a whole-run USD cap across all
    of them (ADR-0009 D3); writes each subject's own run.json/summary.md plus a
    campaign.json + comparison.{json,md} for the whole campaign.

    Exit 0 whether every subject completed or some were budget-skipped -- a
    budget-skipped subject is a clean, resumable outcome, never a harness failure
    (mirrors `ao-bench run`'s own `skipped_budget` convention, one level up); exit 1 on
    a usage or spec-loading error (including a disabled tier without --enable-xlarge).
    """
    from .campaign import run_campaign

    try:
        result = run_campaign(
            suite,
            subject,
            max_parallel=max_parallel,
            cost_budget_usd=cost_budget_usd,
            run_budget_usd=run_budget_usd,
            out_dir=out_dir,
            force=force,
            enable_xlarge=enable_xlarge,
        )
    except (SpecValidationError, BenchError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(EXIT_USAGE_ERROR) from exc

    _print_campaign_summary(result)
    raise typer.Exit(EXIT_OK)


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
