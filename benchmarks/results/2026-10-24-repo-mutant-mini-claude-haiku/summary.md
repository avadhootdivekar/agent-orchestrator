# Bench summary — repo-mutant-mini × claude-haiku

- bench_run_id: `2026-10-24-repo-mutant-mini-claude-haiku`
- domain: software
- subject: `claude-haiku` (type=claude_cli, model=claude-haiku-5-5)
- started_at: 2026-10-24T04:41:47.560535+00:00
- ended_at: 2026-10-24T04:43:15.723809+00:00

| Task | Category | Solved | Score | Wall(s) | Cost($) | Tokens(in/out) | Turns | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| attrs-excise-ne-validator | feature | yes | 1.00 | 85.42 | 0.0156 | 32/9475 | 41 | succeeded |
| attrs-to-bool-shorthand | bugfix | yes | 1.00 | 56.22 | 0.0075 | 18/3841 | 22 | succeeded |
| click-range-open-bounds | bugfix | yes | 1.00 | 26.22 | 0.0060 | 14/2912 | 14 | succeeded |
| rich-rate-unit-boundary | bugfix | yes | 1.00 | 36.09 | 0.0075 | 14/3927 | 17 | succeeded |

## Aggregate

- solved: 4/4 (100.0%)
- total_cost: $0.0366
- cost_per_solved: 0.0092
- total_wall_clock: 203.95s

## Environment isolation

- workspace_root: `/var/tmp/ao-bench/1000/ws`
- grading_root: `/var/tmp/ao-bench/1000/_ao_bench_grading`
- env_contaminated: no
- baseline fingerprint: `62181309c7b1`
- user config: `874197f82a89` {'model': 'opus[1m]', 'effortLevel': 'xhigh'}
