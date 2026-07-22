"""A tiny capacity-bounded LRU cache (bench fixture: bugfix-cache-key-collision)."""

from __future__ import annotations

from collections import OrderedDict

from .keys import normalize_key


class LRUCache:
    """Least-recently-used cache bounded to `capacity` entries."""

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self._data: OrderedDict[str, object] = OrderedDict()

    def get(self, key: str) -> object | None:
        norm = normalize_key(key)
        if norm not in self._data:
            return None
        self._data.move_to_end(norm)
        return self._data[norm]

    def set(self, key: str, value: object) -> None:
        norm = normalize_key(key)
        if norm in self._data:
            self._data.move_to_end(norm)
        self._data[norm] = value
        if len(self._data) > self.capacity:
            self._data.popitem(last=False)  # evict the least-recently-used entry

    def __len__(self) -> int:
        return len(self._data)

    def keys(self) -> list[str]:
        return list(self._data.keys())
