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
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from ..models import RunState
from ..project_config import ProjectConfig, find_project_config, load_project_config
from .files import FileBrowser, Root
from .processes import LaunchError, LaunchRecord, ProcessSupervisor
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


class DashboardError(Exception):
    """Raised for dashboard-level failures that map to a 4xx response."""


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
    ) -> None:
        self.workspace_root = str(Path(workspace_root).resolve())
        self._browser = browser or FileBrowser(
            roots=[Root(name="workspace", path=self.workspace_root, role="workspace")]
        )
        self._repo = repository or RunRepository(self.workspace_root)
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
        payload["launch"] = record.to_dict() if record else None
        return payload

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
        return record.to_dict()

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
        return record.to_dict()

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

    def list_launches(self) -> list[dict]:
        """Every dashboard-initiated launch, reconciled against live PIDs."""
        return [r.to_dict() for r in self._supervisor.reconcile()]


__all__ = [
    "DashboardError",
    "DashboardService",
    "LaunchRecord",
    "WorkflowInfo",
    "WorkspaceInfo",
]
