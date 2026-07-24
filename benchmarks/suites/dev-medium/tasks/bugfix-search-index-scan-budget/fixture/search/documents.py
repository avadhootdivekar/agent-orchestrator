"""Tiny in-memory document corpus + tokenizer (bench fixture:
bugfix-search-index-scan-budget)."""

from __future__ import annotations

import re
from dataclasses import dataclass

_TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass
class Document:
    id: int
    text: str


def tokenize(text: str) -> list[str]:
    """Split `text` into lowercase whole-word tokens (alphanumeric runs). Note this
    is WORD tokenization, not substring extraction: `"category"` tokenizes to
    `["category"]`, never `["cat", "egory"]` or anything containing `"cat"` as a
    separate token.
    """
    return _TOKEN_RE.findall(text.lower())
