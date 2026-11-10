# Bench summary — gen-calib-cvrp × claude-haiku

- bench_run_id: `2026-11-02-gen-calib-cvrp-claude-haiku`
- domain: software
- subject: `claude-haiku` (type=claude_cli, model=claude-haiku-5-5)
- started_at: 2026-11-02T09:38:05.372900+00:00
- ended_at: 2026-11-02T09:45:59.787969+00:00

| Task | Category | Solved | Score | Wall(s) | Cost($) | Tokens(in/out) | Turns | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cvrp-ladder-py-s47000 | feature | yes | 1.00 | 436.76 | 0.0332 | 32/34080 | 42 | succeeded |

## Aggregate

- solved: 1/1 (100.0%)
- total_cost: $0.0332
- cost_per_solved: 0.0332
- total_wall_clock: 436.76s

## Environment isolation

- workspace_root: `/var/tmp/ao-bench/1000/ws`
- grading_root: `/var/tmp/ao-bench/1000/_ao_bench_grading`
- env_contaminated: no
- baseline fingerprint: `66a6bdba277f`
- user config: `fa4bfaf5ac45` {'model': 'opus[1m]', 'effortLevel': 'xhigh'}
