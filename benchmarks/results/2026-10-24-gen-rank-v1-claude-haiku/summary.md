# Bench summary — gen-rank-v1 × claude-haiku

- bench_run_id: `2026-10-24-gen-rank-v1-claude-haiku`
- domain: software
- subject: `claude-haiku` (type=claude_cli, model=claude-haiku-5-5)
- started_at: 2026-10-24T04:45:59.248810+00:00
- ended_at: 2026-10-24T04:59:17.715120+00:00

| Task | Category | Solved | Score | Wall(s) | Cost($) | Tokens(in/out) | Turns | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| crud-relational-py-s9511 | feature | yes | 1.00 | 319.12 | 0.1175 | 48/72920 | 62 | succeeded |
| crud-relational-py-s9512 | feature | yes | 0.85 | 506.88 | 0.2025 | 50/83413 | 60 | succeeded |
| crud-relational-py-s9513 | feature | yes | 1.00 | 548.60 | 0.2486 | 60/90089 | 77 | failed |
| job-queue-py-s9511 | feature | yes | 1.00 | 156.97 | 0.0335 | 22/36224 | 28 | succeeded |
| job-queue-py-s9512 | feature | yes | 1.00 | 161.85 | 0.0329 | 30/33395 | 37 | succeeded |
| job-queue-py-s9513 | feature | yes | 1.00 | 155.40 | 0.0336 | 26/35916 | 34 | succeeded |

## Aggregate

- solved: 6/6 (100.0%)
- total_cost: $0.6686
- cost_per_solved: 0.1114
- total_wall_clock: 1848.82s

## Environment isolation

- workspace_root: `/var/tmp/ao-bench/1000/ws`
- grading_root: `/var/tmp/ao-bench/1000/_ao_bench_grading`
- env_contaminated: no
- baseline fingerprint: `62181309c7b1`
- user config: `874197f82a89` {'model': 'opus[1m]', 'effortLevel': 'xhigh'}
