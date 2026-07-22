"""Grader script for test-write-validation-spec (invoked by the suite's `command`
grader: `uv run python .grading/run.py`, cwd = the mutated repo root).

Proves a NEW test file was written AND that it actually exercises each of the four
`validation.rules` functions' real behavior (scaled-up version of dev-core's
test-write-clamp mutation check, design doc §4.3's test-writing requirement: "a
mutation/negative check ... so an empty test can't pass"):

  1. `tests/test_validation.py` must exist and be non-empty.
  2. The new test(s) must PASS against the correct `validation/rules.py` (already in
     the repo, untouched -- this task's instruction says not to modify it).
  3. The SAME new test(s), run against a COPY of the repo with EACH of four
     independently seeded-buggy `validation/rules.py` variants (one bug per function:
     email multi-"@" acceptance, exclusive range boundaries, a missing password
     character-class check, and a phone number normalized without its leading-"1"
     guard), must FAIL for EVERY variant. A test suite that only covers one or two of
     the four functions (or covers them shallowly enough to miss a mutant) fails this
     grade -- unlike dev-core's single-mutant check_tests.py, ALL four mutants must be
     caught, not just one.

Exits 0 only if all conditions hold; exits 1 otherwise with a one-line reason on stdout
so a failed grade is diagnosable from run.json's `grader_raw_tail`. Never mutates the
repo's own `validation/rules.py` -- each seeded-buggy variant only ever exists in a
disposable tempdir copy.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
NEW_TEST_REL = Path("tests") / "test_validation.py"
MODULE_REL = Path("validation") / "rules.py"

_MUTANTS: dict[str, str] = {
    "email_allows_multiple_at_signs": '''
"""Seeded-buggy validation/rules.py variant (see _MUTANTS key)."""
from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"^\\S+@\\S+\\.\\S+$")


def is_valid_email(value: str) -> bool:
    if not isinstance(value, str):
        return False
    # BUG: no longer rejects a second "@" (e.g. "user@sub@example.com" now passes).
    return bool(_EMAIL_RE.match(value))


def in_range(value: float, low: float, high: float) -> bool:
    return low <= value <= high


def is_strong_password(value: str) -> bool:
    if len(value) < 8:
        return False
    has_digit = any(c.isdigit() for c in value)
    has_upper = any(c.isupper() for c in value)
    has_lower = any(c.islower() for c in value)
    return has_digit and has_upper and has_lower


def normalize_phone(value: str) -> str | None:
    digits = "".join(c for c in value if c.isdigit())
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits[0] == "1":
        return "+" + digits
    return None
''',
    "in_range_exclusive_boundaries": '''
"""Seeded-buggy validation/rules.py variant (see _MUTANTS key)."""
from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"^\\S+@\\S+\\.\\S+$")


def is_valid_email(value: str) -> bool:
    if not isinstance(value, str):
        return False
    if value.count("@") != 1:
        return False
    return bool(_EMAIL_RE.match(value))


def in_range(value: float, low: float, high: float) -> bool:
    # BUG: boundaries are now EXCLUSIVE (value == low or value == high wrongly False).
    return low < value < high


def is_strong_password(value: str) -> bool:
    if len(value) < 8:
        return False
    has_digit = any(c.isdigit() for c in value)
    has_upper = any(c.isupper() for c in value)
    has_lower = any(c.islower() for c in value)
    return has_digit and has_upper and has_lower


def normalize_phone(value: str) -> str | None:
    digits = "".join(c for c in value if c.isdigit())
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits[0] == "1":
        return "+" + digits
    return None
''',
    "password_missing_uppercase_check": '''
"""Seeded-buggy validation/rules.py variant (see _MUTANTS key)."""
from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"^\\S+@\\S+\\.\\S+$")


def is_valid_email(value: str) -> bool:
    if not isinstance(value, str):
        return False
    if value.count("@") != 1:
        return False
    return bool(_EMAIL_RE.match(value))


def in_range(value: float, low: float, high: float) -> bool:
    return low <= value <= high


def is_strong_password(value: str) -> bool:
    if len(value) < 8:
        return False
    has_digit = any(c.isdigit() for c in value)
    has_lower = any(c.islower() for c in value)
    # BUG: the uppercase-letter requirement was dropped entirely.
    return has_digit and has_lower


def normalize_phone(value: str) -> str | None:
    digits = "".join(c for c in value if c.isdigit())
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits[0] == "1":
        return "+" + digits
    return None
''',
    "phone_missing_leading_one_check": '''
"""Seeded-buggy validation/rules.py variant (see _MUTANTS key)."""
from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"^\\S+@\\S+\\.\\S+$")


def is_valid_email(value: str) -> bool:
    if not isinstance(value, str):
        return False
    if value.count("@") != 1:
        return False
    return bool(_EMAIL_RE.match(value))


def in_range(value: float, low: float, high: float) -> bool:
    return low <= value <= high


def is_strong_password(value: str) -> bool:
    if len(value) < 8:
        return False
    has_digit = any(c.isdigit() for c in value)
    has_upper = any(c.isupper() for c in value)
    has_lower = any(c.islower() for c in value)
    return has_digit and has_upper and has_lower


def normalize_phone(value: str) -> str | None:
    digits = "".join(c for c in value if c.isdigit())
    if len(digits) == 10:
        return "+1" + digits
    # BUG: dropped the "starts with 1" guard -- any 11-digit string now normalizes.
    if len(digits) == 11:
        return "+" + digits
    return None
''',
}


def _run_pytest(cwd: Path) -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=no", "tests"],
        cwd=cwd,
    )
    return result.returncode


def main() -> int:
    new_test_path = REPO_DIR / NEW_TEST_REL
    if not new_test_path.is_file():
        print(f"FAIL: {NEW_TEST_REL} not found -- no new test written")
        return 1
    if not new_test_path.read_text().strip():
        print(f"FAIL: {NEW_TEST_REL} is empty")
        return 1

    # (1) the new test(s) must pass against the CORRECT implementation.
    if _run_pytest(REPO_DIR) != 0:
        print("FAIL: new test(s) do not pass against the correct implementation")
        return 1

    # (2) mutation check: for EACH seeded mutant, copy the repo, seed that one bug,
    # and the SAME new test(s) must FAIL -- proves the suite actually exercises every
    # function's real behavior, not just a subset or a tautology.
    for mutant_name, buggy_module in _MUTANTS.items():
        with tempfile.TemporaryDirectory(prefix="bench-mutation-") as tmp:
            mutated_dir = Path(tmp) / "repo"
            shutil.copytree(REPO_DIR, mutated_dir)
            (mutated_dir / MODULE_REL).write_text(buggy_module)
            mutation_rc = _run_pytest(mutated_dir)
        if mutation_rc == 0:
            print(
                f"FAIL: new test(s) still pass against the seeded-buggy mutant "
                f"{mutant_name!r} -- the suite must catch this regression too"
            )
            return 1

    print(
        "OK: new test(s) exist, pass on the correct implementation, and catch all "
        f"{len(_MUTANTS)} seeded-buggy mutants"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
