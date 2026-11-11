"""Governance usage view (T-rwj4nV; HLD §14.1): the document behind ``ao schedule usage``.

Two pure pieces, shared with the dashboard (T-8knqVe) so the two surfaces never disagree:

* :func:`build_usage_doc` -- reads (never writes) a ledger, the fire records and the event log and
  returns the ``ao.schedule.usage/v1`` dict. This dict IS the dashboard API body.
* :func:`render_usage` -- turns that dict into the HLD §14.1 text. All text formatting lives here.

Also the compact ``LIMITS`` rendering used by ``ao schedule list``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any, Final
from zoneinfo import ZoneInfo

from ..service.fire_store import (
    FireIntentRecord,
    FireIntentStore,
    ScheduleEventLog,
)
from ..service.governance_gate import LedgerPort
from .governance import LimitsSpec, limits_digest
from .models import ResolvedBinding
from .windows import PERIODS, Period, WeekStart, windows_for

USAGE_SCHEMA: Final = "ao.schedule.usage/v1"
GOVERNANCE_EVENT_PREFIX: Final = "schedule.governance."
EVENT_TAIL: Final = 5
# Governance events are a thin slice of the log: read this many raw events to find EVENT_TAIL.
EVENT_SCAN: Final = 500
DIGEST_SHORT: Final = 6
PERIOD_ABBREV: Final[dict[str, str]] = {"day": "d", "week": "w", "month": "m"}
NO_VALUE: Final = "-"
# Gate-detail fields copied from an event into the doc (the rest of the event is not API).
EVENT_DETAIL_FIELDS: Final = ("period", "used", "max", "resets_at", "remaining_usd", "needed_usd")
_PERIOD_ORDER: Final = {p: i for i, p in enumerate(PERIODS)}


def limit_periods(limits: LimitsSpec | None) -> list[Period]:
    """Every period a limits block constrains, day -> week -> month."""
    if limits is None:
        return []
    found: set[Period] = {q.period for q in limits.quotas}
    if limits.cost is not None:
        found |= {c.period for c in limits.cost.periods}
    return sorted(found, key=_PERIOD_ORDER.__getitem__)


def compact_limits(limits: LimitsSpec | None) -> str:
    """``3/d 10/w $2/run $20/w`` (``-`` when nothing is declared)."""
    if limits is None:
        return NO_VALUE
    parts = [f"{q.max_runs}/{PERIOD_ABBREV[q.period]}" for q in limits.quotas]
    cost = limits.cost
    if cost is not None:
        if cost.per_run_usd is not None:
            parts.append(f"${cost.per_run_usd:g}/run")
        parts += [f"${c.max_usd:g}/{PERIOD_ABBREV[c.period]}" for c in cost.periods]
    return " ".join(parts) or NO_VALUE


def paused_records(
    fires: FireIntentStore, workspace_root: str, schedule_id: str
) -> list[FireIntentRecord]:
    """Fire records of *schedule_id* whose run is paused by a cap, oldest first."""
    recs = [
        r
        for r in fires.list(workspace_root=workspace_root)
        if r.schedule_id == schedule_id and r.governance is not None
        and r.governance.cap_state == "paused"
    ]  # fmt: skip
    return sorted(recs, key=lambda r: (r.scheduled_for, r.fire_key))


def _iso(value: datetime) -> str:
    return value.isoformat()


def _window_row(
    period: Period, w: Any, usage: Any, limits: LimitsSpec, tz: ZoneInfo
) -> dict[str, Any]:
    max_runs = next((q.max_runs for q in limits.quotas if q.period == period), None)
    max_usd = None
    if limits.cost is not None:
        max_usd = next((c.max_usd for c in limits.cost.periods if c.period == period), None)
    return {
        "period": period,
        "key": w.key,
        "start": _iso(w.start),
        "start_local_date": w.start.astimezone(tz).date().isoformat(),
        "resets_at": _iso(w.end),
        "runs": usage.runs,
        "max_runs": max_runs,
        "actual_usd": usage.actual_usd,
        "reserved_usd": usage.reserved_usd,
        "max_usd": max_usd,
        "confidence": usage.confidence,
    }


def paused_row(rec: FireIntentRecord) -> dict[str, Any]:
    """One paused run as the shared usage-doc row (CLI and dashboard; carries ``overshoot_usd``)."""
    g = rec.governance
    assert g is not None
    cap = g.run_cost_cap_usd
    cost = g.cost_observed_usd  # the charge (may be reserved-inflated / summary-inclusive)
    # G-7: overshoot is on the cap's own metric; old records without it fall back to the charge.
    engine = g.engine_cost_usd if g.engine_cost_usd is not None else cost
    return {
        "run_id": rec.run_id,
        "fire_key": rec.fire_key,
        "cost_usd": cost,
        "engine_cost_usd": engine,
        "cap_usd": cap,
        "overshoot_usd": round(max(0.0, engine - cap), 6) if cap is not None else 0.0,
        "paused_at": _iso(g.paused_at) if g.paused_at else None,
        "expires_at": _iso(g.pause_expires_at) if g.pause_expires_at else None,
    }


def governance_events(
    events: ScheduleEventLog, workspace_root: str, schedule_id: str, limit: int = EVENT_TAIL
) -> list[dict[str, Any]]:
    """The newest *limit* ``schedule.governance.*`` events of one schedule, newest first."""
    raw = events.read(limit=EVENT_SCAN, schedule_id=schedule_id, workspace_root=workspace_root)
    out: list[dict[str, Any]] = []
    for e in raw:
        name = str(e.get("event") or "")
        if not name.startswith(GOVERNANCE_EVENT_PREFIX):
            continue
        row: dict[str, Any] = {
            "ts": e.get("ts"),
            "event": name,
            "reason": e.get("reason"),
            "run_id": e.get("run_id"),
        }
        row.update({k: e[k] for k in EVENT_DETAIL_FIELDS if k in e})
        out.append(row)
        if len(out) >= limit:
            break
    return out


def governance_fields(event: dict[str, Any]) -> dict[str, Any] | None:
    """The ``governance`` object of a history row (``None`` for non-governance events)."""
    if not str(event.get("event") or "").startswith(GOVERNANCE_EVENT_PREFIX):
        return None
    return {k: event[k] for k in ("reason", *EVENT_DETAIL_FIELDS) if k in event}


def reason_with_period(event: dict[str, Any]) -> str:
    """History REASON cell: ``quota_exhausted (day)`` for a governance skip with a period."""
    reason = str(event.get("reason") or NO_VALUE)
    period = event.get("period")
    return f"{reason} ({period})" if period and reason != NO_VALUE else reason


def _binding_tz_name(binding: ResolvedBinding) -> str:
    """The binding's IANA zone name, defaulting to UTC (one place for the default)."""
    return binding.timezone or "UTC"


