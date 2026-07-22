"""Reference solution for bench fixture: feature-plugin-priority-registry.

Used only by `FakeSubject(scripted_effect="copy-solution")` (bench/subjects.py) --
overlay-copied onto the workspace repo, then the marker dir is removed so it never
reaches a real subject/grader. Never read by ClaudeCliSubject/AoWorkflowSubject.
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
    def __init__(self) -> None:
        self._slots: dict[str, list[_Entry]] = {}
        self._seq = 0

    def register(self, name: str, handler: Handler, priority: int = 0) -> None:
        entries = self._slots.setdefault(name, [])
        entries.append(_Entry(handler=handler, priority=priority, seq=self._seq))
        self._seq += 1

    def unregister(self, name: str, handler: Handler) -> None:
        entries = self._slots.get(name)
        if not entries:
            return
        self._slots[name] = [e for e in entries if e.handler is not handler]

    def is_registered(self, name: str, handler: Handler) -> bool:
        return any(e.handler is handler for e in self._slots.get(name, []))

    def handlers_for(self, name: str) -> list[Handler]:
        # Stable sort by (-priority, seq): highest priority first; ties keep
        # registration order since Python's sort is stable and seq is monotonic.
        entries = sorted(self._slots.get(name, []), key=lambda e: (-e.priority, e.seq))
        return [e.handler for e in entries]
