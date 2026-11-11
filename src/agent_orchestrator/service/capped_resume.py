"""Pause / resume / expiry of a cost-capped scheduled run (T-nI4DBf; governance HLD §9.4/§9.5).

``paused → resumed → (paused again | done)``, ``paused → expired``, ``aborted`` (final).

* :func:`request_capped_resume` is the **operator** side: it validates and writes one drop-request
  file (``kind: "resume_capped"``) into ``<state_dir>/schedules/requests/`` -- it never launches.
* :func:`drain_resume_capped` is the **engine** side and the single launch authority (ADR-0014 D10):
  it re-checks every condition under the governance flock, rewrites the fire record through
  :meth:`FireIntentStore.update_governance`, and only then asks the launcher to resume the run.
* :func:`sweep_pause_expiry` flips overdue ``paused`` records to ``expired`` (final).

Nothing here resumes anything automatically: only the drain calls ``launch_capped_resume``
(grep-tested). A resume is the same fire (same ``fire_key``), so it consumes no quota slot; it is
charged to the *original* fire's windows, so the period caps still bound the total.
"""

from __future__ import annotations

import fcntl
import json
import logging
import math
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Protocol
from zoneinfo import ZoneInfo

from ..schedules.governance import MAX_RESUME_FACTOR, MAX_USD, MIN_RESERVATION_USD
from ..schedules.windows import Period, WeekStart, windows_for
from .fire_store import (
    SCHEDULES_SUBDIR,
    FireIntentRecord,
    FireIntentStore,
)
from .governance_gate import GOVERNANCE_LOCK_NAME, LedgerPort
from .statefile import atomic_write_bytes

if TYPE_CHECKING:
    from ..schedules.models import ResolvedBinding
    from .usage_ledger import UsageLedger

logger = logging.getLogger(__name__)

REQUESTS_SUBDIR: Final = "requests"
REQUEST_KIND_RESUME_CAPPED: Final = "resume_capped"
REQUEST_TTL_SECONDS: Final = 300  # == schedule_engine.REQUEST_TTL_SECONDS (E-Sc9Rt4 rule)
_MONEY_DECIMALS: Final = 6
MAX_REQUEST_TEXT: Final = 128  # `requested_by` bound in a drop request
_REQUEST_ID_RE: Final = re.compile(r"[A-Za-z0-9_-]{1,64}")

# ``ResumeRefused.reason`` values (map 1:1 to CLI exit-3 messages and HTTP 409 bodies).
REASON_NOT_PAUSED: Final = "not_paused"
REASON_EXPIRED: Final = "expired"
REASON_ABORTED: Final = "aborted"
REASON_NO_HEADROOM: Final = "no_headroom"
REASON_BAD_AMOUNT: Final = "bad_amount"
REASON_SCHEDULE_NOT_FOUND: Final = "schedule_not_found"
REASON_OVERLAP: Final = "overlap"
REASON_LAUNCH_FAILED: Final = "launch_failed"  # drain-only: the launcher could not resume the run
RESUME_REASONS: Final = frozenset(
    {
        REASON_NOT_PAUSED,
        REASON_EXPIRED,
        REASON_ABORTED,
        REASON_NO_HEADROOM,
        REASON_BAD_AMOUNT,
        REASON_SCHEDULE_NOT_FOUND,
        REASON_OVERLAP,
        REASON_LAUNCH_FAILED,
    }
)

EVENT_RESUMED: Final = "schedule.governance.resumed"
EVENT_RESUME_REFUSED: Final = "schedule.governance.resume_refused"
EVENT_PAUSE_EXPIRED: Final = "schedule.governance.pause_expired"

REQUESTED_BY_CLI: Final = "cli"


def _money(value: float) -> float:
    return round(value, _MONEY_DECIMALS)


