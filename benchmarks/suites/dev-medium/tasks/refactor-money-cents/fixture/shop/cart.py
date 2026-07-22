"""Shopping cart (bench fixture: refactor-money-cents)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CartItem:
    name: str
    unit_price: float
    quantity: int
    discount_percent: float = 0.0


@dataclass
class Cart:
    items: list[CartItem] = field(default_factory=list)

    def add(
        self,
        name: str,
        unit_price: float,
        quantity: int,
        discount_percent: float = 0.0,
    ) -> None:
        self.items.append(CartItem(name, unit_price, quantity, discount_percent))

    def subtotal(self) -> float:
        """Sum of every item's line total.

        NOTE: re-derives the discounted-price math independently instead of calling
        `pricing.line_total` -- the same discount formula, copy-pasted here, rounding
        the per-unit price to the cent BEFORE multiplying by quantity. See
        instruction.md: this duplication is exactly what drifts out of sync with
        `pricing.py` (and `receipt.py`, which uses `pricing.line_total` for its
        per-item lines) once one of the two copies gets fixed and the other doesn't.
        """
        total = 0.0
        for item in self.items:
            discounted_unit = item.unit_price * (1 - item.discount_percent / 100)
            total += round(discounted_unit, 2) * item.quantity
        return total
