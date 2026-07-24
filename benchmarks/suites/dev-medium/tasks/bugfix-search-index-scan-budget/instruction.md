# Fix: `test_query_matches_whole_words_only` fails

Running the test suite (`tests/test_search.py`), `test_query_matches_whole_words_only`
fails: a query for `"cat"` incorrectly also returns documents that only contain
`"category"` or `"cats"` — it should return only documents containing the whole word
`"cat"`.

`search/query.py`'s `search(documents, index, query, scan_counter=None)` is where the
bug lives: it checks whether each query term is a **substring** of each document's raw
text, instead of checking for a whole-word match. Fix it to match whole words only
(case-insensitive), using the already-built `index` (an `InvertedIndex`, see
`search/index.py`'s `postings(term)`, which already maps each whole word to the set of
document ids containing it) — `search` currently builds its own list of matches by
reading `document.text` directly for every document on every call and never uses
`index` at all. A correct, whole-word-matching query engine needs no per-document text
scan: it can answer a single-term query directly from `index.postings(term)`, and an
AND query (space-separated terms) by intersecting the postings for each term.

`scan_counter`, when passed in, is incremented once per document whose full text
`search` reads; an implementation that only consults `index` never touches it.

Fix `search/query.py` so that `tests/test_search.py` passes.

Do not modify `tests/test_search.py`, and do not modify anything under `.grading/` — it
is a held-out grading harness the run is scored against and is not part of what you are
asked to implement or test against directly.
