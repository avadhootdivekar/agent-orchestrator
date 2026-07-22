"""Cache key normalization (bench fixture: bugfix-cache-key-collision).

Cache keys should be treated case-insensitively, with surrounding whitespace ignored,
so callers don't have to worry about exact capitalization or leading/trailing spacing
when looking a value back up -- e.g. `"  Foo Bar  "` and `"foo bar"` must normalize to
the SAME key. Internal whitespace is part of the key's identity and must be preserved:
`"ali ce"` and `"alice"` are two different keys.
"""

from __future__ import annotations


def normalize_key(key: str) -> str:
    """Normalize `key` for case-insensitive, surrounding-whitespace-tolerant lookups."""
    return key.lower().replace(" ", "")
