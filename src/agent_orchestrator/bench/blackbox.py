"""`blackbox` grader (HLD §5.2, bench-probe-dsl.md) -- STUB, owner: blackbox track.

Drives the artefact over its run contract (http/jsonl/cli) with the probe DSL, scores
core/trap/perf groups, and compares perf against the hidden reference implementation.
"""

from __future__ import annotations

from typing import Any

from .errors import GraderError
from .graders import Grader, GraderContext, GradeResult
from .registries import register_grader
from .spec import GraderConfig

BLACKBOX_GRADER_TYPE = "blackbox"


def match(expected: Any, actual: Any, path: str = "$") -> list[str]:
    """Matcher language (bench-probe-dsl.md §3.4). Returns [] on match, else mismatch paths."""
    raise GraderError("blackbox.match is not implemented yet (E-Bn7Dsc)")


class BlackboxGrader(Grader):
    def _grade(self, cfg: GraderConfig, ctx: GraderContext) -> GradeResult:
        raise GraderError("blackbox grader is not implemented yet (E-Bn7Dsc)")


register_grader(BLACKBOX_GRADER_TYPE, BlackboxGrader)
