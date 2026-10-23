"""Artefact sandbox (HLD §4.1 "Sandbox", bench-probe-dsl.md §1) -- STUB, owner: blackbox track.

Runs an agent-produced artefact for grading inside a per-language Docker image (deps
pre-installed, cpu/mem/pid limits, no network except the published port) with a tagged
`process` fallback. `images_command` is the `ao-bench images` verb.
"""

from __future__ import annotations

import typer

from .errors import GraderError


def images_command(
    build: bool = typer.Option(
        False, "--build", help="Build/pull the per-language sandbox images."
    ),
) -> None:
    """Manage the sandbox images used to grade generated-task artefacts."""
    raise typer.Exit(_not_implemented("ao-bench images"))


def _not_implemented(what: str) -> int:
    typer.echo(f"ERROR: {what} is not implemented yet (E-Bn7Dsc)", err=True)
    return 1


class SandboxUnavailable(GraderError):
    """Raised when neither docker nor the process fallback can run the artefact."""
