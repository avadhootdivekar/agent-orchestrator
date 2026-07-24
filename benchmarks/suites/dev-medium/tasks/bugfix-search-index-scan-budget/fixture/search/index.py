"""Inverted index over a document corpus (bench fixture:
bugfix-search-index-scan-budget). Already correct/complete -- `search/query.py` is
where this task's fix belongs.
"""

from __future__ import annotations

from .documents import Document, tokenize


class InvertedIndex:
    def __init__(self) -> None:
        self._postings: dict[str, set[int]] = {}
        self._doc_count = 0

    def build(self, documents: list[Document]) -> None:
        """Populate postings: word -> set of doc ids containing that whole word."""
        self._postings = {}
        self._doc_count = len(documents)
        for doc in documents:
            for token in set(tokenize(doc.text)):
                self._postings.setdefault(token, set()).add(doc.id)

    def postings(self, term: str) -> set[int]:
        """Doc ids containing the whole word `term` (case-insensitive). Empty set if
        `term` was never indexed."""
        return set(self._postings.get(term.lower(), set()))

    @property
    def doc_count(self) -> int:
        return self._doc_count
