# Architect Breakdown — Sum of Array

## Task

Break down the approved design into implementation tasks and write a task manifest.

## Inputs

- `output/design.md` — the design document
- `output/design-review.md` — the design review

## Output

Write a task manifest to `output/tasks-manifest.json`.

## REQUIRED manifest contract (exact shape — do not deviate)

The real agent MUST write EXACTLY the following JSON to `output/tasks-manifest.json`.
The deterministic tier pre-seeds this file; the real-LLM tier must produce the same shape.

```json
{
  "tasks": [
    {
      "id": "impl-t1",
      "agent": "developer",
      "instruction": "instructions/developer.md",
      "depends_on": ["architect-breakdown"],
      "inputs": ["output/design.md"],
      "outputs": ["output/tasks/t1/impl.md"]
    },
    {
      "id": "testwrite-t1",
      "agent": "test-writer",
      "instruction": "instructions/test-writer.md",
      "depends_on": ["impl-t1"],
      "inputs": ["output/tasks/t1/impl.md"],
      "outputs": ["output/tasks/t1/tests.md"]
    },
    {
      "id": "taskreview-t1",
      "agent": "reviewer",
      "instruction": "instructions/reviewer.md",
      "depends_on": ["testwrite-t1"],
      "inputs": ["output/tasks/t1/impl.md", "output/tasks/t1/tests.md"],
      "outputs": ["output/tasks/t1/review.md"]
    }
  ]
}
```

Key constraints:
- Task ids MUST be exactly `impl-t1`, `testwrite-t1`, `taskreview-t1`.
- Output paths MUST be exactly `output/tasks/t1/impl.md`, `output/tasks/t1/tests.md`,
  `output/tasks/t1/review.md` (unique paths; no other paths).
- `depends_on` MUST be as shown (chain: architect-breakdown → impl-t1 → testwrite-t1 → taskreview-t1).
- No other tasks should be emitted.
