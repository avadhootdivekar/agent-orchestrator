# Developer — Sum of Array (Implementation)

## Task

Implement `sum_array(nums: list[int]) -> int` in Python and write it as a real `.py` source file.

## Inputs

- `output/design.md` — the design to follow

## Output

Write your implementation to `output/tasks/t1/solution.py` — a real Python source file (NOT a markdown file).

## Requirements

- Function must be named `sum_array` and accept `list[int]`, return `int`.
- Handle empty list (return 0).
- Handle negative integers correctly.
- No imports required.
- Keep it simple: an explicit loop or `return sum(nums)` renamed as `sum_array` is fine.

## Example

```python
def sum_array(nums: list[int]) -> int:
    total = 0
    for n in nums:
        total += n
    return total
```

## Steps

1. Create directory `output/tasks/t1/` if it doesn't exist.
2. Write the Python function directly to `output/tasks/t1/solution.py` (plain `.py` file, no markdown wrapper).
3. Verify the file exists and is valid Python by running: `python -c "import ast; ast.parse(open('output/tasks/t1/solution.py').read()); print('syntax OK')"`.
