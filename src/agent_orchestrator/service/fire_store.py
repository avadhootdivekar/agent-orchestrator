"""Durability layer for the scheduler daemon (HLD §5.3 / §6.6 / §12, ADR-0014 D5).

Three things live here, all under ``<state_dir>/schedules/``:

* :class:`FireIntentStore` -- one fsync-durable JSON file per *logical fire*
  (``fires/<workspace_slug>/<fire_key>.json``). This is what makes a fire **at-most-once**
  across crashes, reboots and clock changes.
* :class:`ScheduleStateStore` -- per-schedule runtime counters in a single ``state.json``.
* :class:`ScheduleEventLog` -- the structured ``events.jsonl`` log (single ``.1`` rollover).

Everything time-dependent takes an injectable clock; nothing here launches a process. The
launcher (``T-Lp4Wt6``) writes the intent, launches, then updates; on restart the engine calls
:meth:`FireIntentStore.reconcile_orphans` and emits ``schedule.fire_orphaned`` for what it
returns. Nobody ever relaunches an orphan -- at-most-once is chosen over at-least-once (§6.6).

Concurrency: the engine is single-threaded and single-process, so ``state.json`` has **no lock**
(one lock domain for every workspace). If that ever stops being true this file needs the
``ServiceRegistry.mutate`` treatment, otherwise concurrent read-modify-write cycles lose counters.
"""

from __future__ import annotations

import builtins
import hashlib
import json
import logging
import os
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .statefile import atomic_write_bytes, read_json_model, write_json_model_atomic
from .supervisor import _slug_for_root

logger = logging.getLogger(__name__)

# -- constants ---------------------------------------------------------------------------------
SCHEDULES_SUBDIR = "schedules"
FIRES_SUBDIR = "fires"
STATE_FILENAME = "state.json"
EVENTS_FILENAME = "events.jsonl"
EVENTS_ROLLOVER_SUFFIX = ".1"
EVENT_LOG_MAX_BYTES = 8 * 1024 * 1024
FIRE_RECORD_MAX_AGE_DAYS = 90
FIRE_RECORD_MAX_RECORDS = 10_000
STATE_KEY_SEP = "|"

FireStatus = Literal["intended", "launched", "suppressed", "orphaned", "launch_failed"]
CapState = Literal["none", "paused", "aborted", "expired", "resumed"]
CostConfidence = Literal["pending", "actual", "partial", "unknown"]
GOVERNANCE_RECORD_SCHEMA = "ao.schedule.governance/v1"
ScheduleStatus = Literal["active", "disabled", "completed", "quarantined"]

# Fields of a fire record that identify it and so can never be changed by ``update``.
_IMMUTABLE_FIRE_FIELDS = frozenset({"fire_key", "workspace_root", "schedule_id", "scheduled_for"})

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    """Default wall clock. Production-only; every test injects a fixed clock instead."""
    return datetime.now(UTC)


def _resolved_root(workspace_root: str | Path) -> str:
    """Workspace identity -- the exact expression ``service/registry.py`` uses."""
    return str(Path(workspace_root).resolve())


def fire_key(
    workspace_root: str | Path,
    schedule_id: str,
    scheduled_for: datetime,
    discriminator: str = "",
) -> str:
    """Stable identity of one logical fire (HLD §6.6).

    ``scheduled_for`` is normalised to UTC before hashing: cron yields instants in the binding's
    tz while interval anchors and rehydrated state are UTC, so without this the same instant
    could serialise two ways, produce two keys and double-fire after a restart or tz edit.
    A naive datetime raises ``ValueError`` rather than silently producing a different key.

    *discriminator* (event triggers only, T-Fw8Gp4 M2) is a content-derived token so two
    different states observed in the same second get different keys. Empty (cron/interval/
    webhook) leaves the material -- and so every pre-existing key -- unchanged.
    """
    if scheduled_for.tzinfo is None or scheduled_for.utcoffset() is None:
        raise ValueError("scheduled_for must be timezone-aware to derive a fire_key")
    instant = scheduled_for.astimezone(UTC).isoformat()
    material = f"{_resolved_root(workspace_root)}|{schedule_id}|{instant}"
    if discriminator:
        material += f"|{discriminator}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# -- fire-intent store -------------------------------------------------------------------------


