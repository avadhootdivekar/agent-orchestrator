# Refactor: cent-precise money math

This package (`shop/`) computes discounted prices for a shopping cart in three places:
`shop/pricing.py`'s `apply_discount`/`line_total`, `shop/cart.py`'s `Cart.subtotal`
(which currently re-derives the SAME discount formula independently instead of calling
`pricing.line_total`), and `shop/receipt.py` (which formats per-item lines via
`pricing.line_total` and the grand total via `cart.subtotal`).

Two problems, both must be fixed:

1. **Per-unit rounding before multiplying by quantity.** `pricing.line_total` rounds
   the discounted PER-UNIT price to the cent, then multiplies by quantity. Any
   rounding error in the per-unit price gets scaled up by quantity. The line total
   must be computed from the exact (unrounded) discounted amount for the whole line,
   and rounded to the cent exactly once.

2. **Binary-float rounding.** Rounding a value like `1.005` via Python's built-in
   `round()` (or an `f"{x:.2f}"` format string) does not always round the way a human
   reading the decimal digits would expect, because most decimal fractions (including
   `1.005`) are not exactly representable in binary floating point. Money amounts must
   be rounded from the EXACT decimal value the number represents (not from whatever
   binary float artifact happens to be stored), using standard half-up rounding to the
   nearest cent.

**Refactor `shop/cart.py`'s `Cart.subtotal` to delegate to `pricing.line_total`**
instead of re-deriving the discount math independently — this is the duplication this
task asks you to remove, and it is also why the bug must be fixed consistently: once
`pricing.line_total` is correct, `cart.py`'s own separate copy of the (still-buggy)
formula would otherwise make the cart's grand total silently disagree with the
individual line amounts `receipt.py` prints for each item.

Keep `tests/test_shop.py` passing exactly as it is (it already passes on the
unmodified fixture — the numbers it uses happen not to expose either bug). Do not
modify `tests/test_shop.py`, and do not modify anything under `.grading/` — it is a
held-out grading harness the run is scored against and is not part of what you are
asked to implement or test against directly.
