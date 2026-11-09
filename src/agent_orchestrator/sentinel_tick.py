"""Sentinel main-loop tick: control-inbox drain, live overrides, utilisation metrics.

E-Sn7tL2 T-Sn3Tck (ADR-0024, ``docs-md/sentinel-hld.md`` §4). Everything here runs on the engine's
main thread against in-memory ``RunState`` with an injectable clock (no ``time``/``datetime.now``
calls): the engine owns persistence, this module only mutates state and moves inbox files.

Only constructed when the sentinel is enabled; a disabled run never imports or calls it.
"""

from __future__ import annotations

import json
import logging
import os
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from . import graph_check, sentinel_powers
from .models import (
    SENTINEL_GEN_TASK_PREFIX,
    CancelToken,
    EffectiveSetting,
    RunMetrics,
    RunState,
    SentinelKill,
    SentinelNote,
    SentinelOverride,
    SentinelRecoverySpec,
    SentinelState,
    TaskRecovery,
    TaskRunState,
)

if TYPE_CHECKING:
    from .sentinel_gen import GenerationLane

ACTOR_PREFIX: Final = SENTINEL_GEN_TASK_PREFIX
"""``actor`` prefix of LLM-generation proposals (``sentinel-gNN``, same as the task-row id)."""
CTL_SCHEMA: Final = "ao.ctl/v1"
INBOX_DIR: Final = "inbox"
PROCESSED_DIR: Final = "inbox-processed"
REJECTED_DIR: Final = "inbox-rejected"
CONTROL_DIR: Final = "control"
INBOX_MAX_BYTES: Final = 1024 * 1024
CONSUMED_RING_MAX: Final = 1000
PROCESSED_KEEP: Final = 200
NOTES_MAX: Final = 200
NOTE_TEXT_MAX: Final = 4000
ID_MIN_LEN: Final = 6
ID_MAX_LEN: Final = 96
_ID_CHARS: Final = frozenset("abcdefghijklmnopqrstuvwxyz0123456789:_.-")

KINDS_ALL: Final = frozenset(
    {
        "set",
        "pause_llm",
        "resume_llm",
        "note",
        "kill",
        "release_backlog",
        "inject",
        "edit_pending",
        "directive",
        "request_hold",
        "request_cancel",
        "cancel",
    }
)
KINDS_ENABLED: Final = frozenset(
    {
        "set",
        "pause_llm",
        "resume_llm",
        "note",
        "kill",
        "request_hold",
        "request_cancel",
        "cancel",
        "inject",
        "edit_pending",
        "directive",
    }
)
GRAPH_KINDS: Final = frozenset({"inject", "edit_pending"})
CANCEL_KINDS: Final = frozenset({"request_cancel", "cancel"})
OVERRIDABLE_KEYS: Final = frozenset({"max_parallel"})
"""Live-overridable keys implemented so far (later tasks extend this set)."""

# Quarantine rule ids (sidecar ``<id>.reason.json`` -> ``rule_id``).
RULE_SIZE: Final = "CTL-SIZE"
RULE_JSON: Final = "CTL-JSON"
RULE_SCHEMA: Final = "CTL-SCHEMA"
RULE_KIND: Final = "CTL-KIND"
RULE_NOT_ENABLED: Final = "not_enabled_yet"
RULE_PAYLOAD: Final = "CTL-PAYLOAD"
RULE_SET_KEY: Final = "CTL-SET-KEY"
RULE_CEILING: Final = "CTL-CEILING"
RULE_FILENAME: Final = "CTL-FILENAME"

SOURCE_LIVE_OVERRIDE: Final = "live-override"

# Recovery ladder (T-Sn4Rec, HLD §4.3).
KILL_SOURCE_MECHANICAL: Final = "mechanical"
KILL_SOURCE_MANUAL: Final = "manual"
REASON_IDLE: Final = "idle"
REASON_STUCK: Final = "stuck_factor"
REASON_COST: Final = "cost"
STUCK_MIN_SAMPLES: Final = 3
"""Same-kind completed tasks needed before the stuck-factor trigger may fire."""
SYSTEMIC_WINDOW_SECONDS: Final = 3600
KILL_LOG_MAX: Final = 200
ALARMS_FILE: Final = "sentinel-alarms.jsonl"
DECISION_RETRY: Final = "retry"
DECISION_ESCALATE: Final = "escalate"
DECISION_FAIL: Final = "fail"


