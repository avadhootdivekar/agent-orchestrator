# Bench comparison — swe-verified-mini

- generated_at: 2026-07-22T18:27:28.660937+00:00

## Per-subject

| Subject | Solved/Total | Solve Rate | Total Cost($) | Cost/Solved($) | Total Wall(s) |
| --- | --- | --- | --- | --- | --- |
| claude-sonnet | 9/10 | 90.0% | 7.5931 | 0.8437 | 1663.12 |
| claude-opus | 9/10 | 90.0% | 9.9118 | 1.1013 | 2280.73 |
| ao-epic-sonnet | 9/10 | 90.0% | 8.1571 | 0.9063 | 1917.07 |

## Per-task (solved / cost)

| Task | claude-sonnet | claude-opus | ao-epic-sonnet |
| --- | --- | --- | --- |
| django__django-11138 | yes / 1.2036 | yes / 1.4844 | no / 0.0934 |
| django__django-11292 | yes / 0.7887 | yes / 1.2719 | yes / 0.8530 |
| django__django-12304 | yes / 0.6117 | yes / 0.4611 | yes / 0.8435 |
| django__django-14007 | yes / 0.7572 | yes / 1.0969 | yes / 0.9598 |
| django__django-14053 | yes / 0.5528 | yes / 0.9108 | yes / 0.9717 |
| psf__requests-2931 | yes / 0.7457 | yes / 0.6570 | yes / 1.1335 |
| pytest-dev__pytest-10356 | no / 0.8846 | no / 1.1524 | yes / 0.9477 |
| pytest-dev__pytest-7571 | yes / 0.7531 | yes / 0.8046 | yes / 0.8138 |
| sphinx-doc__sphinx-10466 | yes / 0.8129 | yes / 1.1163 | yes / 0.7796 |
| sympy__sympy-20590 | yes / 0.4829 | yes / 0.9564 | yes / 0.7611 |

## Winners

- Highest solve rate: ao-epic-sonnet (90.0%, tied with 2 others)
- Lowest total cost: claude-sonnet ($7.5931)
- Lowest cost per solved: claude-sonnet ($0.8437)
- Fastest total wall-clock: claude-sonnet (1663.12s)
