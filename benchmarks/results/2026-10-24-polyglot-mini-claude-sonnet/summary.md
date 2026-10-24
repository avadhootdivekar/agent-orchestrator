# Bench summary — polyglot-mini × claude-sonnet

- bench_run_id: `2026-10-24-polyglot-mini-claude-sonnet`
- domain: software
- subject: `claude-sonnet` (type=claude_cli, model=claude-sonnet-5-5)
- started_at: 2026-10-24T07:05:06.879455+00:00
- ended_at: 2026-10-24T07:09:54.109745+00:00

| Task | Category | Solved | Score | Wall(s) | Cost($) | Tokens(in/out) | Turns | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ex-cpp-clock | feature | yes | 1.00 | 37.75 | 0.1056 | 12/3306 | 9 | succeeded |
| ex-cpp-diamond | feature | yes | 1.00 | 21.22 | 0.0759 | 8/1736 | 5 | succeeded |
| ex-cpp-linked-list | feature | yes | 1.00 | 28.39 | 0.0925 | 8/2680 | 6 | succeeded |
| ex-cpp-phone-number | feature | no | 0.00 | 42.32 | 0.1035 | 12/3316 | 9 | succeeded |
| ex-cpp-sublist | feature | yes | 1.00 | 26.67 | 0.0851 | 10/2394 | 7 | succeeded |
| ex-cpp-zebra-puzzle | feature | yes | 1.00 | 31.19 | 0.0912 | 10/2732 | 7 | succeeded |
| ex-go-bottle-song | feature | yes | 1.00 | 13.14 | 0.0649 | 8/1519 | 5 | succeeded |
| ex-go-connect | feature | yes | 1.00 | 25.23 | 0.0887 | 10/3184 | 8 | succeeded |
| ex-go-pig-latin | feature | yes | 1.00 | 18.97 | 0.0695 | 8/1757 | 5 | succeeded |
| ex-go-tree-building | feature | yes | 1.00 | 15.86 | 0.0670 | 8/1829 | 5 | succeeded |
| ex-go-trinary | feature | no | 0.00 | 27.74 | 0.0876 | 12/2890 | 10 | succeeded |
| ex-go-variable-length-quantity | feature | yes | 1.00 | 17.32 | 0.0733 | 8/2164 | 5 | succeeded |
| ex-javascript-beer-song | feature | yes | 1.00 | 42.07 | 0.0749 | 10/1919 | 7 | succeeded |
| ex-javascript-book-store | feature | yes | 1.00 | 31.97 | 0.0913 | 12/2869 | 9 | succeeded |
| ex-javascript-meetup | feature | yes | 1.00 | 23.24 | 0.0789 | 8/2291 | 5 | succeeded |
| ex-javascript-parallel-letter-frequency | feature | yes | 1.00 | 24.00 | 0.0764 | 8/1928 | 5 | succeeded |
| ex-javascript-pig-latin | feature | yes | 1.00 | 20.28 | 0.0700 | 8/1752 | 5 | succeeded |
| ex-python-affine-cipher | feature | yes | 1.00 | 27.36 | 0.0975 | 14/2957 | 12 | succeeded |
| ex-python-bowling | feature | yes | 1.00 | 20.60 | 0.0827 | 8/2741 | 5 | succeeded |
| ex-python-dominoes | feature | yes | 1.00 | 15.25 | 0.0641 | 8/1710 | 5 | succeeded |
| ex-python-grade-school | feature | yes | 1.00 | 15.75 | 0.0684 | 8/1880 | 5 | succeeded |
| ex-python-pov | feature | no | 0.95 | 17.31 | 0.0719 | 8/1965 | 5 | succeeded |
| ex-python-proverb | feature | yes | 1.00 | 11.94 | 0.0588 | 8/1200 | 5 | succeeded |
| ex-rust-nucleotide-codons | feature | yes | 1.00 | 29.89 | 0.0973 | 10/3613 | 7 | succeeded |
| ex-rust-ocr-numbers | feature | yes | 1.00 | 30.39 | 0.1076 | 16/3826 | 12 | succeeded |
| ex-rust-scale-generator | feature | no | 0.92 | 24.92 | 0.0929 | 10/3242 | 7 | succeeded |
| ex-rust-word-count | feature | yes | 1.00 | 16.05 | 0.0660 | 8/1648 | 5 | succeeded |
| ex-rust-wordy | feature | yes | 1.00 | 20.03 | 0.0783 | 8/2613 | 5 | succeeded |
| ex-rust-xorcism | feature | yes | 1.00 | 36.66 | 0.1204 | 12/4421 | 9 | succeeded |

## Aggregate

- solved: 25/29 (86.2%)
- total_cost: $2.4021
- cost_per_solved: 0.0961
- total_wall_clock: 713.54s

## Environment isolation

- workspace_root: `/var/tmp/ao-bench/1000/ws`
- grading_root: `/var/tmp/ao-bench/1000/_ao_bench_grading`
- env_contaminated: no
- baseline fingerprint: `62181309c7b1`
- user config: `fa4bfaf5ac45` {'model': 'opus[1m]', 'effortLevel': 'xhigh'}