class _Reject(Exception):
    def __init__(self, rule_id: str, detail: str) -> None:
        super().__init__(detail)
        self.rule_id = rule_id
        self.detail = detail


@dataclass
class TickResult:
    """What one tick changed, so the engine knows whether to persist / react."""

    changed: bool = False
    cancel_requested: bool = False
    applied: list[str] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class SentinelRuntime:
    """Per-``run()`` mutable sentinel context (not persisted; ``state.sentinel`` is)."""

    ceiling: int
    tick_seconds: int
    inbox_batch_max: int
    live_limit: int
    control_dir: Path
    clock: Callable[[], datetime]
    log: logging.Logger | logging.LoggerAdapter[Any]
    last_tick: datetime | None = None
    last_metrics_at: datetime | None = None
    cancel_requested: bool = False
    pending_retire: list[Path] = field(default_factory=list)
    occ_running: int = 0
    occ_ready: int = 0
    """Occupancy after the latest FILL: attributed to the interval until the next sample."""
    recovery: SentinelRecoverySpec | None = None
    """None => liveness/kill ladder off (no tokens are wired, executors untouched)."""
    tokens: dict[str, CancelToken] = field(default_factory=dict)
    """Live cancel token per running task id (main thread owns the dict)."""
    alarmed: set[str] = field(default_factory=set)
    stat_size: Callable[[str], int | None] = lambda path: _stat_size(path)
    """Injectable file-size probe (tests substitute a fake stalled file)."""
    lane: GenerationLane | None = None
    """LLM generation lane (T-Sn8Gen); None => mechanical sentinel only."""
    edit_applier: Callable[[str, dict[str, Any], str, str, str], None] | None = None
    """Engine-owned ``inject``/``edit_pending`` applier (T-Sn6Brr); None => kinds not enabled."""
    reshape_pending: bool = False
    """Set by the applier; ``run()`` re-derives graph/order/preds on the next loop pass."""
    proposal_gate: Callable[[dict[str, Any], RunState, datetime], None] | None = None
    """LLM-power policy (T-Sn9Pow): raises ``graph_check.EditRejected`` for a refused proposal."""
    max_consecutive_rejections: int | None = None
    """Auto-pause the LLM lane after this many consecutive rejected sentinel proposals."""


def ensure_state(state: RunState) -> SentinelState:
    """Create ``state.sentinel`` / ``state.metrics`` on first use (idempotent)."""
    sent = state.sentinel if state.sentinel is not None else SentinelState()
    state.sentinel = sent
    if state.metrics is None:
        state.metrics = RunMetrics()
    return sent


def restore_overrides(state: RunState, rt: SentinelRuntime) -> None:
    """On (re)start, re-apply persisted overrides to the live limit (resume safety)."""
    sent = state.sentinel
    if sent is None:
        return
    ov = sent.overrides.get("max_parallel")
    if ov is not None and isinstance(ov.value, int) and not isinstance(ov.value, bool):
        rt.live_limit = max(1, min(ov.value, rt.ceiling))


def update_metrics(state: RunState, rt: SentinelRuntime, now: datetime) -> None:
    """Accumulate slot-seconds for the interval since the previous sample.

    Slots = the live soft limit. ``running`` slots are busy; the rest are idle-with-ready (up to
    ``ready`` of them) or idle-no-ready. The occupancy is the one recorded after the previous FILL
    (``rt.occ_running``/``rt.occ_ready``): piecewise-constant between loop wake-ups.
    """
    running, ready = rt.occ_running, rt.occ_ready
    metrics = state.metrics
    if metrics is None:
        return
    prev = rt.last_metrics_at
    rt.last_metrics_at = now
    if prev is None:
        return
    dt = (now - prev).total_seconds()
    if dt <= 0:
        return
    limit = max(rt.live_limit, running)
    busy = min(running, limit)
    idle = limit - busy
    idle_ready = min(idle, max(ready, 0))
    metrics.slot_seconds_busy += busy * dt
    metrics.slot_seconds_idle_with_ready += idle_ready * dt
    metrics.slot_seconds_idle_no_ready += (idle - idle_ready) * dt
    total = (
        metrics.slot_seconds_busy
        + metrics.slot_seconds_idle_with_ready
        + metrics.slot_seconds_idle_no_ready
    )
    metrics.utilisation_pct = round(100.0 * metrics.slot_seconds_busy / total, 2) if total else 0.0


