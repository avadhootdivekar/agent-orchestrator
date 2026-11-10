"""Usage ledger (T-Uz0Uol; governance HLD §10, ADR-0025 D3).

Answers one question: for a schedule, in a set of windows, how many invocations and how much
spend (actual + reserved)? It stores **nothing** of its own. Totals are derived on demand from the
fire records plus each run's ``state.json`` and ``summary/summary.json``; the only persisted datum
is the monotonic high-water mark ``governance.cost_observed_usd`` on the fire record.

Import direction (no cycle): ``usage_ledger`` -> ``fire_store``. ``FireGovernance`` is defined in
``fire_store`` (it is part of the record schema) and re-exported here.

Cost metrics (L3): ``cost_observed_usd`` = engine cost (``compute_run_usage_totals``) **plus** the
run summariser's ``summary_cost_usd``; the engine's own cap gate sees the first term only. Use
:meth:`UsageLedger.engine_cost_usd` when comparing against the engine's metric.

In-run under-count (M1): a killed attempt reports no usage, so a run with
``attempts_without_actuals > 0`` has spend the ledger cannot see. Such a run is ``partial`` and,
while the fire's window is open, is charged ``max(observed, reserved)``. After the window closes
``partial`` falls back to the best-known observed value; a ``unknown`` (blind) run is always
charged ``max(observed, reserved)``.
"""

from __future__ import annotations

import bisect
import json
import logging
import os
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from ..models import RunState, compute_run_usage_totals
from ..schedules.windows import Period, Window
from ..summarizer import SUMMARY_DIR_NAME, SUMMARY_META_FILE
from .fire_store import CostConfidence, FireGovernance, FireIntentRecord, FireIntentStore

logger = logging.getLogger(__name__)

__all__ = [
    "COST_REPORTING_EXECUTORS",
    "EVENT_COST_UNKNOWN",
    "HWM_PERSIST_STEP_USD",
    "STATE_PROBE_GRACE_SECONDS",
    "Charge",
    "FireGovernance",
    "RunProbe",
    "UsageLedger",
    "WindowUsage",
    "probe_run",
    "run_confidence",
]

# Executors whose agents report cost (``ao schedule validate`` warns about the rest).
COST_REPORTING_EXECUTORS: Final = frozenset({"claude_cli"})
# A launched run with no state.json this long after its fire record was written is "blind".
STATE_PROBE_GRACE_SECONDS: Final = 300
HWM_PERSIST_STEP_USD: Final = 0.01
EVENT_COST_UNKNOWN: Final = "schedule.governance.cost_unknown"
PROBE_CACHE_MAX: Final = 4096
# A directory mtime this close to "now" may not reflect a write in the same timestamp tick, so the
# record index is rescanned rather than trusted.
FS_SETTLE_NS: Final = 2_000_000_000
_MONEY_DECIMALS: Final = 6
_RUNS_SUBPATH: Final = (".orchestrator", "runs")
_STATE_FILE: Final = "state.json"
_TERMINAL_UNSETTLED: Final = frozenset({"pending", "running", "not_taken", "skipped", "killed"})
# worst-of order for aggregation
_CONF_RANK: Final[dict[str, int]] = {"actual": 0, "pending": 1, "partial": 2, "unknown": 3}

RunStateLoader = Callable[[str, str], RunState | None]
SummaryCostLoader = Callable[[str, str], float]
EventSink = Callable[..., None]


def _money(v: float) -> float:
    return round(v, _MONEY_DECIMALS)


def _utc_now() -> datetime:
    """Production default clock; tests inject a fixed one."""
    return datetime.now(UTC)


def run_dir(workspace_root: str, run_id: str) -> Path:
    return Path(workspace_root).joinpath(*_RUNS_SUBPATH, run_id)


# -- probes ------------------------------------------------------------------------------------


