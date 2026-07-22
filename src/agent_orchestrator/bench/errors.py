"""Bench-local error hierarchy.

`bench/` is a separate module that is never imported by core (SI-1) but is free to
import *from* core (§3.2 of the design doc). `SpecValidationError` is reused as-is for
every suite/subject spec-loading failure (malformed JSON, schema violation, duplicate
id, unknown grader/subject type, missing fixture/instruction path) so callers only need
to catch one error type for `load_suite`/`load_subject` (mirrors core `spec.py`'s own
convention). `BenchError` is the base of a small bench-only hierarchy for *runtime*
failures that don't exist yet in this task -- `SubjectError`/`GraderError` are declared
here as the seam T-Sbj9Ka/T-Grd7Vx raise from their `Subject.run`/`Grader.grade`
implementations.
"""

from __future__ import annotations

from agent_orchestrator.errors import SpecValidationError

__all__ = ["BenchError", "SpecValidationError", "SubjectError", "GraderError"]


class BenchError(Exception):
    """Base class for all bench-specific runtime errors (mirrors core OrchestratorError)."""


class SubjectError(BenchError):
    """Raised when a `Subject` adapter fails to run a task (implemented in T-Sbj9Ka)."""


class GraderError(BenchError):
    """Raised when a `Grader` adapter fails to grade a task (implemented in T-Grd7Vx)."""