def _entry_now(at: str) -> datetime:
    return datetime.fromisoformat(at)


def _valid_id(entry_id: object) -> bool:
    return (
        isinstance(entry_id, str)
        and ID_MIN_LEN <= len(entry_id) <= ID_MAX_LEN
        and entry_id[0] not in ":_.-"
        and set(entry_id) <= _ID_CHARS
    )


def _quarantine(control: Path, src: Path, rule_id: str, detail: str) -> None:
    rej = control / REJECTED_DIR
    rej.mkdir(parents=True, exist_ok=True)
    dst = rej / src.name
    sidecar = rej / f"{src.stem}.reason.json"
    sidecar.write_text(json.dumps({"rule_id": rule_id, "detail": detail}, sort_keys=True))
    try:
        src.replace(dst)
    except OSError:
        src.unlink(missing_ok=True)


def _parse_entry(path: Path) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise _Reject(RULE_SIZE, f"unreadable: {exc}") from exc
    if size > INBOX_MAX_BYTES:
        raise _Reject(RULE_SIZE, f"{size} bytes exceeds {INBOX_MAX_BYTES}")
    try:
        data = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise _Reject(RULE_JSON, str(exc)) from exc
    if not isinstance(data, dict):
        raise _Reject(RULE_SCHEMA, "entry is not a JSON object")
    if data.get("schema") != CTL_SCHEMA:
        raise _Reject(RULE_SCHEMA, f"schema must be {CTL_SCHEMA!r}")
    if not _valid_id(data.get("id")):
        raise _Reject(RULE_SCHEMA, "id missing or malformed")
    for key in ("kind", "actor", "created_at"):
        if not isinstance(data.get(key), str) or not data[key]:
            raise _Reject(RULE_SCHEMA, f"{key} must be a non-empty string")
    payload = data.get("payload", {})
    if not isinstance(payload, dict):
        raise _Reject(RULE_SCHEMA, "payload must be an object")
    rationale = data.get("rationale")
    if rationale is not None and (not isinstance(rationale, str) or len(rationale) > 4000):
        raise _Reject(RULE_SCHEMA, "rationale must be a string of at most 4000 chars")
    extra = set(data) - {"schema", "id", "kind", "actor", "created_at", "rationale", "payload"}
    if extra:
        raise _Reject(RULE_SCHEMA, f"unknown fields: {sorted(extra)}")
    if data["kind"] not in KINDS_ALL:
        raise _Reject(RULE_KIND, f"unknown kind {data['kind']!r}")
    if data["kind"] not in KINDS_ENABLED:
        raise _Reject(RULE_NOT_ENABLED, f"kind {data['kind']!r} lands in a later task")
    return data


def _apply_set(
    entry: dict[str, Any], state: RunState, rt: SentinelRuntime, sent: SentinelState, at: str
) -> None:
    values = entry.get("payload", {}).get("values")
    if not isinstance(values, dict) or not values:
        raise _Reject(RULE_PAYLOAD, "set requires payload.values (non-empty object)")
    staged: dict[str, Any] = {}
    for key, value in values.items():
        if key not in OVERRIDABLE_KEYS:
            raise _Reject(RULE_SET_KEY, f"{key!r} is not live-overridable")
        if key == "max_parallel":
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise _Reject(RULE_PAYLOAD, "max_parallel must be an integer >= 1")
            if value > rt.ceiling:
                raise _Reject(RULE_CEILING, f"max_parallel {value} exceeds ceiling {rt.ceiling}")
        staged[key] = value
    # All-or-nothing: only mutate after every key validated.
    for key, value in staged.items():
        sent.overrides[key] = SentinelOverride(value=value, actor=entry["actor"], at=at)
        if key == "max_parallel":
            rt.live_limit = value
        state.effective_settings[f"sentinel.{key}"] = EffectiveSetting(
            value=value, source=SOURCE_LIVE_OVERRIDE
        )


