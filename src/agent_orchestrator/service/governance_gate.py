"""Pre-fire governance gate and reservation (T-PpE5lf; governance HLD §8, §11).

Two layers, one authority:

* :func:`gate` is **pure**: given the effective limits, the instant being fired and a usage
  ledger it says admit/skip and (for cost limits) how much to reserve. ``ScheduleEngine.evaluate``
  calls it to explain a decision.
* :func:`check_and_reserve` is the **authoritative** check, called by ``ScheduledLauncher`` under
  an exclusive flock on ``<state_dir>/schedules/usage.lock``: it re-gates against freshly read
  records, reads the fixed prompt once, and writes the ``intended`` record -- reservation and
  prompt snapshot included -- in one atomic write, then releases the lock before any spawn.

The usage ledger proper (``T-Uz0Uol``) is not merged yet. This module therefore defines the
:class:`UsageLedger` protocol the gate needs plus :class:`FireRecordLedger`, an interim
implementation that derives window usage from fire records alone (run counts exact; cost is the
record's persisted high-water mark ``governance.cost_observed_usd`` and its open reservation, with
no ``state.json`` probe and no blind-executor rule). ``T-Uz0Uol`` replaces it behind the same
protocol.
"""

from __future__ import annotations

import fcntl
import hashlib
import math
import os
import stat
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Protocol
from zoneinfo import ZoneInfo

from ..schedules.governance import (
    MAX_PROMPT_BYTES,
    MIN_RESERVATION_USD,
    LimitsSpec,
    limits_digest,
)
from ..schedules.windows import Period, WeekStart, Window, windows_for
from .fire_store import (
    FireGovernance,
    FireIntentRecord,
    FireIntentStore,
    PromptMeta,
)

if TYPE_CHECKING:
    from ..schedules.models import ResolvedBinding

GOVERNANCE_LOCK_NAME: Final = "usage.lock"
# cap states in which a run can spend no more (shared by the ledger and the retire hook)
GOVERNANCE_CAPPED_STATES: Final = frozenset({"paused", "aborted", "expired"})

REASON_QUOTA_EXHAUSTED: Final = "quota_exhausted"
REASON_COST_CAP_REACHED: Final = "cost_cap_reached"
REASON_PROMPT_MISSING: Final = "prompt_missing"
REASON_PROMPT_CHANGED: Final = "prompt_changed"
REASON_GOVERNANCE_ERROR: Final = "governance_error"

DETAIL_TOO_LARGE: Final = "too_large"
DETAIL_NOT_UTF8: Final = "not_utf8"

CENTS: Final = 100
_FLOAT_EPS_DECIMALS: Final = 6  # money compares at micro-dollars: 0.1 + 0.2 must equal 0.3
SHA_PREFIX_LEN: Final = 12
DIGEST_PREFIX: Final = "sha256:"
# A directory mtime this close to "now" may not yet reflect a write that lands in the same
# filesystem timestamp tick, so the record index is rescanned rather than trusted.
FS_SETTLE_NS: Final = 2_000_000_000


# -- ledger contract ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WindowUsage:
    """What one calendar window has consumed: runs started, spend (actual) and money still held
    for runs that can still spend (reserved)."""

    runs: int = 0
    actual: float = 0.0
    reserved: float = 0.0


class UsageLedger(Protocol):
    """Window usage for one schedule. ``T-Uz0Uol`` provides the production implementation."""

    def usage(
        self, workspace_root: str, schedule_id: str, windows: Mapping[Period, Window]
    ) -> dict[Period, WindowUsage]: ...

    def invalidate(self, workspace_root: str, schedule_id: str) -> None:
        """Drop cached reads so records written by another process/thread are seen."""
        ...


@dataclass
class _Index:
    dir_mtime_ns: int = -1
    scanned_ns: int = 0
    files: dict[str, tuple[tuple[int, int, int], FireIntentRecord | None]] = field(
        default_factory=dict
    )
    by_schedule: dict[str, list[FireIntentRecord]] = field(default_factory=dict)
    dirty: bool = True


