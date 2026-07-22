"""Chain-of-responsibility dispatch over a Registry (bench fixture)."""

from __future__ import annotations

from .core import Registry


class NoHandlerResolvedError(Exception):
    """Raised when no registered handler for a slot resolves the payload."""


def dispatch(registry: Registry, name: str, payload: dict) -> object:
    """Call each handler registered for `name`, in priority order (see
    `Registry.handlers_for`), STOPPING at the first handler whose return value is not
    None. Raises `NoHandlerResolvedError` if no handler resolves the payload -- either
    no handlers are registered for `name` at all, or every registered handler returned
    None.

    TODO (this task): the implementation below is wrong on two counts, both must be
    fixed:
      1. It does not stop at the first non-None result -- it calls EVERY handler
         registered for `name` and returns whichever result came LAST, even when an
         earlier (higher-priority) handler already resolved the payload. Handlers
         after the first resolving one must not be called at all.
      2. It never raises `NoHandlerResolvedError` -- it silently returns `None` when
         nothing resolves the payload.
    """
    handlers = registry.handlers_for(name)
    result = None
    for handler in handlers:
        outcome = handler(payload)
        if outcome is not None:
            result = outcome
    return result
