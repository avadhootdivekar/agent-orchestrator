# Bench comparison — dev-core

- generated_at: 2026-07-22T11:30:07.814961+00:00

## Per-subject

| Subject | Solved/Total | Solve Rate | Total Cost($) | Cost/Solved($) | Total Wall(s) |
| --- | --- | --- | --- | --- | --- |
| claude-haiku | 6/6 | 100.0% | 0.3732 | 0.0622 | 166.37 |
| ao-epic-haiku | 6/6 | 100.0% | 0.6617 | 0.1103 | 343.05 |
| claude-sonnet | 6/6 | 100.0% | 1.2148 | 0.2025 | 122.28 |
| claude-opus | 6/6 | 100.0% | 1.5483 | 0.2580 | 129.97 |
| ao-epic-sonnet | 6/6 | 100.0% | 2.8744 | 0.4791 | 387.31 |

## Per-task (solved / cost)

| Task | claude-haiku | ao-epic-haiku | claude-sonnet | claude-opus | ao-epic-sonnet |
| --- | --- | --- | --- | --- | --- |
| bugfix-grade-boundary | yes / 0.0542 | yes / 0.1006 | yes / 0.1951 | yes / 0.2411 | yes / 0.4663 |
| bugfix-off-by-one | yes / 0.0552 | yes / 0.1065 | yes / 0.1994 | yes / 0.2367 | yes / 0.4516 |
| feature-count-vowels | yes / 0.0577 | yes / 0.1112 | yes / 0.1930 | yes / 0.2393 | yes / 0.4262 |
| feature-is-leap-year | yes / 0.0573 | yes / 0.1023 | yes / 0.1927 | yes / 0.2557 | yes / 0.4168 |
| refactor-extract-validation | yes / 0.0649 | yes / 0.1125 | yes / 0.2002 | yes / 0.2539 | yes / 0.5241 |
| test-write-clamp | yes / 0.0839 | yes / 0.1286 | yes / 0.2345 | yes / 0.3216 | yes / 0.5894 |

## Winners

- Highest solve rate: ao-epic-haiku (100.0%)
- Lowest total cost: claude-haiku ($0.3732)
- Lowest cost per solved: claude-haiku ($0.0622)
- Fastest total wall-clock: claude-sonnet (122.28s)
