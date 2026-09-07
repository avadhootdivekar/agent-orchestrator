"""Typer-based CLI for the agent orchestrator.

Commands:
  ao validate   — Validate workflow, reposets, and agents specs.
  ao run        — Run a workflow from scratch.
  ao resume     — Resume a previously interrupted run.
  ao status     — Show current status of a run.
  ao init       — Scaffold a per-project .ao/config.yaml.
  ao prune      — Remove stale run artifacts from a workspace.
  ao ui         — Serve the browser dashboard (needs the optional `ui` extra).
  ao templates  — List discovered workflow templates (E-Tpl3x9).
  ao new        — Scaffold (and optionally validate/run) a workflow instance from a template.
  ao hotspots   — Compute the git-churn hotspot signal for soft overlap-aware scheduling
                  (E-Wk9Tz3 FR-11) and write it to .ao/hotspots.json.

Options:
  ao --version / -V         — Show version (+ commit/build info for non-release builds).
  ao --verbose / -v         — Debug-level logging.
  ao --quiet / -q           — Warnings/errors only (mutually exclusive with --verbose).
  ao --install-completion   — Install shell completion for the current shell.
  ao --show-completion      — Print the completion script for the current shell.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

import typer

# `models.py` is already imported transitively by `.service.cli` below (confirmed: this is
# NOT a new eager-import cost) -- unlike `.engine`/`.artifacts`/etc, which stay lazy
# per-function per this module's own convention, these are two plain string constants
# needed at MODULE load time (`typer.Option(help=f"...{ISOLATION_MODE_CHOICES}...")`
# default values are evaluated when `def run(...)`/`def resume(...)` are parsed, i.e. at
# import time) -- so they cannot be lazily imported inside a function body. Reusing them
# (C-3, 2026-09-07 review) avoids a third independent spelling of "none"/"worktree"
# alongside `models.py`'s own `IsolationMode`/`WorkflowIsolation` and
# `project_config.IsolationConfig.mode`.
from .models import ISOLATION_NONE, ISOLATION_WORKTREE
from .service.cli import app as service_app

if TYPE_CHECKING:
    from .artifacts import ArtifactStore
    from .executors.base import Executor
    from .isolation.worktrees import IsolatedRepo
    from .models import BudgetSpec
    from .monitoring import Monitor
    from .project_config import MonitoringConfig, ProjectConfig

app = typer.Typer(name="ao", help="Agent Orchestrator CLI", add_completion=True)
app.add_typer(service_app, name="service")

_PACKAGE_LOGGER = "agent_orchestrator"


def _version_callback(value: bool) -> None:
    if value:
        from ._version import get_version_string

        typer.echo(get_version_string())
        raise typer.Exit(0)


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show the ao version and exit.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable debug-level logging (default is info-level).",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Suppress info-level logging; show only warnings/errors.",
    ),
) -> None:
    """Agent Orchestrator CLI."""
    if verbose and quiet:
        typer.echo("ERROR: --verbose and --quiet are mutually exclusive", err=True)
        raise typer.Exit(1)
    # Set the level explicitly only when asked; otherwise leave it untouched so
    # logging_setup.attach_run_handler's own NOTSET-guarded INFO default applies.
    if verbose:
        logging.getLogger(_PACKAGE_LOGGER).setLevel(logging.DEBUG)
    elif quiet:
        logging.getLogger(_PACKAGE_LOGGER).setLevel(logging.WARNING)


def _resolve_config_defaults(
    workflow: str | None,
    reposets: str | None,
    agents: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Merge CLI args, env vars, and project-config file with correct precedence.

    Precedence (highest to lowest):
      CLI flag > environment variable > project config file

    Returns:
        Tuple of (workflow, reposets, agents) with the highest-priority value
        for each.  Values may still be ``None`` if nothing supplies them.
    """
    from .project_config import (
        apply_project_config_env,
        find_project_config,
        load_project_config,
    )

    # 1. Try to find and load a project config.
    cfg = None
    config_path = find_project_config()
    if config_path is not None:
        try:
            cfg = load_project_config(config_path)
            apply_project_config_env(cfg)
        except Exception as exc:  # ConfigError or unexpected parse failure
            typer.echo(f"WARNING: could not load project config {config_path}: {exc}", err=True)
            cfg = None

    # 2. Merge: CLI > env > project config.
    resolved_workflow = workflow or os.environ.get("AO_WORKFLOW") or (cfg.workflow if cfg else None)
    resolved_reposets = reposets or os.environ.get("AO_REPOSETS") or (cfg.reposets if cfg else None)
    resolved_agents = agents or os.environ.get("AO_AGENTS") or (cfg.agents if cfg else None)

    return resolved_workflow, resolved_reposets, resolved_agents


def _load_all(
    workflow: str | None,
    reposets: str | None,
    agents: str | None,
) -> tuple:
    """Resolve defaults, then load and cross-validate workflow, reposets, and agents."""
    from .config import load_agents, load_reposets
    from .dag import build_dag
    from .spec import cross_validate, load_workflow, validate_run_control

    workflow_path, rp, ap = _resolve_config_defaults(workflow, reposets, agents)

    if not workflow_path:
        typer.echo(
            "ERROR: --workflow is required"
            " (or set AO_WORKFLOW, or add 'workflow' to .ao/config.yaml)",
            err=True,
        )
        raise typer.Exit(1)
    if not rp:
        typer.echo(
            "ERROR: --reposets or AO_REPOSETS required (or add 'reposets' to .ao/config.yaml)",
            err=True,
        )
        raise typer.Exit(1)
    if not ap:
        typer.echo(
            "ERROR: --agents or AO_AGENTS required (or add 'agents' to .ao/config.yaml)",
            err=True,
        )
        raise typer.Exit(1)

    wf = load_workflow(workflow_path)
    reposet_map = load_reposets(rp)
    agent_map = load_agents(ap)
    # cross_validate's return is the isolation/integration/scheduling rules' non-fatal
    # warnings (V4/V5/V7/V10, E-Wk9Tz3 C-3) -- printed the same way validate_run_control's
    # own warnings are printed just below, so both share one visible convention.
    for warning in cross_validate(wf, reposet_map, agent_map):
        typer.echo(f"WARNING: {warning}", err=True)

    # Routing + circuit-breaker static validation (LLD §4.4, epic E-rc7k2v).
    # Must run AFTER build_dag: needs the runtime graph (declared + inferred
    # edges) for reachability/cone computation. topological_order() surfaces
    # CycleError here too (previously only caught inside `ao run`/`resume`).
    graph = build_dag(wf)
    graph.topological_order()
    for warning in validate_run_control(wf, graph):
        typer.echo(f"WARNING: {warning}", err=True)

    return wf, reposet_map, agent_map


# TaskIntegrationState.status values grouped under the "conflict" count in the one-line
# integration summary below -- mirrors runstate.py's own (private) grouping exactly
# (_CONFLICT_INTEGRATION_STATUSES) so the two derivations can never silently drift.
# Duplicated rather than imported: this ticket's boundary is read-only on runstate.py.
_CLI_CONFLICT_INTEGRATION_STATUSES = ("conflict_resolver", "conflict_rerun")


def _echo_integration_summary(
    *,
    active: bool,
    branch: str | None,
    heads: dict,
    tier_counts: dict,
    integrated: int,
    conflict: int,
    failed: int,
) -> None:
    """One-line-ish integration summary shared by `_print_state`/`_print_status_snapshot`
    (E-Wk9Tz3 AC-8: per-task integration status/tier counts, integration ref/head).

    No-ops when isolation never activated for this run (`active` is False -- the default
    for every pre-epic/non-isolated run), so output stays byte-identical otherwise.
    """
    if not active:
        return
    heads_str = ", ".join(f"{repo}={sha[:12]}" for repo, sha in sorted(heads.items())) or "-"
    tiers_str = ", ".join(f"{k}={v}" for k, v in sorted(tier_counts.items()) if v) or "-"
    typer.echo(f"\nIntegration:  branch={branch or '-'}  head(s): {heads_str}")
    typer.echo(
        f"              integrated={integrated} conflict={conflict} failed={failed}  "
        f"tiers: {tiers_str}"
    )


def _print_state(state) -> None:
    from .models import compute_run_usage_totals

    typer.echo(f"\nRun:    {state.run_id}")
    typer.echo(f"Status: {state.status}")
    typer.echo(
        f"\n{'Task':<30} {'Status':<15} {'Route':<20} {'Attempts':<9} "
        f"{'Tokens (in/out)':<20} {'Cost($)'}"
    )
    typer.echo("-" * 110)
    for tid, ts in state.tasks.items():
        route = ts.route or ""
        tokens = f"{ts.cumulative_input_tokens}/{ts.cumulative_output_tokens}"
        typer.echo(
            f"{tid:<30} {ts.status:<15} {route:<20} {ts.attempts:<9} "
            f"{tokens:<20} {ts.cumulative_cost_usd:.4f}"
        )
    # Routing + circuit-breaker observability trailer (LLD §10.2, FR-CB4).
    for tb in getattr(state, "tripped_breakers", []):
        typer.echo(f"Tripped breakers: {tb.id} ({tb.condition}, action={tb.action})")
    # Run-wide actual usage totals (E-9h3m7k FR-3) — real values, not estimates.
    totals = compute_run_usage_totals(state)
    typer.echo(
        f"\nTotal tokens: in={totals.input_tokens} out={totals.output_tokens} "
        f"cache_creation={totals.cache_creation_input_tokens} "
        f"cache_read={totals.cache_read_input_tokens}"
    )
    typer.echo(f"Total cost:   ${totals.cost_usd:.4f}")

    # Run-wide + per-task git isolation/integration observability (E-Wk9Tz3 AC-8).
    integration_counts = {"integrated": 0, "conflict": 0, "failed": 0}
    for ti in getattr(state, "task_integration", {}).values():
        if ti.status == "integrated":
            integration_counts["integrated"] += 1
        elif ti.status in _CLI_CONFLICT_INTEGRATION_STATUSES:
            integration_counts["conflict"] += 1
        elif ti.status == "failed":
            integration_counts["failed"] += 1
    integration = getattr(state, "integration", None)
    _echo_integration_summary(
        active=bool(integration.active) if integration else False,
        branch=integration.branch if integration else None,
        heads=integration.heads if integration else {},
        tier_counts=integration.tier_counts if integration else {},
        integrated=integration_counts["integrated"],
        conflict=integration_counts["conflict"],
        failed=integration_counts["failed"],
    )


