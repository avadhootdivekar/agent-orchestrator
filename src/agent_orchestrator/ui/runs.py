"""Run discovery, per-run detail, aggregate stats, and deletion (E-Ui7Kq2 FR-R3..FR-R5).

Reads the run directories the engine already writes under
``<workspace>/.orchestrator/runs/<run_id>/`` — this module adds **no new persisted state**.
Every number the dashboard reports is derived from ``state.json`` on demand, for the same
reason ``models.compute_run_usage_totals`` is derived rather than stored: a separately
maintained counter can drift from the audit trail, a pure function cannot.

``status.json`` is preferred where it exists (the engine writes it as a cheap snapshot next
to ``state.json``), with ``state.json`` as the authoritative fallback.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from pathlib import Path

from ..models import RunState, compute_run_active_seconds, compute_run_usage_totals

# Layout the engine writes; kept as named constants so the dashboard and the engine cannot
# drift on where runs live.
ORCHESTRATOR_DIR = ".orchestrator"
RUNS_DIR = "runs"
STATE_FILE = "state.json"
STATUS_FILE = "status.json"

# Terminal run statuses — a run in any other status is still considered live and is
# therefore resumable/cancellable from the UI.
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


class RunNotFoundError(Exception):
    """Raised when a run id has no directory under the workspace."""


@dataclass(frozen=True)
class TaskStat:
    """Per-task row in a run detail view."""

    id: str
    status: str
    attempts: int
    started_at: str | None
    ended_at: str | None
    duration_seconds: float | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    origin: str
    route: str | None
    output_artifact_path: str | None
    outputs: list[str] = field(default_factory=list)
    # E-Wk9Tz3 T-Cx4Jf1 AC-9/S-5: the "Integration" column. `None`/0 for a task that was
    # never isolated (no `task_integration` entry), so a pre-epic run renders blank rather
    # than inventing a status it never had.
    integration_status: str | None = None
    tier_reached: str | None = None
    conflicted_count: int = 0


@dataclass(frozen=True)
class RunIntegration:
    """Run-wide integration header line (E-Wk9Tz3 T-Cx4Jf1 AC-9/S-5).

    Present on `RunDetail` only when the run actually activated isolation; `None`
    otherwise, so the dashboard has a single "is there anything to show" test rather than
    having to distinguish "no isolation" from "isolation with an empty branch".

    `tier_counts` is the S-5 signal: a `rerere`/`mechanical` resolution lands at the same
    zero-review tier as a clean auto-merge, so the only way an operator can see how much of
    a run was absorbed by the non-free tiers is this histogram.
    """

    active: bool
    branch: str | None
    heads: dict[str, str]
    tier_counts: dict[str, int]
    degraded_reason: str | None


@dataclass(frozen=True)
class RunSummary:
    """One row in the runs list (FR-R3)."""

    run_id: str
    workflow_id: str
    status: str
    started_at: str
    updated_at: str
    task_count: int
    task_counts: dict[str, int]
    cost_usd: float
    input_tokens: int
    output_tokens: int
    wall_seconds: float
    active_seconds: float
    is_terminal: bool


@dataclass(frozen=True)
class RunDetail:
    """Full per-run view (FR-R5.2): stats, tasks, breakers, routing."""

    summary: RunSummary
    tasks: list[TaskStat]
    tripped_breakers: list[dict]
    route_decisions: dict[str, list[str]]
    monitor_decisions: list[dict]
    run_dir: str
    # `None` for every run without isolation (E-Wk9Tz3 NFR-2: a pre-epic run's payload
    # gains one null key and renders exactly as it did before).
    integration: RunIntegration | None = None


@dataclass(frozen=True)
class AggregateStats:
    """Totals across every run in the workspace (FR-R5.1)."""

    total_runs: int
    runs_by_status: dict[str, int]
    total_tasks: int
    tasks_by_status: dict[str, int]
    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    total_wall_seconds: float
    total_active_seconds: float


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _wall_seconds(state: RunState) -> float:
    """Elapsed wall-clock time for the run, in seconds.

    Uses ``updated_at`` as the end marker: for a finished run it is the moment of the final
    save, and for a live one it is the last heartbeat — which is the honest answer to "how
    long has this been going" without the dashboard needing a clock of its own (and without
    a server-clock/run-clock skew producing negative durations).
    """
    started = _parse_iso(state.started_at)
    updated = _parse_iso(state.updated_at)
    if started is None or updated is None:
        return 0.0
    return max(0.0, (updated - started).total_seconds())


class RunRepository:
    """Reads, summarizes, and deletes runs under one workspace root."""

    def __init__(self, workspace_root: str) -> None:
        self._root = Path(workspace_root).resolve()

    @property
    def runs_dir(self) -> Path:
        return self._root / ORCHESTRATOR_DIR / RUNS_DIR

    def run_dir(self, run_id: str) -> Path:
        """Return the directory for *run_id*, refusing ids that escape the runs dir.

        ``run_id`` reaches this from an HTTP path segment, so it is treated as untrusted:
        an id like ``../../etc`` must not resolve to a deletable directory.
        """
        candidate = (self.runs_dir / run_id).resolve()
        if candidate != self.runs_dir and self.runs_dir.resolve() not in candidate.parents:
            raise RunNotFoundError(run_id)
        return candidate

    def exists(self, run_id: str) -> bool:
        try:
            return self.run_dir(run_id).is_dir()
        except RunNotFoundError:
            return False

    def list_run_ids(self) -> list[str]:
        """Run ids present on disk, newest-first by directory mtime."""
        if not self.runs_dir.is_dir():
            return []
        dirs = [p for p in self.runs_dir.iterdir() if p.is_dir()]
        dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return [p.name for p in dirs]

    def load_state(self, run_id: str) -> RunState:
        """Load and validate ``state.json`` for *run_id*.

        Raises:
            RunNotFoundError: If the run directory or its state file is missing/unreadable.
        """
        path = self.run_dir(run_id) / STATE_FILE
        if not path.is_file():
            raise RunNotFoundError(run_id)
        try:
            return RunState.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RunNotFoundError(f"{run_id}: unreadable state ({exc})") from exc

    def summarize(self, run_id: str) -> RunSummary:
        """Build the list-row summary for *run_id*."""
        state = self.load_state(run_id)
        return self._summarize_state(run_id, state)

    def _summarize_state(self, run_id: str, state: RunState) -> RunSummary:
        totals = compute_run_usage_totals(state)
        counts: dict[str, int] = {}
        for ts in state.tasks.values():
            counts[ts.status] = counts.get(ts.status, 0) + 1
        return RunSummary(
            run_id=run_id,
            workflow_id=state.workflow_id,
            status=state.status,
            started_at=state.started_at,
            updated_at=state.updated_at,
            task_count=len(state.tasks),
            task_counts=counts,
            cost_usd=totals.cost_usd,
            input_tokens=totals.input_tokens,
            output_tokens=totals.output_tokens,
            wall_seconds=_wall_seconds(state),
            active_seconds=compute_run_active_seconds(state),
            is_terminal=state.status in TERMINAL_STATUSES,
        )

    def list_summaries(self) -> list[RunSummary]:
        """Summaries for every readable run, newest-first.

        A run whose ``state.json`` is missing or corrupt is skipped rather than failing the
        whole listing — one bad directory (e.g. a run killed mid-write) must not make the
        dashboard unusable.
        """
        summaries: list[RunSummary] = []
        for run_id in self.list_run_ids():
            try:
                summaries.append(self.summarize(run_id))
            except RunNotFoundError:
                continue
        return summaries

    def detail(self, run_id: str) -> RunDetail:
        """Build the full per-run view for *run_id* (FR-R5.2)."""
        state = self.load_state(run_id)
        summary = self._summarize_state(run_id, state)

        tasks: list[TaskStat] = []
        for tid, ts in state.tasks.items():
            started, ended = _parse_iso(ts.started_at), _parse_iso(ts.ended_at)
            duration = (ended - started).total_seconds() if started and ended else None
            ti = state.task_integration.get(tid)
            tasks.append(
                TaskStat(
                    id=tid,
                    status=ts.status,
                    attempts=ts.attempts,
                    started_at=ts.started_at,
                    ended_at=ts.ended_at,
                    duration_seconds=duration,
                    input_tokens=ts.cumulative_input_tokens,
                    output_tokens=ts.cumulative_output_tokens,
                    cost_usd=ts.cumulative_cost_usd,
                    origin=ts.origin,
                    route=ts.route,
                    output_artifact_path=ts.output_artifact_path,
                    outputs=list(ts.dynamic_outputs),
                    integration_status=ti.status if ti is not None else None,
                    tier_reached=ti.tier_reached if ti is not None else None,
                    conflicted_count=len(ti.conflicted_paths) if ti is not None else 0,
                )
            )

        integration = state.integration
        return RunDetail(
            summary=summary,
            tasks=tasks,
            tripped_breakers=[asdict_safe(tb) for tb in state.tripped_breakers],
            route_decisions=dict(state.route_decisions),
            monitor_decisions=[asdict_safe(md) for md in state.monitor_decisions],
            run_dir=str(self.run_dir(run_id)),
            integration=(
                RunIntegration(
                    active=integration.active,
                    branch=integration.branch,
                    heads=dict(integration.heads),
                    tier_counts=dict(integration.tier_counts),
                    degraded_reason=integration.degraded_reason,
                )
                if integration.active
                else None
            ),
        )

    def aggregate(self) -> AggregateStats:
        """Totals across every readable run in the workspace (FR-R5.1)."""
        summaries = self.list_summaries()
        runs_by_status: dict[str, int] = {}
        tasks_by_status: dict[str, int] = {}
        for s in summaries:
            runs_by_status[s.status] = runs_by_status.get(s.status, 0) + 1
            for status, n in s.task_counts.items():
                tasks_by_status[status] = tasks_by_status.get(status, 0) + n

        return AggregateStats(
            total_runs=len(summaries),
            runs_by_status=runs_by_status,
            total_tasks=sum(s.task_count for s in summaries),
            tasks_by_status=tasks_by_status,
            total_cost_usd=sum(s.cost_usd for s in summaries),
            total_input_tokens=sum(s.input_tokens for s in summaries),
            total_output_tokens=sum(s.output_tokens for s in summaries),
            total_wall_seconds=sum(s.wall_seconds for s in summaries),
            total_active_seconds=sum(s.active_seconds for s in summaries),
        )

    def delete(self, run_id: str) -> None:
        """Delete a run's directory and everything under it (FR-R4).

        Raises:
            RunNotFoundError: If no such run directory exists.
        """
        target = self.run_dir(run_id)
        if not target.is_dir():
            raise RunNotFoundError(run_id)
        shutil.rmtree(target)

    def status_snapshot(self, run_id: str) -> dict | None:
        """Return the engine's ``status.json`` snapshot for *run_id*, if readable.

        Cheaper than a full state load for poll-heavy views. Returns ``None`` when the file
        is absent or corrupt so callers fall back to ``state.json``.
        """
        path = self.run_dir(run_id) / STATUS_FILE
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None


def asdict_safe(obj: object) -> dict:
    """Best-effort conversion of a pydantic model or dataclass to a plain dict.

    RunState fields are pydantic models today, but the persisted shapes are versioned and
    this runs on every run-detail request — so it degrades through dataclass and then plain
    ``__dict__`` rather than raising on an object shape it did not expect.
    """
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        return dict(dump())
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    return dict(vars(obj))
