from grades import letter_grade


def test_boundary_scores_are_inclusive() -> None:
    assert letter_grade(90) == "A"
    assert letter_grade(80) == "B"
    assert letter_grade(70) == "C"
    assert letter_grade(60) == "D"


def test_below_boundary_scores() -> None:
    assert letter_grade(89) == "B"
    assert letter_grade(59) == "F"


def test_extremes() -> None:
    assert letter_grade(100) == "A"
    assert letter_grade(0) == "F"
