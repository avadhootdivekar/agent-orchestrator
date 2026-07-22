"""Validation rule library (bench fixture: test-write-validation-spec).

Already correct and complete -- this task is to WRITE TESTS against the spec in
instruction.md, not to fix anything here. Do not modify this file.
"""

from __future__ import annotations

import re

# Non-whitespace local part + domain, domain has at least one "." with a non-empty
# label each side. Deliberately permissive about "@" here (the exactly-one-"@" rule is
# enforced separately below by `value.count("@") != 1`) -- both checks together are
# what the spec requires.
_EMAIL_RE = re.compile(r"^\S+@\S+\.\S+$")


def is_valid_email(value: str) -> bool:
    """A syntactically-plausible email address: exactly one "@"; a non-empty local
    part before it; a domain part after it containing at least one "."  with a
    non-empty label on each side of that dot; no whitespace anywhere in the string.
    """
    if not isinstance(value, str):
        return False
    if value.count("@") != 1:
        return False
    return bool(_EMAIL_RE.match(value))


def in_range(value: float, low: float, high: float) -> bool:
    """True iff `low <= value <= high` (both ends inclusive)."""
    return low <= value <= high


def is_strong_password(value: str) -> bool:
    """A "strong" password: at least 8 characters, containing at least one digit, at
    least one uppercase letter, and at least one lowercase letter (in any order,
    anywhere in the string)."""
    if len(value) < 8:
        return False
    has_digit = any(c.isdigit() for c in value)
    has_upper = any(c.isupper() for c in value)
    has_lower = any(c.islower() for c in value)
    return has_digit and has_upper and has_lower


def normalize_phone(value: str) -> str | None:
    """Normalize a US phone number to E.164-ish form, or `None` if it can't be.

    Strips everything except digits from `value`. If exactly 10 digits remain,
    returns them prefixed with `"+1"`. If exactly 11 digits remain AND the first is
    `"1"` (a US country code), returns them prefixed with `"+"`. Any other digit
    count (or an 11-digit string not starting with `"1"`) returns `None`.
    """
    digits = "".join(c for c in value if c.isdigit())
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits[0] == "1":
        return "+" + digits
    return None
