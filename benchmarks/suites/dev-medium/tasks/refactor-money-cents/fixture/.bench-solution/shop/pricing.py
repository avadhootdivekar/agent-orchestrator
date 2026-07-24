"""Reference solution for bench fixture: refactor-money-cents.

Used only by `FakeSubject(scripted_effect="copy-solution")` (bench/subjects.py) --
overlay-copied onto the workspace repo, then the marker dir is removed so it never
reaches a real subject/grader. Never read by ClaudeCliSubject/AoWorkflowSubject.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

_CENTS = Decimal("0.01")


def _discounted_line_decimal(unit_price: float, quantity: int, discount_percent: float) -> Decimal:
    # `Decimal(str(x))`, never `Decimal(x)` directly -- the latter would just re-expose
    # the binary float artifact (e.g. 1.005 is stored as 1.00499999999999989...); going
    # through the value's own str/repr first recovers the decimal the caller meant.
    price = Decimal(str(unit_price))
    discount = Decimal(str(discount_percent)) / Decimal(100)
    return price * (Decimal(1) - discount) * quantity


def apply_discount(unit_price: float, discount_percent: float) -> float:
    """Discounted PER-UNIT price, rounded to the cent -- for display of a single
    unit's price only. `line_total` below does NOT multiply this rounded value by
    quantity; it rounds the full line total exactly once instead, so a per-unit
    rounding error is never scaled up by quantity.
    """
    price = Decimal(str(unit_price))
    discount = Decimal(str(discount_percent)) / Decimal(100)
    unit = (price * (Decimal(1) - discount)).quantize(_CENTS, rounding=ROUND_HALF_UP)
    return float(unit)


def line_total(unit_price: float, quantity: int, discount_percent: float = 0.0) -> float:
    """Return the total for `quantity` units at `unit_price`, after discount, rounded
    to the cent exactly once (see `_discounted_line_decimal`)."""
    total = _discounted_line_decimal(unit_price, quantity, discount_percent).quantize(
        _CENTS, rounding=ROUND_HALF_UP
    )
    return float(total)
