# Bench summary — gen-rank-v1 × ao-overseer-sonnet

- bench_run_id: `2026-10-24-gen-rank-v1-ao-overseer-sonnet`
- domain: software
- subject: `ao-overseer-sonnet` (type=ao_workflow, model=claude-sonnet-5-5)
- started_at: 2026-10-24T05:14:46.277301+00:00
- ended_at: 2026-10-24T05:46:13.818278+00:00

| Task | Category | Solved | Score | Wall(s) | Cost($) | Tokens(in/out) | Turns | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| crud-relational-py-s9511 | feature | yes | 1.00 | 578.77 | 2.0304 | 108/80020 | — | failed |
| crud-relational-py-s9512 | feature | yes | 0.85 | 618.25 | 2.3000 | 102/90173 | — | failed |
| crud-relational-py-s9513 | feature | yes | 1.00 | 605.58 | 2.1518 | 102/86115 | — | failed |
| job-queue-py-s9511 | feature | yes | 1.00 | 1191.73 | 2.1217 | 128/60381 | — | failed |
| job-queue-py-s9512 | feature | yes | 1.00 | 881.77 | 1.9400 | 126/63139 | — | succeeded |
| job-queue-py-s9513 | feature | yes | 1.00 | 483.29 | 2.1229 | 140/67826 | — | failed |

## Aggregate

- solved: 6/6 (100.0%)
- total_cost: $12.6668
- cost_per_solved: 2.1111
- total_wall_clock: 4359.38s

## Environment isolation

- workspace_root: `/var/tmp/ao-bench/1000/ws`
- grading_root: `/var/tmp/ao-bench/1000/_ao_bench_grading`
- env_contaminated: no
- baseline fingerprint: `62181309c7b1`
- user config: `874197f82a89` {'model': 'opus[1m]', 'effortLevel': 'xhigh'}
