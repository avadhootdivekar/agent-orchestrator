"""`ao-bench calibrate` (HLD §7) -- STUB, owner: stats track.

Runs anchor subjects at r=2 over a suite/instance set, computes pooled pass rates, writes
each task's `calibration` record and reports which tasks fall outside the 20-80% band.
"""

from __future__ import annotations

import typer


def calibrate_command(
    suite: str = typer.Option(..., "--suite", help="Suite to calibrate."),
    subject: list[str] = typer.Option(..., "--subject", help="Anchor subject(s), repeatable."),
) -> None:
    """Calibrate a suite's tasks against anchor subjects (pooled pass-rate band check)."""
    typer.echo("ERROR: ao-bench calibrate is not implemented yet (E-Bn7Dsc)", err=True)
    raise typer.Exit(1)