class FireRecordLedger:
    """Interim :class:`UsageLedger`: usage derived from fire records, cached by mtime.

    One directory ``stat`` decides whether the per-workspace index is trusted; when it is not,
    the directory is rescanned but only files whose ``(mtime, size, inode)`` changed are parsed
    again, so a tick over hundreds of schedules costs one cheap pass, not one per schedule.
    """

    def __init__(
        self, fires: FireIntentStore, *, fs_now_ns: Callable[[], int] = time.time_ns
    ) -> None:
        self._fires = fires
        self._fs_now_ns = fs_now_ns
        self._index: dict[str, _Index] = {}
        self.scans = 0  # observable: how many directory rescans happened (cache tests)

    def invalidate(self, workspace_root: str, schedule_id: str) -> None:
        self._index.setdefault(str(Path(workspace_root).resolve()), _Index()).dirty = True

    def _refresh(self, workspace_root: str) -> _Index:
        key = str(Path(workspace_root).resolve())
        idx = self._index.setdefault(key, _Index())
        directory = self._fires.directory(key)
        try:
            dir_mtime = directory.stat().st_mtime_ns
        except OSError:
            idx.files, idx.by_schedule, idx.dirty = {}, {}, False
            return idx
        if (
            not idx.dirty
            and idx.dir_mtime_ns == dir_mtime
            and idx.scanned_ns - dir_mtime > FS_SETTLE_NS
        ):
            return idx
        self.scans += 1
        scanned_ns = self._fs_now_ns()
        files: dict[str, tuple[tuple[int, int, int], FireIntentRecord | None]] = {}
        by_schedule: dict[str, list[FireIntentRecord]] = {}
        with os.scandir(directory) as it:
            for entry in it:
                if not entry.name.endswith(".json"):
                    continue
                try:
                    st = entry.stat()
                except OSError:
                    continue
                sig = (st.st_mtime_ns, st.st_size, st.st_ino)
                cached = idx.files.get(entry.name)
                rec = (
                    cached[1]
                    if cached is not None and cached[0] == sig
                    else self._fires.read_file(Path(entry.path))
                )
                files[entry.name] = (sig, rec)
                if rec is not None:
                    by_schedule.setdefault(rec.schedule_id, []).append(rec)
        idx.files, idx.by_schedule = files, by_schedule
        idx.dir_mtime_ns, idx.scanned_ns, idx.dirty = dir_mtime, scanned_ns, False
        return idx

    def usage(
        self, workspace_root: str, schedule_id: str, windows: Mapping[Period, Window]
    ) -> dict[Period, WindowUsage]:
        records = self._refresh(workspace_root).by_schedule.get(schedule_id, [])
        out: dict[Period, WindowUsage] = {}
        for period, w in windows.items():
            runs, actual, reserved = 0, 0.0, 0.0
            for rec in records:
                # Bound-based membership (HLD §7 L5 calls the label authoritative; the two only
                # differ across the pre-2011 tzdata transitions listed in windows.py: accepted).
                if not (w.start <= rec.scheduled_for < w.end):
                    continue
                c = _charge(rec)
                runs, actual, reserved = runs + c.runs, actual + c.actual, reserved + c.reserved
            out[period] = WindowUsage(runs, actual, reserved)
        return out


def _charge(rec: FireIntentRecord) -> WindowUsage:
    """One fire's contribution (HLD §10.2, record-only subset). Suppressed and launch-failed
    fires cost nothing; any other record is one run (``orphaned`` included: its quota slot stays
    spent), holding its reservation until the retire hook marks the governance ``final``."""
    if rec.status in ("suppressed", "launch_failed"):
        return WindowUsage()
    g = rec.governance
    if g is None:
        return WindowUsage(runs=1)
    observed = g.cost_observed_usd
    if rec.status == "orphaned" or g.final or g.cap_state in GOVERNANCE_CAPPED_STATES:
        return WindowUsage(1, observed, 0.0)  # nothing can spend any more
    held = max(0.0, (g.reserved_usd or 0.0) - observed)
    return WindowUsage(1, observed, held)


# -- results -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class PromptSnapshot:
    """The exact bytes a fire runs with (read once)."""

    path: str | None = None
    sha256: str | None = None
    bytes: int = 0
    text: str | None = None

    def meta(self) -> PromptMeta | None:
        if self.path is None or self.sha256 is None:
            return None
        return PromptMeta(path=self.path, sha256=self.sha256, bytes=self.bytes)


@dataclass(frozen=True)
class GateResult:
    admit: bool
    reason: str | None = None
    reservation_usd: float | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    prompt: PromptSnapshot | None = None  # set by check_and_reserve on admit


