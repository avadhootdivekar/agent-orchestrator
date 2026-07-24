"""Reference solution for bench fixture: refactor-money-cents.

Used only by `FakeSubject(scripted_effect="copy-solution")` (bench/subjects.py) --
overlay-copied onto the workspace repo, then the marker dir is removed so it never
reaches a real subject/grader. Never read by ClaudeCliSubject/AoWorkflowSubject.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .pricing import line_total


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
        # Delegates to pricing.line_total for every item instead of re-deriving the
        # discount math independently -- removes the duplicate this refactor targets,
        # and guarantees this always agrees with what format_receipt prints per line.
        return sum(line_total(i.unit_price, i.quantity, i.discount_percent) for i in self.items)
