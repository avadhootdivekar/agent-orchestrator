# Refactor: extract the duplicated validation

`shapes.py`'s `rectangle_area(w, h)` and `triangle_area(base, height)` both start with
the exact same validation check (raising `ValueError("dimensions must be non-negative")`
when either argument is negative). This duplication violates DRY.

Extract the shared check into a private helper function `_validate_non_negative(*values)`
in `shapes.py` (it should raise `ValueError("dimensions must be non-negative")` if any
value in `values` is negative) and call it from both `rectangle_area` and
`triangle_area` instead of repeating the check inline.

Behavior must not change: `tests/test_shapes.py` must keep passing exactly as-is (it
already passes on the unmodified file). Do not modify `tests/test_shapes.py` or
`check.py`.