def _apply_entry(
    entry: dict[str, Any],
    state: RunState,
    rt: SentinelRuntime,
    sent: SentinelState,
    result: TickResult,
    at: str,
) -> None:
    kind = entry["kind"]
    payload = entry.get("payload", {})
    if rt.proposal_gate is not None and is_sentinel_actor(entry["actor"]):
        try:
            rt.proposal_gate(entry, state, _entry_now(at))
        except graph_check.EditRejected as rej:
            raise _Reject(rej.rule_id, rej.detail) from rej
    if kind == "directive":
        try:
            sentinel_powers.append_directive(rt.control_dir, entry, at)
        except graph_check.EditRejected as rej:
            raise _Reject(rej.rule_id, rej.detail) from rej
    elif kind == "set":
        _apply_set(entry, state, rt, sent, at)
    elif kind == "pause_llm":
        sent.llm_paused = True
    elif kind == "resume_llm":
        sent.llm_paused = False
        sent.llm_pause_reason = None
        sent.consecutive_rejections = 0
    elif kind == "note":
        text = payload.get("text")
        if not isinstance(text, str) or not text or len(text) > NOTE_TEXT_MAX:
            raise _Reject(RULE_PAYLOAD, f"note requires payload.text (1..{NOTE_TEXT_MAX} chars)")
        sent.notes.append(SentinelNote(id=entry["id"], actor=entry["actor"], at=at, text=text))
        del sent.notes[:-NOTES_MAX]
    elif kind == "kill":
        tid = payload.get("task_id")
        if not isinstance(tid, str) or tid not in rt.tokens:
            raise _Reject(RULE_PAYLOAD, "kill requires payload.task_id of a running task")
        no_retry = payload.get("no_retry", False)
        if not isinstance(no_retry, bool):
            raise _Reject(RULE_PAYLOAD, "kill payload.no_retry must be a boolean")
        reason = entry.get("rationale") or payload.get("reason") or "manual"
        if not request_kill(
            state, rt, tid, str(reason)[:200], KILL_SOURCE_MANUAL, _entry_now(at), no_retry=no_retry
        ):
            raise _Reject(RULE_PAYLOAD, f"task {tid!r} already has a kill in flight")
    elif kind in GRAPH_KINDS:
        if rt.edit_applier is None:
            raise _Reject(RULE_NOT_ENABLED, f"kind {kind!r} needs the engine edit applier")
        try:
            rt.edit_applier(kind, payload, entry["actor"], entry["id"], at)
        except graph_check.EditRejected as rej:
            raise _Reject(rej.rule_id, rej.detail) from rej
    elif kind == "request_hold":
        sent.hold_requested = True
        sent.hold_reason = entry.get("rationale") or payload.get("reason")
    elif kind in CANCEL_KINDS:
        rt.cancel_requested = True
        result.cancel_requested = True


def is_sentinel_actor(actor: object) -> bool:
    return isinstance(actor, str) and actor.startswith(ACTOR_PREFIX)


def _note_rejection(sent: SentinelState, rt: SentinelRuntime, path: Path, now: datetime) -> None:
    """Count a rejected *sentinel-authored* proposal; auto-pause the LLM lane on a streak.

    The actor is read best-effort from the raw file (a rejected entry never reaches the parsed
    path); human/CLI entries never count.
    """
    try:
        actor = json.loads(path.read_bytes()).get("actor")
    except (OSError, ValueError, AttributeError):
        return
    if not is_sentinel_actor(actor):
        return
    sent.consecutive_rejections += 1
    limit = rt.max_consecutive_rejections
    if limit is not None and sent.consecutive_rejections >= limit and not sent.llm_paused:
        sent.llm_paused = True
        sent.llm_pause_reason = f"{sent.consecutive_rejections} consecutive rejected proposals"
        rt.log.warning(
            "sentinel.llm.auto_paused: %s",
            sent.llm_pause_reason,
            extra={"event": "sentinel.llm.auto_paused", "at": now.isoformat()},
        )