def _print_status_snapshot(snap: dict) -> None:
    """Print a status.json snapshot in the same table format as _print_state."""
    typer.echo(f"\nRun:          {snap.get('run_id', '')}")
    typer.echo(f"Status:       {snap.get('status', '')}")
    current = snap.get("current_task")
    if current:
        typer.echo(f"Current task: {current}")
    typer.echo(
        f"\n{'Task':<30} {'Status':<15} {'Route':<20} {'Attempts':<9} "
        f"{'Tokens (in/out)':<20} {'Cost($)'}"
    )
    typer.echo("-" * 110)
    for task_entry in snap.get("tasks", []):
        route = task_entry.get("route") or ""
        tokens = f"{task_entry.get('input_tokens', 0)}/{task_entry.get('output_tokens', 0)}"
        typer.echo(
            f"{task_entry.get('id', ''):<30} "
            f"{task_entry.get('status', ''):<15} "
            f"{route:<20} "
            f"{task_entry.get('attempts', 0):<9} "
            f"{tokens:<20} "
            f"{task_entry.get('cost_usd', 0.0):.4f}"
        )
    # Routing + circuit-breaker observability trailer (LLD §10.2, FR-CB4).
    for tb in snap.get("tripped_breakers", []):
        typer.echo(f"Tripped breakers: {tb['id']} ({tb['condition']}, action={tb['action']})")
    # Run-wide actual usage totals (E-9h3m7k FR-3) — real values, not estimates.
    totals = snap.get("usage_totals") or {}
    typer.echo(
        f"\nTotal tokens: in={totals.get('input_tokens', 0)} out={totals.get('output_tokens', 0)} "
        f"cache_creation={totals.get('cache_creation_input_tokens', 0)} "
        f"cache_read={totals.get('cache_read_input_tokens', 0)}"
    )
    typer.echo(f"Total cost:   ${totals.get('cost_usd', 0.0):.4f}")

    # Run-wide + per-task git isolation/integration observability (E-Wk9Tz3 AC-8).
    integ = snap.get("integration") or {}
    _echo_integration_summary(
        active=bool(integ.get("active", False)),
        branch=integ.get("branch"),
        heads=integ.get("heads") or {},
        tier_counts=integ.get("tier_counts") or {},
        integrated=integ.get("integrated", 0),
        conflict=integ.get("conflict", 0),
        failed=integ.get("failed", 0),
    )


def _load_project_config_or_none() -> ProjectConfig | None:
    """Discover + load the nearest project config.

    Returns `None` ONLY when no config file exists at all (`find_project_config` found
    nothing) — a present-but-invalid config file now RAISES `ConfigError` instead of being
    silently swallowed (C-2, 2026-09-07 review: this previously caught `except Exception`
    unconditionally, so a typo'd `isolation.mode: workree` or any other malformed config
    silently fell back to "as if the file didn't exist" instead of erroring loudly, which
    is exactly the class of mistake a config schema exists to catch). Callers that need a
    clean CLI exit on a malformed file should call `_load_project_config_or_exit` instead
    of this function directly.

    Shared by `_resolve_run_settings`, `_resolve_monitoring_settings`,
    `_resolve_isolation_settings` and others — previously each had its own verbatim copy of
    this find-then-load block (late-gate reviewer finding; CLAUDE.md's DRY rule: "no
    duplicate logic — extract to a function... if logic appears twice"). Each resolver
    still calls this once per `ao run`/`ao resume` invocation (multiple calls total per
    command, same as before) — this extraction removes the duplicated *logic*, not the
    duplicated *call*; a single-parse-per-command optimization would need a shared cache,
    which isn't worth the added state for a small, local config file read.
    """
    from .project_config import find_project_config, load_project_config

    config_path = find_project_config()
    if config_path is None:
        return None
    return load_project_config(config_path)


def _load_project_config_or_exit() -> ProjectConfig | None:
    """`_load_project_config_or_none`, converting a malformed (present but invalid) config
    file's `ConfigError` into a clean `typer.Exit(1)` with the actionable message (C-2,
    2026-09-07 review). Every CLI command that reads the project config uses this rather
    than the raw resolver, so a real misconfiguration is always reported loudly instead of
    silently discarded.
    """
    from .errors import ConfigError

    try:
        return _load_project_config_or_none()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1) from exc


# Env var carrying the workspace-scoped general-instruction list, os.pathsep-separated
# (":" on POSIX, ";" on Windows) — the same convention PATH/PYTHONPATH use, so shells and
# CI systems can compose it without inventing a project-specific delimiter.
ENV_GENERAL_INSTRUCTIONS = "AO_GENERAL_INSTRUCTIONS"

# Env var for the run-level isolation-mode fill-in chain (E-Wk9Tz3 HLD §11 M9):
# --isolation > AO_ISOLATION > .ao/config.yaml isolation.mode > unset (no override). Named
# so it is never a bare literal at either call site, mirroring AO_MAX_PARALLEL's own
# convention.
ENV_ISOLATION_MODE = "AO_ISOLATION"

# `--isolation`/AO_ISOLATION/isolation.mode's valid values (HLD §11 M9, amended 2026-09-07
# per coordinator C-1 decision). No "auto" member: reviewed and confirmed neither
# `models.py` nor the HLD define an isolation-mode "auto" constant (unlike the unrelated
# conflict-resolver-tier "auto", `models.TIER_AUTO`/`ResolverTier`) -- "no override" is
# represented by the resolved mode being `None`, not a third string value, so
# `_resolve_isolation_settings`'s return type is `str | None`. `None`/unset makes no
# change at all -- every workflow spec's own defaults/per-task isolation stands exactly as
# authored. `ISOLATION_NONE`/`ISOLATION_WORKTREE` (imported from `models.py`, C-3) become
# `WorkflowSpec.defaults.isolation`'s new value: a FILL-IN for tasks left at
# `isolation="inherit"` only (ADR-0006 -- a run-level flag fills in a per-task setting, it
# never clobbers one a task/spec author declared explicitly; `resolve_task_isolation`'s own
# "inherit" semantics are unchanged by this). For a TRUE, loud kill switch that overrides
# even an explicit per-task declaration, see `--no-isolation`/`AO_NO_ISOLATION` below.
ISOLATION_MODE_CHOICES = (ISOLATION_NONE, ISOLATION_WORKTREE)

# `--no-isolation`/AO_NO_ISOLATION (C-1 coordinator decision, 2026-09-07): an emergency kill
# switch, not a setting -- deliberately NO `.ao/config.yaml` layer (a config-file default
# that silently disabled isolation workspace-wide would defeat the "loud override" premise
# ADR-0006 itself carves out for exactly this case). Mutually exclusive with
# `--isolation worktree` on the same invocation (a usage error, not a precedence question).
ENV_NO_ISOLATION = "AO_NO_ISOLATION"


def resolve_general_instructions(
    cli_paths: list[str] | None,
    workflow_paths: list[str] | None = None,
) -> list[str]:
    """Build the effective general-instruction path list for a run (E-Ui7Kq2 FR-GI1).

    General instructions are **additive across every layer**, not an override chain — this
    is the one setting in AO that deliberately does NOT follow the CLI > env > config
    precedence of `_resolve_run_settings`. The user-facing contract is "define them once in
    the workspace and every task gets them regardless"; a precedence chain would mean a
    single `--general-instruction` on the command line silently *dropped* the workspace's
    house rules, which is the opposite of that contract.

    Merge order (stable, first occurrence wins on duplicates):
        project config -> env var -> CLI flags -> workflow spec

    Ordering is "broadest scope first" so workspace-wide rules are presented to the agent
    before workflow-specific ones. De-duplication is by resolved path string, so the same
    file named twice across layers is passed to the agent only once.

    Args:
        cli_paths: Values of repeated ``--general-instruction`` flags (may be None/empty).
        workflow_paths: ``WorkflowSpec.general_instructions`` (may be None/empty).

    Returns:
        De-duplicated, order-preserving list of instruction paths. Empty list when no layer
        declares any — which keeps every pre-epic run byte-identical.
    """
    cfg = _load_project_config_or_exit()

    env_raw = os.environ.get(ENV_GENERAL_INSTRUCTIONS, "")
    env_paths = [p for p in env_raw.split(os.pathsep) if p.strip()]

    merged: list[str] = []
    seen: set[str] = set()
    for layer in (
        list(cfg.general_instructions) if cfg else [],
        env_paths,
        list(cli_paths or []),
        list(workflow_paths or []),
    ):
        for path in layer:
            candidate = path.strip()
            if candidate and candidate not in seen:
                seen.add(candidate)
                merged.append(candidate)
    return merged


def _apply_prompt(
    wf,  # WorkflowSpec — untyped to keep this module's lazy-import style
    store: ArtifactStore,
    prompt: str | None,
    prompt_file: str | None,
) -> None:
    """Write ``--prompt`` / ``--prompt-file`` content into the workflow's prompt artifact.

    This is the per-RUN input path (E-Ui7Kq2 FR-P1) and is deliberately separate from
    general instructions (per-WORKSPACE): the prompt says *what this run should do*, the
    general instructions say *how every task in this workspace should behave*.

    The destination is ``WorkflowSpec.prompt_path``, resolved through the artifact store so
    it is path-guarded to the workspace root exactly like every other artifact. A workflow
    consumes the prompt by listing that same path in a task's ``inputs``.

    No-ops when neither flag is given, so a workflow with a hand-written prompt file (the
    ``meta/ao/epics/<id>/prompt.md`` convention) keeps working untouched.

    Raises:
        typer.Exit: If both flags are given, if the workflow declares no ``prompt_path``,
            or if ``--prompt-file`` points at an unreadable file.
    """
    if prompt is None and prompt_file is None:
        return

    if prompt is not None and prompt_file is not None:
        typer.echo("ERROR: --prompt and --prompt-file are mutually exclusive", err=True)
        raise typer.Exit(1)

    if not wf.prompt_path:
        typer.echo(
            f"ERROR: workflow '{wf.id}' declares no `prompt_path`, so there is nowhere to "
            'write the prompt. Add `"prompt_path": "<path>"` to the workflow spec and '
            "list that same path in the inputs of the task that should consume it.",
            err=True,
        )
        raise typer.Exit(1)

    if prompt_file is not None:
        try:
            text = Path(prompt_file).read_text(encoding="utf-8")
        except OSError as exc:
            typer.echo(f"ERROR: cannot read --prompt-file {prompt_file}: {exc}", err=True)
            raise typer.Exit(1)
    else:
        text = prompt or ""

    from .errors import ArtifactPathError

    try:
        dest = Path(store.resolve(wf.prompt_path))
    except ArtifactPathError as exc:
        typer.echo(f"ERROR: invalid workflow prompt_path: {exc}", err=True)
        raise typer.Exit(1)

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    typer.echo(f"Wrote prompt ({len(text)} chars) to {dest}")


