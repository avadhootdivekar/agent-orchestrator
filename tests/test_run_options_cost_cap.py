"""Option definition + validation helper tests for `--cost-cap-usd` (T-et7Edf AC1/AC2, AC14a)."""

from __future__ import annotations

import math

import pytest

from agent_orchestrator import run_options as ro


def test_option_definition_is_cli_only_sticky_and_in_concurrency_group() -> None:
    """Defined but not yet registered (see COST_CAP_OPTION note): the cli.py wiring registers it."""
    o = ro.COST_CAP_OPTION
    assert (o.flag, o.type, o.env, o.config) == ("--cost-cap-usd", "float", None, None)
    assert o.sticky_on_resume is True
    assert o.ui_group == ro.GROUP_CONCURRENCY and o.ui_label == "Cost cap (USD)"
    assert not any(x.sticky_on_resume for x in ro.OPTIONS)


@pytest.mark.parametrize("bad", [0, 0.0, -1.0, math.nan, math.inf, -math.inf])
def test_validate_rejects_non_positive_and_non_finite(bad: float) -> None:
    with pytest.raises(ValueError, match="cost-cap-usd"):
        ro.validate_cost_cap_usd(bad)


def test_validate_accepts_positive_and_none() -> None:
    assert ro.validate_cost_cap_usd(None) is None
    assert ro.validate_cost_cap_usd(0.5) == 0.5


def test_sticky_resolution() -> None:
    assert ro.resolve_sticky_cost_cap(0.5, None) == ro.Resolved(0.5, ro.SOURCE_STATE)
    assert ro.resolve_sticky_cost_cap(0.5, 2.0) == ro.Resolved(2.0, ro.SOURCE_CLI)
    assert ro.resolve_sticky_cost_cap(None, None) == ro.Resolved(None, ro.SOURCE_UNSET)
