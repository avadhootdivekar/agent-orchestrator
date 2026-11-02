# Bench summary — gen-calib-cvrp × claude-sonnet

- bench_run_id: `2026-11-02-gen-calib-cvrp-claude-sonnet`
- domain: software
- subject: `claude-sonnet` (type=claude_cli, model=claude-sonnet-5-5)
- started_at: 2026-11-02T09:38:05.375151+00:00
- ended_at: 2026-11-02T09:43:15.349471+00:00

| Task | Category | Solved | Score | Wall(s) | Cost($) | Tokens(in/out) | Turns | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cvrp-ladder-py-s47000 | feature | yes | 1.00 | 272.48 | 0.2511 | 18/13327 | 16 | succeeded |

## Aggregate

- solved: 1/1 (100.0%)
- total_cost: $0.2511
- cost_per_solved: 0.2511
- total_wall_clock: 272.48s

## Environment isolation

- workspace_root: `/var/tmp/ao-bench/1000/ws`
- grading_root: `/var/tmp/ao-bench/1000/_ao_bench_grading`
- env_contaminated: no
- baseline fingerprint: `66a6bdba277f`
- user config: `fa4bfaf5ac45` {'model': 'opus[1m]', 'effortLevel': 'xhigh'}
