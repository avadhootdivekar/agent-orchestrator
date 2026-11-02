# Bench summary — gen-calib-scen × claude-sonnet

- bench_run_id: `2026-11-02-gen-calib-scen-claude-sonnet`
- domain: software
- subject: `claude-sonnet` (type=claude_cli, model=claude-sonnet-5-5)
- started_at: 2026-11-02T09:38:05.375590+00:00
- ended_at: 2026-11-02T09:41:31.532939+00:00

| Task | Category | Solved | Score | Wall(s) | Cost($) | Tokens(in/out) | Turns | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| game-scenarios-py-s43009 | feature | yes | 0.64 | 123.86 | 0.2489 | 12/13607 | 10 | succeeded |

## Aggregate

- solved: 1/1 (100.0%)
- total_cost: $0.2489
- cost_per_solved: 0.2489
- total_wall_clock: 123.86s

## Environment isolation

- workspace_root: `/var/tmp/ao-bench/1000/ws`
- grading_root: `/var/tmp/ao-bench/1000/_ao_bench_grading`
- env_contaminated: no
- baseline fingerprint: `66a6bdba277f`
- user config: `fa4bfaf5ac45` {'model': 'opus[1m]', 'effortLevel': 'xhigh'}
