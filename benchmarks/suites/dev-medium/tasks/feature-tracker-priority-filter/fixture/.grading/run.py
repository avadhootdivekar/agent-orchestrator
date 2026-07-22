"""Grader script for feature-tracker-priority-filter (invoked by the suite's `command`
grader: `uv run python .grading/run.py`, cwd = the mutated repo root).

Verifies BOTH:
  1. The VISIBLE suite (`tests/`) passes -- the basic priority-filter contract the
     instruction asks for, plus "you didn't break what already worked".
  2. The HELD-OUT suite (`.grading/tests/`) passes -- sort ordering, combined
     filters, invalid-value handling, and the raw-string-vs-enum contract a fix aimed
     only at making the single visible priority-filter test pass tends to miss.

Exits 0 only if both hold; exits 1 otherwise with a one-line reason on stdout so a
failed grade is diagnosable from run.json's `grader_raw_tail`. Never mutates the repo.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent


def _run_pytest(*targets: str) -> int:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=no", *targets],
        cwd=REPO_DIR,
    ).returncode


def main() -> int:
    if _run_pytest("tests") != 0:
        print("FAIL: visible tests/ did not pass")
        return 1
    if _run_pytest(".grading/tests") != 0:
        print("FAIL: held-out .grading/tests did not pass")
        return 1
    print("OK: visible and held-out tests both pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