# -- helpers -----------------------------------------------------------------------------------


def _money(value: float) -> float:
    return round(value, _FLOAT_EPS_DECIMALS)


def round_down_cents(value: float) -> float:
    return math.floor(_money(value) * CENTS + 1e-9) / CENTS


def digest_of(limits: LimitsSpec) -> str:
    return DIGEST_PREFIX + limits_digest(limits)


def skeleton(
    limits: LimitsSpec, tz: str, week_start: str, detail: Mapping[str, Any]
) -> FireGovernance:
    """The ``governance`` object of a suppressed fire (HLD §6.4)."""
    return FireGovernance(
        limits_digest=digest_of(limits), tz=tz, week_start=week_start, detail=dict(detail)
    )


def _iso(value: datetime) -> str:
    return value.isoformat()


# -- the gate ----------------------------------------------------------------------------------


def gate(
    binding: ResolvedBinding,
    limits: LimitsSpec,
    scheduled_for: datetime,
    *,
    ledger: UsageLedger,
    workspace_root: str,
    tz: ZoneInfo,
    week_start: WeekStart,
) -> GateResult:
    """HLD §8.2. Quotas first, in declared order (first failing reported); then cost."""
    periods: list[Period] = [q.period for q in limits.quotas]
    if limits.cost is not None:
        periods += [c.period for c in limits.cost.periods if c.period not in periods]
    windows = windows_for(scheduled_for, periods, tz, week_start)
    usage = ledger.usage(workspace_root, binding.id, windows) if windows else {}

    for q in limits.quotas:
        used = usage[q.period].runs
        if used >= q.max_runs:
            return GateResult(
                False,
                REASON_QUOTA_EXHAUSTED,
                None,
                {
                    "period": q.period,
                    "used": used,
                    "max": q.max_runs,
                    "resets_at": _iso(windows[q.period].end),
                },
            )

    cost = limits.cost
    if cost is None:
        return GateResult(True)
    remaining, argmin = math.inf, None
    for c in cost.periods:
        left = _money(c.max_usd - usage[c.period].actual - usage[c.period].reserved)
        if left < remaining:
            remaining, argmin = left, c.period
    if cost.per_run_usd is not None:
        needed, reservation = cost.per_run_usd, cost.per_run_usd
    elif argmin is not None:
        needed, reservation = MIN_RESERVATION_USD, round_down_cents(remaining)
    else:  # on_cap-only cost block: nothing to reserve against
        return GateResult(True)
    if remaining < needed:
        detail: dict[str, Any] = {
            "remaining_usd": max(0.0, remaining),
            "needed_usd": needed,
        }
        if argmin is not None:
            detail["period"] = argmin
            detail["resets_at"] = _iso(windows[argmin].end)
        return GateResult(False, REASON_COST_CAP_REACHED, None, detail)
    return GateResult(True, reservation_usd=reservation)


# -- fixed prompt (HLD §11) --------------------------------------------------------------------


