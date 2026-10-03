"""``DashboardService`` — the whole dashboard API as plain Python (E-Ui7Kq2).

Deliberately free of any web-framework import. The FastAPI layer in ``app.py`` is a thin
adapter that maps HTTP verbs onto these methods, which means:

* every behaviour here is unit-testable without spinning up a server or installing the
  ``[ui]`` extra;
* swapping the transport (a different framework, a TUI, an MCP server) touches one file;
* the pluggability rule in CLAUDE.md holds — ``FileBrowser``, ``RunRepository``, and
  ``ProcessSupervisor`` are injected, so tests substitute fakes instead of monkeypatching.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from ..config import load_agents, load_reposets
from ..errors import OrchestratorError
from ..feedback import (
    FeedbackCapError,
    FeedbackEntry,
    FeedbackError,
    add_feedback,
    effective_for_task,
    effective_ratings,
    load_feedback,
    validate_run_id,
)
from ..implicit_signals import apply_survival, implicit_signals
from ..models import RunState
from ..project_config import ProjectConfig, find_project_config, load_project_config
from ..spec import cross_validate, load_workflow
from ..survival import compute_survival, validate_ref
from ..templates import TemplateError, discover_templates, instantiate
from ..usage import MAX_REPORT_RUN_IDS, build_usage_report, usage_report_payload
from .files import FileBrowser, Root
from .htmlpreview import build_html_preview
from .processes import (
    STATUS_FAILED_TO_START,
    LaunchError,
    LaunchRecord,
    ProcessSupervisor,
)
from .runs import RunNotFoundError, RunRepository

# Extensions scanned when discovering workflow specs on disk.
WORKFLOW_SUFFIXES = (".json", ".yaml", ".yml")

# Directories never descended into when auto-discovering workflow specs. These hold
# thousands of files and no workflows; walking them makes the discovery endpoint slow
# enough to feel broken.
DISCOVERY_SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        ".orchestrator",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".hatch",
    }
)

# Bound on how deep workflow discovery walks below each search root.
DISCOVERY_MAX_DEPTH = 4

# Distinct `DashboardError` message prefixes `create_instance()` uses so `app.py` can map
# status codes unambiguously. A naive "is this message about a missing template" substring
# check on the underlying `TemplateError` text would collide: `templates._render` raises
# "unknown template variable '...' in ..." for a rendering failure (400), which contains
# "unknown template" as a substring of `templates.load_template`'s own "unknown template
# 'x': ..." (404) text. Prefixing deliberately here, at the one call site that knows which
# case it is, sidesteps that collision entirely.
TEMPLATE_NOT_FOUND_PREFIX = "template not found"
PROMPT_CONFLICT_PREFIX = "prompt conflict"

# Template param whose value names a repo set; its choices come from the workspace reposets.
REPO_SET_PARAM = "repo_set"
ENV_REPOSETS = "AO_REPOSETS"
ENV_AGENTS = "AO_AGENTS"

# A "failed to start" launch stays in the runs list's failure strip for this long.
FAILED_LAUNCH_WINDOW_HOURS = 24.0

# Non-alnum run -> single hyphen, for deriving a slug from a free-text prompt (HLD §2.6).
_SLUG_DERIVE_RE = re.compile(r"[^a-z0-9]+")


# Survival shells out to git per run/task. Sync routes already run in Starlette's threadpool
# (never on the event loop), so the bounds here are about not letting a few requests saturate
# the host: at most this many survival computations at once (others get a 429-style busy
# error), and an implicit run-id list is capped to the newest N runs.
MAX_CONCURRENT_SURVIVAL = 2
SURVIVAL_DEFAULT_MAX_RUNS = 50
DASHBOARD_FEEDBACK_SOURCE = "dashboard"


class DashboardError(Exception):
    """Raised for dashboard-level failures that map to a 4xx response."""


class DashboardValidationError(DashboardError):
    """Caller input is invalid (HTTP 400)."""


class DashboardNotFoundError(DashboardError):
    """Unknown run/task (HTTP 404)."""


class DashboardConflictError(DashboardError):
    """State conflict, e.g. feedback entry cap reached (HTTP 409)."""


class DashboardBusyError(DashboardError):
    """Too many expensive computations in flight (HTTP 429)."""


class DashboardStoreError(DashboardError):
    """A server-side store (e.g. corrupt feedback.json) is unusable (HTTP 500)."""


@dataclass(frozen=True)
class WorkflowInfo:
    """A workflow spec discovered on disk, as the launcher form needs it."""

    id: str
    name: str
    path: str
    task_count: int
    prompt_path: str | None
    general_instructions: list[str] = field(default_factory=list)
    error: str | None = None
    """Set when the file parsed as a spec but failed validation — surfaced so a broken
    workflow shows up in the picker with its reason rather than vanishing silently."""


@dataclass(frozen=True)
class WorkspaceInfo:
    """What the dashboard knows about the workspace it is serving."""

    workspace_root: str
    config_path: str | None
    workflow: str | None
    reposets: str | None
    agents: str | None
    general_instructions: list[str]
    roots: list[dict]


def _load_workflow_info(path: Path) -> WorkflowInfo | None:
    """Parse *path* as a workflow spec, returning ``None`` if it clearly is not one.

    Uses a cheap structural check (a mapping with ``id`` and ``tasks``) before full
    validation so the discovery walk is not quadratic in unrelated JSON/YAML files.
    """
    try:
        raw = path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) if path.suffix in (".yaml", ".yml") else json.loads(raw)
    except (OSError, ValueError, yaml.YAMLError):
        return None

    if not isinstance(data, dict) or "tasks" not in data or "id" not in data:
        return None

    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return None

    gi = data.get("general_instructions")
    return WorkflowInfo(
        id=str(data.get("id", "")),
        name=str(data.get("name", "") or data.get("id", "")),
        path=str(path),
        task_count=len(tasks),
        prompt_path=data.get("prompt_path"),
        general_instructions=[str(g) for g in gi] if isinstance(gi, list) else [],
    )


def _derive_slug_from_prompt(prompt: str | None) -> str | None:
    """Kebab-case slug from the first non-empty line of *prompt*, or ``None`` if there is
    no usable text to derive one from (HLD §2.6: "derive a slug from the first prompt
    line").

    Shaped to satisfy the bare-slug pattern ``templates._SLUG_RE`` requires
    (``^[a-z0-9][a-z0-9-]*$``): lowercased, runs of non-alnum characters collapsed to a
    single hyphen, leading/trailing hyphens stripped. A line with no alnum characters at
    all (e.g. "!!!") yields ``None`` rather than an empty string, so the caller's
    "missing slug" error fires instead of `instantiate()` rejecting an empty slug less
    clearly.
    """
    if not prompt:
        return None
    for line in prompt.splitlines():
        stripped = line.strip()
        if stripped:
            return _SLUG_DERIVE_RE.sub("-", stripped.lower()).strip("-") or None
    return None


def _started_after(started_at: str, cutoff: datetime) -> bool:
    """True if the ISO timestamp is at/after *cutoff*; unparseable timestamps are excluded."""
    try:
        started = datetime.fromisoformat(started_at)
    except (TypeError, ValueError):
        return False
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return started >= cutoff


class _SurvivalSlot:
    """Context manager taking a non-blocking slot on the survival semaphore (no-op if None)."""

    def __init__(self, sem: threading.BoundedSemaphore | None) -> None:
        self._sem = sem

    def __enter__(self) -> None:
        if self._sem is not None and not self._sem.acquire(blocking=False):
            raise DashboardBusyError("too many survival computations in progress; retry shortly")

    def __exit__(self, *exc: object) -> None:
        if self._sem is not None:
            self._sem.release()


class DashboardService:
    """Backend for the browser dashboard.

    Args:
        workspace_root: Root of the workspace whose runs and files are served.
        browser: File browser. Defaults to one rooted at *workspace_root*.
        repository: Run repository. Defaults to one rooted at *workspace_root*.
        supervisor: Process supervisor. Defaults to one rooted at *workspace_root*.
        project_config: Pre-loaded project config. Defaults to discovery from
            *workspace_root*.
        config_path: Path the config was loaded from, for display.
        search_roots: Directories scanned for workflow specs. Defaults to *workspace_root*.
        clock: Time source for launch-age filtering. Defaults to the wall clock.
    """

    def __init__(
        self,
        workspace_root: str,
        browser: FileBrowser | None = None,
        repository: RunRepository | None = None,
        supervisor: ProcessSupervisor | None = None,
        project_config: ProjectConfig | None = None,
        config_path: str | None = None,
        search_roots: list[str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._clock_now: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))
        self.workspace_root = str(Path(workspace_root).resolve())
        self._browser = browser or FileBrowser(
            roots=[Root(name="workspace", path=self.workspace_root, role="workspace")]
        )
        self._repo = repository or RunRepository(self.workspace_root)
        self._survival_sem = threading.BoundedSemaphore(MAX_CONCURRENT_SURVIVAL)
        self._supervisor = supervisor or ProcessSupervisor(self.workspace_root)

        if project_config is None:
            found = find_project_config(Path(self.workspace_root))
            if found is not None:
                try:
                    project_config = load_project_config(found)
                    config_path = config_path or str(found)
                except Exception:
                    project_config = None
        self._config = project_config
        self._config_path = config_path
        self._search_roots = [
            str(Path(p).resolve()) for p in (search_roots or [self.workspace_root])
        ]

    # -- workspace -------------------------------------------------------------

    def workspace_info(self) -> WorkspaceInfo:
        """Summarize the served workspace, including the effective general instructions."""
        cfg = self._config
        return WorkspaceInfo(
            workspace_root=self.workspace_root,
            config_path=self._config_path,
            workflow=cfg.workflow if cfg else None,
            reposets=cfg.reposets if cfg else None,
            agents=cfg.agents if cfg else None,
            general_instructions=list(cfg.general_instructions) if cfg else [],
            roots=[asdict(r) for r in self._browser.roots],
        )

    def general_instructions(self) -> list[dict]:
        """Effective workspace-scoped general instructions, with existence flags.

        Read-only view of the "defined once per workspace" layer (FR-GI1). Reporting
        ``exists`` matters because a typo'd path is otherwise invisible: the engine drops
        unresolvable general instructions with a log warning and carries on, so the
        dashboard is where an operator finds out.
        """
        cfg = self._config
        paths = list(cfg.general_instructions) if cfg else []
        out: list[dict] = []
        for p in paths:
            candidate = Path(p)
            if not candidate.is_absolute():
                candidate = Path(self.workspace_root) / p
            out.append({"path": p, "resolved": str(candidate), "exists": candidate.is_file()})
        return out

    # -- file browsing ---------------------------------------------------------

    def list_dir(self, root: str | None = None, path: str = "") -> dict:
        """Directory listing, including hidden and binary entries (FR-B1)."""
        entries = self._browser.list_dir(root, path)
        resolved_root, resolved_path = self._browser.resolve(root, path)
        return {
            "root": resolved_root.name,
            "path": self._browser.relative(resolved_root, resolved_path),
            "absolute": str(resolved_path),
            "entries": [asdict(e) for e in entries],
        }

    def read_file(self, root: str | None = None, path: str = "") -> dict:
        """File contents for the code viewer (FR-B2)."""
        return asdict(self._browser.read_file(root, path))

    def read_html_preview(self, root: str | None = None, path: str = "") -> dict:
        """Sanitized, self-contained HTML/SVG preview safe for an `iframe srcdoc`.

        Thin pass-through to `htmlpreview.build_html_preview` — see that module for the
        sanitizer itself; this method exists only so `app.py` (like every other route)
        talks to the framework-free service layer rather than a sanitizer module directly.
        """
        return asdict(build_html_preview(self._browser, root, path))

    # -- workflows -------------------------------------------------------------

    def list_workflows(self) -> list[WorkflowInfo]:
        """Discover workflow specs under the search roots, plus the configured one.

        The configured workflow is always included even if it lives outside the search
        roots, since that is the one an operator most likely wants to launch.
        """
        found: dict[str, WorkflowInfo] = {}

        for root in self._search_roots:
            for path in self._walk_specs(Path(root)):
                info = _load_workflow_info(path)
                if info is not None:
                    found.setdefault(info.path, info)

        if self._config and self._config.workflow:
            configured = Path(self._config.workflow)
            if configured.is_file() and str(configured) not in found:
                info = _load_workflow_info(configured)
                if info is not None:
                    found[info.path] = info

        return sorted(found.values(), key=lambda w: (w.id, w.path))

    def _walk_specs(self, root: Path) -> list[Path]:
        """Depth-bounded walk yielding candidate spec files under *root*."""
        if not root.is_dir():
            return []
        out: list[Path] = []
        root_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            current = Path(dirpath)
            if len(current.parts) - root_depth >= DISCOVERY_MAX_DEPTH:
                dirnames[:] = []
            # Prune in place so os.walk never descends into the skipped trees at all.
            dirnames[:] = [d for d in dirnames if d not in DISCOVERY_SKIP_DIRS]
            for name in filenames:
                if name.endswith(WORKFLOW_SUFFIXES):
                    out.append(current / name)
        return out

    # -- templates (E-Tpl3x9, HLD §2.6) -----------------------------------------

    def list_templates(self) -> list[dict]:
        """Discovered templates (built-in + workspace-registered) for the template picker."""
        try:
            infos = discover_templates(self.workspace_root, self._config)
        except TemplateError as exc:
            raise DashboardError(str(exc)) from exc
        names = self._reposet_names()
        out = [asdict(info) for info in infos]
        if names:
            for entry in out:
                for param in entry["params"]:
                    if param["name"] == REPO_SET_PARAM and not param["enum"]:
                        # Offer the workspace's real repo sets instead of free text; a
                        # declared default that is not one of them would pre-select a value
                        # the engine rejects, so it is dropped.
                        param["enum"] = list(names)
                        if param["default"] not in names:
                            param["default"] = None
        return out

    # -- launch preflight (root-cause prevention) -------------------------------

    def _spec_path(self, configured: str | None, env_name: str) -> str | None:
        """Reposets/agents path as the spawned ``ao run`` will resolve it.

        The launcher passes the project-config path as an explicit flag, which outranks
        the env var in ``cli._resolve_config_defaults`` -- same order here.
        """
        return configured or os.environ.get(env_name) or None

    def _reposet_names(self) -> list[str] | None:
        """Sorted repo-set names from the workspace reposets, or None when unreadable."""
        path = self._spec_path(self._config.reposets if self._config else None, ENV_REPOSETS)
        if not path:
            return None
        try:
            return sorted(load_reposets(path))
        except (OrchestratorError, OSError, ValueError, TypeError, AttributeError):
            return None

    def _check_repo_set(self, repo_set: str) -> None:
        """Reject *repo_set* with the valid names listed, when the reposets are readable."""
        names = self._reposet_names()
        if names is not None and repo_set not in names:
            raise DashboardValidationError(
                f"Unknown repo_set {repo_set}; available: {', '.join(names) or '(none)'}"
            )

    def _preflight(self, workflow_path: str) -> None:
        """Validate *workflow_path* against the workspace reposets/agents before spawning.

        Uses the engine's own loaders and ``cross_validate`` so a spec the engine would
        refuse is refused here with a precise 4xx instead of as a process that dies a
        second later. Only the repo-set check is re-phrased (to list valid names).
        Skipped when no reposets file is configured at all -- the engine then reports the
        missing configuration itself, which this layer should not second-guess.
        """
        reposets_path = self._spec_path(
            self._config.reposets if self._config else None, ENV_REPOSETS
        )
        if not reposets_path:
            return
        try:
            workflow = load_workflow(workflow_path)
            reposets = load_reposets(reposets_path)
        except (OrchestratorError, OSError, ValueError) as exc:
            raise DashboardValidationError(f"cannot launch {workflow_path}: {exc}") from exc

        if workflow.repo_set not in reposets:
            raise DashboardValidationError(
                f"Unknown repo_set {workflow.repo_set}; available: "
                f"{', '.join(sorted(reposets)) or '(none)'} (reposets file: {reposets_path})"
            )

        agents_path = self._spec_path(self._config.agents if self._config else None, ENV_AGENTS)
        if not agents_path:
            return
        try:
            cross_validate(workflow, reposets, load_agents(agents_path))
        except (OrchestratorError, OSError, ValueError) as exc:
            raise DashboardValidationError(f"cannot launch {workflow_path}: {exc}") from exc

    def create_instance(
        self,
        name: str,
        slug_or_id: str | None = None,
        params: dict[str, str] | None = None,
        prompt: str | None = None,
        start: bool = False,
        options: dict | None = None,
    ) -> dict:
        """Scaffold (and optionally launch) a run instance from template *name* (HLD §2.6).

        Mirrors ``ao new`` (``cli.py``) but resolves the slug from the prompt's first line
        when the caller doesn't supply one, and — when *start* is True — launches through
        the same :class:`ProcessSupervisor` path :meth:`start_run` uses. ``instantiate()``
        below already writes *prompt* into the instance's ``prompt.md``, so *prompt* is
        deliberately NOT forwarded to ``launch_run`` — that would write it a second time,
        through a separate launch-scoped prompt file, over a prompt.md the caller may have
        already asked to keep (`keep_existing`).

        *name* is resolved ONLY against ``discover_templates()``'s vetted list — the exact
        same list ``list_templates()``/``GET /api/templates`` returns — never through
        ``templates.load_template()``'s ad-hoc-filesystem-path branch (M1 fix). That
        ad-hoc-path affordance (``ao new /path/to/template ...``) is a deliberate CLI-only
        convenience (HLD §2.1 point 3 / §2.5); the HTTP surface must not inherit it, since
        that would let an HTTP caller instantiate an unregistered, un-vetted template
        directory the server process merely happens to be able to read, wider than
        anything the dashboard UI ever shows.
        """
        try:
            discovered = discover_templates(self.workspace_root, self._config)
        except TemplateError as exc:
            raise DashboardError(str(exc)) from exc

        template = next((t for t in discovered if t.name == name), None)
        if template is None:
            raise DashboardError(
                f"{TEMPLATE_NOT_FOUND_PREFIX}: {name!r} (not one of the discovered "
                "templates; see GET /api/templates -- ad-hoc filesystem paths are a "
                "CLI-only affordance, not available here)"
            )

        resolved_slug = (slug_or_id or "").strip() or _derive_slug_from_prompt(prompt)
        if not resolved_slug:
            raise DashboardError(
                "no slug_or_id given and no prompt to derive one from — provide at least one"
            )

        prompt_text = prompt if prompt and prompt.strip() else None

        # Fail before scaffolding anything: a bad repo set would otherwise leave a
        # half-useful instance dir behind and only surface when the run process dies.
        chosen_repo_set = (params or {}).get(REPO_SET_PARAM, "").strip()
        if chosen_repo_set:
            self._check_repo_set(chosen_repo_set)

        try:
            result = instantiate(
                template,
                self.workspace_root,
                slug_or_id=resolved_slug,
                params=params or {},
                prompt_text=prompt_text,
            )
        except TemplateError as exc:
            # `instantiate()` already raises its prompt-conflict TemplateError with a
            # message starting with `PROMPT_CONFLICT_PREFIX` (see `templates._write_prompt_text`),
            # so no re-prefixing is needed here — passed through as-is.
            raise DashboardError(str(exc)) from exc

        workflow_info = _load_workflow_info(Path(result.workflow_path))
        if workflow_info is None:
            raise DashboardError(f"rendered workflow is not a valid spec: {result.workflow_path}")

        launch: dict | None = None
        if start:
            self._preflight(result.workflow_path)
            try:
                record = self._supervisor.launch_run(
                    workflow_path=result.workflow_path,
                    reposets=self._config.reposets if self._config else None,
                    agents=self._config.agents if self._config else None,
                    options=options or {},
                )
            except LaunchError as exc:
                raise DashboardError(str(exc)) from exc
            launch = self._supervisor.describe(record, include_log=True)

        return {
            "instance_dir": result.instance_dir,
            "workflow_path": result.workflow_path,
            "workflow": asdict(workflow_info),
            "launch": launch,
        }

    # -- runs ------------------------------------------------------------------

    def list_runs(self) -> list[dict]:
        """All runs in the workspace, newest-first, annotated with live-process state."""
        self._supervisor.reconcile()
        rows: list[dict] = []
        for summary in self._repo.list_summaries():
            row = asdict(summary)
            row["is_live"] = self._supervisor.is_running(summary.run_id)
            record = self._supervisor.record_for_run(summary.run_id)
            row["launch_id"] = record.launch_id if record else None
            rows.append(row)
        return rows

    def run_detail(self, run_id: str) -> dict:
        """Full detail for one run (FR-R3, FR-R5.2)."""
        try:
            detail = self._repo.detail(run_id)
        except RunNotFoundError as exc:
            raise DashboardError(f"run not found: {run_id}") from exc

        record = self._supervisor.record_for_run(run_id)
        payload = asdict(detail)
        payload["is_live"] = self._supervisor.is_running(run_id)
        payload["launch"] = self._supervisor.describe(record) if record else None
        return payload

    def run_graph(self, run_id: str) -> dict:
        """The run's dependency/spawn graph for the Graph tab (T-AsQ77e, HLD §8.4/§14.2).

        ``RunRepository.load_graph`` already turns every degraded case short of "no
        such run" into a 200-shaped ``RunGraph`` (``source="unavailable"`` plus
        ``warnings``) -- this method's only job is the same ``RunNotFoundError`` ->
        ``DashboardError`` translation every other run-scoped method here does
        (``run_detail``, ``delete_run``), so `app.py` maps it to 404 exactly like them.
        There is no launch-record fallback here; an unreadable/missing run is a 404,
        never a 5xx.
        """
        try:
            graph = self._repo.load_graph(run_id)
        except RunNotFoundError as exc:
            raise DashboardError(f"run not found: {run_id}") from exc
        return asdict(graph)

    def run_activity(self, run_id: str) -> dict:
        """Live per-task activity (turns/tokens/last action/stuck) for the run page."""
        try:
            activity = self._repo.load_activity(run_id)
        except RunNotFoundError as exc:
            raise DashboardError(f"run not found: {run_id}") from exc
        return asdict(activity)

    def run_summary(self, run_id: str) -> dict:
        """Live Haiku digest of an expensive run (written by the engine, see summarizer.py)."""
        from ..summarizer import read_summary

        try:
            run_dir = self._repo.run_dir(run_id)
        except RunNotFoundError as exc:
            raise DashboardError(f"run not found: {run_id}") from exc
        return {"run_id": run_id, **read_summary(run_dir)}

    def run_stats(self) -> dict:
        """Aggregate stats across all runs (FR-R5.1)."""
        return asdict(self._repo.aggregate())

    def delete_run(self, run_id: str) -> dict:
        """Delete a run's artifacts (FR-R4).

        Refuses while the dashboard's own process for that run is alive: deleting the
        directory the engine is actively writing to would leave a half-run on disk and a
        confusing crash in the child. Cancel first, then delete.
        """
        if self._supervisor.is_running(run_id):
            raise DashboardError(f"run {run_id} is still running — cancel it before deleting.")
        try:
            self._repo.delete(run_id)
        except RunNotFoundError as exc:
            raise DashboardError(f"run not found: {run_id}") from exc
        return {"deleted": run_id}

    def run_log(self, run_id: str, max_bytes: int = 200_000) -> dict:
        """Tail of the CLI output for a dashboard-launched run."""
        record = self._supervisor.record_for_run(run_id)
        if record is None:
            return {"run_id": run_id, "launch_id": None, "text": ""}
        return {
            "run_id": run_id,
            "launch_id": record.launch_id,
            "text": self._supervisor.read_log(record.launch_id, max_bytes=max_bytes),
        }

    # -- run control -----------------------------------------------------------

    def start_run(
        self,
        workflow_path: str | None = None,
        prompt: str | None = None,
        options: dict | None = None,
    ) -> dict:
        """Launch a new run, optionally with a prompt typed into the UI (FR-R1).

        The prompt lands in the workflow's declared ``prompt_path`` — the same per-run
        input an operator would pass with ``ao run --prompt``. A prompt supplied for a
        workflow that declares no ``prompt_path`` is rejected here rather than silently
        dropped, so the operator finds out immediately instead of after paying for a run
        that ignored what they typed.
        """
        workflow_path = workflow_path or (self._config.workflow if self._config else None)
        if not workflow_path:
            raise DashboardError("no workflow specified and none configured in .ao/config.yaml")
        if not Path(workflow_path).is_file():
            raise DashboardError(f"workflow spec not found: {workflow_path}")

        if prompt and prompt.strip():
            info = _load_workflow_info(Path(workflow_path))
            if info is None:
                raise DashboardError(f"not a valid workflow spec: {workflow_path}")
            if not info.prompt_path:
                raise DashboardError(
                    f"workflow '{info.id}' declares no `prompt_path`, so there is nowhere to "
                    "put the prompt. Add a `prompt_path` to the spec and list that same path "
                    "in the inputs of the task that should consume it."
                )

        self._preflight(workflow_path)
        try:
            record = self._supervisor.launch_run(
                workflow_path=workflow_path,
                prompt=prompt,
                reposets=self._config.reposets if self._config else None,
                agents=self._config.agents if self._config else None,
                options=options or {},
            )
        except LaunchError as exc:
            raise DashboardError(str(exc)) from exc
        return self._supervisor.describe(record, include_log=True)

    def resume_run(self, run_id: str, options: dict | None = None) -> dict:
        """Resume an interrupted run (FR-R2)."""
        if not self._repo.exists(run_id):
            raise DashboardError(f"run not found: {run_id}")
        if self._supervisor.is_running(run_id):
            raise DashboardError(f"run {run_id} is already running")

        workflow_path = self._workflow_for_run(run_id)
        try:
            record = self._supervisor.launch_resume(
                run_id,
                workflow_path=workflow_path,
                reposets=self._config.reposets if self._config else None,
                agents=self._config.agents if self._config else None,
                options=options or {},
            )
        except LaunchError as exc:
            raise DashboardError(str(exc)) from exc
        return self._supervisor.describe(record, include_log=True)

    def _workflow_for_run(self, run_id: str) -> str | None:
        """Best guess at the workflow spec a run used.

        Prefers the spec path recorded on the original launch (exact), then falls back to
        the configured workflow. ``ao resume`` re-loads the spec, so getting this wrong
        surfaces as a clear CLI error in the run log rather than silent misbehaviour.
        """
        record = self._supervisor.record_for_run(run_id)
        if record and record.workflow_path:
            return record.workflow_path
        return self._config.workflow if self._config else None

    def cancel_run(self, run_id: str) -> dict:
        """Cancel a running run and mark its persisted state ``cancelled`` (FR-R2).

        Two steps, in this order: kill the process group, then rewrite the run state. The
        engine cannot record its own cancellation once it has been signalled, so if the
        dashboard skipped the second step the run would sit at ``running`` forever and
        would never be resumable or deletable.
        """
        try:
            record = self._supervisor.cancel(run_id)
        except LaunchError as exc:
            raise DashboardError(str(exc)) from exc

        self._mark_cancelled(run_id)
        return record.to_dict()

    def _mark_cancelled(self, run_id: str) -> None:
        """Persist ``status="cancelled"`` for *run_id*, keeping status.json consistent.

        Goes through ``RunStateStore.save`` rather than writing state.json directly so the
        derived status.json snapshot is regenerated in the same call — the two files are
        explicitly required never to diverge (ADR-002).
        """
        from ..artifacts import LocalFsArtifactStore
        from ..runstate import RunStateStore

        try:
            state: RunState = self._repo.load_state(run_id)
        except RunNotFoundError:
            return

        state.status = "cancelled"
        # Any task still marked running was killed with the process; leaving it "running"
        # would make the run look live forever in every downstream view.
        for ts in state.tasks.values():
            if ts.status == "running":
                ts.status = "cancelled"

        store = LocalFsArtifactStore(self.workspace_root)
        RunStateStore(self.workspace_root, store).save(state)

    # -- launches --------------------------------------------------------------

    # -- usage / feedback / signals (E-Us9Kd4) ---------------------------------------

    def usage_report(
        self,
        run_ids: list[str] | None = None,
        *,
        survival: bool = False,
        ref: str | None = None,
    ) -> dict:
        """The shared usage report (same builder + payload as `ao report-usage --json`)."""
        ids = list(dict.fromkeys(run_ids or []))  # de-dupe, keep order
        if len(ids) > MAX_REPORT_RUN_IDS:
            raise DashboardValidationError(f"too many run ids ({len(ids)} > {MAX_REPORT_RUN_IDS})")
        for rid in ids:
            self._checked_run_id(rid)
        self._check_ref(ref, survival)
        capped = False
        if survival and not ids:
            all_ids = self._repo.list_run_ids()  # newest first
            capped = len(all_ids) > SURVIVAL_DEFAULT_MAX_RUNS
            ids = all_ids[:SURVIVAL_DEFAULT_MAX_RUNS]
        with self._survival_slot(survival):
            try:
                report = build_usage_report(
                    self.workspace_root, ids or None, with_survival=survival, ref=ref
                )
            except ValueError as exc:
                raise DashboardValidationError(str(exc)) from exc
        payload = usage_report_payload(report)
        payload["survival_runs_capped"] = capped
        return payload

    def get_feedback(self, run_id: str) -> dict:
        """Entries (history), effective entries, and each task's effective rating."""
        state = self._known_run_state(run_id)
        try:
            entries = load_feedback(self.workspace_root, run_id).entries
        except FeedbackError as exc:
            raise self._map_feedback_error(exc) from exc
        return self._feedback_view(run_id, state, entries)

    def add_run_feedback(
        self,
        run_id: str,
        *,
        scope: str,
        rating: str,
        reasons: list[str],
        note: str | None = None,
        task_id: str | None = None,
    ) -> dict:
        """Append one dashboard-sourced entry via the shared `feedback.add_feedback`."""
        state = self._known_run_state(run_id)
        try:
            entry = add_feedback(
                self.workspace_root,
                run_id,
                scope=scope,
                rating=rating,
                reasons=reasons,
                note=note,
                task_id=task_id,
                source=DASHBOARD_FEEDBACK_SOURCE,
            )
            entries = load_feedback(self.workspace_root, run_id).entries
        except FeedbackError as exc:
            raise self._map_feedback_error(exc) from exc
        return {
            "entry": entry.model_dump(),
            "feedback": self._feedback_view(run_id, state, entries),
        }

    def run_signals(self, run_id: str, *, survival: bool = False, ref: str | None = None) -> dict:
        """Implicit signals plus (on demand) diff survival for one run."""
        state = self._known_run_state(run_id)
        self._check_ref(ref, survival)
        survival_part: dict = {
            "requested": survival,
            "available": False,
            "reason": None,
            "ref": ref,
            "total": None,
            "tasks": [],
        }
        with self._survival_slot(survival):
            sig = implicit_signals(state, self.workspace_root)
            if survival:
                report = compute_survival([state], self.workspace_root, ref=ref)
                run = report.runs[0] if report.runs else None
                reason = report.unavailable or (run.unavailable if run else "no survival result")
                survival_part["reason"] = reason
                if run is not None and not reason:
                    survival_part["available"] = True
                    survival_part["total"] = run.total.model_dump()
                    survival_part["tasks"] = [t.model_dump() for t in run.tasks]
                    apply_survival(sig, run)
        return {"run_id": run_id, "signals": sig.model_dump(), "survival": survival_part}

    def _checked_run_id(self, run_id: str) -> str:
        try:
            return validate_run_id(run_id)
        except FeedbackError as exc:
            raise DashboardValidationError(str(exc)) from exc

    def _known_run_state(self, run_id: str) -> RunState:
        self._checked_run_id(run_id)
        try:
            return self._repo.load_state(run_id)
        except RunNotFoundError as exc:
            raise DashboardNotFoundError(f"run not found: {run_id!r}") from exc

    @staticmethod
    def _check_ref(ref: str | None, survival: bool) -> None:
        # Mirrors `ao report-usage`: --ref only means something with survival.
        if ref is None:
            return
        if not survival:
            raise DashboardValidationError("ref requires survival=true")
        err = validate_ref(ref)
        if err:
            raise DashboardValidationError(f"invalid ref: {err}")

    @staticmethod
    def _map_feedback_error(exc: FeedbackError) -> DashboardError:
        message = str(exc)
        if isinstance(exc, FeedbackCapError):
            return DashboardConflictError(message)
        if "unknown task" in message or "run not found" in message:
            return DashboardNotFoundError(message)
        if message.startswith("corrupt feedback file") or "cannot read run state" in message:
            return DashboardStoreError(message)
        return DashboardValidationError(message)

    @staticmethod
    def _feedback_view(run_id: str, state: RunState, entries: list[FeedbackEntry]) -> dict:
        tasks: dict[str, dict | None] = {}
        for tid in state.tasks:
            eff = effective_for_task(entries, tid)
            tasks[tid] = eff.model_dump() if eff else None
        return {
            "run_id": run_id,
            "entries": [e.model_dump() for e in entries],
            "effective": [e.model_dump() for e in effective_ratings(entries).values()],
            "tasks": tasks,
        }

    def _survival_slot(self, survival: bool) -> _SurvivalSlot:
        return _SurvivalSlot(self._survival_sem if survival else None)

    def list_launches(
        self, status: str | None = None, since_hours: float | None = None
    ) -> list[dict]:
        """Dashboard-initiated launches (newest first), reconciled against live PIDs.

        Each row carries the derived ``status``. *status* filters on it; *since_hours*
        keeps launches that started within that many hours. For the failure strip
        (``status=failed_to_start``) cancelled launches are excluded -- an operator who
        cancelled a launch does not need to be told it did not start.
        """
        rows = [self._supervisor.describe(r) for r in self._supervisor.reconcile()]
        if status:
            rows = [r for r in rows if r["status"] == status]
            if status == STATUS_FAILED_TO_START:
                rows = [r for r in rows if not r["cancelled"] and r["exit_code"] != 0]
        if since_hours is not None:
            cutoff = self._clock_now() - timedelta(hours=since_hours)
            rows = [r for r in rows if _started_after(r["started_at"], cutoff)]
        return rows

    def get_launch(self, launch_id: str) -> dict:
        """One launch with its bounded log tail (404 for an unknown or malformed id)."""
        record = self._supervisor.get_launch(launch_id)
        if record is None:
            raise DashboardNotFoundError(f"launch not found: {launch_id}")
        return self._supervisor.describe(record, include_log=True)


__all__ = [
    "PROMPT_CONFLICT_PREFIX",
    "TEMPLATE_NOT_FOUND_PREFIX",
    "DashboardError",
    "DashboardService",
    "LaunchRecord",
    "WorkflowInfo",
    "WorkspaceInfo",
]
