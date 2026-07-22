"""Unit tests for bench/tiers.py: load_tier_config / resolve_effective (T-Tr1Km8 AC3-5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_orchestrator.bench.errors import SpecValidationError
from agent_orchestrator.bench.tiers import (
    TIERS_FILE,
    TierConfig,
    load_tier_config,
    resolve_effective,
)

# ---------------------------------------------------------------------------
# Committed benchmarks/tiers.json (AC3)
# ---------------------------------------------------------------------------


def test_committed_tiers_file_exists() -> None:
    assert TIERS_FILE.is_file()


def test_load_tier_config_small_defaults() -> None:
    tc = load_tier_config("small")
    assert tc.enabled is True
    assert tc.cost_budget_usd_per_run == 10
    assert tc.cost_budget_usd_per_subject == 5
    assert tc.default_max_parallel == 1
    assert tc.default_timeout_seconds == 900


def test_load_tier_config_medium_defaults() -> None:
    tc = load_tier_config("medium")
    assert tc.enabled is True
    assert tc.cost_budget_usd_per_run == 150
    assert tc.cost_budget_usd_per_subject == 50
    assert tc.default_max_parallel == 4
    assert tc.default_timeout_seconds == 3600


def test_load_tier_config_large_defaults() -> None:
    tc = load_tier_config("large")
    assert tc.cost_budget_usd_per_run == 800
    assert tc.cost_budget_usd_per_subject == 100
    assert tc.enabled is True


def test_load_tier_config_xlarge_disabled() -> None:
    tc = load_tier_config("xlarge")
    assert tc.enabled is False
    assert tc.cost_budget_usd_per_run == 8000
    assert tc.cost_budget_usd_per_subject == 1000


# ---------------------------------------------------------------------------
# Malformed / missing tiers.json (AC4) -- structured error, never a raw traceback
# ---------------------------------------------------------------------------


def test_load_tier_config_missing_file_raises_typed_error(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist" / "tiers.json"
    with pytest.raises(SpecValidationError, match="repo checkout") as exc_info:
        load_tier_config("small", path=missing)
    assert str(missing) in str(exc_info.value)


def test_load_tier_config_malformed_json_raises_typed_error(tmp_path: Path) -> None:
    bad = tmp_path / "tiers.json"
    bad.write_text("{not valid json")
    with pytest.raises(SpecValidationError, match="Failed to read"):
        load_tier_config("small", path=bad)


def test_load_tier_config_missing_tiers_key_raises_typed_error(tmp_path: Path) -> None:
    bad = tmp_path / "tiers.json"
    bad.write_text(json.dumps({"version": "1.0"}))
    with pytest.raises(SpecValidationError, match="Malformed tiers config"):
        load_tier_config("small", path=bad)


def test_load_tier_config_non_object_top_level_raises_typed_error(tmp_path: Path) -> None:
    bad = tmp_path / "tiers.json"
    bad.write_text(json.dumps([1, 2, 3]))
    with pytest.raises(SpecValidationError, match="Malformed tiers config"):
        load_tier_config("small", path=bad)


def test_load_tier_config_unknown_tier_raises_typed_error(tmp_path: Path) -> None:
    cfg = tmp_path / "tiers.json"
    cfg.write_text(
        json.dumps(
            {
                "version": "1.0",
                "tiers": {
                    "small": {
                        "enabled": True,
                        "cost_budget_usd_per_run": 10,
                        "cost_budget_usd_per_subject": 5,
                        "default_max_parallel": 1,
                        "default_timeout_seconds": 900,
                    }
                },
            }
        )
    )
    with pytest.raises(SpecValidationError, match="not found") as exc_info:
        load_tier_config("gigantic", path=cfg)
    assert "gigantic" in str(exc_info.value)


def test_load_tier_config_invalid_tier_entry_raises_typed_error(tmp_path: Path) -> None:
    cfg = tmp_path / "tiers.json"
    cfg.write_text(
        json.dumps(
            {
                "version": "1.0",
                "tiers": {
                    "small": {
                        "enabled": True,
                        "cost_budget_usd_per_run": "not-a-number",
                        "cost_budget_usd_per_subject": 5,
                        "default_max_parallel": 1,
                        "default_timeout_seconds": 900,
                    }
                },
            }
        )
    )
    with pytest.raises(SpecValidationError, match="Invalid tier config"):
        load_tier_config("small", path=cfg)


def test_load_tier_config_missing_schemas_dir_style_uses_path_param(tmp_path: Path) -> None:
    """Non-default `path` (the hard-rule "allow a path parameter for tests" note) must
    be honored rather than silently falling back to the committed TIERS_FILE.
    """
    cfg = tmp_path / "custom" / "tiers.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        json.dumps(
            {
                "version": "1.0",
                "tiers": {
                    "small": {
                        "enabled": True,
                        "cost_budget_usd_per_run": 99,
                        "cost_budget_usd_per_subject": 42,
                        "default_max_parallel": 7,
                        "default_timeout_seconds": 123,
                    }
                },
            }
        )
    )
    tc = load_tier_config("small", path=cfg)
    assert tc.cost_budget_usd_per_run == 99
    assert tc.default_max_parallel == 7


# ---------------------------------------------------------------------------
# resolve_effective precedence: CLI > tier default > builtin fallback (AC5)
# ---------------------------------------------------------------------------


def test_resolve_effective_cli_overrides_win() -> None:
    tc = TierConfig(
        enabled=True,
        cost_budget_usd_per_run=150,
        cost_budget_usd_per_subject=50,
        default_max_parallel=4,
        default_timeout_seconds=3600,
    )
    effective = resolve_effective(tc, cli_max_parallel=2, cli_cost_budget=9.5, cli_timeout=60)
    assert effective == {"max_parallel": 2, "cost_budget_usd": 9.5, "timeout": 60}


def test_resolve_effective_falls_back_to_tier_defaults_when_no_cli() -> None:
    tc = TierConfig(
        enabled=True,
        cost_budget_usd_per_run=800,
        cost_budget_usd_per_subject=100,
        default_max_parallel=3,
        default_timeout_seconds=5400,
    )
    effective = resolve_effective(tc)
    assert effective == {"max_parallel": 3, "cost_budget_usd": 100, "timeout": 5400}


def test_resolve_effective_falls_back_to_builtin_when_tc_none() -> None:
    effective = resolve_effective(None)
    assert effective == {"max_parallel": 1, "cost_budget_usd": 5, "timeout": 1800}


def test_resolve_effective_cli_wins_even_over_builtin_fallback() -> None:
    effective = resolve_effective(None, cli_max_parallel=8, cli_cost_budget=0, cli_timeout=10)
    # cli_cost_budget=0 is an explicit CLI value (falsy but not None) -- must win, not
    # silently fall back (guards against the classic `x or default` footgun).
    assert effective == {"max_parallel": 8, "cost_budget_usd": 0, "timeout": 10}
