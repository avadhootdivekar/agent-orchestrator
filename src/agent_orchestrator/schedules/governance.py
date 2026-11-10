"""Governance limits for scheduled runs: quotas and cost caps.

See HLD ``scheduled-run-governance-hld.md`` §6.2 and ADR-0025 D1/D7.

Pure data model + validation; no I/O and no clock reads. The ledger and the pre-fire gate (later
tickets) consume :class:`LimitsSpec` via ``ScheduleBinding.effective_limits`` and
:func:`limits_digest`. Cross-field rules that need the whole schedules file (D-2 "every schedule
declares a limit", inheritance from ``defaults``) live in ``models.ScheduleFile``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import ScheduleFileError
from .windows import Period, WeekStart

__all__ = [
    "DEFAULT_PAUSE_TTL_SECONDS",
    "LIMITS_REQUIRED",
    "MAX_PAUSE_TTL_SECONDS",
    "MAX_PROMPT_BYTES",
    "MAX_QUOTA_RUNS",
    "MAX_RESUME_FACTOR",
    "MAX_USD",
    "MIN_RESERVATION_USD",
    "CostLimits",
    "LimitsSpec",
    "OnCap",
    "Period",
    "PeriodCostCap",
    "QuotaSpec",
    "WeekStart",
    "limits_digest",
]

OnCap = Literal["pause", "abort"]

MAX_QUOTA_RUNS: Final = 10_000
MAX_USD: Final = 100_000.0
# Below this remaining period budget a fire is not worth starting (gate, later ticket).
MIN_RESERVATION_USD: Final = 0.10
DEFAULT_PAUSE_TTL_SECONDS: Final = 72 * 3600
MAX_PAUSE_TTL_SECONDS: Final = 30 * 86400
# A single resume may add at most ``per_run_usd * MAX_RESUME_FACTOR`` (resume ticket).
MAX_RESUME_FACTOR: Final = 1.0
# Upper bound on a fixed prompt file the governance layer will hash (prompt-pin ticket).
MAX_PROMPT_BYTES: Final = 256 * 1024

LIMITS_REQUIRED: Final = (
    "schedule {id!r}: every schedule must declare at least one limit (limits.quotas, "
    "limits.cost.per_run_usd or limits.cost.periods) — here or in defaults.limits (D-2)"
)


def _dup_periods(periods: list[str]) -> list[str]:
    seen: set[str] = set()
    dups: list[str] = []
    for p in periods:
        if p in seen and p not in dups:
            dups.append(p)
        seen.add(p)
    return dups


# Numbers come from a hand-edited file: strict, so a quoted "3" is an error, not a silent coercion.


class QuotaSpec(BaseModel):
    """At most ``max_runs`` invocations per calendar ``period``."""

    model_config = ConfigDict(extra="forbid")

    period: Period
    max_runs: int = Field(strict=True, ge=1, le=MAX_QUOTA_RUNS)


class PeriodCostCap(BaseModel):
    """At most ``max_usd`` of run cost charged to one calendar ``period``."""

    model_config = ConfigDict(extra="forbid")

    period: Period
    max_usd: float = Field(strict=True, gt=0, le=MAX_USD, allow_inf_nan=False)


def _empty_periods() -> list[PeriodCostCap]:
    return []


def _empty_quotas() -> list[QuotaSpec]:
    return []


class CostLimits(BaseModel):
    """Per-run (mid-run, D-5) and per-period cost caps."""

    model_config = ConfigDict(extra="forbid")

    per_run_usd: float | None = Field(
        default=None, strict=True, gt=0, le=MAX_USD, allow_inf_nan=False
    )
    on_cap: OnCap = "pause"
    pause_ttl_seconds: int = Field(
        default=DEFAULT_PAUSE_TTL_SECONDS, strict=True, ge=0, le=MAX_PAUSE_TTL_SECONDS
    )
    periods: list[PeriodCostCap] = Field(default_factory=_empty_periods)

    @model_validator(mode="after")
    def _one_per_period(self) -> CostLimits:
        dups = _dup_periods([p.period for p in self.periods])
        if dups:
            raise ScheduleFileError(
                f"duplicate cost period {dups[0]!r} in limits.cost.periods "
                "(one max_usd per period)",
                field="limits.cost.periods",
            )
        return self

    @model_validator(mode="after")
    def _per_run_fits_periods(self) -> CostLimits:
        if self.per_run_usd is not None and self.periods:
            smallest = min(p.max_usd for p in self.periods)
            if self.per_run_usd > smallest:
                raise ScheduleFileError(
                    f"limits.cost.per_run_usd ({self.per_run_usd}) exceeds the smallest period "
                    f"max_usd ({smallest}): such a run could never be afforded",
                    field="limits.cost.per_run_usd",
                )
        return self


class LimitsSpec(BaseModel):
    """The governance ``limits`` block of a binding (or of ``defaults``)."""

    model_config = ConfigDict(extra="forbid")

    quotas: list[QuotaSpec] = Field(default_factory=_empty_quotas)
    cost: CostLimits | None = None

    @model_validator(mode="after")
    def _one_quota_per_period(self) -> LimitsSpec:
        dups = _dup_periods([q.period for q in self.quotas])
        if dups:
            raise ScheduleFileError(
                f"duplicate quota period {dups[0]!r} in limits.quotas (one max_runs per period)",
                field="limits.quotas",
            )
        return self

    def declares_any_limit(self) -> bool:
        """D-2: a quota, a per-run cap or a period cap. ``on_cap`` alone, ``quotas: []`` and
        ``until.max_runs`` (a lifetime cap, Q-2) do not count."""
        return bool(self.quotas) or (
            self.cost is not None and (self.cost.per_run_usd is not None or bool(self.cost.periods))
        )


def _canonical(node: Any) -> Any:
    """Normalise for hashing: integral floats become ints (``2`` == ``2.0``)."""
    if isinstance(node, dict):
        return {k: _canonical(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_canonical(v) for v in node]
    if isinstance(node, float) and node.is_integer():
        return int(node)
    return node


def limits_digest(limits: LimitsSpec) -> str:
    """sha256 hex of the canonical JSON of *limits* (sorted keys, no whitespace, defaults
    included so an explicit default equals an omitted one; list order is irrelevant)."""
    payload = limits.model_dump(mode="json")
    # Periods are unique per list, so sorting by period makes list order irrelevant.
    payload["quotas"].sort(key=lambda q: q["period"])
    if payload["cost"] is not None:
        payload["cost"]["periods"].sort(key=lambda p: p["period"])
    payload = _canonical(payload)
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
