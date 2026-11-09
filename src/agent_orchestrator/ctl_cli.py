"""`ao ctl` Typer sub-app (E-Sn7tL2 T-Sn3Ctl, HLD §12): live control of a running run.

Every mutating subcommand writes exactly one ``ao.ctl/v1`` entry into the run's
``control/inbox/`` (via :mod:`control`) and, with ``--wait``, prints the engine's verdict.
Module-level imports stay cheap (cli.py imports this at startup); the work is imported lazily.
"""

from __future__ import annotations

import json
from typing import Any

import typer

ctl_app = typer.Typer(name="ctl", no_args_is_help=True)

EXIT_REJECTED = 3
EXIT_TIMEOUT = 4
EXIT_NOT_ENABLED = 5

_WORKSPACE = typer.Option(None, "--workspace", "-w", help="Workspace root (or AO_WORKSPACE_ROOT).")
_JSON = typer.Option(False, "--json", help="Print exactly one JSON document on stdout.")
_WAIT = typer.Option(False, "--wait", help="Wait for the engine to consume/reject the entry.")
_WAIT_TIMEOUT = typer.Option(60.0, "--wait-timeout", help="Seconds to wait with --wait.")
_REASON = typer.Option(None, "--reason", help="Rationale recorded on the entry.")


@ctl_app.callback()
def _ctl_group() -> None:
    """Control a running run through its control inbox (sentinel must be enabled for the run)."""


def _fail(msg: str, as_json: bool, code: int = 1) -> typer.Exit:
    if as_json:
        typer.echo(json.dumps({"error": msg}))
    else:
        typer.echo(f"ERROR: {msg}", err=True)
    return typer.Exit(code)


def _locate(workspace: str | None, run: str, as_json: bool) -> Any:
    from . import control
    from .cli import _resolve_workspace_root

    ws = _resolve_workspace_root(workspace, None, None, None, no_repo_default=True)
    try:
        return control.resolve_run_dir(ws, run)
    except control.ControlError as exc:
        raise _fail(str(exc), as_json) from exc


def _submit(
    workspace: str | None,
    run: str,
    command: str,
    payload: dict[str, Any],
    *,
    reason: str | None,
    wait: bool,
    wait_timeout: float,
    as_json: bool,
    run_dir: Any = None,
) -> None:
    from . import control

    run_dir = run_dir or _locate(workspace, run, as_json)
    entry = control.build_entry(control.KIND_BY_COMMAND[command], payload, rationale=reason)
    try:
        control.write_entry(run_dir, entry)
    except control.ControlError as exc:
        raise _fail(str(exc), as_json) from exc
    result: dict[str, Any] = {"id": entry["id"], "kind": entry["kind"], "run_id": run_dir.name}
    code = 0
    if wait:
        outcome = control.wait_outcome(run_dir, entry["id"], timeout=wait_timeout)
        result.update(outcome)
        if outcome["outcome"] == "rejected":
            code = (
                EXIT_NOT_ENABLED if outcome["rule_id"] == control.NOT_ENABLED_YET else EXIT_REJECTED
            )
        elif outcome["outcome"] == "timeout":
            code = EXIT_TIMEOUT
    else:
        result["outcome"] = "queued"
    if as_json:
        typer.echo(json.dumps(result, sort_keys=True))
    else:
        line = f"{result['outcome']}: {entry['kind']} ({entry['id']}) -> {run_dir.name}"
        if result.get("rule_id"):
            line += f"  [{result['rule_id']}] {result.get('detail', '')}"
        typer.echo(line, err=code != 0)
    raise typer.Exit(code)


@ctl_app.command("status")
def status_command(
    run: str = typer.Argument(..., help="Run id or unique prefix."),
    workspace: str | None = _WORKSPACE,
    as_json: bool = _JSON,
) -> None:
    """Show live-control state (concurrency, utilisation, inbox, notes) of a run."""
    from . import control

    run_dir = _locate(workspace, run, as_json)
    try:
        doc = control.build_status(control.load_state(run_dir), run_dir)
    except control.ControlError as exc:
        raise _fail(str(exc), as_json) from exc
    typer.echo(json.dumps(doc, sort_keys=True, indent=2) if as_json else control.format_status(doc))


