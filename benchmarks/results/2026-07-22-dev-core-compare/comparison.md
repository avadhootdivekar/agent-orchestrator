# Bench comparison — dev-core

- generated_at: 2026-07-22T11:07:17.951958+00:00

## Per-subject

| Subject | Solved/Total | Solve Rate | Total Cost($) | Cost/Solved($) | Total Wall(s) |
| --- | --- | --- | --- | --- | --- |
| ao-epic-haiku | 6/6 | 100.0% | 0.6617 | 0.1103 | 343.05 |
| claude-haiku | 6/6 | 100.0% | 0.3732 | 0.0622 | 166.37 |
| fake-pass | 6/6 | 100.0% | 0.0000 | 0.0000 | 0.00 |

## Per-task (solved / cost)

| Task | ao-epic-haiku | claude-haiku | fake-pass |
| --- | --- | --- | --- |
| bugfix-grade-boundary | yes / 0.1006 | yes / 0.0542 | yes / 0.0000 |
| bugfix-off-by-one | yes / 0.1065 | yes / 0.0552 | yes / 0.0000 |
| feature-count-vowels | yes / 0.1112 | yes / 0.0577 | yes / 0.0000 |
| feature-is-leap-year | yes / 0.1023 | yes / 0.0573 | yes / 0.0000 |
| refactor-extract-validation | yes / 0.1125 | yes / 0.0649 | yes / 0.0000 |
| test-write-clamp | yes / 0.1286 | yes / 0.0839 | yes / 0.0000 |

## Winners

- Highest solve rate: ao-epic-haiku (100.0%)
- Lowest total cost: fake-pass ($0.0000)
- Lowest cost per solved: fake-pass ($0.0000)
- Fastest total wall-clock: fake-pass (0.00s)
