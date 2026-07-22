"""Receipt formatting (bench fixture: refactor-money-cents)."""

from __future__ import annotations

from .cart import Cart
from .pricing import line_total


def format_receipt(cart: Cart) -> str:
    lines = []
    for item in cart.items:
        total = line_total(item.unit_price, item.quantity, item.discount_percent)
        lines.append(f"{item.name} x{item.quantity}: ${total:.2f}")
    lines.append(f"TOTAL: ${cart.subtotal():.2f}")
    return "\n".join(lines)