class PromptMeta(BaseModel):
    """The prompt a governed fire ran with (HLD §6.4/§11): path, content hash and size."""

    path: str
    sha256: str
    bytes: int


class FireGovernance(BaseModel):
    """Governance sub-object of a fire record (governance HLD §6.4). Written in the SAME atomic
    ``intended`` write as the record itself; optional so pre-governance records stay valid."""

    model_config = ConfigDict(populate_by_name=True)

    schema_: str = Field(default=GOVERNANCE_RECORD_SCHEMA, alias="schema")
    limits_digest: str | None = None
    tz: str | None = None
    week_start: str | None = None
    prompt: PromptMeta | None = None
    reserved_usd: float | None = None
    run_cost_cap_usd: float | None = None
    on_cap: str | None = None
    pause_ttl_seconds: int | None = None
    cost_observed_usd: float = 0.0  # high-water mark; only ever rises
    cost_confidence: CostConfidence = "pending"
    cap_state: CapState = "none"
    paused_at: AwareDatetime | None = None
    pause_expires_at: AwareDatetime | None = None
    resumes: list[dict[str, Any]] = Field(default_factory=list)
    final: bool = False
    detail: dict[str, Any] = Field(default_factory=dict)  # gate detail of a governance skip


class FireIntentRecord(BaseModel):
    """One file per logical fire (HLD §5.3)."""

    fire_key: str
    workspace_root: str
    schedule_id: str
    scheduled_for: AwareDatetime
    effective_due: AwareDatetime | None = None
    created_at: AwareDatetime
    status: FireStatus
    reason: str | None = None
    launch_id: str | None = None
    pid: int | None = None
    run_id: str | None = None
    argv_digest: str | None = None
    governance: FireGovernance | None = None


class FireAlreadyRecordedError(Exception):
    """``write`` was called for a ``fire_key`` that already has a record (any status)."""


