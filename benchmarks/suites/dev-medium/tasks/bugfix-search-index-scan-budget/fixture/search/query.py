"""Query engine over an `InvertedIndex` (bench fixture:
bugfix-search-index-scan-budget).

`search(documents, index, query, scan_counter=None)` executes a simple AND query
(space-separated terms; a doc matches only if it contains ALL terms as whole,
case-insensitive words) and returns the matching doc ids, sorted ascending.

`scan_counter`, if given, is incremented once per document whose full `.text` this
function reads during the query -- see instruction.md: a correct, index-based
implementation never needs to read a document's raw text at query time, since the
index already knows which docs contain each word.
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
    """Return doc ids matching every (whole-word, case-insensitive) term in `query`.

    TODO (this task): the implementation below is wrong on two counts, both must be
    fixed:
      1. Correctness -- it checks whether each term is a SUBSTRING of the document's
         raw (lowercased) text, so a query for "cat" incorrectly matches a document
         that only contains "category" or "cats". Matching must be on WHOLE WORDS,
         via the already-built `index` (see `InvertedIndex.postings`).
      2. Performance -- it re-reads every document's full text on every call and
         never consults `index` at all (`index` is unused below!). A query must
         answer purely from `index.postings(term)` (set intersection across terms
         for the AND semantics), never by reading `document.text` --
         `scan_counter`, when given, must stay at 0 for such a query.
    """
    terms = query.lower().split()
    if not terms:
        return []
    matches = []
    for doc in documents:
        if scan_counter is not None:
            scan_counter[0] += 1
        text = doc.text.lower()
        if all(term in text for term in terms):
            matches.append(doc.id)
    return sorted(matches)
