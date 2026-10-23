"""`ao-bench gen` (HLD §4.1/§7) -- STUB, owner: generators track."""

from __future__ import annotations

import typer


def gen_command(
    out: str = typer.Option(..., "--out", help="Suite dir to (re)generate into."),
    family: list[str] = typer.Option([], "--family", help="Family to include (repeatable)."),
    seed: int = typer.Option(
        0, "--seed", help="Base seed; instances derive from it deterministically."
    ),
) -> None:
    """Materialise a generated suite (SPEC skeletons + hidden probes/reference per task)."""
    typer.echo("ERROR: ao-bench gen is not implemented yet (E-Bn7Dsc)", err=True)
    raise typer.Exit(1)