class FireIntentStore:
    """Atomic, fsync-durable store of :class:`FireIntentRecord` files.

    ``prune`` can reopen a key by deleting its record; it therefore only removes records older
    than the retention window (far beyond any catch-up window) and never an ``intended`` one.
    """

    def __init__(self, state_dir: Path, *, clock: Clock = _utc_now) -> None:
        self._root = Path(state_dir) / SCHEDULES_SUBDIR / FIRES_SUBDIR
        self._clock = clock

    # -- paths --
    def _dir_for(self, workspace_root: str | Path) -> Path:
        return self._root / _slug_for_root(_resolved_root(workspace_root))

    def _locate(self, key: str, workspace_root: str | Path | None = None) -> Path | None:
        if workspace_root is not None:
            path = self._dir_for(workspace_root) / f"{key}.json"
            return path if path.exists() else None
        if not self._root.is_dir():
            return None
        for path in self._root.glob(f"*/{key}.json"):
            return path
        return None

    # -- reads --
    def exists(self, fire_key: str, workspace_root: str | Path | None = None) -> bool:
        """True when ANY record exists for *fire_key*, whatever its status.

        This single rule is what suppresses re-firing, catch-up re-firing and clock-rewind
        re-firing. Do NOT scope it to ``launched``: a ``suppressed`` record must also block a
        later attempt (otherwise a skipped fire is resurrected by catch-up), and an ``intended``/
        ``orphaned``/``launch_failed`` record means a launch may already have happened, so
        retrying risks a duplicate real-money run. Existence is judged by the file being present,
        not by it parsing: a corrupt record still suppresses (fail safe toward not firing).
        """
        return self._locate(fire_key, workspace_root) is not None

    def get(
        self, fire_key: str, workspace_root: str | Path | None = None
    ) -> FireIntentRecord | None:
        path = self._locate(fire_key, workspace_root)
        return self._read(path) if path is not None else None

    def directory(self, workspace_root: str | Path) -> Path:
        """The directory holding *workspace_root*'s fire records (may not exist yet)."""
        return self._dir_for(workspace_root)

    @classmethod
    def read_file(cls, path: Path) -> FireIntentRecord | None:
        """Parse one record file; ``None`` (logged) when unreadable. For index builders."""
        return cls._read(path)

    @staticmethod
    def _read(path: Path) -> FireIntentRecord | None:
        try:
            return FireIntentRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("fire record %s unreadable (%s); ignoring it", path, exc)
            return None

    def list(
        self,
        status: FireStatus | None = None,
        workspace_root: str | Path | None = None,
        limit: int | None = None,
    ) -> builtins.list[FireIntentRecord]:
        """Records newest-first by ``created_at`` (``limit`` keeps the newest N).

        Only ``*.json`` files are considered, so a half-written ``*.json.tmp`` is never returned.
        """
        if workspace_root is not None:
            dirs = [self._dir_for(workspace_root)]
        else:
            dirs = (
                sorted(p for p in self._root.glob("*") if p.is_dir()) if self._root.is_dir() else []
            )
        records: list[FireIntentRecord] = []
        for d in dirs:
            for path in sorted(d.glob("*.json")):
                rec = self._read(path)
                if rec is not None and (status is None or rec.status == status):
                    records.append(rec)
        records.sort(key=lambda r: (r.created_at, r.fire_key), reverse=True)
        return records[:limit] if limit is not None else records

    # -- writes --
    def _persist(self, record: FireIntentRecord) -> Path:
        """Durable atomic write: unique tmp -> write -> fsync file -> ``os.replace`` -> fsync dir.

        The ``fsync`` is the point of the whole design (HLD §6.6): a record that only reached the
        page cache when the machine loses power cannot make a fire at-most-once. REMOVING IT
        REMOVES THE AT-MOST-ONCE GUARANTEE; ``test_write_calls_fsync`` fails loudly if it goes.
        The directory fsync makes the *rename* durable too, and the unique (``mkstemp``) tmp name
        keeps two writer processes from sharing one tmp file (T-Se4Bk5 R-1: this used to be a
        second hand-rolled idiom with a fixed ``<key>.json.tmp`` and no directory fsync).
        """
        path = self._dir_for(record.workspace_root) / f"{record.fire_key}.json"
        atomic_write_bytes(
            path, record.model_dump_json(indent=2, by_alias=True).encode("utf-8"), durable=True
        )
        return path

    def write(self, record: FireIntentRecord) -> None:
        """Create the record durably. Refuses (``FireAlreadyRecordedError``) if one exists in any
        status, so a re-issued intent can never regress a ``launched`` record. Use ``update`` for
        later transitions."""
        if self.exists(record.fire_key, record.workspace_root):
            raise FireAlreadyRecordedError(record.fire_key)
        self._persist(record)

    def update(self, fire_key: str, **fields: Any) -> FireIntentRecord:
        """Apply *fields* to an existing record (revalidated) and persist durably."""
        current = self.get(fire_key)
        if current is None:
            raise KeyError(f"no fire record for {fire_key}")
        forbidden = _IMMUTABLE_FIRE_FIELDS & fields.keys()
        if forbidden:
            raise ValueError(f"fire record fields are immutable: {sorted(forbidden)}")
        updated = FireIntentRecord.model_validate({**current.model_dump(), **fields})
        self._persist(updated)
        return updated

    def update_governance(self, fire_key: str, **fields: Any) -> FireIntentRecord:
        """Merge *fields* into the record's ``governance`` object and persist atomically
        (same write-then-rename + fsync as every other write). ``cost_observed_usd`` is a
        high-water mark: a lower value than the stored one is ignored, never written.

        Read-modify-write without a lock: safe because the engine is the single writer of
        governance state; the ledger only writes the monotonic mark through here.
        """
        current = self.get(fire_key)
        if current is None:
            raise KeyError(f"no fire record for {fire_key}")
        gov = current.governance or FireGovernance()
        if "cost_observed_usd" in fields:
            fields["cost_observed_usd"] = max(gov.cost_observed_usd, fields["cost_observed_usd"])
        merged = FireGovernance.model_validate({**gov.model_dump(), **fields})
        updated = current.model_copy(update={"governance": merged})
        self._persist(updated)
        return updated

    def find_by_run_id(self, workspace_root: str | Path, run_id: str) -> FireIntentRecord | None:
        """The record whose ``run_id`` is *run_id* in *workspace_root* (linear scan), else None."""
        d = self._dir_for(workspace_root)
        if not d.is_dir():
            return None
        for path in sorted(d.glob("*.json")):
            rec = self._read(path)
            if rec is not None and rec.run_id == run_id:
                return rec
        return None

    def iter_schedule(
        self, workspace_root: str | Path, schedule_id: str, since: datetime
    ) -> builtins.list[FireIntentRecord]:
        """Records of one schedule with ``scheduled_for >= since``, oldest first."""
        d = self._dir_for(workspace_root)
        out: builtins.list[FireIntentRecord] = []
        if d.is_dir():
            for path in sorted(d.glob("*.json")):
                rec = self._read(path)
                if (
                    rec is not None
                    and rec.schedule_id == schedule_id
                    and rec.scheduled_for >= since
                ):
                    out.append(rec)
        out.sort(key=lambda r: (r.scheduled_for, r.fire_key))
        return out

    def write_suppressed(
        self,
        fire_key: str,
        reason: str,
        *,
        workspace_root: str | Path,
        schedule_id: str,
        scheduled_for: datetime,
        effective_due: datetime | None = None,
        governance: FireGovernance | None = None,
    ) -> FireIntentRecord:
        """Record a deliberately-skipped fire; it burns the key like any other record."""
        record = FireIntentRecord(
            fire_key=fire_key,
            workspace_root=_resolved_root(workspace_root),
            schedule_id=schedule_id,
            scheduled_for=scheduled_for,
            effective_due=effective_due,
            created_at=self._clock(),
            status="suppressed",
            reason=reason,
            governance=governance,
        )
        self.write(record)
        return record

    def reconcile_orphans(
        self,
        pid_alive: Callable[[int], bool],
        run_exists: Callable[[str, str], bool],
    ) -> builtins.list[FireIntentRecord]:
        """HLD §6.6 recovery, run once at engine start.

        Each ``intended`` record becomes ``launched`` when its pid is alive or its run directory
        exists (``run_exists(workspace_root, run_id)``); otherwise ``orphaned``, and it is
        returned so the caller can emit ``schedule.fire_orphaned``. Never relaunches, never
        deletes: an orphaned record still burns its key.
        """
        orphaned: list[FireIntentRecord] = []
        for rec in self.list(status="intended"):
            if rec.pid is not None and pid_alive(rec.pid):
                self.update(rec.fire_key, status="launched")
            elif rec.run_id is not None and run_exists(rec.workspace_root, rec.run_id):
                self.update(rec.fire_key, status="launched")
            else:
                orphaned.append(self.update(rec.fire_key, status="orphaned"))
        return orphaned

    def prune(
        self,
        max_age_days: int = FIRE_RECORD_MAX_AGE_DAYS,
        max_records: int = FIRE_RECORD_MAX_RECORDS,
    ) -> int:
        """Delete records older than *max_age_days*, then the oldest beyond *max_records*.
        ``intended`` records are never pruned (reconcile must see them). Returns the count."""
        cutoff = self._clock() - timedelta(days=max_age_days)
        removed = 0
        survivors: list[FireIntentRecord] = []
        for rec in self.list():  # newest-first
            if rec.status != "intended" and rec.created_at < cutoff:
                removed += self._delete(rec)
            else:
                survivors.append(rec)
        for rec in survivors[max_records:]:
            if rec.status != "intended":
                removed += self._delete(rec)
        return removed

    def _delete(self, rec: FireIntentRecord) -> int:
        path = self._dir_for(rec.workspace_root) / f"{rec.fire_key}.json"
        try:
            path.unlink()
        except FileNotFoundError:
            return 0
        return 1


