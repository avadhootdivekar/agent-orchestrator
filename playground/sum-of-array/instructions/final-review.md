# Final Reviewer — Sum of Array

## Task

Perform the final review of the deliverable and decide whether another round is needed.

## Inputs

- `output/bugfix.md` — the (possibly fixed) deliverable

## Output (two files)

### 1. Review report: `output/final-review.md`

Write a brief final verdict.

### 2. Gate verdict: `output/final-verdict.json`  (REQUIRED — do not omit)

Write EXACTLY the following JSON to `output/final-verdict.json` (or the iteration-suffixed
path provided by the engine):

```json
{ "continue": false }
```

Set `"continue": true` ONLY if a critical issue requires another bugfix+review iteration.
Set `"continue": false` to stop the loop and proceed to `done`.

## REQUIRED gate-file contract

The engine reads this file to decide whether to loop. The field name MUST be `"continue"` and
the value MUST be a boolean. Any other format will cause the loop to fail.

Example — stop the loop (happy path):
```json
{ "continue": false }
```

Example — request another round:
```json
{ "continue": true }
```
