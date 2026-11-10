# Bench summary — repo-mutant-mini × ao-overseer-sonnet

- bench_run_id: `2026-10-24-repo-mutant-mini-ao-overseer-sonnet`
- domain: software
- subject: `ao-overseer-sonnet` (type=ao_workflow, model=claude-sonnet-5-5)
- started_at: 2026-10-24T05:47:35.617565+00:00
- ended_at: 2026-10-24T05:56:23.869785+00:00

| Task | Category | Solved | Score | Wall(s) | Cost($) | Tokens(in/out) | Turns | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| attrs-excise-ne-validator | feature | yes | 1.00 | 387.78 | 1.4866 | 132/41056 | — | succeeded |
| attrs-to-bool-shorthand | bugfix | yes | 1.00 | 327.13 | 1.3360 | 120/32335 | — | succeeded |
| click-range-open-bounds | bugfix | yes | 1.00 | 258.22 | 1.1965 | 100/29679 | — | succeeded |
| rich-rate-unit-boundary | bugfix | yes | 1.00 | 266.97 | 1.2446 | 98/30105 | — | succeeded |

## Aggregate

- solved: 4/4 (100.0%)
- total_cost: $5.2638
- cost_per_solved: 1.3159
- total_wall_clock: 1240.11s

## Environment isolation

- workspace_root: `/var/tmp/ao-bench/1000/ws`
- grading_root: `/var/tmp/ao-bench/1000/_ao_bench_grading`
- env_contaminated: no
- baseline fingerprint: `62181309c7b1`
- user config: `874197f82a89` {'model': 'opus[1m]', 'effortLevel': 'xhigh'}