# -- schedule state ----------------------------------------------------------------------------


class ReadOnlyFireStore(FireIntentStore):
    """A fire store whose governance write-back is applied in memory only. The ledger persists
    cost high-water marks on read; ``update_governance`` is an unlocked read-modify-write, so a
    view process (CLI, dashboard) doing it would race the engine's fire writes (T-Se4Bk5 R-2)."""

    def update_governance(self, fire_key: str, **fields: Any) -> FireIntentRecord:
        rec = self.get(fire_key)
        if rec is None:
            raise KeyError(fire_key)
        gov = (rec.governance or FireGovernance()).model_copy(update=fields)
        return rec.model_copy(update={"governance": gov})


def readonly_fire_store(state_dir: Path) -> FireIntentStore:
    """Store for view callers (CLI usage/next, dashboard usage API): never writes fire records."""
    return ReadOnlyFireStore(state_dir)


class ScheduleState(BaseModel):
    """Runtime state for one ``<workspace_slug>|<schedule_id>`` (HLD §5.3)."""

    status: ScheduleStatus = "active"
    last_scheduled_for: AwareDatetime | None = None
    last_fire_at: AwareDatetime | None = None
    next_fire_at: AwareDatetime | None = None
    last_run_id: str | None = None
    last_status: str | None = None
    runs_count: int = 0
    consecutive_failures: int = 0
    consecutive_eval_errors: int = 0
    queued_fire_key: str | None = None
    # Original instant of the queued fire; persisted so a queue survives a service restart.
    # Optional with a None default: legacy state files (no such key) load unchanged.
    queued_scheduled_for: AwareDatetime | None = None
    # Event-trigger change discriminator of the queued fire (T-Fw8Gp4 N2): needed to re-derive the
    # fire key after a restart. Expand-only: legacy state reads as "".
    queued_discriminator: str = ""
    active_fire_keys: list[str] = Field(default_factory=list)
    disabled_reason: str | None = None


