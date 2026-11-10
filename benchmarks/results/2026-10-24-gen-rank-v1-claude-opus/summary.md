# Bench summary — gen-rank-v1 × claude-opus

- bench_run_id: `2026-10-24-gen-rank-v1-claude-opus`
- domain: software
- subject: `claude-opus` (type=claude_cli, model=claude-opus-5-5)
- started_at: 2026-10-24T05:05:06.621957+00:00
- ended_at: 2026-10-24T05:14:45.074209+00:00

| Task | Category | Solved | Score | Wall(s) | Cost($) | Tokens(in/out) | Turns | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| crud-relational-py-s9511 | feature | yes | 1.00 | 328.08 | 0.8906 | 26/23153 | 31 | succeeded |
| crud-relational-py-s9512 | feature | yes | 0.85 | 499.40 | 0.9383 | 26/25095 | 32 | succeeded |
| crud-relational-py-s9513 | feature | yes | 0.85 | 168.81 | 0.6858 | 14/18436 | 12 | succeeded |
| job-queue-py-s9511 | feature | yes | 1.00 | 85.06 | 0.3833 | 14/8728 | 14 | succeeded |
| job-queue-py-s9512 | feature | yes | 1.00 | 90.56 | 0.4035 | 14/9288 | 14 | succeeded |
| job-queue-py-s9513 | feature | yes | 1.00 | 100.81 | 0.4463 | 16/10912 | 16 | succeeded |

## Aggregate

- solved: 6/6 (100.0%)
- total_cost: $3.7478
- cost_per_solved: 0.6246
- total_wall_clock: 1272.72s

## Environment isolation

- workspace_root: `/var/tmp/ao-bench/1000/ws`
- grading_root: `/var/tmp/ao-bench/1000/_ao_bench_grading`
- env_contaminated: no
- baseline fingerprint: `62181309c7b1`
- user config: `874197f82a89` {'model': 'opus[1m]', 'effortLevel': 'xhigh'}