@ctl_app.command("set")
def set_command(
    run: str = typer.Argument(..., help="Run id or unique prefix."),
    items: list[str] = typer.Argument(..., help="key=value pairs (e.g. max_parallel=4)."),
    workspace: str | None = _WORKSPACE,
    reason: str | None = _REASON,
    wait: bool = _WAIT,
    wait_timeout: float = _WAIT_TIMEOUT,
    as_json: bool = _JSON,
) -> None:
    """Live-override settings (validated client-side against the run-options registry)."""
    from . import control

    run_dir = _locate(workspace, run, as_json)
    try:
        values = control.parse_set_items(
            items, ceiling=control.run_ceiling(control.load_state(run_dir))
        )
    except control.ControlError as exc:
        raise _fail(str(exc), as_json, 2) from exc
    _submit(
        workspace,
        run,
        "set",
        {"values": values},
        reason=reason,
        wait=wait,
        wait_timeout=wait_timeout,
        as_json=as_json,
        run_dir=run_dir,
    )


def _simple(command: str, doc: str) -> None:
    def cmd(
        run: str = typer.Argument(..., help="Run id or unique prefix."),
        workspace: str | None = _WORKSPACE,
        reason: str | None = _REASON,
        wait: bool = _WAIT,
        wait_timeout: float = _WAIT_TIMEOUT,
        as_json: bool = _JSON,
    ) -> None:
        _submit(
            workspace,
            run,
            command,
            {},
            reason=reason,
            wait=wait,
            wait_timeout=wait_timeout,
            as_json=as_json,
        )

    cmd.__doc__ = doc
    ctl_app.command(command)(cmd)


_simple("pause-llm", "Pause the sentinel's LLM lane (mechanical lane keeps running).")
_simple("resume-llm", "Resume the sentinel's LLM lane.")
_simple("cancel", "Request a graceful cancel of the run (drains in-flight tasks).")


@ctl_app.command("note")
def note_command(
    run: str = typer.Argument(..., help="Run id or unique prefix."),
    text: str = typer.Argument(..., help="Note text (1..4000 chars)."),
    workspace: str | None = _WORKSPACE,
    wait: bool = _WAIT,
    wait_timeout: float = _WAIT_TIMEOUT,
    as_json: bool = _JSON,
) -> None:
    """Record an operator note in the run's sentinel state."""
    from . import sentinel_tick

    if not text.strip() or len(text) > sentinel_tick.NOTE_TEXT_MAX:
        raise _fail(f"note text must be 1..{sentinel_tick.NOTE_TEXT_MAX} chars", as_json, 2)
    _submit(
        workspace,
        run,
        "note",
        {"text": text},
        reason=None,
        wait=wait,
        wait_timeout=wait_timeout,
        as_json=as_json,
    )


@ctl_app.command("kill")
def kill_command(
    run: str = typer.Argument(..., help="Run id or unique prefix."),
    task: str = typer.Argument(..., help="Task id to kill."),
    reason: str = typer.Option(..., "--reason", help="Why (required)."),
    no_retry: bool = typer.Option(False, "--no-retry", help="Fail the task instead of retrying."),
    workspace: str | None = _WORKSPACE,
    wait: bool = _WAIT,
    wait_timeout: float = _WAIT_TIMEOUT,
    as_json: bool = _JSON,
) -> None:
    """Kill one running task (sentinel recovery lane; the ladder retries or escalates)."""
    _submit(
        workspace,
        run,
        "kill",
        {"task_id": task, "no_retry": no_retry},
        reason=reason,
        wait=wait,
        wait_timeout=wait_timeout,
        as_json=as_json,
    )


@ctl_app.command("release")
def release_command(
    run: str = typer.Argument(..., help="Run id or unique prefix."),
    ids: list[str] = typer.Argument(None, help="Backlog ids (default: all)."),
    workspace: str | None = _WORKSPACE,
    reason: str | None = _REASON,
    wait: bool = _WAIT,
    wait_timeout: float = _WAIT_TIMEOUT,
    as_json: bool = _JSON,
) -> None:
    """Release held backlog items now (sentinel backlog; all items, or the given bk-ids)."""
    _submit(
        workspace,
        run,
        "release",
        {"ids": list(ids or [])},
        reason=reason,
        wait=wait,
        wait_timeout=wait_timeout,
        as_json=as_json,
    )
