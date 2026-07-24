"""Per-tier default budget/parallelism/timeout config loader (E-Bt4Xk9 T-Tr1Km8).

Mirrors `bench/spec.py`'s load pattern (read JSON -> guard errors -> pydantic-parse) for
a single small config file, `benchmarks/tiers.json`, that holds the per-tier defaults
every downstream task in this epic consumes (T-Bg2Wq4 per-subject/per-run USD caps,
T-Pl3Rx7 default parallelism, T-Cm9Tb4 whole-run cap + xlarge gate). This module only
resolves `benchmarks/tiers.json` -- validating a suite's OWN `tier` field against the
closed `KNOWN_TIERS` list lives in `bench/spec.py::load_suite` (kept separate on
purpose: neither module imports the other, keeping both sides of this Wave-A split
independently testable).

Precedence for any given knob is CLI > tier-default (`benchmarks/tiers.json`) > a
documented builtin fallback (`_BUILTIN_FALLBACK`) -- see `resolve_effective()`. This
task only provides the config + loader; wiring these knobs into `runner.py` is
downstream (T-Bg2Wq4/T-Pl3Rx7/T-Cm9Tb4), per the epic's Handoff Boundary.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ValidationError

from .errors import SpecValidationError

# benchmarks/tiers.json lives at the repo root (four levels up from this file's package
# dir: bench/ -> agent_orchestrator/ -> src/ -> repo root) -- identical depth/rationale
# to `spec.py`'s `_SCHEMAS_DIR` (see that module's docstring/comment for why).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
TIERS_FILE: Path = _REPO_ROOT / "benchmarks" / "tiers.json"


class TierConfig(BaseModel):
    """Per-tier defaults resolved from one entry of `benchmarks/tiers.json` (FR-1)."""

    enabled: bool
    cost_budget_usd_per_run: float
    cost_budget_usd_per_subject: float
    default_max_parallel: int
    default_timeout_seconds: int


# Used only when a caller has no TierConfig to fall back on (resolve_effective() called
# with tc=None -- e.g. a caller that could not resolve any tier config at all).
# Deliberately conservative (serial, small budget, 30 min) so an unexpected fallback
# never silently grants a large/expensive run.
_BUILTIN_FALLBACK = TierConfig(
    enabled=True,
    cost_budget_usd_per_run=10,
    cost_budget_usd_per_subject=5,
    default_max_parallel=1,
    default_timeout_seconds=1800,
)


def load_tier_config(tier: str, path: str | Path = TIERS_FILE) -> TierConfig:
    """Load *tier*'s effective config from the tiers config file at *path*.

    *path* defaults to the committed `benchmarks/tiers.json` (`TIERS_FILE`); tests may
    override it to point at a tmp fixture.

    Raises SpecValidationError (naming the file) for: a missing/non-packaged tiers
    config (mirrors `spec.py::_validate_against_schema`'s "must run from a repo
    checkout" guard -- `benchmarks/tiers.json`, like `benchmarks/schemas/`, only exists
    on a repo checkout, not a packaged install), malformed JSON, a document missing/
    malformed `tiers`, an unknown *tier* name, or a tier entry that fails `TierConfig`'s
    field validation. Never lets a raw exception escape.
    """
    p = Path(path)
    if not p.is_file():
        raise SpecValidationError(
            "ao-bench must run from a repo checkout (benchmarks/tiers.json is not "
            f"packaged); tiers config not found: {p}",
            path=str(p),
        )
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SpecValidationError(f"Failed to read tiers config {p}: {exc}", path=str(p)) from exc

    if not isinstance(data, dict) or not isinstance(data.get("tiers"), dict):
        raise SpecValidationError(
            f"Malformed tiers config {p}: expected a top-level object with a 'tiers' object",
            path=str(p),
        )

    tiers = data["tiers"]
    if tier not in tiers:
        raise SpecValidationError(
            f"Tier {tier!r} not found in {p}; known tiers: {sorted(tiers)}",
            path=f"tiers.{tier}",
        )

    try:
        return TierConfig(**tiers[tier])
    except ValidationError as exc:
        raise SpecValidationError(
            f"Invalid tier config for {tier!r} in {p}: {exc}", path=f"tiers.{tier}"
        ) from exc


def resolve_effective(
    tc: TierConfig | None,
    *,
    cli_max_parallel: int | None = None,
    cli_cost_budget: float | None = None,
    cli_timeout: int | None = None,
) -> dict[str, float | int]:
    """Resolve the effective `{max_parallel, cost_budget_usd, timeout}` knobs.

    Precedence per knob: an explicit CLI value wins; else *tc*'s tier default; else (if
    *tc* is `None` -- no tier config was available) `_BUILTIN_FALLBACK`'s default.
    Callers resolve their own tier via `load_tier_config` first and pass its result as
    *tc*; passing `None` is only for a caller that could not resolve a tier config at
    all and still wants a safe, documented default rather than crashing.

    `cost_budget_usd` resolves against `cost_budget_usd_per_subject` (the per-task-run
    cap this task's callers need; the epic's "budget vs token-budget naming" invariant
    keeps this distinct from the pre-existing token `budget_total`). The whole-run cap
    (`cost_budget_usd_per_run`) is read directly off `TierConfig` by its consumer
    (T-Cm9Tb4's campaign command), not through this per-subject helper.
    """
    effective_tc = tc if tc is not None else _BUILTIN_FALLBACK
    return {
        "max_parallel": (
            cli_max_parallel if cli_max_parallel is not None else effective_tc.default_max_parallel
        ),
        "cost_budget_usd": (
            cli_cost_budget
            if cli_cost_budget is not None
            else effective_tc.cost_budget_usd_per_subject
        ),
        "timeout": cli_timeout if cli_timeout is not None else effective_tc.default_timeout_seconds,
    }