def _resolve_run_settings(
    max_attempts: int | None,
    max_turns: int | None,
    model: str | None,
    effort: str | None,
    quota_max_wait: int | None,
    quota_poll_interval: int | None,
    max_parallel: int | None,
) -> tuple[int | None, int | None, str | None, str | None, int, int, int]:
    """Merge CLI flags, env vars, and project config for runtime execution settings.

    Precedence (highest to lowest): CLI flag > env var > project config > built-in default.
    Returns (max_attempts, max_turns, model, effort, quota_max_wait_seconds, quota_poll_seconds,
    max_parallel).
    """
    from .models import (
        DEFAULT_MAX_PARALLEL,
        DEFAULT_QUOTA_MAX_WAIT_SECONDS,
        DEFAULT_QUOTA_POLL_SECONDS,
    )

    def _int_env(name: str) -> int | None:
        v = os.environ.get(name)
        try:
            return int(v) if v else None
        except ValueError:
            return None

    cfg = _load_project_config_or_exit()

    resolved_max_attempts = (
        max_attempts or _int_env("AO_MAX_ATTEMPTS") or (cfg.max_attempts if cfg else None)
    )
    resolved_max_turns = max_turns or _int_env("AO_MAX_TURNS") or (cfg.max_turns if cfg else None)
    resolved_model = model or os.environ.get("AO_MODEL") or (cfg.model if cfg else None)
    resolved_effort = effort or os.environ.get("AO_EFFORT") or (cfg.effort if cfg else None)
    resolved_quota_max_wait = int(
        quota_max_wait
        or _int_env("AO_QUOTA_MAX_WAIT_SECONDS")
        or (cfg.quota_max_wait_seconds if cfg else None)
        or DEFAULT_QUOTA_MAX_WAIT_SECONDS
    )
    resolved_quota_poll = int(
        quota_poll_interval
        or _int_env("AO_QUOTA_POLL_SECONDS")
        or (cfg.quota_poll_seconds if cfg else None)
        or DEFAULT_QUOTA_POLL_SECONDS
    )
    # NOTE: like every other `or`-chain above, a falsy 0 here is indistinguishable from
    # "unset" and falls through to DEFAULT_MAX_PARALLEL (=1) -- consistent with how
    # --quota-max-wait/--max-attempts 0 already behave in this function. A negative value is
    # truthy in Python so it survives the chain unchanged and is what the guard below rejects.
    resolved_max_parallel = int(
        max_parallel
        or _int_env("AO_MAX_PARALLEL")
        or (cfg.max_parallel if cfg else None)
        or DEFAULT_MAX_PARALLEL
    )
    if resolved_max_parallel < 1:
        typer.echo("ERROR: --max-parallel must be >= 1", err=True)
        raise typer.Exit(1)
    return (
        resolved_max_attempts,
        resolved_max_turns,
        resolved_model,
        resolved_effort,
        resolved_quota_max_wait,
        resolved_quota_poll,
        resolved_max_parallel,
    )


def _apply_isolation_state_dir_env(cfg: ProjectConfig | None) -> None:
    """Fill `AO_STATE_DIR` in from `.ao/config.yaml`'s `isolation.state_dir`, ONLY when the
    real env var isn't already set in this process (E-Wk9Tz3 HLD §11 M9: "override
    $AO_STATE_DIR for worktrees"). Mirrors `apply_project_config_env`'s own "explicit env
    always wins" rule for the top-level `env:` block.

    Must run before ANY `isolation.paths` call in this process -- worktree/state-dir
    resolution reads `AO_STATE_DIR` fresh at call time, never cached -- so every command
    that can touch worktrees (`run`, `resume`, `prune`) calls this once, early, right after
    discovering the project config.
    """
    if cfg is None or not cfg.isolation.state_dir:
        return
    from .isolation.paths import STATE_ENV

    if STATE_ENV not in os.environ:
        os.environ[STATE_ENV] = cfg.isolation.state_dir


def _resolve_isolation_settings(
    isolation: str | None,
) -> tuple[str | None, bool, dict[str, dict[str, str]]]:
    """Merge CLI flag, env var, and project config for the isolation-mode fill-in, plus the
    config-file-only `strict`/`env` knobs (E-Wk9Tz3 HLD §11 M9, amended 2026-09-07 per
    coordinator C-1 decision).

    `mode` precedence (highest to lowest): `--isolation` > `AO_ISOLATION` >
    `.ao/config.yaml isolation.mode` > unset (`None`) -- exactly mirrors `--max-parallel`'s
    own chain (`_resolve_run_settings`), including "an empty env var falls through" (the
    `or`-chain below is falsy-transparent, matching every other resolver in this module).
    `None` (no layer supplies a value) means "no override": the caller leaves
    `WorkflowSpec.defaults.isolation` untouched, matching what a since-removed `"auto"`
    string sentinel used to mean (dropped per C-1: no `models.py`/HLD constant for an
    isolation-mode "auto" was ever found).

    `strict`/`env` have NO CLI/env surface: HLD §11 M9's own interface table states
    `isolation.env` "comes only from `.ao/config.yaml` ... never from a workflow spec or a
    manifest" -- the same trust boundary as the top-level `env:` block today. They are read
    straight from the project config, defaulting to `False`/`{}` when absent.

    Returns `(mode, strict, env_overlay)`. `mode is None` is the caller's cue to leave
    `WorkflowSpec.defaults.isolation` untouched; `strict`/`env_overlay` are passed to
    `Orchestrator(isolation_strict=, isolation_env=)` verbatim.

    Raises:
        typer.Exit: `mode` (from any layer) is not `None` and not one of
            `ISOLATION_MODE_CHOICES`; or the discovered `.ao/config.yaml` is malformed
            (C-2 -- `_load_project_config_or_exit` already reports and exits for that case).
    """
    cfg = _load_project_config_or_exit()
    _apply_isolation_state_dir_env(cfg)

    resolved_mode = (
        isolation or os.environ.get(ENV_ISOLATION_MODE) or (cfg.isolation.mode if cfg else None)
    )
    if resolved_mode is not None and resolved_mode not in ISOLATION_MODE_CHOICES:
        typer.echo(
            f"ERROR: --isolation must be one of {ISOLATION_MODE_CHOICES}, got '{resolved_mode}'",
            err=True,
        )
        raise typer.Exit(1)

    resolved_strict = cfg.isolation.strict if cfg else False
    resolved_env = {k: dict(v) for k, v in cfg.isolation.env.items()} if cfg else {}

    return resolved_mode, resolved_strict, resolved_env


def _resolve_no_isolation(no_isolation: bool) -> bool:
    """`--no-isolation` > `AO_NO_ISOLATION` (truthy) -- deliberately NO config-file layer
    (C-1 coordinator decision, 2026-09-07): this is an emergency kill switch, not a
    workspace-wide setting."""
    if no_isolation:
        return True
    return os.environ.get(ENV_NO_ISOLATION, "").strip().lower() in ("1", "true", "yes", "on")


def _apply_no_isolation_kill_switch(
    wf,  # WorkflowSpec -- untyped to keep this module's lazy-import style (matches
    # `_apply_prompt`'s own `wf` param)
    no_isolation: bool,
) -> None:
    """`--no-isolation`/`AO_NO_ISOLATION`: force EVERY task's isolation to `"none"`,
    regardless of what the task, `defaults.isolation`, or `.ao/config.yaml` declared (C-1
    coordinator decision, 2026-09-07). Unlike `--isolation none`'s fill-in-only semantics
    (`_resolve_isolation_settings`, ADR-0006), this is a TRUE, loud override -- an
    emergency lever for a spec whose per-task `isolation: worktree` declarations are
    causing a systemic failure, where the fill-in mechanism has no effect at all.

    Logs exactly one WARNING naming every task whose isolation was actually changed (never
    silent) -- a task already at `isolation: "none"` isn't reported, so the message is
    empty/absent for a workflow the kill switch had no real effect on.
    """
    if not no_isolation:
        return
    overridden = sorted(t.id for t in wf.tasks if t.isolation != ISOLATION_NONE)
    for t in wf.tasks:
        t.isolation = ISOLATION_NONE
    wf.defaults.isolation = ISOLATION_NONE
    if overridden:
        typer.echo(
            f"WARNING: --no-isolation forced isolation={ISOLATION_NONE!r} for "
            f"{len(overridden)} task(s) that declared otherwise: {', '.join(overridden)}",
            err=True,
        )


def _resolve_monitoring_settings(self_heal: bool | None) -> MonitoringConfig:
    """Merge CLI flag, env var, and project config for monitoring settings (E-XyfjuZ).

    `self_heal` uses the SAME tri-state CLI > env var > project config > built-in default
    precedence as every other runtime setting in `_resolve_run_settings` (revised from an
    earlier monotonic-enable-only draft per early-gate reviewer feedback: an operator must
    be able to force self-heal OFF via CLI/env even when the project config enables it,
    matching every other setting's override symmetry). Every other monitoring knob
    (`monitor` selection, bounds, `transient_patterns`) is config-file-ONLY — no CLI/env —
    per the epic brief's "config file is the primary surface; don't explode flag count."

    Returns the fully-resolved `MonitoringConfig` (never `None` — an absent project config
    resolves to `MonitoringConfig()`'s all-defaults, byte-identical to pre-epic behavior).
    """
    from .project_config import MonitoringConfig

    cfg = _load_project_config_or_exit()
    base = cfg.monitoring if cfg else MonitoringConfig()

    def _bool_env(name: str) -> bool | None:
        v = os.environ.get(name)
        if not v:
            return None
        return v.strip().lower() in ("1", "true", "yes", "on")

    resolved_self_heal = self_heal
    if resolved_self_heal is None:
        resolved_self_heal = _bool_env("AO_SELF_HEAL")
    if resolved_self_heal is None:
        resolved_self_heal = base.self_heal

    return base.model_copy(update={"self_heal": resolved_self_heal})


def _build_monitor(
    cfg: MonitoringConfig, agent_map: dict, store: ArtifactStore, executor: Executor
) -> Monitor:
    """Construct the configured `Monitor` implementation (E-XyfjuZ).

    `cfg.monitor == "rules"` (default) -> `RuleBasedMonitor` seeded from `cfg`'s
    `heal_wait_seconds`/`transient_patterns`. Any other value -> looked up in *agent_map*
    (`agents.json`) and wrapped in `AgentMonitor`; a clean `typer.Exit(1)` if the name isn't
    a known agent (fails fast, before any task dispatch, mirroring `_load_all`'s existing
    validation style).
    """
    from .monitoring import AgentMonitor, RuleBasedMonitor

    if cfg.monitor == "rules":
        return RuleBasedMonitor(
            wait_seconds=cfg.heal_wait_seconds,
            extra_transient_patterns=cfg.transient_patterns,
        )
    if cfg.monitor not in agent_map:
        typer.echo(
            f"ERROR: monitoring.monitor {cfg.monitor!r} not found in agents "
            f"(known agents: {sorted(agent_map)})",
            err=True,
        )
        raise typer.Exit(1)
    return AgentMonitor(agent_map[cfg.monitor], executor, store, name=cfg.monitor)


