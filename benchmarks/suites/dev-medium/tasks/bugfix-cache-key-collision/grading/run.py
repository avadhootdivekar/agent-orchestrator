"""Grader script for bugfix-cache-key-collision, run by the suite's `command` grader as
`uv run python {grading_dir}/run.py --repo {repo_dir}` (cwd = the mutated repo root).

Verifies BOTH:
  1. The VISIBLE suite (`tests/`) passes -- including the eviction test whose failure
     is the misleading symptom this task presents.
  2. The HELD-OUT suite (`grading/tests/`) passes -- direct regression coverage of
     the actual root cause (key normalization collapsing distinct keys), which a fix
     aimed only at the eviction logic in `cache/store.py` will not satisfy.

Exits 0 only if both hold; exits 1 otherwise with a one-line reason on stdout so a
failed grade is diagnosable from run.json's `grader_raw_tail`. Never mutates the repo.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

# This script lives in the task's grader-owned `grading/` dir (copied into the run's
# agent-hidden `grading_dir`); the held-out tests sit next to it.
HELD_OUT_TESTS = Path(__file__).resolve().parent / "tests"


def _run_pytest(repo_dir: Path, *targets: str) -> int:
    # The held-out tests live outside the repo, so put the repo on PYTHONPATH (the repo's
    # own conftest.py only covers tests collected underneath it).
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(repo_dir), env.get("PYTHONPATH")]))
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=no", "-p", "no:cacheprovider", *targets],
        cwd=repo_dir,
        env=env,
    ).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="mutated repo (default: cwd)")
    repo_dir = parser.parse_args(argv).repo.resolve()

    if _run_pytest(repo_dir, "tests") != 0:
        print("FAIL: visible tests/ did not pass")
        return 1
    if _run_pytest(repo_dir, str(HELD_OUT_TESTS)) != 0:
        print("FAIL: held-out tests did not pass")
        return 1
    print("OK: visible and held-out tests both pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