class RunProbe:
    """Reads ``state.json`` keyed by ``(path, st_mtime_ns, st_size)``; a missing or corrupt file
    yields ``None`` and never raises. Bounded LRU so a long-lived service does not grow."""

    def __init__(self, max_entries: int = PROBE_CACHE_MAX) -> None:
        self._max = max_entries
        self._cache: OrderedDict[str, tuple[tuple[int, int], RunState]] = OrderedDict()
        self.parses = 0  # observable for cache tests

    def __call__(self, workspace_root: str, run_id: str) -> RunState | None:
        path = run_dir(workspace_root, run_id) / _STATE_FILE
        try:
            st = path.stat()
        except OSError:
            return None
        sig = (st.st_mtime_ns, st.st_size)
        key = str(path)
        hit = self._cache.get(key)
        if hit is not None and hit[0] == sig:
            self._cache.move_to_end(key)
            return hit[1]
        try:
            state = RunState.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("run state %s unreadable (%s); treating as not started", path, exc)
            self._cache.pop(key, None)
            return None
        self.parses += 1
        self._cache[key] = (sig, state)
        while len(self._cache) > self._max:
            self._cache.popitem(last=False)
        return state


_DEFAULT_PROBE = RunProbe()


def probe_run(workspace_root: str, run_id: str) -> RunState | None:
    """Module-level mtime-cached ``state.json`` probe (``None`` when absent/corrupt)."""
    return _DEFAULT_PROBE(workspace_root, run_id)


def summary_cost_usd(workspace_root: str, run_id: str) -> float:
    """``summary_cost_usd`` from the run's summary meta; 0.0 when absent/corrupt/negative."""
    path = run_dir(workspace_root, run_id) / SUMMARY_DIR_NAME / SUMMARY_META_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8")).get("summary_cost_usd", 0.0)
        value = float(raw)
    except (OSError, ValueError, TypeError, AttributeError):
        return 0.0
    return value if value > 0 else 0.0


def run_confidence(state: RunState) -> CostConfidence:
    """HLD §10.4 / H2, from the PERSISTED per-task ``actuals_available`` and
    ``attempts_without_actuals``. ``pending`` while a still-running run has settled nothing."""
    settled = [t for t in state.tasks.values() if t.status not in _TERMINAL_UNSETTLED]
    killed = sum(t.attempts_without_actuals for t in state.tasks.values())
    with_actuals = sum(1 for t in settled if t.actuals_available)
    if not settled or with_actuals == 0:
        if state.status == "running" and not settled and killed == 0:
            return "pending"
        return "unknown"
    if with_actuals == len(settled) and killed == 0:
        return "actual"
    return "partial"


# -- value types -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Charge:
    """One fire's contribution to a window."""

    runs: int = 0
    actual_usd: float = 0.0
    reserved_usd: float = 0.0
    confidence: CostConfidence = "actual"
    overshoot_usd: float = 0.0


@dataclass(frozen=True)
class WindowUsage:
    """Totals for one window. ``confidence`` is the worst of the contributing charges."""

    runs: int = 0
    actual_usd: float = 0.0
    reserved_usd: float = 0.0
    confidence: CostConfidence = "actual"

    def __add__(self, c: Charge) -> WindowUsage:
        worst = max(self.confidence, c.confidence, key=_CONF_RANK.__getitem__)
        return WindowUsage(
            self.runs + c.runs,
            _money(self.actual_usd + c.actual_usd),
            _money(self.reserved_usd + c.reserved_usd),
            worst,
        )


@dataclass
class _Index:
    dir_mtime_ns: int = -1
    scanned_ns: int = 0
    dirty: bool = True
    files: dict[str, tuple[tuple[int, int, int], FireIntentRecord | None]] = field(
        default_factory=dict
    )
    recs: dict[str, FireIntentRecord] = field(default_factory=dict)
    # schedule_id -> sorted [(scheduled_for, fire_key)]
    by_schedule: dict[str, list[tuple[datetime, str]]] = field(default_factory=dict)


# -- the ledger --------------------------------------------------------------------------------


