"""Held-out grading tests for bugfix-search-index-scan-budget -- NOT part of the
visible tests/ suite an agent sees. Checks the performance half of the contract (query
must answer purely from the prebuilt index, never by re-reading document text) via a
deterministic scan counter -- never wall-clock timing, so this stays fast and
non-flaky -- plus one more whole-word-vs-substring correctness edge case the visible
suite does not exercise.
"""

from __future__ import annotations

from search.documents import Document
from search.index import InvertedIndex
from search.query import search

_DOCS = [
    Document(1, "The cat sat on the mat"),
    Document(2, "A category of felines"),
    Document(3, "Dogs and cats are pets"),
    Document(4, "The mat was red"),
    Document(5, "Please concatenate the strings"),
]


def _build_index() -> InvertedIndex:
    idx = InvertedIndex()
    idx.build(_DOCS)
    return idx


def test_whole_word_query_does_not_match_substrings_of_other_words() -> None:
    idx = _build_index()
    assert search(_DOCS, idx, "con") == []
    assert search(_DOCS, idx, "cat") == [1]


def test_query_uses_the_index_not_a_full_document_scan() -> None:
    idx = _build_index()
    scan_counter = [0]
    result = search(_DOCS, idx, "cat", scan_counter=scan_counter)
    assert result == [1]
    assert scan_counter[0] == 0, (
        "search() read a document's raw text at query time -- it must answer purely "
        "from the prebuilt index (InvertedIndex.postings), not by re-scanning every "
        "document on every call"
    )


def test_and_query_across_multiple_terms_uses_the_index() -> None:
    idx = _build_index()
    scan_counter = [0]
    result = search(_DOCS, idx, "mat red", scan_counter=scan_counter)
    assert result == [4]
    assert scan_counter[0] == 0


def test_query_for_nonexistent_term_returns_empty() -> None:
    idx = _build_index()
    assert search(_DOCS, idx, "nonexistent") == []


_LARGER_DOCS = _DOCS + [
    Document(6, "The red category catalog listing"),
    Document(7, "A concatenated string of catnip"),
    Document(8, "Red mat everywhere, red cats roam"),
]


def _build_larger_index() -> InvertedIndex:
    idx = InvertedIndex()
    idx.build(_LARGER_DOCS)
    return idx


def test_three_term_and_query_over_a_larger_corpus_uses_only_the_index() -> None:
    """A harder AND-intersection case than the two-term visible/held-out scenarios
    above: three terms, an 8-document corpus with several near-miss substring traps
    ("category", "catalog", "concatenated", "catnip" all contain "cat" as a
    substring; "red"/"mat" appear in multiple combinations) -- the query must still
    answer purely from postings intersection, never a raw text re-scan.
    """
    idx = _build_larger_index()
    scan_counter = [0]
    result = search(_LARGER_DOCS, idx, "red mat everywhere", scan_counter=scan_counter)
    assert result == [8]
    assert scan_counter[0] == 0


def test_whole_word_matching_holds_over_the_larger_corpus_substring_traps() -> None:
    idx = _build_larger_index()
    # "cat" alone must still only match doc 1 -- none of "category"/"catalog"/
    # "concatenated"/"catnip"/"cats" qualify as the whole word "cat".
    assert search(_LARGER_DOCS, idx, "cat") == [1]
    assert search(_LARGER_DOCS, idx, "red") == [4, 6, 8]