def drain_inbox(state: RunState, rt: SentinelRuntime, now: datetime) -> TickResult:
    """Apply (or quarantine) pending ``control/inbox/*.json`` entries in lexicographic order.

    ``*.tmp`` files are never read (writers rename atomically). An id already in
    ``inbox_consumed`` is dropped silently (crash between the engine's save and the file move).
    Consumed ids are recorded on ``state`` here; the *caller* saves the state and then calls
    :func:`finalize_inbox` so the ids are durable before any file is retired (G5).
    """
    result = TickResult()
    sent = ensure_state(state)
    inbox = rt.control_dir / INBOX_DIR
    if not inbox.is_dir():
        return result
    at = now.isoformat()
    paths = sorted(p for p in inbox.iterdir() if p.suffix == ".json" and p.is_file())
    for path in paths[: rt.inbox_batch_max]:
        entry_id = path.stem
        if entry_id in sent.inbox_consumed:
            # Replay after a crash: effect already persisted, just retire the file.
            result.applied.append(f"{entry_id}:replay")
            rt.pending_retire.append(path)
            continue
        try:
            entry = _parse_entry(path)
            if entry["id"] != entry_id:
                raise _Reject(RULE_FILENAME, f"file name {path.name!r} does not match id")
            if entry["id"] in sent.inbox_consumed:
                rt.pending_retire.append(path)
                continue
            _apply_entry(entry, state, rt, sent, result, at)
        except _Reject as rej:
            _note_rejection(sent, rt, path, now)
            _quarantine(rt.control_dir, path, rej.rule_id, rej.detail)
            result.rejected.append((entry_id, rej.rule_id))
            result.changed = True
            rt.log.warning(
                "ctl.rejected",
                extra={"event": "ctl.rejected", "entry": entry_id, "rule_id": rej.rule_id},
            )
            continue
        if is_sentinel_actor(entry["actor"]):
            sent.consecutive_rejections = 0
        sent.inbox_consumed.append(entry["id"])
        del sent.inbox_consumed[:-CONSUMED_RING_MAX]
        rt.pending_retire.append(path)
        result.applied.append(entry["id"])
        result.changed = True
        rt.log.info(
            "ctl.applied",
            extra={"event": "ctl.applied", "entry": entry["id"], "kind": entry["kind"]},
        )
    return result


def finalize_inbox(rt: SentinelRuntime) -> None:
    """Move applied entries to ``inbox-processed/`` (keep the newest ``PROCESSED_KEEP``).

    Must run only after the state save that recorded their ids (see :func:`drain_inbox`).
    """
    paths, rt.pending_retire = rt.pending_retire, []
    if not paths:
        return
    done = rt.control_dir / PROCESSED_DIR
    done.mkdir(parents=True, exist_ok=True)
    for path in paths:
        try:
            path.replace(done / path.name)
        except OSError:
            path.unlink(missing_ok=True)
    kept = sorted(done.glob("*.json"))
    for old in kept[:-PROCESSED_KEEP]:
        old.unlink(missing_ok=True)


# ----------------------------------------------------------------------------------------------
# Liveness + recovery ladder (E-Sn7tL2 T-Sn4Rec)
# ----------------------------------------------------------------------------------------------


def _stat_size(path: str) -> int | None:
    try:
        return os.stat(path).st_size
    except OSError:
        return None


def _parse(ts_iso: str | None) -> datetime | None:
    if not ts_iso:
        return None
    try:
        return datetime.fromisoformat(ts_iso)
    except ValueError:
        return None


