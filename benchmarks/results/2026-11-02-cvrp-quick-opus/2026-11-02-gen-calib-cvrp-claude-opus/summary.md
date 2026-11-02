# Bench summary — gen-calib-cvrp × claude-opus

- bench_run_id: `2026-11-02-gen-calib-cvrp-claude-opus`
- domain: software
- subject: `claude-opus` (type=claude_cli, model=claude-opus-5-5)
- started_at: 2026-11-02T09:38:05.375456+00:00
- ended_at: 2026-11-02T09:58:45.435543+00:00

| Task | Category | Solved | Score | Wall(s) | Cost($) | Tokens(in/out) | Turns | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cvrp-ladder-py-s47000 | feature | yes | 0.95 | 1200.04 | — | —/— | 44 | timed_out |

## Aggregate

- solved: 1/1 (100.0%)
- total_cost: $0.0000 (partial: some tasks had no recorded cost)
- cost_per_solved: 0.0000
- total_wall_clock: 1200.04s

## Environment isolation

- workspace_root: `/var/tmp/ao-bench/1000/ws`
- grading_root: `/var/tmp/ao-bench/1000/_ao_bench_grading`
- env_contaminated: no
- baseline fingerprint: `66a6bdba277f`
- user config: `fa4bfaf5ac45` {'model': 'opus[1m]', 'effortLevel': 'xhigh'}
