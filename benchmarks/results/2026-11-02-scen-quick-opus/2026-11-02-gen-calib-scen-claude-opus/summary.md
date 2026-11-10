# Bench summary — gen-calib-scen × claude-opus

- bench_run_id: `2026-11-02-gen-calib-scen-claude-opus`
- domain: software
- subject: `claude-opus` (type=claude_cli, model=claude-opus-5-5)
- started_at: 2026-11-02T09:38:05.387027+00:00
- ended_at: 2026-11-02T09:59:37.724223+00:00

| Task | Category | Solved | Score | Wall(s) | Cost($) | Tokens(in/out) | Turns | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| game-scenarios-py-s43009 | feature | no | 0.47 | 1200.06 | — | —/— | 92 | timed_out |

## Aggregate

- solved: 0/1 (0.0%)
- total_cost: $0.0000 (partial: some tasks had no recorded cost)
- cost_per_solved: —
- total_wall_clock: 1200.06s

## Environment isolation

- workspace_root: `/var/tmp/ao-bench/1000/ws`
- grading_root: `/var/tmp/ao-bench/1000/_ao_bench_grading`
- env_contaminated: no
- baseline fingerprint: `66a6bdba277f`
- user config: `fa4bfaf5ac45` {'model': 'opus[1m]', 'effortLevel': 'xhigh'}
