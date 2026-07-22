from stats import running_total


def test_running_total_basic() -> None:
    assert running_total([1, 2, 3, 4]) == [1, 3, 6, 10]


def test_running_total_single_item() -> None:
    assert running_total([5]) == [5]


def test_running_total_empty() -> None:
    assert running_total([]) == []
