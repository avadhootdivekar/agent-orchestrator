"""Minimal `ao-bench` CLI -- `validate` only (T-Sc4Hm2).

`run|report|list` land in T-Cli8Nf, which extends this same Typer app. No
`[project.scripts]` entry is added yet (T-Cli8Nf's job) -- tests invoke this app
directly via `typer.testing.CliRunner`.
"""

from __future__ import annotations

import subprocess

import typer

from ..errors import SpecValidationError
from .errors import BenchError
from .spec import load_subject, load_suite

app = typer.Typer(name="ao-bench", help="Benchmark harness CLI for agent-orchestrator subjects.")


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