class ResumeRefused(Exception):
    """A resume request is refused. ``reason`` is one of :data:`RESUME_REASONS`; ``remaining_usd``
    and ``window`` carry the headroom that was checked (``no_headroom`` / ``bad_amount``)."""

    def __init__(
        self,
        reason: str,
        message: str,
        *,
        remaining_usd: float | None = None,
        window: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.remaining_usd = remaining_usd
        self.window = window


# -- ports ---------------------------------------------------------------------------------------


class EventSink(Protocol):
    def emit(self, event: str, level: int | None = None, **fields: Any) -> None: ...


class CappedResumeLauncher(Protocol):
    """What the drain needs from the launcher (``ScheduledLauncher.launch_capped_resume``)."""

    def launch_capped_resume(self, rec: FireIntentRecord, new_cap_usd: float) -> Any:
        """Resume ``rec.run_id`` with ``--cost-cap-usd new_cap_usd``; returns the launch record
        (``launch_id``/``pid``). Raises on failure."""
        ...


Clock = Callable[[], datetime]


@dataclass(frozen=True)
class ResumeCappedRequest:
    """The drop-request file body (HLD §9.5)."""

    workspace_root: str
    schedule_id: str
    run_id: str
    add_usd: float
    new_cap_usd: float
    requested_by: str
    requested_at: datetime
    request_id: str

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": REQUEST_KIND_RESUME_CAPPED,
            "workspace_root": self.workspace_root,
            "schedule_id": self.schedule_id,
            "run_id": self.run_id,
            "add_usd": self.add_usd,
            "new_cap_usd": self.new_cap_usd,
            "requested_by": self.requested_by,
            "requested_at": self.requested_at.isoformat(),
            "request_id": self.request_id,
        }

    @classmethod
    def from_json(cls, raw: object) -> ResumeCappedRequest | None:
        """Parse a drained file; ``None`` if malformed (the drain discards it as invalid)."""
        if not isinstance(raw, dict) or raw.get("kind") != REQUEST_KIND_RESUME_CAPPED:
            return None
        try:
            at = datetime.fromisoformat(str(raw["requested_at"]))
            add, cap = float(raw["add_usd"]), float(raw["new_cap_usd"])
            ws, sid, rid = raw["workspace_root"], raw["schedule_id"], raw["run_id"]
            by, req_id = str(raw.get("requested_by", REQUESTED_BY_CLI)), str(raw["request_id"])
        except (KeyError, TypeError, ValueError):
            return None
        if not (isinstance(ws, str) and isinstance(sid, str) and isinstance(rid, str)):
            return None
        if at.tzinfo is None or not (math.isfinite(add) and math.isfinite(cap)):
            return None
        # Free-text fields land in the fire record and events: bound them (T-lTW88y audit).
        if len(by) > MAX_REQUEST_TEXT or not _REQUEST_ID_RE.fullmatch(req_id):
            return None
        return cls(ws, sid, rid, add, cap, by, at, req_id)


@dataclass(frozen=True)
class ResumeCheck:
    """A request that passed every check: what to resume with."""

    rec: FireIntentRecord
    add_usd: float
    new_cap_usd: float
    remaining_usd: float


# -- helpers -------------------------------------------------------------------------------------


def requests_dir(state_dir: Path) -> Path:
    return Path(state_dir) / SCHEDULES_SUBDIR / REQUESTS_SUBDIR


def is_governed_run(workspace_root: str | Path, run_id: str, *, fires: FireIntentStore) -> bool:
    """AC12 (M5): does a governing fire record own *run_id*? Generic resume routes use this to
    refuse (dashboard 409) or warn (CLI) instead of bypassing the governed resume path."""
    rec = fires.find_by_run_id(workspace_root, run_id)
    return rec is not None and rec.governance is not None


def max_add(binding: ResolvedBinding, remaining_usd: float) -> float:
    """Largest single top-up: ``per_run_usd * MAX_RESUME_FACTOR``, or (no per-run cap) whatever
    headroom is left."""
    cost = binding.limits.cost if binding.limits is not None else None
    if cost is not None and cost.per_run_usd is not None:
        return _money(cost.per_run_usd * MAX_RESUME_FACTOR)
    # No per-run cap: headroom bounds it, and with no period cap either (remaining == inf) the
    # absolute MAX_USD does (T-lTW88y audit: an unbounded top-up would mint an unbounded cap).
    return min(max(0.0, remaining_usd), MAX_USD)


def default_add(binding: ResolvedBinding) -> float:
    """ "Extend by the same": ``per_run_usd``, else :data:`MIN_RESERVATION_USD`."""
    cost = binding.limits.cost if binding.limits is not None else None
    if cost is not None and cost.per_run_usd is not None:
        return cost.per_run_usd
    return MIN_RESERVATION_USD


