"""Sentinel bandwidth lane: L1 backlog release + L2 early checkpoint + injection cap.

E-Sn7tL2 T-Sn7Bkl (ADR-0024, ``docs-md/sentinel-hld.md`` §6.3/§6.4/§6.6). Pure functions over
``RunState`` / ``WorkflowSpec`` with an injectable clock (no I/O besides reading the published
backlog file); the engine owns persistence and calls these from the tick, on the main thread.

Published backlog (``<run_dir>/control/backlog.json``, schema ``ao.sentinel.backlog/v1``) is the
engine-ready form the overseer tool writes after validating a checkpoint's
``outputs/backlog.json`` (``ao.overseer.backlog/v1``): every entry carries a fully-formed
``task`` (``TaskSpec`` dict with its final ``s-NN-<slug>`` id). The engine never interprets
overseer semantics; it re-runs the shared checker (``graph_check``) on every release, with the
stage / work-item context the file carries, so a hand-edited file cannot bypass the rules.

Priority: HIGHER ``priority`` releases first (same convention as ``edit_pending`` priorities),
file order breaks ties. An entry with ``expires_at_ckpt = N`` expires as soon as checkpoint task
``ck-NN`` has left ``pending`` (it started -- it re-confirms or replaces the backlog itself).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from . import graph_check
from .models import (
    EarlyCheckpoint,
    RunState,
    SentinelBandwidthSpec,
    SentinelSpec,
    WorkflowSpec,
)

BACKLOG_FILE: Final = "backlog.json"
BACKLOG_SCHEMA: Final = "ao.sentinel.backlog/v1"
BACKLOG_MAX_BYTES: Final = 1024 * 1024
BACKLOG_MAX_ENTRIES: Final = 50
SENTINEL_TASK_ID_RE: Final = re.compile(r"^s-\d{2,}-[a-z0-9]+(?:-[a-z0-9]+)*$")
CKPT_ID_RE: Final = re.compile(r"^ck-(\d+)$")
SETTLED: Final = frozenset({"succeeded", "failed", "skipped", "cancelled"})
"""Task statuses that count as "settled" for the early-checkpoint fraction."""
RUNNING_STATES: Final = frozenset({"running"})

RULE_BK_ID: Final = "BK-ID"
RULE_BK_DUP: Final = "BK-DUP"
RULE_EARLY: Final = "GE-EARLY"
DEFAULT_CAP_FLOOR: Final = 20
CAP_PER_SLOT_HOUR: Final = 3


@dataclass(frozen=True)
class BacklogEntry:
    id: str
    priority: int
    expires_at_ckpt: int
    task: dict[str, Any]
    kind: str | None = None
    work_item: str | None = None


@dataclass(frozen=True)
class Backlog:
    checkpoint_id: str
    stage: str | None
    known_work_items: frozenset[str]
    entries: tuple[BacklogEntry, ...]


@dataclass(frozen=True)
class EarlyPlan:
    checkpoint_id: str
    settled: tuple[str, ...]
    carry_over: tuple[str, ...]


def ckpt_task_id(number: int) -> str:
    return f"ck-{number:02d}"


# ---------------------------------------------------------------------------------------------
# reading the published backlog
# ---------------------------------------------------------------------------------------------


def read_backlog(control_dir: Path) -> Backlog | None:
    """Tolerant reader: a missing/oversize/malformed file is ``None`` (never raises).

    Entries that are individually malformed are dropped; the rest stay usable.
    """
    path = control_dir / BACKLOG_FILE
    try:
        if path.stat().st_size > BACKLOG_MAX_BYTES:
            return None
        data = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != BACKLOG_SCHEMA:
        return None
    raw = data.get("entries")
    if not isinstance(raw, list):
        return None
    entries: list[BacklogEntry] = []
    seen: set[str] = set()
    for item in raw[:BACKLOG_MAX_ENTRIES]:
        entry = _parse_entry(item)
        if entry is not None and entry.id not in seen:
            seen.add(entry.id)
            entries.append(entry)
    stage = data.get("stage")
    known = data.get("known_work_items")
    return Backlog(
        checkpoint_id=str(data.get("checkpoint_id", "")),
        stage=stage if isinstance(stage, str) else None,
        known_work_items=frozenset(x for x in known if isinstance(x, str))
        if isinstance(known, list)
        else frozenset(),
        entries=tuple(entries),
    )


def _parse_entry(item: Any) -> BacklogEntry | None:
    if not isinstance(item, dict):
        return None
    task = item.get("task")
    prio = item.get("priority")
    exp = item.get("expires_at_ckpt")
    if (
        not isinstance(task, dict)
        or not isinstance(task.get("id"), str)
        or isinstance(prio, bool)
        or not isinstance(prio, int)
        or isinstance(exp, bool)
        or not isinstance(exp, int)
    ):
        return None
    eid = item.get("id")
    kind = item.get("kind")
    work_item = item.get("work_item")
    return BacklogEntry(
        id=eid if isinstance(eid, str) and eid else task["id"],
        priority=prio,
        expires_at_ckpt=exp,
        task=task,
        kind=kind if isinstance(kind, str) else None,
        work_item=work_item if isinstance(work_item, str) else None,
    )


def entry_expired(entry: BacklogEntry, state: RunState) -> bool:
    """True once checkpoint ``ck-<expires_at_ckpt>`` has started (or finished)."""
    ts = state.tasks.get(ckpt_task_id(entry.expires_at_ckpt))
    return ts is not None and ts.status != "pending"


def available(state: RunState, backlog: Backlog | None) -> tuple[list[BacklogEntry], list[str]]:
    """``(releasable entries best-first, newly expired ids)``; never mutates state."""
    if backlog is None:
        return [], []
    sent = state.sentinel
    done = set(sent.backlog_released) | set(sent.backlog_rejected) if sent else set()
    live: list[BacklogEntry] = []
    expired: list[str] = []
    for e in backlog.entries:
        if e.id in done or e.task["id"] in state.tasks:
            continue
        if entry_expired(e, state):
            expired.append(e.id)
        else:
            live.append(e)
    live.sort(key=lambda e: -e.priority)  # stable: file order among equal priorities
    return live, expired


# ---------------------------------------------------------------------------------------------
# FR-19 injection cap
# ---------------------------------------------------------------------------------------------


def injection_cap(spec: SentinelSpec, max_parallel: int, workflow: WorkflowSpec | None) -> int:
    """``spec.max_injected_tasks`` or ``max(20, 3 x max_parallel x expected_hours)``.

    ``expected_hours`` = the workflow's ``run_wall_clock_seconds`` breaker threshold in hours,
    else 1.
    """
    if spec.max_injected_tasks is not None:
        return spec.max_injected_tasks
    hours = 1.0
    for br in workflow.circuit_breakers if workflow is not None else []:
        if br.condition == "run_wall_clock_seconds" and br.threshold:
            hours = max(1.0, br.threshold / 3600.0)
    return max(DEFAULT_CAP_FLOOR, math.ceil(CAP_PER_SLOT_HOUR * max_parallel * hours))


def cap_remaining(state: RunState, cap: int) -> int:
    used = state.sentinel.injected_count if state.sentinel is not None else 0
    return max(0, cap - used)


# ---------------------------------------------------------------------------------------------
# L1 release
# ---------------------------------------------------------------------------------------------

Applier = Callable[..., None]


def policy_for(backlog: Backlog) -> graph_check.CheckPolicy:
    return graph_check.CheckPolicy(
        stage=backlog.stage,
        known_work_items=backlog.known_work_items,
        task_meta={
            e.task["id"]: {"kind": e.kind or "", "work_item": e.work_item or ""}
            for e in backlog.entries
        },
    )


def release(
    state: RunState,
    backlog: Backlog,
    entries: list[BacklogEntry],
    slots: int,
    applier: Applier,
    at: str,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Release up to *slots* entries, each as its own ``inject`` through the shared checker.

    Returns ``(released ids, [(rejected id, rule id)])``. A rejected entry is recorded in
    ``sentinel.backlog_rejected`` and never retried.
    """
    sent = state.sentinel
    assert sent is not None
    released: list[str] = []
    rejected: list[tuple[str, str]] = []
    policy = policy_for(backlog)
    for e in entries:
        if len(released) >= slots:
            break
        try:
            if not SENTINEL_TASK_ID_RE.match(e.task["id"]):
                raise graph_check.EditRejected(
                    RULE_BK_ID, f"backlog task id {e.task['id']!r} is not s-NN-<slug>"
                )
            applier(
                "inject",
                {"tasks": [e.task]},
                graph_check.PARENT_TICK,
                f"bk-{e.id}"[:90],
                at,
                policy=policy,
            )
        except graph_check.EditRejected as rej:
            sent.backlog_rejected[e.id] = rej.rule_id
            rejected.append((e.id, rej.rule_id))
            continue
        sent.backlog_released.append(e.id)
        released.append(e.id)
    del sent.backlog_released[:-500]
    return released, rejected