def _binding_week_start(binding: ResolvedBinding) -> WeekStart:
    """The binding's week-start day, defaulting to Monday (one place for the default)."""
    return binding.week_start or "monday"


def usage_windows(
    *, workspace: str, at: datetime, binding: ResolvedBinding, ledger: LedgerPort
) -> list[dict[str, Any]]:
    """The per-period window rows of one binding at *at* (shared by the CLI doc and the dashboard,
    so a caller that only needs windows never scans paused records or the event log)."""
    limits = binding.limits
    if limits is None:
        return []
    tz = ZoneInfo(_binding_tz_name(binding))
    week_start = _binding_week_start(binding)
    periods = limit_periods(limits)
    wins = windows_for(at, periods, tz, week_start)
    usage = ledger.usage(workspace, binding.id, wins)
    return [_window_row(p, wins[p], usage[p], limits, tz) for p in periods]


def build_usage_doc(
    *,
    workspace: str,
    at: datetime,
    generated_at: datetime,
    bindings: Iterable[ResolvedBinding],
    ledger: LedgerPort,
    fires: FireIntentStore,
    events: ScheduleEventLog,
) -> dict[str, Any]:
    """The ``ao.schedule.usage/v1`` document for *bindings* with windows evaluated at *at*.

    Read-only: the caller must hand in a ledger whose store never writes (the CLI does)."""
    schedules: list[dict[str, Any]] = []
    for b in bindings:
        limits = b.limits
        tz_name = _binding_tz_name(b)
        week_start = _binding_week_start(b)
        rows = usage_windows(workspace=workspace, at=at, binding=b, ledger=ledger)
        cost = limits.cost if limits is not None else None
        schedules.append(
            {
                "id": b.id,
                "tz": tz_name,
                "week_start": week_start,
                "limits_digest": limits_digest(limits) if limits is not None else None,
                "limits": compact_limits(limits),
                "per_run_usd": cost.per_run_usd if cost is not None else None,
                "on_cap": cost.on_cap if cost is not None else None,
                "windows": rows,
                "paused": [paused_row(r) for r in paused_records(fires, workspace, b.id)],
                "events": governance_events(events, workspace, b.id),
            }
        )
    return {
        "schema": USAGE_SCHEMA,
        "generated_at": _iso(generated_at),
        "at": _iso(at),
        "workspace": workspace,
        "schedules": schedules,
    }


