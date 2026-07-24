# Bench comparison — dev-medium

- generated_at: 2026-07-22T17:41:26.345336+00:00

## Per-subject

| Subject | Solved/Total | Solve Rate | Total Cost($) | Cost/Solved($) | Total Wall(s) |
| --- | --- | --- | --- | --- | --- |
| claude-sonnet | 6/6 | 100.0% | 1.9346 | 0.3224 | 314.13 |
| claude-opus | 6/6 | 100.0% | 3.2723 | 0.5454 | 491.45 |
| ao-epic-sonnet | 6/6 | 100.0% | 4.2324 | 0.7054 | 658.17 |
| ao-epic-plus-sonnet | 6/6 | 100.0% | 7.6140 | 1.2690 | 1340.34 |

## Per-task (solved / cost)

| Task | claude-sonnet | claude-opus | ao-epic-sonnet | ao-epic-plus-sonnet |
| --- | --- | --- | --- | --- |
| bugfix-cache-key-collision | yes / 0.2401 | yes / 0.3761 | yes / 0.6264 | yes / 1.1589 |
| bugfix-search-index-scan-budget | yes / 0.3130 | yes / 0.4090 | yes / 0.7265 | yes / 1.1023 |
| feature-plugin-priority-registry | yes / 0.2905 | yes / 0.4375 | yes / 0.6328 | yes / 1.1140 |
| feature-tracker-priority-filter | yes / 0.3009 | yes / 0.6087 | yes / 0.7470 | yes / 1.1316 |
| refactor-money-cents | yes / 0.4940 | yes / 0.8422 | yes / 0.7945 | yes / 1.7127 |
| test-write-validation-spec | yes / 0.2962 | yes / 0.5989 | yes / 0.7052 | yes / 1.3945 |

## Winners

- Highest solve rate: ao-epic-plus-sonnet (100.0%, tied with 3 others)
- Lowest total cost: claude-sonnet ($1.9346)
- Lowest cost per solved: claude-sonnet ($0.3224)
- Fastest total wall-clock: claude-sonnet (314.13s)
