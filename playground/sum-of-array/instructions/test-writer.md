# Test Writer — Sum of Array

## Task

Write pytest tests for the `sum_array` function in `output/tasks/t1/solution.py`, then run them and confirm they pass.

## Inputs

- `output/tasks/t1/solution.py` — the implementation to test

## Output

Write your tests to `output/tasks/t1/test_solution.py` — a real pytest file (NOT a markdown file).

## Test cases to cover

- `sum_array([]) == 0`
- `sum_array([1, 2, 3]) == 6`
- `sum_array([-1, 1]) == 0`
- `sum_array([5]) == 5`
- `sum_array([-3, -2]) == -5`

## Example test file

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from solution import sum_array

def test_empty():
    assert sum_array([]) == 0

def test_positives():
    assert sum_array([1, 2, 3]) == 6

def test_mixed():
    assert sum_array([-1, 1]) == 0

def test_single():
    assert sum_array([5]) == 5

def test_negatives():
    assert sum_array([-3, -2]) == -5
```

## Steps

1. Write the test file to `output/tasks/t1/test_solution.py` (plain `.py` file, no markdown wrapper).
2. Run the tests: `python -m pytest output/tasks/t1/test_solution.py -v`
3. All 5 tests must pass. If any fail, fix `output/tasks/t1/solution.py` until they do.
4. Report the pytest output in a brief note.
