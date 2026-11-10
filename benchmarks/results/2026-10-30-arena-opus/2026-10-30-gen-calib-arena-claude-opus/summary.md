# Bench summary — gen-calib-arena × claude-opus

- bench_run_id: `2026-10-30-gen-calib-arena-claude-opus`
- domain: software
- subject: `claude-opus` (type=claude_cli, model=claude-opus-5-5)
- started_at: 2026-10-30T07:34:41.392017+00:00
- ended_at: 2026-10-30T11:25:52.034183+00:00

| Task | Category | Solved | Score | Wall(s) | Cost($) | Tokens(in/out) | Turns | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| game-arena-py-s43008 | feature | yes | 1.00 | 3600.01 | 1.9878 | 4/50 | 54 | timed_out |
| game-arena-py-s43009 | feature | yes | 1.00 | 3600.03 | — | —/— | 99 | timed_out |
| game-arena-py-s43010 | feature | yes | 1.00 | 3600.03 | 3.2867 | 12/4268 | 80 | timed_out |

## Aggregate

- solved: 3/3 (100.0%)
- total_cost: $5.2744 (partial: some tasks had no recorded cost)
- cost_per_solved: 1.7581
- total_wall_clock: 10800.07s

## Environment isolation

- workspace_root: `/var/tmp/ao-bench/1000/ws`
- grading_root: `/var/tmp/ao-bench/1000/_ao_bench_grading`
- env_contaminated: no
- baseline fingerprint: `66a6bdba277f`
- user config: `fa4bfaf5ac45` {'model': 'opus[1m]', 'effortLevel': 'xhigh'}
