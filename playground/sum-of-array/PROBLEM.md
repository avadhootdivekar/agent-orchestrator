# Problem: Sum of Array

## Statement

Implement the function:

```python
def sum(nums: list[int]) -> int:
    ...
```

Returns the sum of all integers in `nums`. Returns 0 for an empty list.

## Constraints

- Input: a list of integers (may be empty, may contain negatives).
- Output: a single integer.
- No external libraries required.

## Examples

| Input | Output |
|-------|--------|
| `[]` | `0` |
| `[1, 2, 3]` | `6` |
| `[-1, 1]` | `0` |
| `[5]` | `5` |

## Note

This problem is intentionally trivial and low-burn. It exists to exercise
the AO orchestration pipeline (design → implement → review → integrate),
not to challenge the implementing agent.
