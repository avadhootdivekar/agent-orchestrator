# Bench summary — gen-rank-v1 × claude-sonnet

- bench_run_id: `2026-10-24-gen-rank-v1-claude-sonnet`
- domain: software
- subject: `claude-sonnet` (type=claude_cli, model=claude-sonnet-5-5)
- started_at: 2026-10-24T04:59:19.062914+00:00
- ended_at: 2026-10-24T05:04:06.183515+00:00

| Task | Category | Solved | Score | Wall(s) | Cost($) | Tokens(in/out) | Turns | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| crud-relational-py-s9511 | feature | yes | 1.00 | 85.87 | 0.2529 | 10/12500 | 8 | succeeded |
| crud-relational-py-s9512 | feature | yes | 0.85 | 122.85 | 0.3174 | 16/16338 | 14 | succeeded |
| crud-relational-py-s9513 | feature | yes | 1.00 | 90.85 | 0.2726 | 10/13732 | 7 | succeeded |
| job-queue-py-s9511 | feature | yes | 1.00 | 48.99 | 0.1510 | 10/6707 | 8 | succeeded |
| job-queue-py-s9512 | feature | yes | 1.00 | 39.11 | 0.1326 | 8/5570 | 6 | succeeded |
| job-queue-py-s9513 | feature | yes | 1.00 | 51.95 | 0.1614 | 12/7197 | 10 | succeeded |

## Aggregate

- solved: 6/6 (100.0%)
- total_cost: $1.2880
- cost_per_solved: 0.2147
- total_wall_clock: 439.62s

## Environment isolation

- workspace_root: `/var/tmp/ao-bench/1000/ws`
- grading_root: `/var/tmp/ao-bench/1000/_ao_bench_grading`
- env_contaminated: no
- baseline fingerprint: `62181309c7b1`
- user config: `874197f82a89` {'model': 'opus[1m]', 'effortLevel': 'xhigh'}