def _build_effective_budget(
    wf_budget: BudgetSpec | None,
    budget_total: int | None,
    rate_tokens: int | None,
    rate_window: str | None,
    on_exhaustion_override: str | None,
    pessimism_buffer: float | None,
) -> BudgetSpec | None:
    """Build effective BudgetSpec by merging CLI flags over the workflow budget block.

    Precedence: CLI > spec > unset (no limit).
    Returns None if no budget is configured anywhere (no-op path).
    Raises typer.Exit(1) on invalid combinations.
    """
    from .errors import SpecValidationError
    from .models import BudgetSpec, EstimatorConfig, RateLimit
    from .spec import budget_cross_validate

    # Validate on_exhaustion value if provided
    if on_exhaustion_override is not None and on_exhaustion_override not in ("stop", "wait"):
        typer.echo(
            f"ERROR: --on-exhaustion must be 'stop' or 'wait', got '{on_exhaustion_override}'",
            err=True,
        )
        raise typer.Exit(1)

    # Start with spec as base (or empty defaults)
    base_total = wf_budget.total_tokens if wf_budget else None
    base_rate = wf_budget.rate if wf_budget else None
    base_on_exhaustion = wf_budget.on_exhaustion if wf_budget else "stop"
    base_estimator = wf_budget.estimator if wf_budget else EstimatorConfig()

    # CLI overrides (per-field, not whole-object replacement)
    effective_total = budget_total if budget_total is not None else base_total

    # Rate: CLI tokens + window override the spec rate if either is provided.
    # Precedence is per-field: if only one of the two is given on the CLI,
    # fall back to the spec rate for the missing piece.
    if rate_tokens is not None or rate_window is not None:
        resolved_rate_tokens = (
            rate_tokens
            if rate_tokens is not None
            else (base_rate.tokens if base_rate is not None else None)
        )
        resolved_rate_window = (
            rate_window
            if rate_window is not None
            else (base_rate.window if base_rate is not None else None)
        )
        if resolved_rate_tokens is None or resolved_rate_window is None:
            typer.echo(
                "ERROR: --rate-tokens and --rate-window must both be provided"
                " when specifying a rate limit via CLI",
                err=True,
            )
            raise typer.Exit(1)
        # Validate that the window value is a known literal before constructing RateLimit.
        # budget_cross_validate will catch this too, but a clearer message here is better.
        valid_windows = ("minute", "ten_minutes", "hour")
        if resolved_rate_window not in valid_windows:
            typer.echo(
                f"ERROR: --rate-window must be one of {valid_windows},"
                f" got '{resolved_rate_window}'",
                err=True,
            )
            raise typer.Exit(1)
        from typing import Literal, cast

        effective_rate: RateLimit | None = RateLimit(
            tokens=resolved_rate_tokens,
            window=cast(Literal["minute", "ten_minutes", "hour"], resolved_rate_window),
        )
    else:
        effective_rate = base_rate

    # Cast on_exhaustion to the expected Literal type after validation above.
    from typing import Literal, cast

    effective_on_exhaustion = cast(
        "Literal['stop', 'wait']",
        on_exhaustion_override if on_exhaustion_override is not None else base_on_exhaustion,
    )

    if pessimism_buffer is not None:
        effective_estimator = base_estimator.model_copy(
            update={"pessimism_buffer": pessimism_buffer}
        )
    else:
        effective_estimator = base_estimator

    # No-op path: no budget anywhere
    if effective_total is None and effective_rate is None:
        return None

    eff = BudgetSpec(
        total_tokens=effective_total,
        rate=effective_rate,
        on_exhaustion=effective_on_exhaustion,
        estimator=effective_estimator,
    )

    try:
        budget_cross_validate(eff)
    except SpecValidationError as e:
        typer.echo(f"ERROR: budget config invalid: {e}", err=True)
        raise typer.Exit(1)

    return eff


@app.command()
def validate(
    workflow: str | None = typer.Option(None, help="Path to workflow JSON/YAML"),
    reposets: str | None = typer.Option(None, help="Path to reposets config JSON/YAML"),
    agents: str | None = typer.Option(None, help="Path to agents config JSON/YAML"),
) -> None:
    """Validate workflow, reposets, and agents specs."""
    from .errors import OrchestratorError

    try:
        _load_all(workflow, reposets, agents)
        typer.echo("OK: all specs valid")
    except OrchestratorError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def run(
    workflow: str | None = typer.Option(None, help="Path to workflow JSON/YAML"),
    reposets: str | None = typer.Option(None, help="Path to reposets config JSON/YAML"),
    agents: str | None = typer.Option(None, help="Path to agents config JSON/YAML"),
    budget_total: int | None = typer.Option(
        None, "--budget-total", help="Total token budget for the run"
    ),
    rate_tokens: int | None = typer.Option(
        None, "--rate-tokens", help="Max tokens per rate window"
    ),
    rate_window: str | None = typer.Option(
        None, "--rate-window", help="Rate window: minute, ten_minutes, or hour"
    ),
    on_exhaustion: str | None = typer.Option(
        None, "--on-exhaustion", help="Action on budget exhaustion: stop (default) or wait"
    ),
    pessimism_buffer: float | None = typer.Option(
        None, "--pessimism-buffer", help="Estimator pessimism multiplier (default 1.3)"
    ),
    max_attempts: int | None = typer.Option(
        None,
        "--max-attempts",
        help=(
            "Max attempts per task (overrides workflow defaults.retries.max_attempts)."
            " Env: AO_MAX_ATTEMPTS"
        ),
    ),
    max_turns: int | None = typer.Option(
        None,
        "--max-turns",
        help=(
            "Max turns per claude agent invocation (overrides effort-derived value)."
            " Env: AO_MAX_TURNS"
        ),
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help="Claude model for all agents (e.g. claude-sonnet-4-6). Env: AO_MODEL",
    ),
    effort: str | None = typer.Option(
        None,
        "--effort",
        help="Effort level for all agents: low, medium, or high. Env: AO_EFFORT",
    ),
    quota_max_wait: int | None = typer.Option(
        None,
        "--quota-max-wait",
        help=(
            "Max seconds to wait during a Claude quota-exhaustion episode before failing"
            " (default 21600 = 6 h). Env: AO_QUOTA_MAX_WAIT_SECONDS"
        ),
    ),
    quota_poll_interval: int | None = typer.Option(
        None,
        "--quota-poll-interval",
        help=(
            "Seconds to sleep between quota-exhaustion re-run attempts"
            " (default 900 = 15 min). Env: AO_QUOTA_POLL_SECONDS"
        ),
    ),
    max_parallel: int | None = typer.Option(
        None,
        "--max-parallel",
        help=(
            "Max independent ready tasks to run at once (default 1 = serial)."
            " Env: AO_MAX_PARALLEL. Config: max_parallel."
        ),
    ),
    self_heal: bool | None = typer.Option(
        None,
        "--self-heal/--no-self-heal",
        help=(
            "Enable/disable task-failure self-healing (Consult Point B, E-XyfjuZ);"
            " overrides env/config in either direction. Env: AO_SELF_HEAL (1/0)."
            " Default: off. Unrelated to recommend-mode breaker consult (Consult Point A),"
            " which activates purely from a workflow's own circuit_breakers[].mode."
        ),
    ),
    general_instruction: list[str] = typer.Option(
        [],
        "--general-instruction",
        help=(
            "PATH to an instruction file applied to EVERY task (repeatable). ADDITIVE, not an"
            " override: merged with .ao/config.yaml general_instructions,"
            f" {ENV_GENERAL_INSTRUCTIONS} (os.pathsep-separated), and the workflow's own"
            " general_instructions."
        ),
    ),
    isolation: str | None = typer.Option(
        None,
        "--isolation",
        help=(
            f"Isolation-mode fill-in for tasks left at isolation=inherit: one of "
            f"{ISOLATION_MODE_CHOICES} (default: unset, honours the spec/defaults.isolation"
            " unchanged). Never overrides a task's own explicit isolation -- for a true"
            f" kill switch see --no-isolation. Env: {ENV_ISOLATION_MODE}. Config:"
            " isolation.mode."
        ),
    ),
    no_isolation: bool = typer.Option(
        False,
        "--no-isolation",
        help=(
            "Emergency kill switch: force EVERY task to isolation=none, regardless of"
            " task/defaults.isolation/isolation.mode (unlike --isolation none's"
            " fill-in-only semantics). Logs one WARNING naming the overridden task ids."
            f" Env: {ENV_NO_ISOLATION} (1/true/yes/on). No config-file layer (emergency"
            " override, not a setting). Mutually exclusive with --isolation worktree."
        ),
    ),
    prompt: str | None = typer.Option(
        None,
        "--prompt",
        help=(
            "Prompt text for this run; written to the workflow's declared `prompt_path`"
            " before the run starts. Requires the workflow to declare prompt_path."
        ),
    ),
    prompt_file: str | None = typer.Option(
        None,
        "--prompt-file",
        help=(
            "Read the run prompt from this file instead of --prompt; its contents are copied"
            " into the workflow's declared `prompt_path`. Mutually exclusive with --prompt."
        ),
    ),
) -> None:
    """Run a workflow from scratch."""
    from datetime import UTC
    from datetime import datetime as _dt

    from .artifacts import LocalFsArtifactStore
    from .budget import DefaultBudgetManager
    from .engine import Orchestrator
    from .errors import CycleError, OrchestratorError
    from .estimator import HeuristicTokenEstimator
    from .executors import DispatchExecutor
    from .runstate import RunStateStore

    try:
        wf, reposet_map, agent_map = _load_all(workflow, reposets, agents)
    except OrchestratorError as e:
        # Includes SpecValidationError/CycleError from the new build_dag +
        # validate_run_control pass in _load_all (LLD §4.4) — must exit non-zero,
        # not silently return (was previously unreachable here since _load_all
        # never built the DAG; cycles used to surface only from orch.run() below).
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)
    except SystemExit:
        return

    (
        eff_max_attempts,
        eff_max_turns,
        eff_model,
        eff_effort,
        eff_quota_max_wait,
        eff_quota_poll,
        eff_max_parallel,
    ) = _resolve_run_settings(
        max_attempts,
        max_turns,
        model,
        effort,
        quota_max_wait,
        quota_poll_interval,
        max_parallel,
    )

    if eff_max_attempts is not None:
        wf.defaults.retries.max_attempts = eff_max_attempts
    if eff_max_turns is not None:
        for spec in agent_map.values():
            spec.max_turns = eff_max_turns
    if eff_model is not None:
        for spec in agent_map.values():
            spec.model = eff_model
    if eff_effort is not None:
        _valid_efforts = ("low", "medium", "high")
        if eff_effort not in _valid_efforts:
            typer.echo(
                f"ERROR: --effort must be one of {_valid_efforts}, got '{eff_effort}'",
                err=True,
            )
            raise typer.Exit(1)
        from typing import Literal, cast

        for spec in agent_map.values():
            spec.effort = cast("Literal['low', 'medium', 'high']", eff_effort)

    # Isolation-mode fill-in + config-file-only strict/env (E-Wk9Tz3 HLD §11 M9). Unset
    # (None) makes no change; ISOLATION_NONE/ISOLATION_WORKTREE become the new
    # WorkflowSpec.defaults.isolation, a fill-in only for tasks left at isolation="inherit"
    # (never a task's own explicit isolation -- ADR-0006, resolve_task_isolation).
    eff_no_isolation = _resolve_no_isolation(no_isolation)
    if eff_no_isolation and isolation == ISOLATION_WORKTREE:
        typer.echo(
            "ERROR: --isolation worktree and --no-isolation are mutually exclusive", err=True
        )
        raise typer.Exit(1)

    eff_isolation_mode, eff_isolation_strict, eff_isolation_env = _resolve_isolation_settings(
        isolation
    )
    if eff_isolation_mode is not None:
        wf.defaults.isolation = eff_isolation_mode

    # --no-isolation/AO_NO_ISOLATION (C-1 coordinator decision): a TRUE kill switch, applied
    # last so it always wins over the fill-in above, including a task's own explicit
    # isolation="worktree" declaration.
    _apply_no_isolation_kill_switch(wf, eff_no_isolation)

    workspace = os.environ.get("AO_WORKSPACE_ROOT") or reposet_map[wf.repo_set].workspace_root
    store = LocalFsArtifactStore(workspace)
    rs_store = RunStateStore(workspace, store)
    executor = DispatchExecutor()

    # Materialize the run prompt BEFORE the run starts, so the task that lists prompt_path
    # in its inputs sees the new text (and its missing-inputs gate passes).
    _apply_prompt(wf, store, prompt, prompt_file)

    effective_general_instructions = resolve_general_instructions(
        general_instruction, wf.general_instructions
    )

    # Build effective budget (CLI > spec > unset)
    effective_budget = _build_effective_budget(
        wf.budget, budget_total, rate_tokens, rate_window, on_exhaustion, pessimism_buffer
    )

    # Construct BudgetManager + estimator if budget is configured
    budget_manager = None
    estimator = None
    clock = None
    if effective_budget is not None:

        def _wall_clock() -> _dt:
            return _dt.now(UTC)

        clock = _wall_clock
        budget_manager = DefaultBudgetManager(effective_budget, clock)
        estimator = HeuristicTokenEstimator(store)

    # Agent-based monitoring & self-healing (E-XyfjuZ) — config-primary, minimal CLI surface.
    monitoring_cfg = _resolve_monitoring_settings(self_heal)
    monitor = _build_monitor(monitoring_cfg, agent_map, store, executor)

    orch = Orchestrator(
        executor,
        store,
        rs_store,
        budget_manager=budget_manager,
        estimator=estimator,
        clock=clock,
        quota_max_wait_seconds=eff_quota_max_wait,
        quota_poll_seconds=eff_quota_poll,
        monitor=monitor,
        max_extensions_per_breaker=monitoring_cfg.max_extensions_per_breaker,
        max_monitor_calls_per_run=monitoring_cfg.max_monitor_calls_per_run,
        self_heal_enabled=monitoring_cfg.self_heal,
        max_heal_retries_per_task=monitoring_cfg.max_heal_retries_per_task,
        max_parallel=eff_max_parallel,
        general_instructions=effective_general_instructions,
        isolation_strict=eff_isolation_strict,
        isolation_env=eff_isolation_env,
    )

    try:
        state = orch.run(wf, reposet_map, agent_map)
    except (OrchestratorError, CycleError) as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    _print_state(state)
    raise typer.Exit(0 if state.status == "succeeded" else 1)


