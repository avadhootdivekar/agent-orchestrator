# Bench summary — gen-calib-scen × claude-haiku

- bench_run_id: `2026-11-02-gen-calib-scen-claude-haiku`
- domain: software
- subject: `claude-haiku` (type=claude_cli, model=claude-haiku-5-5)
- started_at: 2026-11-02T09:38:05.371823+00:00
- ended_at: 2026-11-02T09:50:49.361611+00:00

| Task | Category | Solved | Score | Wall(s) | Cost($) | Tokens(in/out) | Turns | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| game-scenarios-py-s43009 | feature | yes | 0.72 | 692.70 | 0.0556 | 62/49058 | 79 | succeeded |

## Aggregate

- solved: 1/1 (100.0%)
- total_cost: $0.0556
- cost_per_solved: 0.0556
- total_wall_clock: 692.70s

## Environment isolation

- workspace_root: `/var/tmp/ao-bench/1000/ws`
- grading_root: `/var/tmp/ao-bench/1000/_ao_bench_grading`
- env_contaminated: no
- baseline fingerprint: `66a6bdba277f`
- user config: `fa4bfaf5ac45` {'model': 'opus[1m]', 'effortLevel': 'xhigh'}
