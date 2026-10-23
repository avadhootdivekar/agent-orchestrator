"""Held-out grading tests for feature-plugin-priority-registry -- NOT part of the
visible tests/ suite an agent sees, so passing tests/test_registry.py alone does not
satisfy this task. Checks the priority-ordering + dispatch short-circuit contract
beyond the two-handler case tests/test_registry.py exercises.
"""

from __future__ import annotations

import pytest
from registry.core import Registry
from registry.dispatch import NoHandlerResolvedError, dispatch


def test_handlers_for_ties_keep_registration_order() -> None:
    reg = Registry()

    def a(payload: dict) -> None:
        return None

    def b(payload: dict) -> None:
        return None

    def c(payload: dict) -> None:
        return None

    reg.register("x", a, priority=5)
    reg.register("x", b, priority=5)
    reg.register("x", c, priority=5)
    assert reg.handlers_for("x") == [a, b, c]


def test_handlers_for_mixed_priority_and_ties() -> None:
    reg = Registry()

    def low_a(payload: dict) -> None:
        return None

    def low_b(payload: dict) -> None:
        return None

    def high(payload: dict) -> None:
        return None

    reg.register("x", low_a, priority=0)
    reg.register("x", high, priority=10)
    reg.register("x", low_b, priority=0)
    assert reg.handlers_for("x") == [high, low_a, low_b]


def test_dispatch_short_circuits_and_does_not_call_lower_priority_handlers() -> None:
    calls: list[str] = []

    def high(payload: dict) -> str:
        calls.append("high")
        return "resolved-by-high"

    def low(payload: dict) -> str:
        calls.append("low")
        return "resolved-by-low"

    reg = Registry()
    reg.register("x", low, priority=0)
    reg.register("x", high, priority=10)

    result = dispatch(reg, "x", {})
    assert result == "resolved-by-high"
    assert calls == ["high"], "dispatch must stop at the first resolving handler"


def test_dispatch_raises_on_completely_unregistered_slot() -> None:
    reg = Registry()
    with pytest.raises(NoHandlerResolvedError):
        dispatch(reg, "nonexistent-slot", {})


def test_unregister_removes_all_matching_occurrences() -> None:
    reg = Registry()

    def h(payload: dict) -> str:
        return "x"

    reg.register("slot", h, priority=1)
    reg.register("slot", h, priority=2)
    reg.unregister("slot", h)
    assert reg.handlers_for("slot") == []