def register_task(state: RunState, rt: SentinelRuntime, tid: str) -> CancelToken | None:
    """Wire a fresh cancel token for a task about to be dispatched (None when recovery is off)."""
    if rt.recovery is None:
        return None
    token = CancelToken(grace_seconds=rt.recovery.kill_grace_seconds)
    rt.tokens[tid] = token
    ts = state.tasks[tid]
    ts.last_activity_at = None
    ts.last_activity_size = None
    ts.running_seconds = None
    return token


def release_task(rt: SentinelRuntime, tid: str) -> CancelToken | None:
    return rt.tokens.pop(tid, None)


def sample_liveness(state: RunState, rt: SentinelRuntime, now: datetime) -> bool:
    """Record activity (size change of ``activity_path``) and running seconds per running task."""
    changed = False
    for tid, token in rt.tokens.items():
        ts = state.tasks.get(tid)
        if ts is None or ts.status != "running":
            continue
        size = rt.stat_size(token.activity_path) if token.activity_path else None
        size = size if size is not None else 0
        if ts.last_activity_at is None or size != ts.last_activity_size:
            ts.last_activity_at = now.isoformat()
            ts.last_activity_size = size
            changed = True
        started = _parse(ts.started_at)
        if started is not None:
            ts.running_seconds = max((now - started).total_seconds(), 0.0)
    return changed


def _peer_durations(state: RunState, tid: str, ts: TaskRunState) -> list[float]:
    """Wall seconds of completed same-kind (same agent) tasks, excluding *tid* itself."""
    out: list[float] = []
    for other_id, other in state.tasks.items():
        if other_id == tid or other.status != "succeeded" or other.agent != ts.agent:
            continue
        a, b = _parse(other.started_at), _parse(other.ended_at)
        if a is not None and b is not None and b >= a:
            out.append((b - a).total_seconds())
    return out


def _trigger_for(
    state: RunState, spec: SentinelRecoverySpec, tid: str, ts: TaskRunState, now: datetime
) -> str | None:
    last = _parse(ts.last_activity_at)
    if last is not None and (now - last).total_seconds() >= spec.idle_kill_minutes * 60:
        return REASON_IDLE
    running = ts.running_seconds or 0.0
    if running >= spec.stuck_min_minutes * 60:
        peers = _peer_durations(state, tid, ts)
        if len(peers) >= STUCK_MIN_SAMPLES and running > spec.stuck_factor * statistics.median(
            peers
        ):
            return REASON_STUCK
    cap = ts.effective_limits.task_cost_cap_usd if ts.effective_limits is not None else None
    if spec.cost_kill_pct is not None and cap:
        # Only cost already settled for earlier attempts is visible mid-flight (the live attempt's
        # spend is reported when it ends): the trigger guards re-dispatches of costly tasks.
        if ts.cumulative_cost_usd >= cap * spec.cost_kill_pct / 100.0:
            return REASON_COST
    return None


def _append_alarm(rt: SentinelRuntime, record: dict[str, Any]) -> None:
    try:
        rt.control_dir.mkdir(parents=True, exist_ok=True)
        with (rt.control_dir / ALARMS_FILE).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        rt.log.warning(
            "sentinel.alarm: could not write alarms file", extra={"event": "sentinel.error"}
        )


def evaluate_recovery(
    state: RunState, rt: SentinelRuntime, now: datetime, protected: set[str]
) -> list[str]:
    """Mechanical triggers: request cancels for stalled/overlong tasks. Returns killed task ids.

    Barrier (*protected*) tasks are alarm-only unless ``include_barriers``. Nothing is killed once
    the systemic alarm is raised.
    """
    spec = rt.recovery
    sent = state.sentinel
    if spec is None or not spec.enabled or sent is None or sent.systemic_alarm:
        return []
    killed: list[str] = []
    for tid in sorted(rt.tokens):
        token = rt.tokens[tid]
        ts = state.tasks.get(tid)
        if ts is None or ts.status != "running" or token.is_set():
            continue
        reason = _trigger_for(state, spec, tid, ts, now)
        if reason is None:
            continue
        if tid in protected and not spec.include_barriers:
            if tid not in rt.alarmed:
                rt.alarmed.add(tid)
                rt.log.warning(
                    "sentinel.alarm: barrier task %s tripped %s (alarm only)",
                    tid,
                    reason,
                    extra={"event": "sentinel.alarm", "task": tid, "reason": reason},
                )
                _append_alarm(rt, {"at": now.isoformat(), "kind": "barrier", "task": tid})
            continue
        if request_kill(state, rt, tid, reason, KILL_SOURCE_MECHANICAL, now):
            killed.append(tid)
    return killed


