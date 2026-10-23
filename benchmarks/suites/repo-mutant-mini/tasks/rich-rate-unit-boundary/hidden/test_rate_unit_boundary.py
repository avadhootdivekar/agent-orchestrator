"""Held-out tests: unit selection at exact powers of 1000 / 1024."""

import pytest
from rich import filesize
from rich.progress import TaskProgressColumn


@pytest.mark.parametrize(
    ("speed", "expected"),
    [
        pytest.param(999.0, "999.0 it/s", id="below-1k"),
        pytest.param(1000.0, "1.0×10³ it/s", id="exactly-1k"),
        pytest.param(1500.0, "1.5×10³ it/s", id="1.5k"),
        pytest.param(1_000_000.0, "1.0×10⁶ it/s", id="exactly-1m"),
        pytest.param(1_000_000_000.0, "1.0×10⁹ it/s", id="exactly-1g"),
    ],
)
def test_render_speed_unit_switches_at_exact_powers_of_1000(speed, expected):
    assert TaskProgressColumn.render_speed(speed).plain == expected


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        pytest.param(1023, (1, "bytes"), id="below-1k"),
        pytest.param(1024, (1024, "KB"), id="exactly-1k"),
        pytest.param(1024**2, (1024**2, "MB"), id="exactly-1m"),
    ],
)
def test_pick_unit_and_suffix_at_binary_boundaries(size, expected):
    units = ["bytes", "KB", "MB", "GB"]
    assert filesize.pick_unit_and_suffix(size, units, 1024) == expected


def test_pick_unit_and_suffix_just_below_boundary_stays_in_lower_unit():
    units = ["bytes", "KB", "MB", "GB"]
    assert filesize.pick_unit_and_suffix(1024**2 - 1, units, 1024) == (1024, "KB")
