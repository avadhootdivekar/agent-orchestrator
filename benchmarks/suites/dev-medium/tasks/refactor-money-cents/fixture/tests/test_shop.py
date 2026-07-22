import pytest
from shop.cart import Cart
from shop.pricing import apply_discount, line_total
from shop.receipt import format_receipt


def test_apply_discount_basic() -> None:
    assert apply_discount(100.0, 10) == 90.0


def test_line_total_no_discount() -> None:
    assert line_total(19.99, 3) == pytest.approx(59.97)


def test_line_total_with_discount() -> None:
    assert line_total(50.0, 2, 20) == pytest.approx(80.0)


def test_cart_subtotal_sums_items() -> None:
    cart = Cart()
    cart.add("Widget", 19.99, 3)
    cart.add("Gadget", 50.0, 2, discount_percent=20)
    assert cart.subtotal() == pytest.approx(139.97)


def test_format_receipt_includes_item_lines_and_total() -> None:
    cart = Cart()
    cart.add("Widget", 19.99, 1)
    text = format_receipt(cart)
    assert "Widget x1: $19.99" in text
    assert "TOTAL: $19.99" in text
