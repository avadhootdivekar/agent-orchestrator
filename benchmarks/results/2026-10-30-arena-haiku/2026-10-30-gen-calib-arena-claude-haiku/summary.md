# Bench summary — gen-calib-arena × claude-haiku

- bench_run_id: `2026-10-30-gen-calib-arena-claude-haiku`
- domain: software
- subject: `claude-haiku` (type=claude_cli, model=claude-haiku-5-5)
- started_at: 2026-10-30T04:47:04.514279+00:00
- ended_at: 2026-10-30T06:09:51.756220+00:00

| Task | Category | Solved | Score | Wall(s) | Cost($) | Tokens(in/out) | Turns | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| game-arena-py-s43008 | feature | yes | 0.99 | 1142.65 | 0.2495 | 6/1177 | 113 | succeeded |
| game-arena-py-s43009 | feature | yes | 1.00 | 411.26 | 0.0726 | 4/1351 | 98 | succeeded |
| game-arena-py-s43010 | feature | yes | 1.00 | 344.44 | 0.0416 | 48/38782 | 59 | succeeded |

## Aggregate

- solved: 3/3 (100.0%)
- total_cost: $0.3637
- cost_per_solved: 0.1212
- total_wall_clock: 1898.36s

## Environment isolation

- workspace_root: `/var/tmp/ao-bench/1000/ws`
- grading_root: `/var/tmp/ao-bench/1000/_ao_bench_grading`
- env_contaminated: no
- baseline fingerprint: `66a6bdba277f`
- user config: `fa4bfaf5ac45` {'model': 'opus[1m]', 'effortLevel': 'xhigh'}
