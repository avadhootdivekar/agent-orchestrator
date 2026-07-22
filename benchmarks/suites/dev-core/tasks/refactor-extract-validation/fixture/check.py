"""Grader script for the refactor-extract-validation task (invoked by the suite's
`command` grader: `uv run python check.py`, cwd = the mutated repo root).

Verifies BOTH:
  1. `tests/test_shapes.py` still passes (behavior preserved).
  2. The duplicated validation message no longer appears twice in `shapes.py` (the DRY
     invariant this task's instruction asks for -- the unmodified fixture already
     passes (1) but fails (2), so `solved=False` on the unmodified fixture, per this
     suite's AC2).

Exits 0 only if both hold; exits 1 otherwise with a one-line reason on stdout so a
failed grade is diagnosable from run.json's `grader_raw_tail`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
DUP_MARKER = "dimensions must be non-negative"


def main() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=no", "tests"],
        cwd=REPO_DIR,
    )
    if result.returncode != 0:
        print("FAIL: tests/test_shapes.py did not pass")
        return 1

    source = (REPO_DIR / "shapes.py").read_text()
    occurrences = source.count(DUP_MARKER)
    if occurrences != 1:
        print(
            "FAIL: expected the validation message to appear exactly once in "
            f"shapes.py (helper extracted), found {occurrences} occurrence(s)"
        )
        return 1

    print("OK: tests pass and the duplicated validation was extracted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