class _StateFile(BaseModel):
    schedules: dict[str, ScheduleState] = Field(default_factory=dict)


def state_key(workspace_root: str | Path, schedule_id: str) -> str:
    return f"{_slug_for_root(_resolved_root(workspace_root))}{STATE_KEY_SEP}{schedule_id}"


class ScheduleStateStore:
    """All schedules' state in one ``state.json``; every mutator persists immediately.

    A corrupt ``state.json`` logs a warning and starts clean rather than blocking the daemon.
    Cost: ``runs_count``/``consecutive_*`` counters and ``next_fire_at`` reset (fire idempotency is
    unaffected -- that lives in :class:`FireIntentStore`). Single-writer; see the module docstring.
    """

    def __init__(self, state_dir: Path) -> None:
        self._path = Path(state_dir) / SCHEDULES_SUBDIR / STATE_FILENAME
        self._data = read_json_model(self._path, _StateFile, default_factory=_StateFile)

    def _entry(self, root: str | Path, schedule_id: str) -> ScheduleState:
        return self._data.schedules.setdefault(state_key(root, schedule_id), ScheduleState())

    def _save(self) -> None:
        write_json_model_atomic(self._path, self._data)

    def get(self, root: str | Path, schedule_id: str) -> ScheduleState | None:
        return self._data.schedules.get(state_key(root, schedule_id))

    def all(self) -> dict[str, ScheduleState]:
        return dict(self._data.schedules)

    def set_next_fire(
        self, root: str | Path, schedule_id: str, next_fire_at: datetime | None
    ) -> None:
        self._entry(root, schedule_id).next_fire_at = next_fire_at
        self._save()

    def record_fire(
        self,
        root: str | Path,
        schedule_id: str,
        *,
        scheduled_for: datetime,
        run_id: str,
        fire_at: datetime,
        fire_key: str | None = None,
    ) -> None:
        """A launch succeeded: bump ``runs_count``, clear the failure streak, track the live key."""
        s = self._entry(root, schedule_id)
        s.runs_count += 1
        s.consecutive_failures = 0
        s.last_scheduled_for = scheduled_for
        s.last_fire_at = fire_at
        s.last_run_id = run_id
        if fire_key is not None and fire_key not in s.active_fire_keys:
            s.active_fire_keys.append(fire_key)
        self._save()

    def mark_active(self, root: str | Path, schedule_id: str, fire_key: str) -> None:
        """Track *fire_key* as live again (capped-run resume); no counters move."""
        s = self._entry(root, schedule_id)
        if fire_key not in s.active_fire_keys:
            s.active_fire_keys.append(fire_key)
            self._save()

    def adopt_active(self, root: str | Path, schedule_id: str, fire_key: str) -> None:
        """Track a still-live *fire_key* whose tracking was lost (start sweep); no counters move."""
        s = self._entry(root, schedule_id)
        if fire_key not in s.active_fire_keys:
            s.active_fire_keys.append(fire_key)
            self._save()

    def record_failure(self, root: str | Path, schedule_id: str, error: str) -> None:
        s = self._entry(root, schedule_id)
        s.consecutive_failures += 1
        s.last_status = f"launch_failed: {error}"
        self._save()

    def set_status(
        self, root: str | Path, schedule_id: str, status: ScheduleStatus, reason: str | None = None
    ) -> None:
        s = self._entry(root, schedule_id)
        s.status = status
        s.disabled_reason = reason if status != "active" else None
        self._save()

    def set_queued(
        self,
        root: str | Path,
        schedule_id: str,
        fire_key: str,
        scheduled_for: datetime | None = None,
        discriminator: str = "",
    ) -> None:
        entry = self._entry(root, schedule_id)
        entry.queued_fire_key = fire_key
        entry.queued_scheduled_for = scheduled_for
        entry.queued_discriminator = discriminator
        self._save()

    def clear_queued(self, root: str | Path, schedule_id: str) -> None:
        entry = self._entry(root, schedule_id)
        entry.queued_fire_key = None
        entry.queued_scheduled_for = None
        entry.queued_discriminator = ""
        self._save()

    def advance_anchor(self, root: str | Path, schedule_id: str, scheduled_for: datetime) -> None:
        """Move ``last_scheduled_for`` forward (e.g. after a skipped/suppressed fire)."""
        self._entry(root, schedule_id).last_scheduled_for = scheduled_for
        self._save()

    def bump_eval_error(self, root: str | Path, schedule_id: str) -> int:
        s = self._entry(root, schedule_id)
        s.consecutive_eval_errors += 1
        self._save()
        return s.consecutive_eval_errors

    def clear_eval_errors(self, root: str | Path, schedule_id: str) -> None:
        self._entry(root, schedule_id).consecutive_eval_errors = 0
        self._save()

    def retire_finished(self, fire_keys: Iterable[str]) -> None:
        """Drop the given (finished) fire keys from every schedule's ``active_fire_keys``."""
        done = set(fire_keys)
        changed = False
        for s in self._data.schedules.values():
            kept = [k for k in s.active_fire_keys if k not in done]
            if len(kept) != len(s.active_fire_keys):
                s.active_fire_keys = kept
                changed = True
        if changed:
            self._save()


