# Fix: `test_store_evicts_least_recently_used` fails

Running the test suite (`tests/test_cache.py`), `test_store_evicts_least_recently_used`
fails: after inserting 4 distinct users into a capacity-3 `LRUCache` and touching three
of them again, the one user that was never touched again after its initial insert is
expected to be evicted -- but it isn't.

`cache/store.py`'s `LRUCache` implements the eviction policy itself (evict the
least-recently-used entry once size exceeds `capacity`); look there first, but do not
assume the bug is necessarily in the eviction logic itself -- trace through what keys
actually end up stored before concluding where the fix belongs. Whichever module turns
out to be at fault, fix it so `tests/test_cache.py` passes.

Do not modify `tests/test_cache.py`, and do not modify anything under `.grading/` — it
is a held-out grading harness the run is scored against and is not part of what you are
asked to implement or test against directly.
