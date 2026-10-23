"""Reference-implementation file bundles (grader-owned, written under ``<grading_dir>/reference/``).

The programs live as real, linted/type-checked Python files in ``reference/`` and are read here
verbatim, so what the grader runs is exactly what the unit tests exercise.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

REFERENCE_DIR = Path(__file__).resolve().parent / "reference"
REFERENCE_PROGRAM = "server.py"
REFERENCE_RUN_SH = (
    "#!/bin/sh\n"
    "# Reference run.sh (grader-owned). INSTANCE_JSON points at the generated instance.json.\n"
    'cd "$(dirname "$0")" || exit 1\n'
    f"exec python3 {REFERENCE_PROGRAM}\n"
)


@cache
def _read(name: str) -> str:
    return (REFERENCE_DIR / name).read_text(encoding="utf-8")


def reference_files(program: str) -> dict[str, str]:
    """``{"run.sh": ..., "server.py": <contents of reference/<program>>}``."""
    return {"run.sh": REFERENCE_RUN_SH, REFERENCE_PROGRAM: _read(program)}
