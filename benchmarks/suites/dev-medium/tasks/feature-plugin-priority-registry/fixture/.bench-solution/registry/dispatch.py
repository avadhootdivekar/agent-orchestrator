"""Reference solution for bench fixture: feature-plugin-priority-registry.

Used only by `FakeSubject(scripted_effect="copy-solution")` (bench/subjects.py) --
overlay-copied onto the workspace repo, then the marker dir is removed so it never
reaches a real subject/grader. Never read by ClaudeCliSubject/AoWorkflowSubject.
"""

from __future__ import annotations

from .core import Registry


class NoHandlerResolvedError(Exception):
    """Raised when no registered handler for a slot resolves the payload."""


def dispatch(registry: Registry, name: str, payload: dict) -> object:
    handlers = registry.handlers_for(name)
    for handler in handlers:
        outcome = handler(payload)
        if outcome is not None:
            return outcome
    raise NoHandlerResolvedError(f"no handler for {name!r} resolved payload {payload!r}")
