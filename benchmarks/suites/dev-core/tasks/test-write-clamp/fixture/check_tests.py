"""Grader script for the test-write-clamp task (invoked by the suite's `command`
grader: `uv run python check_tests.py`, cwd = the mutated repo root).

Proves a NEW test file was written AND that it actually exercises `clamp`'s behavior
(design doc §4.3's test-writing requirement: "a mutation/negative check ... so an
empty test can't pass"):

  1. `tests/test_mathutils.py` must exist and be non-empty.
  2. The new test(s) must PASS against the correct `mathutils.py` (already in the repo,
     untouched -- this task's instruction says not to modify it).
  3. The SAME new test(s), run against a COPY of the repo with a seeded-buggy
     `mathutils.py` (clamp() becomes a no-op), must FAIL. A test that passes either
     way (e.g. an empty test, or one that only checks `clamp` is importable) fails
     this mutation check and the whole grade.

Exits 0 only if all three hold; exits 1 otherwise with a one-line reason on stdout so
a failed grade is diagnosable from run.json's `grader_raw_tail`. Never mutates the
repo's own `mathutils.py` -- the seeded-buggy version only ever exists in a disposable
tempdir copy.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
NEW_TEST_REL = Path("tests") / "test_mathutils.py"
MODULE_REL = Path("mathutils.py")

# A seeded bug: clamp() becomes a no-op. Any test that actually exercises clamping
# behaviour (not just "the module imports") must fail against this.
BUGGY_MODULE = (
    '"""Seeded-buggy mathutils.py for the test-write-clamp mutation check."""\n\n\n'
    "def clamp(value, low, high):\n"
    "    return value  # seeded bug: no clamping\n"
)


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

    # (1) the new test must pass against the CORRECT implementation.
    if _run_pytest(REPO_DIR) != 0:
        print("FAIL: new test(s) do not pass against the correct implementation")
        return 1

    # (2) mutation check: copy the repo, seed the bug, and the SAME new test(s) must
    # now FAIL -- proves the test is not an empty/no-op/tautology.
    with tempfile.TemporaryDirectory(prefix="bench-mutation-") as tmp:
        mutated_dir = Path(tmp) / "repo"
        shutil.copytree(REPO_DIR, mutated_dir)
        (mutated_dir / MODULE_REL).write_text(BUGGY_MODULE)
        mutation_rc = _run_pytest(mutated_dir)

    if mutation_rc == 0:
        print(
            "FAIL: new test(s) still pass against a seeded-buggy implementation "
            "(mutation check) -- the test must actually exercise clamp()'s behavior"
        )
        return 1

    print(
        "OK: new test(s) exist, pass on the correct implementation, and fail on a seeded-buggy one"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
