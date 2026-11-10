# Bench summary — repo-mutant-mini × claude-opus

- bench_run_id: `2026-10-24-repo-mutant-mini-claude-opus`
- domain: software
- subject: `claude-opus` (type=claude_cli, model=claude-opus-5-5)
- started_at: 2026-10-24T04:43:15.747144+00:00
- ended_at: 2026-10-24T04:45:05.777651+00:00

| Task | Category | Solved | Score | Wall(s) | Cost($) | Tokens(in/out) | Turns | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| attrs-excise-ne-validator | feature | yes | 1.00 | 107.39 | 0.3590 | 26/6264 | 29 | succeeded |
| attrs-to-bool-shorthand | bugfix | yes | 1.00 | 17.39 | 0.1201 | 8/1071 | 5 | succeeded |
| click-range-open-bounds | bugfix | yes | 1.00 | 32.37 | 0.1675 | 14/2074 | 9 | succeeded |
| rich-rate-unit-boundary | bugfix | yes | 1.00 | 38.28 | 0.1532 | 12/1582 | 9 | succeeded |

## Aggregate

- solved: 4/4 (100.0%)
- total_cost: $0.7998
- cost_per_solved: 0.1999
- total_wall_clock: 195.44s

## Environment isolation

- workspace_root: `/var/tmp/ao-bench/1000/ws`
- grading_root: `/var/tmp/ao-bench/1000/_ao_bench_grading`
- env_contaminated: no
- baseline fingerprint: `62181309c7b1`
- user config: `874197f82a89` {'model': 'opus[1m]', 'effortLevel': 'xhigh'}