def _read_regular_file(path: Path, rel: str) -> bytes | GateResult:
    """Open ONCE (``O_NONBLOCK`` so a FIFO swapped in cannot block while the usage flock is held
    -- L6), ``fstat`` the descriptor, then read at most ``MAX_PROMPT_BYTES + 1`` bytes."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode):
            return GateResult(False, REASON_PROMPT_MISSING, None, {"path": rel})
        data = fh.read(MAX_PROMPT_BYTES + 1)
    if len(data) > MAX_PROMPT_BYTES:
        return GateResult(
            False, REASON_PROMPT_MISSING, None, {"path": rel, "detail": DETAIL_TOO_LARGE}
        )
    return data


def read_prompt(binding: ResolvedBinding, workspace_root: str) -> PromptSnapshot | GateResult:
    """Read the binding's prompt file ONCE; those bytes are what runs. A skip is returned as a
    non-admitting :class:`GateResult` (``prompt_missing`` / ``prompt_changed``)."""
    rel = binding.prompt_file
    if not rel:
        return PromptSnapshot()
    root = Path(workspace_root).resolve()
    path = (root / rel).resolve()
    if not path.is_relative_to(root) or Path(rel).is_absolute():
        return GateResult(
            False, REASON_PROMPT_MISSING, None, {"path": rel, "detail": "escapes_root"}
        )
    try:
        data = _read_regular_file(path, rel)
    except (FileNotFoundError, NotADirectoryError):
        return GateResult(False, REASON_PROMPT_MISSING, None, {"path": rel})
    except OSError as exc:
        return GateResult(
            False, REASON_PROMPT_MISSING, None, {"path": rel, "detail": f"unreadable: {exc}"}
        )
    if isinstance(data, GateResult):
        return data
    sha = hashlib.sha256(data).hexdigest()
    if binding.prompt_sha256 and sha != binding.prompt_sha256:
        return GateResult(
            False,
            REASON_PROMPT_CHANGED,
            None,
            {
                "path": rel,
                "expected": binding.prompt_sha256[:SHA_PREFIX_LEN],
                "actual": sha[:SHA_PREFIX_LEN],
            },
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return GateResult(
            False, REASON_PROMPT_MISSING, None, {"path": rel, "detail": DETAIL_NOT_UTF8}
        )
    return PromptSnapshot(path=rel, sha256=sha, bytes=len(data), text=text)


# -- authoritative check-and-reserve -----------------------------------------------------------


def check_and_reserve(
    *,
    binding: ResolvedBinding,
    limits: LimitsSpec,
    workspace_root: str,
    schedule_id: str,
    fire_key: str,
    scheduled_for: datetime,
    effective_due: datetime | None,
    ledger: UsageLedger,
    fires: FireIntentStore,
    lock_path: Path,
    now: datetime,
    run_id: str | None = None,
) -> GateResult:
    """Re-gate under the exclusive flock and, on admit, write the ``intended`` record carrying the
    reservation and prompt snapshot in ONE atomic write. The lock is released on return, i.e.
    before the caller spawns anything. Raises ``FireAlreadyRecordedError`` if the key already has a
    record (the caller treats that as "already launched")."""
    tz = ZoneInfo(binding.timezone or "UTC")
    week_start: WeekStart = binding.week_start or "monday"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            ledger.invalidate(workspace_root, schedule_id)
            g = gate(
                binding,
                limits,
                scheduled_for,
                ledger=ledger,
                workspace_root=workspace_root,
                tz=tz,
                week_start=week_start,
            )
            if g.admit:
                snap = read_prompt(binding, workspace_root)
                if isinstance(snap, GateResult):
                    g = snap
                else:
                    g = GateResult(True, None, g.reservation_usd, g.detail, snap)
            if not g.admit:
                fires.write_suppressed(
                    fire_key,
                    g.reason or REASON_GOVERNANCE_ERROR,
                    workspace_root=workspace_root,
                    schedule_id=schedule_id,
                    scheduled_for=scheduled_for,
                    effective_due=effective_due,
                    governance=skeleton(limits, str(tz), week_start, g.detail),
                )
                return g
            cost = limits.cost
            fires.write(
                FireIntentRecord(
                    fire_key=fire_key,
                    workspace_root=workspace_root,
                    schedule_id=schedule_id,
                    scheduled_for=scheduled_for,
                    effective_due=effective_due,
                    created_at=now,
                    status="intended",
                    run_id=run_id,  # M1: deterministic, so reconcile can find the run dir
                    governance=FireGovernance(
                        limits_digest=digest_of(limits),
                        tz=str(tz),
                        week_start=week_start,
                        prompt=g.prompt.meta() if g.prompt else None,
                        reserved_usd=g.reservation_usd,
                        run_cost_cap_usd=g.reservation_usd,
                        on_cap=cost.on_cap if cost else None,
                        pause_ttl_seconds=cost.pause_ttl_seconds if cost else None,
                    ),
                )
            )
            return g
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


__all__ = [
    "GOVERNANCE_CAPPED_STATES",
    "GOVERNANCE_LOCK_NAME",
    "REASON_COST_CAP_REACHED",
    "REASON_GOVERNANCE_ERROR",
    "REASON_PROMPT_CHANGED",
    "REASON_PROMPT_MISSING",
    "REASON_QUOTA_EXHAUSTED",
    "FireRecordLedger",
    "GateResult",
    "PromptSnapshot",
    "UsageLedger",
    "WindowUsage",
    "check_and_reserve",
    "digest_of",
    "gate",
    "read_prompt",
    "round_down_cents",
    "skeleton",
]
