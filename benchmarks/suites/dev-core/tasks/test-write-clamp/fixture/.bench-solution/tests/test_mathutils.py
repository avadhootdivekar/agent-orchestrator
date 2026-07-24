from mathutils import clamp


def test_clamp_below_low() -> None:
    assert clamp(-5, 0, 10) == 0


def test_clamp_above_high() -> None:
    assert clamp(15, 0, 10) == 10


def test_clamp_inside_range() -> None:
    assert clamp(5, 0, 10) == 5
