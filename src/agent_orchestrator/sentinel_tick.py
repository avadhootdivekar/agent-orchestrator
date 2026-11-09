"""Sentinel main-loop tick: control-inbox drain, live overrides, utilisation metrics.

E-Sn7tL2 T-Sn3Tck (ADR-0024, ``docs-md/sentinel-hld.md`` §4). Everything here runs on the engine's
main thread against in-memory ``RunState`` with an injectable clock (no ``time``/``datetime.now``
calls): the engine owns persistence, this module only mutates state and moves inbox files.

Only constructed when the sentinel is enabled; a disabled run never imports or calls it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from .models import (
    EffectiveSetting,
    RunMetrics,
    RunState,
    SentinelNote,
    SentinelOverride,
    SentinelState,
)

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
    {"set", "pause_llm", "resume_llm", "note", "request_hold", "request_cancel", "cancel"}
)
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
    if kind == "set":
        _apply_set(entry, state, rt, sent, at)
    elif kind == "pause_llm":
        sent.llm_paused = True
    elif kind == "resume_llm":
        sent.llm_paused = False
    elif kind == "note":
        text = payload.get("text")
        if not isinstance(text, str) or not text or len(text) > NOTE_TEXT_MAX:
            raise _Reject(RULE_PAYLOAD, f"note requires payload.text (1..{NOTE_TEXT_MAX} chars)")
        sent.notes.append(SentinelNote(id=entry["id"], actor=entry["actor"], at=at, text=text))
        del sent.notes[:-NOTES_MAX]
    elif kind == "request_hold":
        sent.hold_requested = True
        sent.hold_reason = entry.get("rationale") or payload.get("reason")
    elif kind in CANCEL_KINDS:
        rt.cancel_requested = True
        result.cancel_requested = True


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
            _quarantine(rt.control_dir, path, rej.rule_id, rej.detail)
            result.rejected.append((entry_id, rej.rule_id))
            result.changed = True
            rt.log.warning(
                "ctl.rejected",
                extra={"event": "ctl.rejected", "entry": entry_id, "rule_id": rej.rule_id},
            )
            continue
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
