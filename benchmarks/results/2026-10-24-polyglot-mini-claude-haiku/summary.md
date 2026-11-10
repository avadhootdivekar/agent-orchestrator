# Bench summary — polyglot-mini × claude-haiku

- bench_run_id: `2026-10-24-polyglot-mini-claude-haiku`
- domain: software
- subject: `claude-haiku` (type=claude_cli, model=claude-haiku-5-5)
- started_at: 2026-10-24T06:51:12.209271+00:00
- ended_at: 2026-10-24T07:05:06.597882+00:00

| Task | Category | Solved | Score | Wall(s) | Cost($) | Tokens(in/out) | Turns | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ex-cpp-clock | feature | yes | 1.00 | 78.29 | 0.0151 | 26/12279 | 28 | succeeded |
| ex-cpp-diamond | feature | no | 0.00 | 62.36 | 0.0129 | 22/10245 | 29 | succeeded |
| ex-cpp-linked-list | feature | yes | 1.00 | 80.66 | 0.0148 | 24/12111 | 29 | succeeded |
| ex-cpp-phone-number | feature | no | 0.00 | 77.29 | 0.0153 | 22/14122 | 25 | succeeded |
| ex-cpp-sublist | feature | no | 0.00 | 56.25 | 0.0125 | 20/10166 | 24 | succeeded |
| ex-cpp-zebra-puzzle | feature | yes | 1.00 | 60.87 | 0.0129 | 22/10299 | 24 | succeeded |
| ex-go-bottle-song | feature | yes | 1.00 | 28.99 | 0.0068 | 12/5111 | 12 | succeeded |
| ex-go-connect | feature | no | 0.20 | 64.64 | 0.0129 | 14/13169 | 15 | succeeded |
| ex-go-pig-latin | feature | yes | 1.00 | 48.95 | 0.0103 | 14/9153 | 16 | succeeded |
| ex-go-tree-building | feature | yes | 1.00 | 46.64 | 0.0108 | 16/9682 | 17 | succeeded |
| ex-go-trinary | feature | no | 0.00 | 36.20 | 0.0081 | 14/6498 | 17 | succeeded |
| ex-go-variable-length-quantity | feature | yes | 1.00 | 35.69 | 0.0088 | 14/7160 | 17 | succeeded |
| ex-javascript-beer-song | feature | yes | 1.00 | 63.97 | 0.0106 | 18/7276 | 22 | succeeded |
| ex-javascript-book-store | feature | yes | 1.00 | 456.93 | 0.0249 | 32/24189 | 39 | succeeded |
| ex-javascript-meetup | feature | yes | 1.00 | 70.61 | 0.0143 | 22/12755 | 22 | succeeded |
| ex-javascript-parallel-letter-frequency | feature | yes | 1.00 | 46.04 | 0.0095 | 16/7574 | 18 | succeeded |
| ex-javascript-pig-latin | feature | yes | 1.00 | 70.70 | 0.0140 | 22/12141 | 22 | succeeded |
| ex-python-affine-cipher | feature | yes | 1.00 | 36.81 | 0.0089 | 14/7552 | 18 | succeeded |
| ex-python-bowling | feature | yes | 1.00 | 103.12 | 0.0206 | 20/21727 | 23 | succeeded |
| ex-python-dominoes | feature | yes | 1.00 | 52.94 | 0.0119 | 20/10507 | 23 | succeeded |
| ex-python-grade-school | feature | no | 0.85 | 20.76 | 0.0057 | 10/4096 | 10 | succeeded |
| ex-python-pov | feature | yes | 1.00 | 52.13 | 0.0112 | 14/10690 | 16 | succeeded |
| ex-python-proverb | feature | yes | 1.00 | 21.71 | 0.0053 | 8/3995 | 9 | succeeded |
| ex-rust-nucleotide-codons | feature | yes | 1.00 | 82.36 | 0.0171 | 22/16408 | 23 | succeeded |
| ex-rust-ocr-numbers | feature | yes | 1.00 | 111.21 | 0.0233 | 24/23719 | 25 | succeeded |
| ex-rust-scale-generator | feature | no | 0.94 | 132.73 | 0.0370 | 18/28291 | 22 | succeeded |
| ex-rust-word-count | feature | yes | 1.00 | 31.93 | 0.0085 | 18/5675 | 18 | succeeded |
| ex-rust-wordy | feature | yes | 1.00 | 61.22 | 0.0139 | 18/12993 | 18 | succeeded |
| ex-rust-xorcism | feature | yes | 1.00 | 120.17 | 0.0262 | 32/24606 | 36 | succeeded |

## Aggregate

- solved: 22/29 (75.9%)
- total_cost: $0.4043
- cost_per_solved: 0.0184
- total_wall_clock: 2212.19s

## Environment isolation

- workspace_root: `/var/tmp/ao-bench/1000/ws`
- grading_root: `/var/tmp/ao-bench/1000/_ao_bench_grading`
- env_contaminated: no
- baseline fingerprint: `62181309c7b1`
- user config: `fa4bfaf5ac45` {'model': 'opus[1m]', 'effortLevel': 'xhigh'}