@app.command()
def resume(
    run_id: str = typer.Option(..., help="Run ID to resume"),
    workflow: str | None = typer.Option(None, help="Path to workflow JSON/YAML"),
    reposets: str | None = typer.Option(None, help="Path to reposets config JSON/YAML"),
    agents: str | None = typer.Option(None, help="Path to agents config JSON/YAML"),
    budget_total: int | None = typer.Option(
        None, "--budget-total", help="Total token budget override"
    ),
    rate_tokens: int | None = typer.Option(
        None, "--rate-tokens", help="Max tokens per rate window override"
    ),
    rate_window: str | None = typer.Option(
        None, "--rate-window", help="Rate window override: minute, ten_minutes, or hour"
    ),
    on_exhaustion: str | None = typer.Option(
        None, "--on-exhaustion", help="Action on budget exhaustion: stop or wait"
    ),
    pessimism_buffer: float | None = typer.Option(
        None, "--pessimism-buffer", help="Estimator pessimism multiplier override"
    ),
    max_attempts: int | None = typer.Option(
        None,
        "--max-attempts",
        help=(
            "Max attempts per task override (overrides workflow defaults.retries.max_attempts)."
            " Env: AO_MAX_ATTEMPTS"
        ),
    ),
    max_turns: int | None = typer.Option(
        None,
        "--max-turns",
        help=(
            "Max turns per claude agent invocation (overrides effort-derived value)."
            " Env: AO_MAX_TURNS"
        ),
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help="Claude model for all agents (e.g. claude-sonnet-4-6). Env: AO_MODEL",
    ),
    effort: str | None = typer.Option(
        None,
        "--effort",
        help="Effort level for all agents: low, medium, or high. Env: AO_EFFORT",
    ),
    quota_max_wait: int | None = typer.Option(
        None,
        "--quota-max-wait",
        help="Max seconds to wait during a Claude quota-exhaustion episode before failing "
        "(default 21600 = 6 h). Env: AO_QUOTA_MAX_WAIT_SECONDS",
    ),
    quota_poll_interval: int | None = typer.Option(
        None,
        "--quota-poll-interval",
        help="Seconds to sleep between quota-exhaustion re-run attempts "
        "(default 900 = 15 min). Env: AO_QUOTA_POLL_SECONDS",
    ),
    max_parallel: int | None = typer.Option(
        None,
        "--max-parallel",
        help=(
            "Max independent ready tasks to run at once (default 1 = serial)."
            " Env: AO_MAX_PARALLEL. Config: max_parallel."
        ),
    ),
    extend_breaker: str | None = typer.Option(
        None,
        "--extend-breaker",
        help="Circuit breaker id (from workflow.circuit_breakers) to extend and un-latch on "
        "this resume. Requires exactly one of --extend-by-seconds/--extend-by-same. "
        "(E-3JTmVu FR-2d)",
    ),
    extend_by_seconds: float | None = typer.Option(
        None,
        "--extend-by-seconds",
        help="Amount (in the breaker condition's native unit -- seconds for time-based "
        "conditions, USD for cost conditions, a count otherwise) to add to --extend-breaker's "
        "current effective threshold. Mutually exclusive with --extend-by-same.",
    ),
    extend_by_same: bool = typer.Option(
        False,
        "--extend-by-same",
        help="Extend --extend-breaker's current effective threshold by the ORIGINAL "
        "threshold set at startup (spec.threshold) again. Mutually exclusive with "
        "--extend-by-seconds.",
    ),
    self_heal: bool | None = typer.Option(
        None,
        "--self-heal/--no-self-heal",
        help=(
            "Enable/disable task-failure self-healing (Consult Point B, E-XyfjuZ);"
            " overrides env/config in either direction. Env: AO_SELF_HEAL (1/0)."
            " Default: off."
        ),
    ),
    general_instruction: list[str] = typer.Option(
        [],
        "--general-instruction",
        help=(
            "PATH to an instruction file applied to EVERY task (repeatable). ADDITIVE, not an"
            " override: merged with .ao/config.yaml general_instructions,"
            f" {ENV_GENERAL_INSTRUCTIONS} (os.pathsep-separated), and the workflow's own"
            " general_instructions."
        ),
    ),
    isolation: str | None = typer.Option(
        None,
        "--isolation",
        help=(
            f"Isolation-mode fill-in for tasks left at isolation=inherit: one of "
            f"{ISOLATION_MODE_CHOICES} (default: unset, honours the spec/defaults.isolation"
            " unchanged). Never overrides a task's own explicit isolation -- for a true"
            f" kill switch see --no-isolation. Env: {ENV_ISOLATION_MODE}. Config:"
            " isolation.mode."
        ),
    ),
    no_isolation: bool = typer.Option(
        False,
        "--no-isolation",
        help=(
            "Emergency kill switch: force EVERY task to isolation=none, regardless of"
            " task/defaults.isolation/isolation.mode (unlike --isolation none's"
            " fill-in-only semantics). Logs one WARNING naming the overridden task ids."
            f" Env: {ENV_NO_ISOLATION} (1/true/yes/on). No config-file layer (emergency"
            " override, not a setting). Mutually exclusive with --isolation worktree."
        ),
    ),
) -> None:
    """Resume a previously interrupted run.

    Note there is deliberately no ``--prompt`` here: the run prompt is a per-run INPUT
    artifact that the original ``ao run`` already materialized, and tasks that consumed it
    have their outputs on disk. Rewriting it mid-run would make the run irreproducible from
    its own artifacts (the resumability invariant). Start a new run to change the prompt.
    General instructions, by contrast, are resolved fresh on every invocation, so a workspace
    can add house rules and have a resumed run's remaining tasks pick them up.
    """
    from datetime import UTC
    from datetime import datetime as _dt

    from .artifacts import LocalFsArtifactStore
    from .breakers import apply_breaker_extension
    from .budget import DefaultBudgetManager
    from .engine import Orchestrator
    from .errors import OrchestratorError, SpecValidationError
    from .estimator import HeuristicTokenEstimator
    from .executors import DispatchExecutor
    from .logging_setup import attach_run_handler, get_run_logger
    from .runstate import RunStateStore

    try:
        wf, reposet_map, agent_map = _load_all(workflow, reposets, agents)
    except OrchestratorError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)
    except SystemExit:
        return

    workspace = os.environ.get("AO_WORKSPACE_ROOT") or reposet_map[wf.repo_set].workspace_root
    store = LocalFsArtifactStore(workspace)
    rs_store = RunStateStore(workspace, store)

    effective_general_instructions = resolve_general_instructions(
        general_instruction, wf.general_instructions
    )

    (
        eff_max_attempts,
        eff_max_turns,
        eff_model,
        eff_effort,
        eff_quota_max_wait,
        eff_quota_poll,
        eff_max_parallel,
    ) = _resolve_run_settings(
        max_attempts,
        max_turns,
        model,
        effort,
        quota_max_wait,
        quota_poll_interval,
        max_parallel,
    )

    if eff_max_attempts is not None:
        wf.defaults.retries.max_attempts = eff_max_attempts
    if eff_max_turns is not None:
        for spec in agent_map.values():
            spec.max_turns = eff_max_turns
    if eff_model is not None:
        for spec in agent_map.values():
            spec.model = eff_model
    if eff_effort is not None:
        _valid_efforts = ("low", "medium", "high")
        if eff_effort not in _valid_efforts:
            typer.echo(
                f"ERROR: --effort must be one of {_valid_efforts}, got '{eff_effort}'",
                err=True,
            )
            raise typer.Exit(1)
        from typing import Literal, cast

        for spec in agent_map.values():
            spec.effort = cast("Literal['low', 'medium', 'high']", eff_effort)

    # Isolation-mode fill-in + config-file-only strict/env (E-Wk9Tz3 HLD §11 M9) -- same
    # chain and fill-in-only semantics as `ao run` (see there for the full rationale).
    eff_no_isolation = _resolve_no_isolation(no_isolation)
    if eff_no_isolation and isolation == ISOLATION_WORKTREE:
        typer.echo(
            "ERROR: --isolation worktree and --no-isolation are mutually exclusive", err=True
        )
        raise typer.Exit(1)

    eff_isolation_mode, eff_isolation_strict, eff_isolation_env = _resolve_isolation_settings(
        isolation
    )
    if eff_isolation_mode is not None:
        wf.defaults.isolation = eff_isolation_mode
    _apply_no_isolation_kill_switch(wf, eff_no_isolation)

    try:
        existing = rs_store.load(run_id)
        existing = rs_store.prepare_resume(existing, wf)
    except FileNotFoundError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    # ---- Breaker extension (E-3JTmVu FR-2d) ----
    # Applied to the already-loaded/prepared `existing` RunState, BEFORE orch.run() -- the
    # mutation is persisted via orch.run()'s own save path (Orchestrator.run's first action
    # is self._runstate.save(state), engine.py ~:135) since it's the same object instance.
    if extend_breaker is not None:
        breaker_spec = next((b for b in wf.circuit_breakers if b.id == extend_breaker), None)
        if breaker_spec is None:
            typer.echo(
                f"ERROR: no circuit breaker with id {extend_breaker!r} in "
                f"workflow.circuit_breakers",
                err=True,
            )
            raise typer.Exit(1)

        run_dir = os.path.join(workspace, ".orchestrator", "runs", run_id)
        log_path = os.path.join(run_dir, "run.log")
        attach_run_handler(run_id, log_path)  # idempotent; orch.run() attaches the same pair
        extend_log = get_run_logger(run_id)

        def _extend_clock() -> _dt:
            return _dt.now(UTC)

        old_effective = existing.breaker_overrides.get(breaker_spec.id, breaker_spec.threshold)
        try:
            new_threshold = apply_breaker_extension(
                existing,
                breaker_spec,
                extend_by_seconds=extend_by_seconds,
                extend_by_same=extend_by_same,
                clock=_extend_clock,
                run_log=extend_log,
            )
        except (SpecValidationError, ValueError) as e:
            typer.echo(f"ERROR: {e}", err=True)
            raise typer.Exit(1)
        # Persist immediately (don't rely solely on orch.run()'s later save): later CLI-only
        # validation below (e.g. --on-exhaustion) can still typer.Exit(1) before orch.run() is
        # ever reached, which would otherwise silently discard an already-logged extension.
        rs_store.save(existing)
        typer.echo(f"Extended breaker {breaker_spec.id!r}: {old_effective} -> {new_threshold}")
    elif extend_by_seconds is not None or extend_by_same:
        typer.echo("ERROR: --extend-by-seconds/--extend-by-same require --extend-breaker", err=True)
        raise typer.Exit(1)

    # Build effective budget (CLI > spec > unset), using the original workflow budget
    effective_budget = _build_effective_budget(
        wf.budget, budget_total, rate_tokens, rate_window, on_exhaustion, pessimism_buffer
    )

    budget_manager = None
    estimator = None
    clock = None
    if effective_budget is not None:

        def _wall_clock() -> _dt:
            return _dt.now(UTC)

        clock = _wall_clock
        budget_manager = DefaultBudgetManager(effective_budget, clock)
        estimator = HeuristicTokenEstimator(store)

    executor = DispatchExecutor()

    # Agent-based monitoring & self-healing (E-XyfjuZ) — config-primary, minimal CLI surface.
    monitoring_cfg = _resolve_monitoring_settings(self_heal)
    monitor = _build_monitor(monitoring_cfg, agent_map, store, executor)

    orch = Orchestrator(
        executor,
        store,
        rs_store,
        budget_manager=budget_manager,
        estimator=estimator,
        clock=clock,
        quota_max_wait_seconds=eff_quota_max_wait,
        quota_poll_seconds=eff_quota_poll,
        monitor=monitor,
        max_extensions_per_breaker=monitoring_cfg.max_extensions_per_breaker,
        max_monitor_calls_per_run=monitoring_cfg.max_monitor_calls_per_run,
        self_heal_enabled=monitoring_cfg.self_heal,
        max_heal_retries_per_task=monitoring_cfg.max_heal_retries_per_task,
        max_parallel=eff_max_parallel,
        general_instructions=effective_general_instructions,
        isolation_strict=eff_isolation_strict,
        isolation_env=eff_isolation_env,
    )

    try:
        state = orch.run(wf, reposet_map, agent_map, run_state=existing)
    except OrchestratorError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    _print_state(state)
    raise typer.Exit(0 if state.status == "succeeded" else 1)