# -- event log ---------------------------------------------------------------------------------

# HLD §12 table: event name -> logging level.
EVENT_LEVELS: dict[str, int] = {
    "schedule.evaluated": logging.DEBUG,
    "schedule.fired": logging.INFO,
    "schedule.skipped_overlap": logging.INFO,
    "schedule.skipped_concurrency": logging.INFO,
    "schedule.missed": logging.WARNING,
    "schedule.catch_up": logging.INFO,
    "schedule.fire_failed": logging.ERROR,
    "schedule.fire_orphaned": logging.WARNING,
    "schedule.until_satisfied": logging.INFO,
    "schedule.eval_failed": logging.ERROR,
    "schedule.quarantined": logging.ERROR,
    "schedule.file_invalid": logging.ERROR,
    "schedule.enabled": logging.INFO,
    "schedule.disabled": logging.INFO,
    "schedule.request_expired": logging.WARNING,
    "schedule.governance.skipped": logging.INFO,
    "schedule.governance.reserved": logging.DEBUG,
    "schedule.governance.run_cost_capped": logging.WARNING,
    "schedule.governance.resumed": logging.INFO,
    "schedule.governance.resume_refused": logging.WARNING,
    "schedule.governance.pause_expired": logging.WARNING,
    "schedule.governance.launch_unsafe": logging.ERROR,
    "schedule.governance.finalize_failed": logging.ERROR,
    "schedule.governance.error": logging.ERROR,
    "schedule.watch_budget_exceeded": logging.WARNING,
    "webhook.received": logging.INFO,
    "webhook.rejected": logging.WARNING,
}
_COMMON_EVENT_FIELDS = (
    "workspace",
    "schedule_id",
    "fire_key",
    "scheduled_for",
    "run_id",
    "reason",
    "detail",
)