def note_expired(state: RunState, expired: list[str]) -> bool:
    sent = state.sentinel
    assert sent is not None
    new = [i for i in expired if i not in sent.backlog_expired]
    if new:
        sent.backlog_expired.extend(new)
        del sent.backlog_expired[:-500]
    return bool(new)


# ---------------------------------------------------------------------------------------------
# L2 early checkpoint
# ---------------------------------------------------------------------------------------------


def _early_in_flight(state: RunState) -> bool:
    sent = state.sentinel
    if sent is None:
        return False
    for rec in sent.early_checkpoints:
        ts = state.tasks.get(rec.checkpoint_id)
        if ts is None or ts.status in ("pending", "running"):
            return True
    return False


def idle_gate(
    spec: SentinelBandwidthSpec,
    live_limit: int,
    running: int,
    since: datetime | None,
    now: datetime,
) -> tuple[datetime | None, bool]:
    """Track "idle >= min_idle" persistence. Returns ``(new since, grace elapsed)``."""
    min_idle = spec.early_ckpt_min_idle or math.ceil(live_limit / 2)
    if live_limit - running < min_idle:
        return None, False
    since = since or now
    return since, (now - since).total_seconds() >= spec.early_ckpt_grace_seconds


def find_early_plan(
    workflow: WorkflowSpec,
    state: RunState,
    spec: SentinelBandwidthSpec,
    running_ids: set[str],
) -> EarlyPlan | None:
    """The pending soft checkpoint to release early, or ``None``.

    Candidate: pending ``emit_tasks`` + ``barrier: soft`` task with >= 1 *running* predecessor,
    >= 1 settled predecessor and settled fraction >= ``early_ckpt_fraction``. Earliest in
    workflow order wins; at most one early checkpoint may be in flight.
    """
    if _early_in_flight(state):
        return None
    for task in workflow.tasks:
        if not task.emit_tasks or task.barrier != "soft":
            continue
        ts = state.tasks.get(task.id)
        if ts is not None and ts.status != "pending":
            continue
        preds = list(dict.fromkeys(task.depends_on))
        if len(preds) < 2:
            continue
        settled = [p for p in preds if p in state.tasks and state.tasks[p].status in SETTLED]
        carry = [p for p in preds if p not in settled]
        if not settled or not carry or not any(p in running_ids for p in carry):
            continue
        if len(settled) / len(preds) < spec.early_ckpt_fraction:
            continue
        return EarlyPlan(task.id, tuple(settled), tuple(carry))
    return None


