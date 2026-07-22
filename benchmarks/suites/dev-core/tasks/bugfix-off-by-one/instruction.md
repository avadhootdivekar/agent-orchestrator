# Fix: running totals drop the last item

`stats.py`'s `running_total(nums)` should return the list of cumulative sums of
`nums`, one entry per input item (e.g. `running_total([1, 2, 3, 4])` must return
`[1, 3, 6, 10]`).

Right now the last item of `nums` is silently dropped from the result because of an
off-by-one error in the loop bound.

Fix `running_total` in `stats.py` so that `tests/test_stats.py` passes. Do not modify
`tests/test_stats.py`.
