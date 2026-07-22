from search.documents import Document
from search.index import InvertedIndex
from search.query import search

_DOCS = [
    Document(1, "The cat sat on the mat"),
    Document(2, "A category of felines"),
    Document(3, "Dogs and cats are pets"),
    Document(4, "The mat was red"),
]


def _build_index() -> InvertedIndex:
    idx = InvertedIndex()
    idx.build(_DOCS)
    return idx


def test_index_build_populates_postings_for_known_words() -> None:
    idx = _build_index()
    assert idx.postings("mat") == {1, 4}
    assert idx.postings("nope") == set()


def test_single_term_query_matches_expected_docs() -> None:
    idx = _build_index()
    assert search(_DOCS, idx, "mat") == [1, 4]


def test_and_query_requires_all_terms() -> None:
    idx = _build_index()
    assert search(_DOCS, idx, "mat red") == [4]


def test_query_matches_whole_words_only() -> None:
    """ "cat" must not match a document that only contains "category" or "cats"."""
    idx = _build_index()
    assert search(_DOCS, idx, "cat") == [1]
