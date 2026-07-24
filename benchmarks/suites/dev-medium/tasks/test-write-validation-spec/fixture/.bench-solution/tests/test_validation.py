"""Reference solution for bench fixture: test-write-validation-spec.

Used only by `FakeSubject(scripted_effect="copy-solution")` (bench/subjects.py) --
overlay-copied onto the workspace repo, then the marker dir is removed so it never
reaches a real subject/grader. Never read by ClaudeCliSubject/AoWorkflowSubject.
"""

from __future__ import annotations

from validation.rules import in_range, is_strong_password, is_valid_email, normalize_phone


def test_is_valid_email_accepts_plausible_address() -> None:
    assert is_valid_email("user@example.com") is True


def test_is_valid_email_rejects_missing_at_sign() -> None:
    assert is_valid_email("userexample.com") is False


def test_is_valid_email_rejects_more_than_one_at_sign() -> None:
    assert is_valid_email("user@sub@example.com") is False


def test_is_valid_email_rejects_missing_domain_dot() -> None:
    assert is_valid_email("user@examplecom") is False


def test_is_valid_email_rejects_whitespace() -> None:
    assert is_valid_email("user @example.com") is False


def test_in_range_inside() -> None:
    assert in_range(5, 0, 10) is True


def test_in_range_boundaries_are_inclusive() -> None:
    assert in_range(0, 0, 10) is True
    assert in_range(10, 0, 10) is True


def test_in_range_outside() -> None:
    assert in_range(-1, 0, 10) is False
    assert in_range(11, 0, 10) is False


def test_is_strong_password_accepts_valid() -> None:
    assert is_strong_password("Abcdefg1") is True


def test_is_strong_password_rejects_too_short() -> None:
    assert is_strong_password("Ab1defg") is False


def test_is_strong_password_rejects_missing_digit() -> None:
    assert is_strong_password("Abcdefgh") is False


def test_is_strong_password_rejects_missing_uppercase() -> None:
    assert is_strong_password("abcdefg1") is False


def test_is_strong_password_rejects_missing_lowercase() -> None:
    assert is_strong_password("ABCDEFG1") is False


def test_normalize_phone_ten_digits() -> None:
    assert normalize_phone("555-123-4567") == "+15551234567"


def test_normalize_phone_eleven_digits_with_leading_one() -> None:
    assert normalize_phone("1-555-123-4567") == "+15551234567"


def test_normalize_phone_eleven_digits_without_leading_one_is_invalid() -> None:
    assert normalize_phone("9-555-123-4567") is None


def test_normalize_phone_wrong_digit_count_is_invalid() -> None:
    assert normalize_phone("555-1234") is None
