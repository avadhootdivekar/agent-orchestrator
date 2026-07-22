# Feature: priority filtering and sorting for the task tracker

This package (`tracker/`) is a tiny in-memory task tracker with an argv-style command
parser (`tracker/cli.py`), a filter/sort layer (`tracker/filters.py`), and the data
store itself (`tracker/store.py`). `--status` filtering already works end to end.
`--priority` does not, on either side of the CLI/store boundary:

1. **`tracker/cli.py`'s `parse_args`** must accept `--priority=<value>` on BOTH the
   `add` command (e.g. `["add", "Urgent fix", "--priority=high"]`) and the `list`
   command (e.g. `["list", "--priority=high"]`), and a `--sort=priority` option on
   `list`. The raw string value must be converted into a real
   `tracker.store.Priority` enum member (`Priority(value)`) — not left as a plain
   string — so it agrees with what `tracker/store.py` and `tracker/filters.py`
   already expect (`Priority` is deliberately not a `str` subclass: a bare string
   value is not equal to the corresponding enum member). An unrecognized priority
   value (e.g. `"urgent"`) must raise `ValueError` (constructing `Priority(value)`
   already does this for you).

2. **`tracker/filters.py`'s `apply_filters`** must actually filter by `priority` when
   given one (currently a no-op), and must sort by priority (highest first, ties
   broken by the task's existing order — i.e. `id` ascending) when `sort ==
   "priority"` (currently ignored). `tracker/store.py`'s `PRIORITY_SORT_WEIGHT` gives
   you the priority ordering to sort by.

Fix both so that `tests/test_tracker.py` passes.

Do not modify `tests/test_tracker.py`, and do not modify anything under `.grading/` —
it is a held-out grading harness the run is scored against and is not part of what you
are asked to implement or test against directly.
