# Write tests for: clamp

`mathutils.py` defines `clamp(value, low, high)`, which restricts `value` to the
inclusive range `[low, high]` (returns `low` if `value < low`, `high` if
`value > high`, otherwise `value` unchanged). There are no tests for it yet.

Write tests in a new file `tests/test_mathutils.py` that exercise `clamp`'s actual
behavior: at minimum, a value below `low`, a value above `high`, and a value already
inside the range. A test file that doesn't really check `clamp`'s behavior (e.g. an
empty test, or one that would pass no matter what `clamp` does) does not satisfy this
task.

Do not modify `mathutils.py` or `check_tests.py`.
