# Fix: grade boundaries are exclusive when they should be inclusive

`grades.py`'s `letter_grade(score)` maps a 0-100 score to a letter grade using the
standard inclusive thresholds:

- `score >= 90` -> `"A"`
- `score >= 80` -> `"B"`
- `score >= 70` -> `"C"`
- `score >= 60` -> `"D"`
- otherwise -> `"F"`

Right now the comparisons use `>` instead of `>=`, so a score exactly on a boundary
(e.g. `80`) is scored one letter grade too low.

Fix `letter_grade` in `grades.py` so that `tests/test_grades.py` passes. Do not modify
`tests/test_grades.py`.