def apply_early(state: RunState, plan: EarlyPlan, applier: Applier, at: str) -> EarlyCheckpoint:
    """Rewrite the checkpoint's deps through the applier and record the carry-over."""
    applier(
        "early_checkpoint",
        {"checkpoint_id": plan.checkpoint_id, "carry_over": list(plan.carry_over)},
        graph_check.PARENT_TICK,
        f"early-{plan.checkpoint_id}",
        at,
        policy=None,
    )
    rec = EarlyCheckpoint(
        checkpoint_id=plan.checkpoint_id,
        at=at,
        settled=list(plan.settled),
        carry_over=list(plan.carry_over),
    )
    sent = state.sentinel
    assert sent is not None
    sent.early_checkpoints.append(rec)
    del sent.early_checkpoints[:-100]
    return rec


@dataclass
class Bandwidth:
    """Per-``run()`` mutable bookkeeping for the bandwidth lane (not persisted)."""

    early_idle_since: datetime | None = None
    last_backlog_stat: tuple[int, int] | None = None
    notes: list[str] = field(default_factory=list)


def with_cap(policy: graph_check.CheckPolicy | None, remaining: int) -> graph_check.CheckPolicy:
    base = policy or graph_check.CheckPolicy()
    return replace(base, max_new_tasks=remaining)
