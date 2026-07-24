import pytest
from shapes import rectangle_area, triangle_area


def test_rectangle_area() -> None:
    assert rectangle_area(3, 4) == 12


def test_triangle_area() -> None:
    assert triangle_area(6, 4) == 12.0


def test_rectangle_area_rejects_negative() -> None:
    with pytest.raises(ValueError):
        rectangle_area(-1, 4)


def test_triangle_area_rejects_negative() -> None:
    with pytest.raises(ValueError):
        triangle_area(6, -4)
