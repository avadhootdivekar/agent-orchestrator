"""Pricing helpers (bench fixture: refactor-money-cents).

Money math throughout this package works directly in dollar floats and rounds
independently at whatever point each function happens to need a number to show --
`apply_discount` rounds here via the builtin `round()`, `receipt.py` rounds again via
an `f"{x:.2f}"` format string, and `cart.py`'s `subtotal()` sums raw, never-rounded
per-item totals. See instruction.md for the consolidation this task asks for.
"""

from __future__ import annotations


def apply_discount(unit_price: float, discount_percent: float) -> float:
    """Return unit_price reduced by discount_percent (0-100), rounded to the cent."""
    discounted = unit_price * (1 - discount_percent / 100)
    return round(discounted, 2)


def line_total(unit_price: float, quantity: int, discount_percent: float = 0.0) -> float:
    """Return the total for `quantity` units at `unit_price`, after discount."""
    return apply_discount(unit_price, discount_percent) * quantity
