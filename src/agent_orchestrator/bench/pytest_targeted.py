"""`pytest_targeted` grader (HLD §4.2/§8) -- STUB, owner: Tier-2 track.

Restores hidden tests from grading_dir into the repo, runs `fail_to_pass` + a bounded
`pass_to_pass` sample with a per-test timeout, and reports collateral-diff statistics.
"""

from __future__ import annotations

from .errors import GraderError
from .graders import Grader, GraderContext, GradeResult
from .registries import register_grader
from .spec import GraderConfig

PYTEST_TARGETED_GRADER_TYPE = "pytest_targeted"


class PytestTargetedGrader(Grader):
    def _grade(self, cfg: GraderConfig, ctx: GraderContext) -> GradeResult:
        raise GraderError("pytest_targeted grader is not implemented yet (E-Bn7Dsc)")


register_grader(PYTEST_TARGETED_GRADER_TYPE, PytestTargetedGrader)