def _json_default(value: object) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value)


class ScheduleEventLog:
    """Append-only JSONL event log plus mirrored stdlib logging (HLD §12)."""

    def __init__(
        self,
        state_dir: Path,
        *,
        clock: Clock = _utc_now,
        max_bytes: int = EVENT_LOG_MAX_BYTES,
    ) -> None:
        self._path = Path(state_dir) / SCHEDULES_SUBDIR / EVENTS_FILENAME
        self._rolled = self._path.with_name(EVENTS_FILENAME + EVENTS_ROLLOVER_SUFFIX)
        self._clock = clock
        self._max_bytes = max_bytes

    def emit(self, event: str, level: int | None = None, **fields: Any) -> None:
        """Append one event and log it. *level* defaults to the HLD §12 table (INFO if unknown).
        NEVER raises: an unwritable state dir degrades to log-only."""
        lvl = level if level is not None else EVENT_LEVELS.get(event, logging.INFO)
        try:
            entry: dict[str, Any] = {
                "ts": self._clock().astimezone(UTC).isoformat(),
                "event": event,
            }
            for name in _COMMON_EVENT_FIELDS:
                entry[name] = fields.pop(name, None)
            entry.update(fields)
            line = json.dumps(entry, default=_json_default, sort_keys=False)
        except Exception:  # noqa: BLE001 - an unserialisable field must not kill the daemon
            logger.exception("could not serialise event %s", event)
            return
        logger.log(lvl, "%s %s", event, line)
        try:
            self._append(line)
        except Exception as exc:  # noqa: BLE001 - disk full / read-only dir: log-only
            logger.warning("event log %s unwritable (%s); event kept in logs only", self._path, exc)

    def _append(self, line: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if self._path.stat().st_size >= self._max_bytes:
                os.replace(self._path, self._rolled)  # single rollover: .1 is overwritten
        except FileNotFoundError:
            pass
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def read(
        self,
        limit: int | None = None,
        schedule_id: str | None = None,
        workspace_root: str | Path | None = None,
    ) -> list[dict[str, Any]]:
        """Newest-first tail spanning ``events.jsonl`` then ``events.jsonl.1``. Malformed lines
        are skipped with a warning."""
        want_ws = _resolved_root(workspace_root) if workspace_root is not None else None
        out: list[dict[str, Any]] = []
        for path in (self._path, self._rolled):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for raw in reversed(lines):
                if not raw.strip():
                    continue
                try:
                    obj = json.loads(raw)
                    if not isinstance(obj, dict):
                        raise ValueError("not an object")
                except ValueError as exc:
                    logger.warning("skipping malformed event line in %s (%s)", path, exc)
                    continue
                if schedule_id is not None and obj.get("schedule_id") != schedule_id:
                    continue
                if want_ws is not None and (
                    obj.get("workspace") is None or _resolved_root(obj["workspace"]) != want_ws
                ):
                    continue
                out.append(obj)
                if limit is not None and len(out) >= limit:
                    return out
        return out
