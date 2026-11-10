# Bench summary — gen-calib-arena × claude-sonnet

- bench_run_id: `2026-10-30-gen-calib-arena-claude-sonnet`
- domain: software
- subject: `claude-sonnet` (type=claude_cli, model=claude-sonnet-5-5)
- started_at: 2026-10-30T06:09:52.046241+00:00
- ended_at: 2026-10-30T07:34:41.085453+00:00

| Task | Category | Solved | Score | Wall(s) | Cost($) | Tokens(in/out) | Turns | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| game-arena-py-s43008 | feature | yes | 0.99 | 972.80 | 0.4089 | 34/20462 | 32 | succeeded |
| game-arena-py-s43009 | feature | yes | 1.00 | 397.21 | 0.2806 | 28/12983 | 27 | succeeded |
| game-arena-py-s43010 | feature | yes | 1.00 | 794.28 | 0.2852 | 4/99 | 24 | succeeded |

## Aggregate

- solved: 3/3 (100.0%)
- total_cost: $0.9747
- cost_per_solved: 0.3249
- total_wall_clock: 2164.29s

## Environment isolation

- workspace_root: `/var/tmp/ao-bench/1000/ws`
- grading_root: `/var/tmp/ao-bench/1000/_ao_bench_grading`
- env_contaminated: no
- baseline fingerprint: `66a6bdba277f`
- user config: `fa4bfaf5ac45` {'model': 'opus[1m]', 'effortLevel': 'xhigh'}