class UsageLedger:
    """Derived usage per schedule/window. Constructor-injected collaborators (no monkeypatching).

    The per-workspace index ``{schedule_id: sorted[(scheduled_for, fire_key)]}`` is built on first
    use, refreshed when the directory changed (per-file ``(mtime, size, inode)`` so only changed
    files are re-parsed), and updated by :meth:`note_written`. ``usage`` scans only records with
    ``scheduled_for >= min(window.start)``.
    """

    def __init__(
        self,
        fires: FireIntentStore,
        *,
        run_state_loader: RunStateLoader = probe_run,
        summary_cost_loader: SummaryCostLoader = summary_cost_usd,
        clock: Callable[[], datetime] = _utc_now,
        event_sink: EventSink | None = None,
        fs_now_ns: Callable[[], int] | None = None,
    ) -> None:
        self._fires = fires
        self._load = run_state_loader
        self._summary_cost = summary_cost_loader
        self._clock = clock
        self._sink = event_sink
        self._fs_now_ns = fs_now_ns or time.time_ns
        self._index: dict[str, _Index] = {}
        self._hwm: dict[str, float] = {}  # in-process monotonic mark (sub-$0.01 rises)
        self._warned: set[str] = set()
        self.scans = 0  # observable: directory rescans
        self.writes = 0  # observable: high-water-mark write-backs

    # -- index ---------------------------------------------------------------------------------

    @staticmethod
    def _key(workspace_root: str) -> str:
        return str(Path(workspace_root).resolve())

    def invalidate(self, workspace_root: str, schedule_id: str = "") -> None:
        """Drop cached reads so records written by another process/thread are seen."""
        self._index.setdefault(self._key(workspace_root), _Index()).dirty = True

    def note_written(self, rec: FireIntentRecord) -> None:
        """Upsert one record into an already-built index (no-op before the first build)."""
        idx = self._index.get(self._key(rec.workspace_root))
        if idx is None:
            return
        self._upsert(idx, rec)

    @staticmethod
    def _upsert(idx: _Index, rec: FireIntentRecord) -> None:
        if rec.fire_key not in idx.recs:
            bisect.insort(
                idx.by_schedule.setdefault(rec.schedule_id, []), (rec.scheduled_for, rec.fire_key)
            )
        idx.recs[rec.fire_key] = rec

    def _refresh(self, workspace_root: str) -> _Index:
        key = self._key(workspace_root)
        idx = self._index.setdefault(key, _Index())
        directory = self._fires.directory(key)
        try:
            dir_mtime = directory.stat().st_mtime_ns
        except OSError:
            idx.files, idx.recs, idx.by_schedule, idx.dirty = {}, {}, {}, False
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
        fresh = _Index()
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
                    fresh.recs[rec.fire_key] = rec
                    fresh.by_schedule.setdefault(rec.schedule_id, []).append(
                        (rec.scheduled_for, rec.fire_key)
                    )
        for lst in fresh.by_schedule.values():
            lst.sort()
        idx.files, idx.recs, idx.by_schedule = files, fresh.recs, fresh.by_schedule
        idx.dir_mtime_ns, idx.scanned_ns, idx.dirty = dir_mtime, scanned_ns, False
        return idx

    # -- derivation ----------------------------------------------------------------------------

    def _observed(self, rec: FireIntentRecord, st: RunState) -> tuple[float, float]:
        engine = compute_run_usage_totals(st).cost_usd
        assert rec.run_id is not None
        return engine, engine + self._summary_cost(rec.workspace_root, rec.run_id)

    def engine_cost_usd(self, rec: FireIntentRecord) -> float:
        """The engine gate's metric: ``compute_run_usage_totals(state).cost_usd`` only (L3)."""
        if rec.run_id is None:
            return 0.0
        st = self._load(rec.workspace_root, rec.run_id)
        return compute_run_usage_totals(st).cost_usd if st is not None else 0.0

    def _warn_blind(self, rec: FireIntentRecord) -> None:
        if (
            self._sink is None
            or rec.fire_key in self._warned
            or (self._clock() - rec.created_at).total_seconds() <= STATE_PROBE_GRACE_SECONDS
        ):
            return
        self._warned.add(rec.fire_key)
        self._sink(
            EVENT_COST_UNKNOWN,
            level=logging.WARNING,
            workspace=rec.workspace_root,
            schedule_id=rec.schedule_id,
            fire_key=rec.fire_key,
            run_id=rec.run_id,
            reason="no_state_json",
        )

    def _raise_mark(
        self, rec: FireIntentRecord, observed: float, conf: CostConfidence, terminal: bool
    ) -> float:
        """High-water mark: never decreases. Persisted only on a rise >= $0.01 or at terminal."""
        g = rec.governance or FireGovernance()
        mark = max(g.cost_observed_usd, self._hwm.get(rec.fire_key, 0.0), observed)
        self._hwm[rec.fire_key] = mark
        rose = mark - g.cost_observed_usd >= HWM_PERSIST_STEP_USD
        changed = mark > g.cost_observed_usd or conf != g.cost_confidence
        if rec.governance is not None and (rose or (terminal and changed)):
            try:
                updated = self._fires.update_governance(
                    rec.fire_key, cost_observed_usd=mark, cost_confidence=conf
                )
            except (KeyError, OSError) as exc:  # a cache refresh may be lost; next probe redoes it
                logger.warning("could not persist cost mark for %s: %s", rec.fire_key, exc)
            else:
                self.writes += 1
                self.note_written(updated)
        return mark

    def charge(self, rec: FireIntentRecord, *, window_open: bool = True) -> Charge:
        """HLD §10.2: one fire's contribution. ``window_open`` is whether the window being
        charged has not ended yet (partial runs are over-charged only while it is)."""
        if rec.status in ("suppressed", "launch_failed"):
            return Charge()
        g = rec.governance
        reserved = g.reserved_usd if g and g.reserved_usd is not None else 0.0
        st = self._load(rec.workspace_root, rec.run_id) if rec.run_id else None
        stored = max(g.cost_observed_usd if g else 0.0, self._hwm.get(rec.fire_key, 0.0))
        if st is None:
            if rec.status == "orphaned" or (g is not None and g.final):
                return Charge(
                    1, _money(stored), 0.0, g.cost_confidence if g and g.final else "actual"
                )
            if rec.status == "launched":
                self._warn_blind(rec)
            return Charge(1, _money(stored), _money(max(0.0, reserved - stored)), "pending")
        _, observed = self._observed(rec, st)
        conf = run_confidence(st)
        running = st.status == "running"
        mark = self._raise_mark(rec, observed, conf, terminal=not running)
        if running and not (g is not None and g.final):
            return Charge(1, _money(mark), _money(max(0.0, reserved - mark)), conf)
        if conf == "unknown" or (conf == "partial" and window_open):
            return Charge(1, _money(max(mark, reserved)), 0.0, conf)
        cap = g.run_cost_cap_usd if g else None
        over = max(0.0, observed - cap) if cap is not None else 0.0
        return Charge(1, _money(mark), 0.0, conf, _money(over))

    def usage(
        self, workspace_root: str, schedule_id: str, windows: Mapping[Period, Window]
    ) -> dict[Period, WindowUsage]:
        out: dict[Period, WindowUsage] = {p: WindowUsage() for p in windows}
        if not windows:
            return out
        idx = self._refresh(workspace_root)
        entries = idx.by_schedule.get(schedule_id, [])
        lo = bisect.bisect_left(entries, (min(w.start for w in windows.values()),))
        now = self._clock()
        cache: dict[tuple[str, bool], Charge] = {}
        for sf, key in entries[lo:]:
            for period, w in windows.items():
                if not (w.start <= sf < w.end):
                    continue
                open_ = now < w.end
                c = cache.get((key, open_))
                if c is None:
                    c = cache[(key, open_)] = self.charge(idx.recs[key], window_open=open_)
                out[period] = out[period] + c
        return out