@app.command()
def status(
    run_id: str = typer.Option(..., help="Run ID to inspect"),
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        help="Workspace root (or set AO_WORKSPACE_ROOT). "
        "When provided the --workflow/--reposets/--agents triplet is not required.",
    ),
    workflow: str | None = typer.Option(None, help="Path to workflow JSON/YAML"),
    reposets: str | None = typer.Option(None, help="Path to reposets config JSON/YAML"),
    agents: str | None = typer.Option(None, help="Path to agents config JSON/YAML"),
) -> None:
    """Show current status of a run.

    Reads status.json if present (fast path); falls back to state.json.
    Accepts --workspace <root> (or AO_WORKSPACE_ROOT) so the full
    --workflow/--reposets/--agents triplet is not required just to inspect a run.
    """
    import json
    from pathlib import Path

    # Resolve workspace: --workspace > AO_WORKSPACE_ROOT > derive from spec triplet.
    ws_root: str | None = workspace or os.environ.get("AO_WORKSPACE_ROOT")

    if ws_root is None:
        # Fall back to deriving from the spec triplet (backward-compatible path).
        from .artifacts import LocalFsArtifactStore
        from .errors import OrchestratorError
        from .runstate import RunStateStore

        try:
            wf, reposet_map, _ = _load_all(workflow, reposets, agents)
        except OrchestratorError as e:
            typer.echo(f"ERROR: {e}", err=True)
            raise typer.Exit(1)
        except SystemExit:
            return

        ws_root = reposet_map[wf.repo_set].workspace_root

    # Try status.json first (FR-6), fall back to state.json via RunStateStore.
    status_json_path = Path(ws_root) / ".orchestrator" / "runs" / run_id / "status.json"
    if status_json_path.exists():
        try:
            snap = json.loads(status_json_path.read_text())
            _print_status_snapshot(snap)
            return
        except (json.JSONDecodeError, KeyError):
            # Corrupt status.json — fall through to state.json
            pass

    # Fallback: load from state.json via RunStateStore
    from .artifacts import LocalFsArtifactStore
    from .runstate import RunStateStore

    store = LocalFsArtifactStore(ws_root)
    rs_store = RunStateStore(ws_root, store)

    try:
        loaded_state = rs_store.load(run_id)
    except FileNotFoundError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    _print_state(loaded_state)


@app.command(name="init")
def init_cmd(
    directory: str | None = typer.Option(
        None,
        "--dir",
        "-d",
        help="Directory in which to create .ao/config.yaml (default: current directory)",
    ),
) -> None:
    """Scaffold a per-project .ao/config.yaml in the current directory.

    Creates .ao/config.yaml with commented example fields so you can get
    started quickly.  Does not overwrite an existing file.
    """
    from .errors import ConfigError
    from .project_config import scaffold_init

    target = Path(directory) if directory else None
    try:
        config_path = scaffold_init(target)
    except ConfigError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Created {config_path}")
    typer.echo("")
    typer.echo("Next steps:")
    typer.echo(
        "  1. Edit .ao/config.yaml — uncomment and fill in workflow, reposets, agents paths."
    )
    typer.echo("  2. Run `ao validate` to check your config.")
    typer.echo("  3. Run `ao run` to execute your workflow.")


# Loopback default for `ao ui --host`. The dashboard has NO authentication in this release
# and can both read the filesystem and spend money by launching runs, so it must not be
# reachable off-box unless an operator deliberately overrides this.
UI_DEFAULT_HOST = "127.0.0.1"
UI_DEFAULT_PORT = 8765


@app.command(name="ui")
def ui_cmd(
    host: str = typer.Option(
        UI_DEFAULT_HOST,
        "--host",
        help=(
            "Interface to bind. Defaults to loopback because the dashboard is unauthenticated"
            " and can launch runs; only change this on a trusted network."
        ),
    ),
    port: int = typer.Option(UI_DEFAULT_PORT, "--port", "-p", help="Port to listen on."),
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace root to serve (default: current directory). Env: AO_WORKSPACE_ROOT",
    ),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev)."),
    open_browser: bool = typer.Option(
        False, "--open", help="Open the dashboard in the default browser once it is serving."
    ),
) -> None:
    """Serve the browser-based dashboard.

    Requires the optional `ui` extra:  uv sync --extra ui  (or pip install 'agent-orchestrator[ui]')
    """
    try:
        import uvicorn
    except ImportError:
        typer.echo(
            "ERROR: the dashboard needs the optional 'ui' extra.\n"
            "  uv sync --extra ui       (this repo)\n"
            "  pip install 'agent-orchestrator[ui]'",
            err=True,
        )
        raise typer.Exit(1)

    ws = workspace or os.environ.get("AO_WORKSPACE_ROOT") or os.getcwd()
    ws = str(Path(ws).resolve())
    if not Path(ws).is_dir():
        typer.echo(f"ERROR: workspace root is not a directory: {ws}", err=True)
        raise typer.Exit(1)

    from .ui.app import create_app
    from .ui.security import DEFAULT_ALLOWED_HOSTS, resolve_allowed_hosts
    from .ui.service import DashboardService

    url = f"http://{host}:{port}"
    typer.echo(f"Agent Orchestrator dashboard — workspace: {ws}")
    typer.echo(f"Serving on {url}  (Ctrl-C to stop)")
    # Single source of truth for the loopback host list: ui/security.py's DEFAULT_ALLOWED_HOSTS
    # (imported lazily, same as the rest of this function — the [ui] extra must stay optional).
    if host not in DEFAULT_ALLOWED_HOSTS:
        typer.echo(
            f"WARNING: binding {host} exposes an UNAUTHENTICATED dashboard that can browse "
            "files and start runs. Only do this on a trusted network.",
            err=True,
        )

    if open_browser:
        import threading
        import webbrowser

        # Fire after a short delay so the server is accepting connections by the time the
        # browser requests the page.
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    # The Host allowlist always includes the loopback defaults plus whatever host this
    # process actually bound to (see ui/security.py) — so a deliberate off-loopback bind
    # (warned about above) still works, while any *other* Host header (DNS rebinding) is
    # rejected with 421.
    if reload:
        # uvicorn's reloader re-imports the app in a fresh process, so it needs an import
        # string plus the workspace/bound-host handed over via env rather than a live object.
        os.environ["AO_UI_WORKSPACE"] = ws
        os.environ["AO_UI_BOUND_HOST"] = host
        uvicorn.run(
            "agent_orchestrator.ui.app:create_app_from_env",
            host=host,
            port=port,
            reload=True,
            factory=True,
        )
    else:
        app_instance = create_app(
            DashboardService(ws), allowed_hosts=resolve_allowed_hosts(bound_host=host)
        )
        uvicorn.run(app_instance, host=host, port=port)


# ---------------------------------------------------------------------------------------
# Isolation worktree GC for `ao prune` (E-Wk9Tz3 HLD §11 M9, §14).
#
# `ao prune` takes only `--workspace` -- no reposets/workflow triplet -- so it has none of
# a live run's `group_repos(repo_paths)` input to build `IsolatedRepo`s from. Every
# worktree this codebase ever creates lives at a fully deterministic path
# (`isolation.paths.worktree_root_prefix_for(workspace_root, run_id)/<task_id>/<repo_key>`,
# HLD §7.1/§11 M3), so the real git repos a run touches are instead DISCOVERED by probing
# whatever of those directories still exist on disk -- no other input needed.
# ---------------------------------------------------------------------------------------


def _isolation_worktree_root(workspace_root: str) -> Path:
    """The per-workspace worktree root (no run_id component).

    `isolation/paths.py` exposes no direct helper for this (only
    `worktree_root_prefix_for`, which requires a run_id) and this ticket's concurrency
    boundary forbids editing that module to add one. `.parent` strips exactly the run_id
    path component `worktree_root_prefix_for` appends last, so this stays correct even if
    that module's internal directory layout changes.
    """
    from .isolation.paths import worktree_root_prefix_for

    # Placeholder run_id: only its PARENT (the workspace-scoped, run_id-independent root)
    # is used below.
    return worktree_root_prefix_for(workspace_root, "_").parent