def _remaining(
    rec: FireIntentRecord, binding: ResolvedBinding, ledger: LedgerPort
) -> tuple[float, str | None]:
    """Headroom in the ORIGINAL fire's windows: ``min(max_usd - actual - reserved)`` over the
    period caps, and the name/bounds of the binding window. ``(inf, None)`` without period caps."""
    cost = binding.limits.cost if binding.limits is not None else None
    g = rec.governance
    if cost is None or not cost.periods or g is None:
        return math.inf, None
    periods: list[Period] = [c.period for c in cost.periods]
    week_start: WeekStart = g.week_start or binding.week_start or "monday"  # type: ignore[assignment]
    tz = ZoneInfo(g.tz or binding.timezone or "UTC")
    windows = windows_for(rec.scheduled_for, periods, tz, week_start)
    usage = ledger.usage(rec.workspace_root, rec.schedule_id, windows)
    remaining, where = math.inf, None
    for c in cost.periods:
        left = _money(c.max_usd - usage[c.period].actual_usd - usage[c.period].reserved_usd)
        if left < remaining:
            w = windows[c.period]
            remaining = left
            where = f"{c.period} window {w.start.isoformat()} .. {w.end.isoformat()}"
    return remaining, where


def check_resume(
    *,
    binding: ResolvedBinding | None,
    rec: FireIntentRecord | None,
    schedule_id: str,
    add_usd: float | None,
    now: datetime,
    ledger: UsageLedger,
) -> ResumeCheck:
    """The validation shared by the request and the drain re-check, in the AC1 order. Raises
    :class:`ResumeRefused`; never writes."""
    if binding is None:
        raise ResumeRefused(REASON_SCHEDULE_NOT_FOUND, f"no such schedule {schedule_id!r}")
    if rec is None or rec.schedule_id != schedule_id or rec.governance is None:
        raise ResumeRefused(
            REASON_SCHEDULE_NOT_FOUND,
            f"no governed run of schedule {schedule_id!r} with that run id",
        )
    g = rec.governance
    if g.cap_state == "aborted":
        raise ResumeRefused(REASON_ABORTED, "aborted by policy (on_cap: abort); cannot resume")
    if g.cap_state == "expired":
        raise ResumeRefused(REASON_EXPIRED, "the pause expired; cannot resume through governance")
    if g.cap_state != "paused":
        raise ResumeRefused(REASON_NOT_PAUSED, f"run is not paused (cap_state={g.cap_state})")
    if g.pause_expires_at is None or now >= g.pause_expires_at:
        raise ResumeRefused(REASON_EXPIRED, "the pause expired; cannot resume through governance")

    ledger.invalidate(rec.workspace_root, rec.schedule_id)
    remaining, where = _remaining(rec, binding, ledger)
    add = default_add(binding) if add_usd is None else add_usd
    if not math.isfinite(add) or add <= 0:
        raise ResumeRefused(REASON_BAD_AMOUNT, f"add_usd must be a finite number > 0, got {add!r}")
    ceiling = max_add(binding, remaining)
    if remaining <= 0 or (math.isfinite(remaining) and add > remaining and ceiling >= remaining):
        raise ResumeRefused(
            REASON_NO_HEADROOM,
            f"only ${max(remaining, 0.0):.2f} left in the {where} (requested ${add:.2f})",
            remaining_usd=max(remaining, 0.0),
            window=where,
        )
    if add > ceiling + 1e-9:
        raise ResumeRefused(
            REASON_BAD_AMOUNT,
            f"add_usd {add} exceeds the per-resume maximum ${ceiling:.2f}",
            remaining_usd=None if math.isinf(remaining) else remaining,
            window=where,
        )
    if math.isfinite(remaining) and add > remaining + 1e-9:
        raise ResumeRefused(
            REASON_NO_HEADROOM,
            f"only ${remaining:.2f} left in the {where} (requested ${add:.2f})",
            remaining_usd=remaining,
            window=where,
        )
    new_cap = _money(ledger.engine_cost_usd(rec) + add)  # L3: the engine's own metric
    return ResumeCheck(rec, _money(add), new_cap, remaining)


# -- operator side -------------------------------------------------------------------------------


