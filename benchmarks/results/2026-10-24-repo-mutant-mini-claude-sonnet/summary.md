# Bench summary — repo-mutant-mini × claude-sonnet

- bench_run_id: `2026-10-24-repo-mutant-mini-claude-sonnet`
- domain: software
- subject: `claude-sonnet` (type=claude_cli, model=claude-sonnet-5-5)
- started_at: 2026-10-24T05:04:06.459494+00:00
- ended_at: 2026-10-24T05:05:06.332982+00:00

| Task | Category | Solved | Score | Wall(s) | Cost($) | Tokens(in/out) | Turns | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| attrs-excise-ne-validator | feature | yes | 1.00 | 57.29 | 0.1337 | 24/4117 | 17 | succeeded |
| attrs-to-bool-shorthand | bugfix | yes | 1.00 | 18.21 | 0.0714 | 12/1234 | 8 | succeeded |
| click-range-open-bounds | bugfix | yes | 1.00 | 26.48 | 0.0889 | 16/1765 | 11 | succeeded |
| rich-rate-unit-boundary | bugfix | yes | 1.00 | 19.41 | 0.0820 | 14/1579 | 10 | succeeded |

## Aggregate

- solved: 4/4 (100.0%)
- total_cost: $0.3760
- cost_per_solved: 0.0940
- total_wall_clock: 121.39s

## Environment isolation

- workspace_root: `/var/tmp/ao-bench/1000/ws`
- grading_root: `/var/tmp/ao-bench/1000/_ao_bench_grading`
- env_contaminated: no
- baseline fingerprint: `62181309c7b1`
- user config: `874197f82a89` {'model': 'opus[1m]', 'effortLevel': 'xhigh'}
