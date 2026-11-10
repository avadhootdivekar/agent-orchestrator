# Bench summary — polyglot-mini × claude-opus

- bench_run_id: `2026-10-24-polyglot-mini-claude-opus`
- domain: software
- subject: `claude-opus` (type=claude_cli, model=claude-opus-5-5)
- started_at: 2026-10-24T07:09:54.401262+00:00
- ended_at: 2026-10-24T07:18:32.325511+00:00

| Task | Category | Solved | Score | Wall(s) | Cost($) | Tokens(in/out) | Turns | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ex-cpp-clock | feature | yes | 1.00 | 77.99 | 0.3295 | 16/6928 | 15 | succeeded |
| ex-cpp-diamond | feature | yes | 1.00 | 52.84 | 0.2020 | 10/3211 | 8 | succeeded |
| ex-cpp-linked-list | feature | yes | 1.00 | 102.15 | 0.3066 | 12/6626 | 11 | succeeded |
| ex-cpp-phone-number | feature | yes | 1.00 | 62.96 | 0.3062 | 18/6238 | 19 | succeeded |
| ex-cpp-sublist | feature | yes | 1.00 | 52.55 | 0.2236 | 12/4050 | 12 | succeeded |
| ex-cpp-zebra-puzzle | feature | yes | 1.00 | 36.96 | 0.1942 | 10/3082 | 8 | succeeded |
| ex-go-bottle-song | feature | yes | 1.00 | 25.27 | 0.1410 | 8/1933 | 5 | succeeded |
| ex-go-connect | feature | yes | 1.00 | 46.14 | 0.2270 | 10/4887 | 8 | succeeded |
| ex-go-pig-latin | feature | yes | 1.00 | 44.27 | 0.1564 | 10/2187 | 6 | succeeded |
| ex-go-tree-building | feature | yes | 1.00 | 35.56 | 0.1685 | 10/2805 | 6 | succeeded |
| ex-go-trinary | feature | yes | 1.00 | 27.78 | 0.1642 | 10/2715 | 8 | succeeded |
| ex-go-variable-length-quantity | feature | yes | 1.00 | 37.76 | 0.1611 | 8/2673 | 5 | succeeded |
| ex-javascript-beer-song | feature | yes | 1.00 | 55.16 | 0.2178 | 10/2966 | 9 | succeeded |
| ex-javascript-book-store | feature | yes | 1.00 | 51.31 | 0.2106 | 12/3696 | 9 | succeeded |
| ex-javascript-meetup | feature | yes | 1.00 | 32.64 | 0.1679 | 8/2615 | 5 | succeeded |
| ex-javascript-parallel-letter-frequency | feature | yes | 1.00 | 39.03 | 0.2156 | 10/3213 | 8 | succeeded |
| ex-javascript-pig-latin | feature | yes | 1.00 | 39.50 | 0.1524 | 8/2066 | 5 | succeeded |
| ex-python-affine-cipher | feature | yes | 1.00 | 30.44 | 0.1572 | 8/2301 | 5 | succeeded |
| ex-python-bowling | feature | yes | 1.00 | 36.88 | 0.1893 | 12/3194 | 8 | succeeded |
| ex-python-dominoes | feature | yes | 1.00 | 27.30 | 0.1543 | 10/2400 | 7 | succeeded |
| ex-python-grade-school | feature | yes | 1.00 | 20.87 | 0.1400 | 8/1961 | 6 | succeeded |
| ex-python-pov | feature | yes | 1.00 | 31.45 | 0.1911 | 8/3655 | 5 | succeeded |
| ex-python-proverb | feature | yes | 1.00 | 21.17 | 0.1367 | 8/1874 | 5 | succeeded |
| ex-rust-nucleotide-codons | feature | yes | 1.00 | 54.14 | 0.2719 | 12/6213 | 8 | succeeded |
| ex-rust-ocr-numbers | feature | yes | 1.00 | 31.79 | 0.1693 | 8/2962 | 6 | succeeded |
| ex-rust-scale-generator | feature | yes | 1.00 | 49.89 | 0.2283 | 8/4928 | 5 | succeeded |
| ex-rust-word-count | feature | yes | 1.00 | 23.21 | 0.1575 | 8/2475 | 5 | succeeded |
| ex-rust-wordy | feature | yes | 1.00 | 35.19 | 0.1848 | 8/3556 | 5 | succeeded |
| ex-rust-xorcism | feature | yes | 1.00 | 90.28 | 0.3781 | 10/9869 | 9 | succeeded |

## Aggregate

- solved: 29/29 (100.0%)
- total_cost: $5.9030
- cost_per_solved: 0.2036
- total_wall_clock: 1272.47s

## Environment isolation

- workspace_root: `/var/tmp/ao-bench/1000/ws`
- grading_root: `/var/tmp/ao-bench/1000/_ao_bench_grading`
- env_contaminated: no
- baseline fingerprint: `62181309c7b1`
- user config: `fa4bfaf5ac45` {'model': 'opus[1m]', 'effortLevel': 'xhigh'}
