"""Bench-local error hierarchy.

`bench/` is a separate module that is never imported by core (SI-1) but is free to
import *from* core (§3.2 of the design doc). `SpecValidationError` is reused as-is for
every suite/subject spec-loading failure (malformed JSON, schema violation, duplicate
id, unknown grader/subject type, missing fixture/instruction path) so callers only need
to catch one error type for `load_suite`/`load_subject` (mirrors core `spec.py`'s own
convention). `BenchError` is the base of a small bench-only hierarchy for *runtime*
failures -- `SubjectError`/`GraderError` are the seam `bench/subjects.py`/
`bench/graders.py` raise from their `Subject.run`/`Grader.grade` implementations;
`ResultsError` is the seam `bench/results.py` (T-Rpt3Wq) raises for a malformed/missing
`run.json` or a refused (unlike-suite / mixed-fingerprint) comparison -- co-located here
rather than defined locally in `results.py` to match the established one-error-type-per-
producing-module convention already set by `SubjectError`/`GraderError`.
"""

from __future__ import annotations

from agent_orchestrator.errors import SpecValidationError

__all__ = ["BenchError", "SpecValidationError", "SubjectError", "GraderError", "ResultsError"]


class BenchError(Exception):
    """Base class for all bench-specific runtime errors (mirrors core OrchestratorError)."""


class SubjectError(BenchError):
    """Raised when a `Subject` adapter fails to run a task (implemented in T-Sbj9Ka)."""


class GraderError(BenchError):
    """Raised when a `Grader` adapter fails to grade a task (implemented in T-Grd7Vx)."""


class ResultsError(BenchError):
    """Raised by `bench/results.py` (T-Rpt3Wq): a `run.json` that cannot be read/does
    not match `BenchRunRecord`'s schema, or a `build_comparison` call refused because
    the given run dirs span different suites/schema versions, or mix two runs for the
    SAME subject id with different `config_fingerprint`s without `allow_mixed=True`.
    """
