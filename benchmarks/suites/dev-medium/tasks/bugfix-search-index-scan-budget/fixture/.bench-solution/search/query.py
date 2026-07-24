"""Reference solution for bench fixture: bugfix-search-index-scan-budget.

Used only by `FakeSubject(scripted_effect="copy-solution")` (bench/subjects.py) --
overlay-copied onto the workspace repo, then the marker dir is removed so it never
reaches a real subject/grader. Never read by ClaudeCliSubject/AoWorkflowSubject.
"""

from __future__ import annotations

from .documents import Document
from .index import InvertedIndex


def search(
    documents: list[Document],
    index: InvertedIndex,
    query: str,
    scan_counter: list[int] | None = None,
) -> list[int]:
    # `documents`/`scan_counter` are accepted for API compatibility with callers/tests
    # but are never read here -- a correct, index-only query has no reason to touch
    # either a document's raw text or the scan counter.
    del documents, scan_counter
    terms = query.lower().split()
    if not terms:
        return []
    result: set[int] | None = None
    for term in terms:
        result = index.postings(term) if result is None else (result & index.postings(term))
        if not result:
            break
    return sorted(result or set())
