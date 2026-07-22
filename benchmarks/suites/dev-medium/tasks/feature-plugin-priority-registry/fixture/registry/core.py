"""Priority-ordered handler registry (bench fixture: feature-plugin-priority-registry).

A `Registry` holds named "slots"; each slot can have multiple handlers registered at
different priorities. `handlers_for(name)` is SUPPOSED to return handlers ordered from
highest priority to lowest, with ties broken by registration order (the first handler
registered at a given priority sorts before a later one registered at that SAME
priority). Registration/unregistration already work; the ordering contract does not
(see the TODO below) -- that is this task's job to finish.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

Handler = Callable[[dict], object]


@dataclass
class _Entry:
    handler: Handler
    priority: int
    seq: int


class Registry:
    """Named slots of priority-ordered handlers."""

    def __init__(self) -> None:
        self._slots: dict[str, list[_Entry]] = {}
        self._seq = 0

    def register(self, name: str, handler: Handler, priority: int = 0) -> None:
        """Register `handler` under slot `name` at `priority` (higher runs first)."""
        entries = self._slots.setdefault(name, [])
        entries.append(_Entry(handler=handler, priority=priority, seq=self._seq))
        self._seq += 1

    def unregister(self, name: str, handler: Handler) -> None:
        """Remove every entry registered for `handler` under slot `name` (no-op if
        `name` or `handler` was never registered)."""
        entries = self._slots.get(name)
        if not entries:
            return
        self._slots[name] = [e for e in entries if e.handler is not handler]

    def is_registered(self, name: str, handler: Handler) -> bool:
        return any(e.handler is handler for e in self._slots.get(name, []))

    def handlers_for(self, name: str) -> list[Handler]:
        """Return the handlers registered for `name`.

        TODO (this task): must return them ordered from HIGHEST priority to LOWEST;
        entries registered at the SAME priority must keep their relative registration
        order (stable tie-break -- first-registered-at-that-priority comes first).
        The current implementation below ignores `priority` entirely and just returns
        raw registration order -- fix it.
        """
        entries = self._slots.get(name, [])
        return [e.handler for e in entries]