def request_capped_resume(
    workspace_root: str,
    schedule_id: str,
    run_id: str,
    add_usd: float | None = None,
    by: str = REQUESTED_BY_CLI,
    *,
    state_dir: Path,
    binding: ResolvedBinding | None,
    fires: FireIntentStore,
    ledger: UsageLedger,
    clock: Clock,
    new_id: Callable[[], str] = lambda: uuid.uuid4().hex,
    drain: Callable[[datetime], list[ResumeOutcome]] | None = None,
) -> ResumeCappedRequest:
    """Validate and write one ``resume_capped`` drop request (AC1/AC2). Returns the request.

    *drain* is the in-process path (no daemon live): the caller passes the engine's
    :meth:`~.schedule_engine.ScheduleEngine.drain_resume_requests` and the request is drained
    immediately; a drain refusal is re-raised as :class:`ResumeRefused`. With a live daemon the
    caller passes ``None`` and the engine picks the file up on its next tick.
    """
    now = clock()
    rec = fires.find_by_run_id(workspace_root, run_id)
    chk = check_resume(
        binding=binding, rec=rec, schedule_id=schedule_id, add_usd=add_usd, now=now, ledger=ledger
    )
    req = ResumeCappedRequest(
        workspace_root=str(workspace_root),
        schedule_id=schedule_id,
        run_id=run_id,
        add_usd=chk.add_usd,
        new_cap_usd=chk.new_cap_usd,
        requested_by=by,
        requested_at=now,
        request_id=new_id(),
    )
    directory = requests_dir(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(directory / f"{req.request_id}.json", json.dumps(req.to_json()).encode())
    if drain is not None:
        for outcome in drain(now):
            if outcome.request_id == req.request_id and outcome.refused is not None:
                raise outcome.refused
    return req


# -- engine side ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class ResumeOutcome:
    """Result of draining one request: ``refused is None`` means the run was relaunched."""

    request_id: str
    run_id: str
    refused: ResumeRefused | None = None


def drain_resume_capped(
    req: ResumeCappedRequest,
    *,
    binding: ResolvedBinding | None,
    fires: FireIntentStore,
    ledger: UsageLedger,
    launcher: CappedResumeLauncher | None,
    lock_path: Path,
    events: EventSink,
    now: datetime,
    other_run_active: Callable[[FireIntentRecord], bool],
    mark_active: Callable[[FireIntentRecord], None],
) -> ResumeOutcome:
    """Perform one resume as the single launch authority (AC3). Never raises."""

    def refuse(exc: ResumeRefused, rec: FireIntentRecord | None = None) -> ResumeOutcome:
        events.emit(
            EVENT_RESUME_REFUSED,
            level=logging.WARNING,
            workspace=req.workspace_root,
            schedule_id=req.schedule_id,
            fire_key=rec.fire_key if rec else None,
            run_id=req.run_id,
            reason=exc.reason,
            detail=str(exc),
            by=req.requested_by,
            request_id=req.request_id,
        )
        return ResumeOutcome(req.request_id, req.run_id, exc)

    rec: FireIntentRecord | None = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                rec = fires.find_by_run_id(req.workspace_root, req.run_id)
                chk = check_resume(
                    binding=binding,
                    rec=rec,
                    schedule_id=req.schedule_id,
                    add_usd=req.add_usd,
                    now=now,
                    ledger=ledger,
                )
                assert chk.rec.governance is not None
                if binding is not None and binding.overlap != "allow" and other_run_active(chk.rec):
                    raise ResumeRefused(
                        REASON_OVERLAP, "another run of this schedule is active (overlap policy)"
                    )
                before = chk.rec.governance
                resumes = [
                    *before.resumes,
                    {
                        "at": now.isoformat(),
                        "by": req.requested_by,
                        "add_usd": chk.add_usd,
                        "new_cap_usd": chk.new_cap_usd,
                        "request_id": req.request_id,
                    },
                ]
                fires.update_governance(
                    chk.rec.fire_key,
                    cap_state="resumed",
                    reserved_usd=chk.new_cap_usd,
                    run_cost_cap_usd=chk.new_cap_usd,
                    final=False,
                    resumes=resumes,
                )
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except ResumeRefused as exc:
        return refuse(exc, rec)
    except Exception as exc:  # noqa: BLE001 - the drain must never break the tick
        logger.exception("resume of %s failed", req.run_id)
        return refuse(ResumeRefused(REASON_LAUNCH_FAILED, f"resume bookkeeping failed: {exc}"), rec)

    # Launch OUTSIDE the lock (as the pre-fire path does); roll the record back if it fails so a
    # paused run is not left claiming it is running.
    try:
        if launcher is None:
            raise RuntimeError("no launcher configured")
        launched = launcher.launch_capped_resume(chk.rec, chk.new_cap_usd)
    except Exception as exc:  # noqa: BLE001
        logger.warning("resume launch of %s failed: %s", req.run_id, exc)
        try:
            fires.update_governance(
                chk.rec.fire_key,
                cap_state="paused",
                reserved_usd=before.reserved_usd,
                run_cost_cap_usd=before.run_cost_cap_usd,
                final=before.final,
                resumes=before.resumes,
            )
        except Exception:  # noqa: BLE001
            logger.exception("could not roll back resume of %s", chk.rec.fire_key)
        return refuse(ResumeRefused(REASON_LAUNCH_FAILED, f"launch failed: {exc}"), chk.rec)

    try:
        stamp: dict[str, Any] = {}
        if getattr(launched, "launch_id", None):
            stamp["launch_id"], stamp["pid"] = launched.launch_id, launched.pid
        if stamp:
            fires.update(chk.rec.fire_key, **stamp)  # next resume reads the NEWEST launch record
        mark_active(chk.rec)
    except Exception:  # noqa: BLE001 - the child is running; bookkeeping must not undo that
        logger.exception("post-resume bookkeeping failed for %s", chk.rec.fire_key)
    events.emit(
        EVENT_RESUMED,
        workspace=req.workspace_root,
        schedule_id=req.schedule_id,
        fire_key=chk.rec.fire_key,
        run_id=req.run_id,
        add_usd=chk.add_usd,
        new_cap_usd=chk.new_cap_usd,
        by=req.requested_by,
        request_id=req.request_id,
    )
    return ResumeOutcome(req.request_id, req.run_id)


def sweep_pause_expiry(
    fires: FireIntentStore, events: EventSink, now: datetime, *, workspaces: list[str] | None = None
) -> list[str]:
    """Flip every ``paused`` record with ``now >= pause_expires_at`` to ``expired`` (final) and
    emit one WARNING each (AC6). Idempotent; never touches a run's ``state.json``. Returns the
    expired fire keys. *workspaces* limits the scan (default: every known fire record)."""
    expired: list[str] = []
    for rec in fires.list(status="launched"):
        g = rec.governance
        if g is None or g.cap_state != "paused" or g.pause_expires_at is None:
            continue
        if workspaces is not None and rec.workspace_root not in workspaces:
            continue
        if now < g.pause_expires_at:
            continue
        fires.update_governance(rec.fire_key, cap_state="expired", final=True)
        expired.append(rec.fire_key)
        events.emit(
            EVENT_PAUSE_EXPIRED,
            level=logging.WARNING,
            workspace=rec.workspace_root,
            schedule_id=rec.schedule_id,
            fire_key=rec.fire_key,
            run_id=rec.run_id,
            pause_expires_at=g.pause_expires_at,
        )
    return expired


def parse_request_age_ok(req: ResumeCappedRequest, now: datetime) -> bool:
    """Requests older than :data:`REQUEST_TTL_SECONDS` are discarded (E-Sc9Rt4 rule)."""
    return now - req.requested_at <= timedelta(seconds=REQUEST_TTL_SECONDS)


__all__ = [
    "EVENT_PAUSE_EXPIRED",
    "EVENT_RESUMED",
    "EVENT_RESUME_REFUSED",
    "GOVERNANCE_LOCK_NAME",
    "REQUEST_KIND_RESUME_CAPPED",
    "RESUME_REASONS",
    "CappedResumeLauncher",
    "ResumeCappedRequest",
    "ResumeCheck",
    "ResumeOutcome",
    "ResumeRefused",
    "check_resume",
    "default_add",
    "drain_resume_capped",
    "is_governed_run",
    "max_add",
    "parse_request_age_ok",
    "request_capped_resume",
    "requests_dir",
    "sweep_pause_expiry",
]
