from textutils import count_vowels


def test_count_vowels_mixed_case() -> None:
    assert count_vowels("Hello World") == 3


def test_count_vowels_none() -> None:
    assert count_vowels("xyz") == 0


def test_count_vowels_empty() -> None:
    assert count_vowels("") == 0


def test_count_vowels_all_vowels() -> None:
    assert count_vowels("AEIOUaeiou") == 10
