"""Held-out grading tests for refactor-money-cents -- NOT part of the visible tests/
suite an agent sees. Uses adversarial (price, quantity, discount) combinations that
expose the pre-refactor rounding bugs:

  (a) rounding a per-unit discounted price to the cent BEFORE multiplying by
      quantity (instead of rounding the full line total once) -- a per-unit rounding
      error gets scaled up by quantity;
  (b) rounding a raw binary float like 1.005 via Python's `round()`/an `f"{x:.2f}"`
      format string instead of exact-decimal rounding, which misrounds 1.005 down to
      1.00 instead of up to 1.01 because 1.005 is not exactly representable in binary
      floating point (`1.005` is actually stored as `1.00499999999999989...`);
  (c) `cart.py`'s `subtotal()` re-deriving the discount math independently instead of
      delegating to `pricing.line_total` -- fixing only one of the two copies leaves
      the receipt's per-line amounts and its TOTAL line computed by two different
      (and now-diverging) formulas.

Expected values below are the mathematically-correct cent-rounded total: decimal
arithmetic on the EXACT decimal value the price string represents, rounded exactly
ONCE for the whole line (never per-unit-then-multiplied).
"""

from __future__ import annotations

import pytest
from shop.cart import Cart
from shop.pricing import line_total
from shop.receipt import format_receipt

# (unit_price, quantity, discount_percent, expected_line_total)
_ADVERSARIAL_CASES = [
    (0.10, 7, 15, 0.60),
    (10.00, 3, 33.33, 20.00),
    (2.995, 4, 10, 10.78),
    (1.005, 1, 0, 1.01),
]


@pytest.mark.parametrize("unit_price,quantity,discount,expected", _ADVERSARIAL_CASES)
def test_line_total_is_cent_exact_for_adversarial_inputs(
    unit_price: float, quantity: int, discount: float, expected: float
) -> None:
    total = line_total(unit_price, quantity, discount)
    assert total == pytest.approx(expected, abs=1e-9), (
        f"line_total({unit_price}, {quantity}, {discount}) = {total}, expected {expected} "
        "(round the whole line total once, not the per-unit price before multiplying, "
        "and round from an exact decimal value, not a raw binary float)"
    )


def test_cart_subtotal_equals_sum_of_correct_line_totals() -> None:
    cart = Cart()
    for name, (unit_price, quantity, discount, _expected) in zip(
        ["A", "B", "C", "D"], _ADVERSARIAL_CASES, strict=True
    ):
        cart.add(name, unit_price, quantity, discount)
    expected_subtotal = sum(expected for *_, expected in _ADVERSARIAL_CASES)
    assert cart.subtotal() == pytest.approx(expected_subtotal, abs=1e-9)


# A second, harder batch: exact half-cent-per-unit boundaries and a sub-cent price
# scaled by a large quantity -- both amplify the per-unit-rounding-before-multiply bug
# far beyond a single cent, and both still require exact-decimal (not binary-float)
# rounding of the correct value.
_EXTRA_ROUNDING_CASES = [
    (3.335, 2, 0, 6.67),
    (0.015, 100, 0, 1.50),
    (49.995, 1, 0, 50.00),
]


@pytest.mark.parametrize("unit_price,quantity,discount,expected", _EXTRA_ROUNDING_CASES)
def test_line_total_is_cent_exact_for_a_second_batch_of_adversarial_inputs(
    unit_price: float, quantity: int, discount: float, expected: float
) -> None:
    total = line_total(unit_price, quantity, discount)
    assert total == pytest.approx(expected, abs=1e-9), (
        f"line_total({unit_price}, {quantity}, {discount}) = {total}, expected {expected}"
    )


def test_full_discount_line_total_is_exactly_zero() -> None:
    """A 100% discount must zero out the line total exactly, regardless of quantity."""
    assert line_total(2.50, 3, 100) == 0.0


def test_receipt_line_totals_sum_exactly_to_displayed_grand_total() -> None:
    """The invoicing invariant: the sum of the individually displayed line amounts
    must equal the displayed grand total EXACTLY (to the cent) -- no independent
    rounding of the total from raw, unrounded (or independently re-derived) per-item
    values.
    """
    cart = Cart()
    for name, (unit_price, quantity, discount, _expected) in zip(
        ["A", "B", "C", "D"], _ADVERSARIAL_CASES, strict=True
    ):
        cart.add(name, unit_price, quantity, discount)
    text = format_receipt(cart)
    *item_lines, total_line = text.splitlines()
    line_cents = [round(float(line.rsplit("$", 1)[1]) * 100) for line in item_lines]
    total_cents = round(float(total_line.rsplit("$", 1)[1]) * 100)
    assert sum(line_cents) == total_cents