def request_kill(
    state: RunState,
    rt: SentinelRuntime,
    tid: str,
    reason: str,
    source: str,
    now: datetime,
    *,
    no_retry: bool = False,
) -> bool:
    """Signal the task's executor to die. Idempotent per attempt (a second request is a no-op)."""
    token = rt.tokens.get(tid)
    if token is None or token.is_set():
        return False
    token.reason, token.source, token.no_retry = reason, source, no_retry
    token.set()
    rt.log.warning(
        "sentinel.kill: %s (%s, %s)",
        tid,
        reason,
        source,
        extra={"event": "sentinel.kill", "task": tid, "reason": reason, "source": source},
    )
    return True


def settle_kill(
    state: RunState,
    rt: SentinelRuntime,
    tid: str,
    reason: str,
    source: str,
    no_retry: bool,
    now: datetime,
) -> str:
    """Advance the ladder for a killed attempt: ``retry`` -> ``escalate`` -> ``fail``.

    Kills never consume ``max_attempts`` (the engine requeues the task as a fresh dispatch).
    Mutates ``ts.recovery`` and the sentinel kill log; raises the systemic alarm when enough kills
    land inside the window.
    """
    ts = state.tasks[tid]
    sent = ensure_state(state)
    spec = rt.recovery or SentinelRecoverySpec()
    rec = ts.recovery if ts.recovery is not None else TaskRecovery()
    ts.recovery = rec
    rec.kills += 1
    rec.last_reason, rec.last_source, rec.no_retry = reason, source, no_retry
    sent.kill_log.append(
        SentinelKill(task_id=tid, at=now.isoformat(), reason=reason, source=source)
    )
    del sent.kill_log[:-KILL_LOG_MAX]
    _check_systemic(sent, spec, rt, now)
    if no_retry or not spec.enabled or rec.kills > spec.max_recovery_kills_per_task:
        decision = DECISION_FAIL
    elif rec.kills == 1 or not spec.escalate:
        decision = DECISION_RETRY
    else:
        idx = min(rec.kills - 2, len(spec.escalate) - 1)
        rec.escalation_step = idx + 1
        rec.overrides = dict(spec.escalate[idx])
        decision = DECISION_ESCALATE
    rt.log.warning(
        "sentinel.recovery.%s: %s kills=%d",
        decision,
        tid,
        rec.kills,
        extra={"event": f"sentinel.recovery.{decision}", "task": tid, "kills": rec.kills},
    )
    return decision


def _check_systemic(
    sent: SentinelState, spec: SentinelRecoverySpec, rt: SentinelRuntime, now: datetime
) -> None:
    if sent.systemic_alarm:
        return
    recent = [
        k
        for k in sent.kill_log
        if (a := _parse(k.at)) is not None and (now - a).total_seconds() <= SYSTEMIC_WINDOW_SECONDS
    ]
    if len(recent) < spec.systemic_kill_alarm:
        return
    sent.systemic_alarm = True
    sent.systemic_alarm_at = now.isoformat()
    sent.llm_paused = True
    rt.log.error(
        "sentinel.alarm: systemic — %d kills in %ds; automatic kills stopped, LLM paused",
        len(recent),
        SYSTEMIC_WINDOW_SECONDS,
        extra={"event": "sentinel.alarm", "kind": "systemic", "kills": len(recent)},
    )
    _append_alarm(
        rt,
        {
            "at": now.isoformat(),
            "kind": "systemic",
            "kills": len(recent),
            "window_s": SYSTEMIC_WINDOW_SECONDS,
        },
    )