def _discover_run_worktree_repos(workspace_root: str, run_id: str) -> list[IsolatedRepo]:
    """Discover the real git repositories one run's worktrees belong to, by probing the
    physical worktree directories left on disk under that run's own worktree prefix.

    Deliberately does NOT use the worktree directory itself as `IsolatedRepo.toplevel`:
    this run's own GC pass may remove that exact directory mid-operation, and
    `GitRepo.path` must stay a stable invocation cwd for every subsequent call made on the
    same `GitRepo` instance. Recovered instead from `--git-common-dir`, which always
    points at the persistent main checkout (`<main_toplevel>/.git` for every non-bare
    layout this codebase creates) and is never itself one of the paths GC removes.
    """
    from .isolation.git import GitRepo
    from .isolation.paths import worktree_root_prefix_for
    from .isolation.worktrees import IsolatedRepo as _IsolatedRepo

    prefix = worktree_root_prefix_for(workspace_root, run_id)
    if not prefix.is_dir():
        return []

    discovered: dict[str, IsolatedRepo] = {}
    for task_dir in sorted(p for p in prefix.iterdir() if p.is_dir()):
        for repo_dir in sorted(p for p in task_dir.iterdir() if p.is_dir()):
            probed = GitRepo.probe(str(repo_dir))
            if probed is None:
                # C-4 (2026-09-07 review): every other degrade path in this neighborhood
                # (group_repos's worktree.non_git_repo, _gc_run_worktrees'/
                # _preview_run_worktrees' own GitError warnings) reports what it skipped --
                # this one must too, so `ao prune` never silently reports success while a
                # repo's worktrees/refs were left ungc'd.
                typer.echo(
                    f"WARNING: skipping worktree GC for run {run_id!r}: {repo_dir} is not "
                    "a readable git worktree",
                    err=True,
                )
                continue
            common_dir = os.path.normpath(probed.common_dir)
            if common_dir in discovered:
                continue
            git_suffix = os.sep + ".git"
            toplevel = (
                common_dir[: -len(git_suffix)] if common_dir.endswith(git_suffix) else common_dir
            )
            if GitRepo.probe(toplevel) is None:
                # The persistent checkout this worktree points back to is gone/unreadable --
                # nothing safe to invoke git against; skip rather than guess (C-4: warn,
                # never silently).
                typer.echo(
                    f"WARNING: skipping worktree GC for run {run_id!r}: the main checkout "
                    f"{toplevel!r} this worktree points back to is gone or unreadable",
                    err=True,
                )
                continue
            discovered[common_dir] = _IsolatedRepo(
                key=repo_dir.name, toplevel=toplevel, common_dir=common_dir
            )
    return list(discovered.values())


def _gc_run_worktrees(workspace_root: str, run_id: str) -> int:
    """`WorktreeManager.gc_run(run_id)` (R-6: routed through `WorktreeManager`/
    `prune_worktrees_scoped` only -- never raw git) over the repos discovered for
    *run_id*. Returns the number of repos GC'd (0 when the run never created a worktree).

    A `GitError` mid-GC is reported as a warning and swallowed, never crashing the whole
    `ao prune` invocation: the run-directory deletion this ticket wraps around is prune's
    primary job and must still complete even when one run's worktree cleanup hiccups.
    """
    from .errors import GitError
    from .isolation.worktrees import WorktreeManager

    repos = _discover_run_worktree_repos(workspace_root, run_id)
    if not repos:
        return 0
    manager = WorktreeManager(workspace_root, run_id, repos, integration_heads={})
    try:
        manager.gc_run(run_id)
    except GitError as exc:
        typer.echo(f"WARNING: worktree GC for run {run_id!r} failed: {exc}", err=True)
        return 0
    return len(repos)


def _preview_run_worktrees(workspace_root: str, run_id: str) -> tuple[list[str], list[str]]:
    """Read-only preview of exactly what `_gc_run_worktrees` would remove for *run_id*:
    worktree paths under the run's own prefix, plus its `ao/`-namespaced branch and squash
    refs. Used by `ao prune --dry-run` so a dry run touches nothing.

    A `GitError` for one repo is reported as a warning and skipped (that repo's entries
    are simply omitted from the preview), consistent with `_gc_run_worktrees`'s own
    never-crash-the-whole-command handling.
    """
    from .errors import GitError
    from .isolation.git import GitRepo
    from .isolation.paths import AO_REF_NAMESPACE, sanitize_ref_component, worktree_root_prefix_for

    repos = _discover_run_worktree_repos(workspace_root, run_id)
    if not repos:
        return [], []

    prefix = str(worktree_root_prefix_for(workspace_root, run_id))
    san_run = sanitize_ref_component(run_id)
    branch_prefix = f"refs/heads/{AO_REF_NAMESPACE}/{san_run}/"
    squash_prefix = f"refs/{AO_REF_NAMESPACE}/runs/{san_run}/"

    worktree_paths: set[str] = set()
    ref_names: set[str] = set()
    for repo in repos:
        git = GitRepo(repo.toplevel)
        try:
            for entry in git.worktree_list():
                norm = os.path.normpath(entry.path)
                if norm == prefix or norm.startswith(prefix + os.sep):
                    worktree_paths.add(norm)
            ref_names.update(git.list_refs(branch_prefix))
            ref_names.update(git.list_refs(squash_prefix))
        except GitError as exc:
            typer.echo(
                f"WARNING: could not preview worktrees for run {run_id!r} repo {repo.key!r}: {exc}",
                err=True,
            )
            continue
    return sorted(worktree_paths), sorted(ref_names)


def _worktree_run_ids(workspace_root: str) -> list[str]:
    """Every (sanitized) run_id with at least one worktree directory under this
    workspace's worktree root -- the candidate set `--worktrees-only` filters down to
    orphans (run_ids whose `.orchestrator/runs/<id>` directory no longer exists).

    Run ids this codebase generates (`<workflow_id>-<UTC timestamp>`) sanitize to
    themselves unchanged (`sanitize_ref_component`'s charset already matches), so this
    directory name is also the real run_id in every case the ticket's own test scenarios
    (a crashed run's leftovers) cover.
    """
    root = _isolation_worktree_root(workspace_root)
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def _prune_worktrees_only(workspace_root: str, *, dry_run: bool) -> None:
    """`ao prune --worktrees-only`: reap worktrees/refs whose run directory no longer
    exists, WITHOUT deleting any run directory (HLD §14) -- the leak class left by a
    crashed run (a stray worktree plus its orphaned `ao/`-namespaced branch)."""
    runs_dir = Path(workspace_root) / ".orchestrator" / "runs"
    reaped = 0
    for run_id in _worktree_run_ids(workspace_root):
        if (runs_dir / run_id).exists():
            continue  # owned by a run directory that still exists -- not this sweep's job

        if dry_run:
            wt_paths, refs = _preview_run_worktrees(workspace_root, run_id)
            if not wt_paths and not refs:
                continue
            typer.echo(f"Would reap orphaned worktrees for run {run_id}:")
            for wt in wt_paths:
                typer.echo(f"  worktree: {wt}")
            for ref in refs:
                typer.echo(f"  ref: {ref}")
            reaped += 1
        else:
            n = _gc_run_worktrees(workspace_root, run_id)
            if n:
                typer.echo(f"Reaped orphaned worktrees for run {run_id}: {n} repo(s)")
                reaped += 1

    action = "would be reaped" if dry_run else "reaped"
    typer.echo(f"{reaped} orphaned run(s) {action}")


@app.command()
def prune(
    workspace: str = typer.Option(..., "--workspace", "-w", help="Workspace root to prune"),
    older_than: int = typer.Option(
        7, "--older-than", help="Delete runs older than this many days (0 = all)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print what would be deleted without deleting"
    ),
    worktrees: bool = typer.Option(
        True,
        "--worktrees/--no-worktrees",
        help=(
            "Also garbage-collect a deleted run's isolation worktrees/branches/refs "
            "(E-Wk9Tz3 FR-14) via WorktreeManager.gc_run, scoped to that run only -- never"
            " a blanket `git worktree prune`. Default on; --no-worktrees opts out."
        ),
    ),
    worktrees_only: bool = typer.Option(
        False,
        "--worktrees-only",
        help=(
            "Skip run-directory deletion entirely. Instead reap worktrees/ao-namespaced"
            " refs whose run directory no longer exists (a crashed run's leftovers) --"
            " never deletes a run directory. --older-than/--worktrees are ignored in this"
            " mode."
        ),
    ),
) -> None:
    """Remove stale run artifacts from a workspace.

    Deletes run directories under <workspace>/.orchestrator/runs/ that are older than
    --older-than days, and (by default) that run's isolation worktrees/branches/refs
    (E-Wk9Tz3 FR-14) -- never a foreign worktree, and never via raw git (routed through
    WorktreeManager.gc_run / GitRepo.prune_worktrees_scoped only). Use --dry-run to
    preview; --worktrees-only for a standalone worktree-only reconciliation pass that
    touches no run directory. Analogous to `docker system prune` -- run periodically to
    reclaim disk space.

    LIMITATION: this command takes only --workspace (no reposets/workflow), so the real
    git repo(s) a run's worktrees belong to are discovered by probing the physical
    worktree directories still on disk. A worktree directory removed by something other
    than `ao`/`git worktree remove` (a manual `rm -rf`, a filesystem restore) leaves a
    dangling admin entry in the main repo that IS still reaped, as long as at least one
    OTHER worktree directory for the same run/repo survives to bootstrap discovery. If
    EVERY worktree directory for a run's repo is gone, that repo can no longer be
    discovered at all, and its `ao/`-namespaced branch/refs become permanently unreapable
    by any variant of this command -- reported as "0 orphaned run(s)" with no error.
    """
    import shutil
    import time

    ws_root = str(Path(workspace).resolve())

    if worktrees_only:
        _prune_worktrees_only(ws_root, dry_run=dry_run)
        return

    runs_dir = Path(ws_root) / ".orchestrator" / "runs"
    if not runs_dir.exists():
        typer.echo(f"Nothing to prune: {runs_dir}")
        raise typer.Exit(0)

    now = time.time()
    cutoff = now - older_than * 86400  # 0 days => cutoff == now => all dirs qualify

    candidates = [p for p in runs_dir.iterdir() if p.is_dir()]
    to_delete = [p for p in candidates if older_than == 0 or os.path.getmtime(p) < cutoff]

    for p in to_delete:
        run_id = p.name
        if dry_run:
            typer.echo(f"Would delete: {p}")
            if worktrees:
                wt_paths, refs = _preview_run_worktrees(ws_root, run_id)
                for wt in wt_paths:
                    typer.echo(f"  would remove worktree: {wt}")
                for ref in refs:
                    typer.echo(f"  would delete ref: {ref}")
        else:
            shutil.rmtree(p)
            typer.echo(f"Deleted: {p}")
            if worktrees:
                n = _gc_run_worktrees(ws_root, run_id)
                if n:
                    typer.echo(f"  worktrees GC'd for {run_id}: {n} repo(s)")

    action = "would be deleted" if dry_run else "deleted"
    typer.echo(f"{len(to_delete)} run(s) {action}")


def _resolve_templates_workspace_root(workspace: str | None) -> str:
    """Workspace-root resolution shared by `ao templates`/`ao new`/`ao hotspots`
    (E-Tpl3x9, E-Wk9Tz3).

    Mirrors `ui_cmd`'s own `--workspace`/`AO_WORKSPACE_ROOT`/cwd resolution rather than
    `_load_all`'s reposet-derived one: these commands operate directly on a workspace
    directory, with no workflow/reposets/agents triplet loaded (that only happens
    afterwards, for `--validate-only`/`--run`, via the normal `_load_all` path).
    """
    ws = workspace or os.environ.get("AO_WORKSPACE_ROOT") or os.getcwd()
    return str(Path(ws).resolve())


# Repo label fallback when `--repo` is omitted and the resolved workspace path has no
# usable basename (e.g. the filesystem root) -- named so it is never a bare literal at
# the one call site that needs it.
_DEFAULT_HOTSPOTS_REPO_LABEL = "default"


