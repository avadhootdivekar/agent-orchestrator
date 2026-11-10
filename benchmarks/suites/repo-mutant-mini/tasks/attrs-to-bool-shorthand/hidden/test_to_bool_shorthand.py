"""Held-out tests for `attr.converters.to_bool` single-letter answers (y/n)."""

import pytest
from attr.converters import to_bool


@pytest.mark.parametrize("value", ["y", "Y"])
def test_y_is_true(value):
    assert to_bool(value) is True


@pytest.mark.parametrize("value", ["n", "N"])
def test_n_is_false(value):
    assert to_bool(value) is False


def test_documented_true_values_still_work():
    for value in (True, "true", "t", "yes", "on", "1", 1, "TRUE", "Yes"):
        assert to_bool(value) is True


def test_documented_false_values_still_work():
    for value in (False, "false", "f", "no", "off", "0", 0, "FALSE", "No"):
        assert to_bool(value) is False


def test_other_strings_still_rejected():
    with pytest.raises(ValueError, match="Cannot convert value to bool"):
        to_bool("maybe")