# -- text rendering ----------------------------------------------------------------------------

_HEADER_WIDTH = 60


def _local(iso: str | None, tz: ZoneInfo, fmt: str) -> str:
    if not iso:
        return NO_VALUE
    return datetime.fromisoformat(iso).astimezone(tz).strftime(fmt)


def _money(v: float) -> str:
    return f"${v:.2f}"


def _event_detail(e: dict[str, Any]) -> str:
    if "needed_usd" in e:
        where = f"{e['period']} " if e.get("period") else ""
        return f"{where}remaining {_money(e['remaining_usd'])} < needed {_money(e['needed_usd'])}"
    if "max" in e:
        return f"{e.get('period', '')} {e.get('used')}/{e['max']}".strip()
    return ""


def _render_windows(rows: Sequence[dict[str, Any]], tz: ZoneInfo) -> list[str]:
    cells: list[tuple[str, str, str, str, str]] = []
    for w in rows:
        runs = f"runs {w['runs']}/{w['max_runs'] if w['max_runs'] is not None else NO_VALUE}"
        spend = (
            f"spend {_money(w['actual_usd'])} (+{_money(w['reserved_usd'])} reserved) / "
            f"{_money(w['max_usd']) if w['max_usd'] is not None else NO_VALUE}"
        )
        flag = " (cost partial)" if w["confidence"] in ("partial", "unknown") else ""
        reset = "resets " + _local(w["resets_at"], tz, "%Y-%m-%d %H:%M %Z")
        cells.append((w["period"], w["start_local_date"], runs, spend + flag, reset))
    widths = [max(len(c[i]) for c in cells) for i in range(4)] if cells else []
    return ["  " + "  ".join(c[i].ljust(widths[i]) for i in range(4)) + "  " + c[4] for c in cells]


def render_usage(doc: dict[str, Any]) -> str:
    """The HLD §14.1 text layout for a :func:`build_usage_doc` document."""
    lines: list[str] = []
    for s in doc["schedules"]:
        tz = ZoneInfo(s["tz"])
        head = f"{s['id']}  ({s['tz']}, week starts {s['week_start']})"
        digest = s["limits_digest"]
        if digest:
            head = head.ljust(_HEADER_WIDTH) + f"limits digest {digest[:DIGEST_SHORT]}"
        lines.append(head)
        lines += _render_windows(s["windows"], tz)
        for p in s["paused"]:
            cap = _money(p["cap_usd"]) if p["cap_usd"] is not None else NO_VALUE
            over = f" (overshoot {_money(p['overshoot_usd'])})" if p["overshoot_usd"] > 0 else ""
            lines.append(
                f"  paused: {p['run_id']}  {_money(p['engine_cost_usd'])} of {cap}{over}  "
                f"expires {_local(p['expires_at'], tz, '%Y-%m-%d %H:%M %Z')}"
            )
        if s["events"]:
            lines.append("  last events:")
            for e in s["events"]:
                kind = str(e["event"]).removeprefix(GOVERNANCE_EVENT_PREFIX)
                parts = [_local(e["ts"], tz, "%Y-%m-%d %H:%M"), kind, e.get("reason") or ""]
                lines.append("    " + "  ".join(p for p in (*parts, _event_detail(e)) if p))
    return "\n".join(lines)