@app.command(name="hotspots")
def hotspots_cmd(
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Repo/workspace root to scan for churn (default: $AO_WORKSPACE_ROOT or cwd).",
    ),
    repo: str | None = typer.Option(
        None,
        "--repo",
        help="Label for this repo's entry under the output JSON's `repos` map "
        "(default: the resolved workspace directory's name).",
    ),
    since_days: int | None = typer.Option(
        None,
        "--since-days",
        help="Only count commits from this many days back (default: 180; 0 = full "
        "history). Keeps a deleted-long-ago file's old churn from outranking what's "
        "actually hot now.",
    ),
    top: int | None = typer.Option(
        None,
        "--top",
        help="Keep at most this many hotspot entries, ranked by weight, highest first "
        "(default: 40).",
    ),
    output: str | None = typer.Option(
        None,
        "--output",
        help="Where to write the hotspots JSON (default: <workspace>/.ao/hotspots.json).",
    ),
    include_conflicts: bool = typer.Option(
        True,
        "--include-conflicts/--no-include-conflicts",
        help="Fold in integration conflicts observed in this workspace's prior runs "
        "(ground truth, weighted CONFLICT_WEIGHT above raw commit churn).",
    ),
) -> None:
    """Compute the git-churn hotspot signal and write it to .ao/hotspots.json.

    Consumed by soft overlap-aware co-scheduling (`scheduling.overlap.rank_wave`, via
    `isolation.hotspots.load_hotspots`) and by the task-breakdown agent, which declares
    this file as an input so decomposition can steer away from hot files instead of
    guessing. Re-running for a different --repo accumulates into the same output file
    rather than overwriting other repos' previously computed entries; re-running for the
    SAME --repo against unchanged history reproduces the same file (idempotent).

    LIMITATION: raw git-log churn alone is a noisy signal -- a large, frequently touched
    but low-risk file can rank above a small, truly hot one, and a file that WAS a
    hotspot before being deleted no longer is. Mitigated by --since-days windowing,
    dropping paths no longer tracked at HEAD, and --include-conflicts (ground truth from
    actual integration conflicts) -- but treat the result as a hint, never certainty.
    """
    from .errors import GitError
    from .isolation.git import GitRepo
    from .isolation.hotspots import (
        DEFAULT_SINCE_DAYS,
        DEFAULT_TOP_K,
        compute_hotspots,
        load_hotspots,
        merge_hotspots,
        observed_conflicts,
    )
    from .models import DEFAULT_HOTSPOTS_PATH

    since_days = since_days if since_days is not None else DEFAULT_SINCE_DAYS
    top = top if top is not None else DEFAULT_TOP_K

    ws = Path(_resolve_templates_workspace_root(workspace))
    if not ws.is_dir():
        typer.echo(f"ERROR: workspace does not exist or is not a directory: {ws}", err=True)
        raise typer.Exit(1)

    probe = GitRepo.probe(str(ws))
    if probe is None:
        typer.echo(
            f"ERROR: {ws} is not a git repository (or git is unavailable) -- "
            "`ao hotspots` needs real history to compute churn.",
            err=True,
        )
        raise typer.Exit(1)

    repo_id = repo or ws.name or _DEFAULT_HOTSPOTS_REPO_LABEL
    output_path = Path(output).resolve() if output else ws / DEFAULT_HOTSPOTS_PATH

    conflicts = observed_conflicts(str(ws)) if include_conflicts else {}
    git_repo = GitRepo(str(ws))
    try:
        new_hotspots = compute_hotspots(
            repo_id,
            git_repo,
            str(ws),
            since_days=since_days,
            top_k=top,
            conflicts=conflicts,
        )
    except GitError as exc:
        typer.echo(f"ERROR: failed to read git history in {ws}: {exc}", err=True)
        raise typer.Exit(1) from exc

    existing = load_hotspots(str(output_path))
    merged = merge_hotspots(existing, new_hotspots)

    # Atomic write-then-rename (review C-2), mirroring runstate.py's own
    # RunStateStore.save/write_status idiom: a crash/kill mid-write leaves the
    # PREVIOUS file intact rather than a truncated/corrupt one.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(".tmp")
    tmp.write_text(merged.model_dump_json(indent=2) + "\n")
    os.replace(tmp, output_path)

    this_repo = merged.repos.get(repo_id)
    entries = this_repo.entries if this_repo else []
    typer.echo(
        f"Wrote {len(entries)} hotspot entr{'y' if len(entries) == 1 else 'ies'} "
        f"for repo {repo_id!r} to {output_path}"
    )
    if entries:
        top_entry = entries[0]
        typer.echo(
            f"  top: {top_entry.path} (weight={top_entry.weight:g}, "
            f"churn={top_entry.churn}, conflicts={top_entry.conflicts})"
        )


def _parse_param_options(raw: list[str]) -> dict[str, str]:
    """Parse repeated `--param key=value` options into a dict. Raises ValueError on a
    malformed entry (no `=`, or an empty key) so the caller can report a clean CLI error."""
    parsed: dict[str, str] = {}
    for item in raw:
        if "=" not in item:
            raise ValueError(f"--param must be key=value, got {item!r}")
        key, _, value = item.partition("=")
        key = key.strip()
        if not key:
            raise ValueError(f"--param has an empty key: {item!r}")
        parsed[key] = value
    return parsed


@app.command(name="templates")
def templates_cmd(
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace root to discover templates in (default: current directory). "
        "Env: AO_WORKSPACE_ROOT",
    ),
) -> None:
    """List discovered workflow templates (built-in + workspace-registered, E-Tpl3x9)."""
    from .templates import TemplateError, discover_templates

    ws_root = _resolve_templates_workspace_root(workspace)
    cfg = _load_project_config_or_exit()

    try:
        infos = discover_templates(ws_root, cfg)
    except TemplateError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    if not infos:
        typer.echo("No templates discovered.")
        typer.echo(
            "Register one via .ao/config.yaml's `templates:` list, or point `ao new` at a "
            "template directory directly."
        )
        return

    for info in infos:
        typer.echo(f"{info.name}  [{info.source}]")
        if info.description:
            typer.echo(f"  {info.description}")
        if info.required_agents:
            typer.echo(f"  required agents: {', '.join(info.required_agents)}")
        if info.params:
            typer.echo("  params:")
            for p in info.params:
                req = "required" if p.required else "optional"
                extra = f", enum={p.enum}" if p.enum else ""
                default = f", default={p.default!r}" if p.default is not None else ""
                desc = f" — {p.description}" if p.description else ""
                typer.echo(f"    --param {p.name}=<value>  ({req}{extra}{default}){desc}")
        else:
            typer.echo("  params: (none)")
        typer.echo("")


def _invoke_run_in_process(workflow_path: str, reposets: str | None, agents: str | None) -> int:
    """Invoke the existing `run` command in-process for `ao new --run`.

    Deliberately does NOT subprocess to an `ao` binary (the global snapshot can be stale and
    silently ignore features — the exact "routing support" staleness trap new-epic-run.sh's
    `require_routing_support` guards against) and does NOT duplicate `run`'s ~150-line body.
    `typer.main.get_command(app).main(args=[...], standalone_mode=False)` was verified
    directly (manual run against this repo's own example workflow with the fake executor)
    to both suppress `run`'s internal `raise typer.Exit(...)` from propagating as a real
    `SystemExit` AND return its intended exit code, so the caller gets a clean int back.
    """
    import typer.main

    args = ["run", "--workflow", workflow_path]
    if reposets:
        args += ["--reposets", reposets]
    if agents:
        args += ["--agents", agents]
    command = typer.main.get_command(app)
    return int(command.main(args=args, standalone_mode=False))


@app.command(name="new")
def new_cmd(
    template: str = typer.Argument(
        ..., help="Template name (from `ao templates`) or a directory path containing template.yaml"
    ),
    slug_or_id: str = typer.Argument(
        ...,
        help="A bare slug (a random instance id is generated) or a full id already matching "
        "the template's id_pattern",
    ),
    param: list[str] = typer.Option(
        [], "--param", help="Template param as key=value (repeatable)."
    ),
    prompt_file: str | None = typer.Option(
        None,
        "--prompt-file",
        help="Write this file's content into the instance's prompt.md. Conflict-checked "
        "against an existing, differently-edited prompt.md (never silently overwritten).",
    ),
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace root to scaffold into (default: current directory). Must match the "
        "target repo_set's workspace_root for --validate-only/--run to resolve correctly. "
        "Env: AO_WORKSPACE_ROOT",
    ),
    validate_only: bool = typer.Option(
        False,
        "--validate-only",
        help="Scaffold, then `ao validate` the rendered workflow, and stop.",
    ),
    run: bool = typer.Option(
        False,
        "--run",
        help="Scaffold, validate, then run the rendered workflow in-process "
        "(equivalent to `ao run --workflow <rendered>`).",
    ),
    reposets: str | None = typer.Option(
        None, help="Path to reposets config JSON/YAML (used only by --validate-only/--run)"
    ),
    agents: str | None = typer.Option(
        None, help="Path to agents config JSON/YAML (used only by --validate-only/--run)"
    ),
) -> None:
    """Scaffold (and optionally validate/run) a workflow instance from a template (E-Tpl3x9)."""
    from .errors import OrchestratorError
    from .templates import TemplateError, instantiate, load_template

    if validate_only and run:
        typer.echo("ERROR: --validate-only and --run are mutually exclusive", err=True)
        raise typer.Exit(1)

    ws_root = _resolve_templates_workspace_root(workspace)
    cfg = _load_project_config_or_exit()

    try:
        parsed_params = _parse_param_options(param)
    except ValueError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    prompt_text: str | None = None
    if prompt_file is not None:
        try:
            prompt_text = Path(prompt_file).read_text(encoding="utf-8")
        except OSError as e:
            typer.echo(f"ERROR: cannot read --prompt-file {prompt_file}: {e}", err=True)
            raise typer.Exit(1)

    try:
        tmpl = load_template(template, ws_root, cfg)
        result = instantiate(
            tmpl, ws_root, slug_or_id=slug_or_id, params=parsed_params, prompt_text=prompt_text
        )
    except TemplateError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Instance ready: {result.instance_dir}")
    typer.echo(f"  workflow -> {result.workflow_path}")
    if result.prompt_path:
        typer.echo(f"  prompt   -> {result.prompt_path}")
    if result.created:
        typer.echo(f"  created ({len(result.created)}): " + ", ".join(result.created))
    if result.skipped:
        typer.echo(
            f"  skipped ({len(result.skipped)}, already present): " + ", ".join(result.skipped)
        )

    if not validate_only and not run:
        typer.echo("")
        typer.echo("Next steps:")
        step = 1
        if result.prompt_path:
            typer.echo(f"  {step}. Edit your prompt:   $EDITOR {result.prompt_path}")
            step += 1
        typer.echo(f"  {step}. Validate:           ao validate --workflow {result.workflow_path}")
        step += 1
        typer.echo(f"  {step}. Run:                ao run      --workflow {result.workflow_path}")
        return

    try:
        _load_all(result.workflow_path, reposets, agents)
        typer.echo("OK: rendered workflow is valid")
    except OrchestratorError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)
    except SystemExit:
        return

    if not run:
        return

    typer.echo("Running (in-process, equivalent to `ao run --workflow <rendered>`)...")
    exit_code = _invoke_run_in_process(result.workflow_path, reposets, agents)
    raise typer.Exit(exit_code)


if __name__ == "__main__":
    app()
