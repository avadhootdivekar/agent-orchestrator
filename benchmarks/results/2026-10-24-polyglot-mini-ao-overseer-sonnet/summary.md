# Bench summary — polyglot-mini × ao-overseer-sonnet

- bench_run_id: `2026-10-24-polyglot-mini-ao-overseer-sonnet`
- domain: software
- subject: `ao-overseer-sonnet` (type=ao_workflow, model=claude-sonnet-5-5)
- started_at: 2026-10-24T12:48:38.805020+00:00
- ended_at: 2026-10-24T13:38:58.179799+00:00

| Task | Category | Solved | Score | Wall(s) | Cost($) | Tokens(in/out) | Turns | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ex-cpp-clock | feature | yes | 1.00 | 397.06 | 1.5327 | 118/40746 | — | succeeded |
| ex-cpp-diamond | feature | yes | 1.00 | 324.95 | 1.3760 | 92/36126 | — | succeeded |
| ex-cpp-linked-list | feature | yes | 1.00 | 366.75 | 1.4277 | 108/38656 | — | succeeded |
| ex-cpp-phone-number | feature | yes | 1.00 | 364.82 | 1.7386 | 122/44152 | — | succeeded |
| ex-cpp-sublist | feature | yes | 1.00 | 317.56 | 1.4066 | 104/36588 | — | succeeded |
| ex-cpp-zebra-puzzle | feature | yes | 1.00 | 325.90 | 1.4658 | 100/38536 | — | succeeded |
| ex-go-bottle-song | feature | yes | 1.00 | 248.87 | 1.2849 | 90/31160 | — | succeeded |
| ex-go-connect | feature | yes | 1.00 | 338.45 | 1.6661 | 116/43867 | — | succeeded |
| ex-go-pig-latin | feature | yes | 1.00 | 282.03 | 1.3209 | 100/31380 | — | succeeded |
| ex-go-tree-building | feature | yes | 1.00 | 275.49 | 1.3031 | 98/33306 | — | succeeded |
| ex-go-trinary | feature | no | 0.00 | 275.76 | 1.3273 | 96/33338 | — | succeeded |
| ex-go-variable-length-quantity | feature | yes | 1.00 | 262.72 | 1.3718 | 104/35227 | — | succeeded |
| ex-javascript-beer-song | feature | yes | 1.00 | 286.98 | 1.3557 | 96/31660 | — | succeeded |
| ex-javascript-book-store | feature | yes | 1.00 | 486.27 | 1.2688 | 102/33019 | — | succeeded |
| ex-javascript-meetup | feature | yes | 1.00 | 307.58 | 1.3894 | 92/36641 | — | succeeded |
| ex-javascript-parallel-letter-frequency | feature | yes | 1.00 | 266.63 | 1.3488 | 102/33512 | — | succeeded |
| ex-javascript-pig-latin | feature | yes | 1.00 | 279.08 | 1.2628 | 86/31111 | — | succeeded |
| ex-python-affine-cipher | feature | yes | 1.00 | 266.90 | 1.3015 | 92/34502 | — | succeeded |
| ex-python-bowling | feature | yes | 1.00 | 278.06 | 1.3399 | 94/35677 | — | succeeded |
| ex-python-dominoes | feature | no | 0.54 | 285.47 | 1.2489 | 100/29283 | — | succeeded |
| ex-python-grade-school | feature | yes | 1.00 | 261.48 | 1.4759 | 116/32817 | — | succeeded |
| ex-python-pov | feature | no | 0.97 | 285.99 | 1.3889 | 98/36943 | — | succeeded |
| ex-python-proverb | feature | yes | 1.00 | 216.96 | 1.2121 | 88/27965 | — | succeeded |
| ex-rust-nucleotide-codons | feature | yes | 1.00 | 352.86 | 1.6738 | 124/44924 | — | succeeded |
| ex-rust-ocr-numbers | feature | yes | 1.00 | 279.11 | 1.3907 | 106/35708 | — | succeeded |
| ex-rust-scale-generator | feature | no | 0.94 | 295.50 | 1.4706 | 102/40138 | — | succeeded |
| ex-rust-word-count | feature | yes | 1.00 | 252.11 | 1.3024 | 94/31676 | — | succeeded |
| ex-rust-wordy | feature | yes | 1.00 | 272.35 | 1.4013 | 94/36838 | — | succeeded |
| ex-rust-xorcism | feature | yes | 1.00 | 308.22 | 1.4966 | 100/40706 | — | succeeded |

## Aggregate

- solved: 25/29 (86.2%)
- total_cost: $40.5496
- cost_per_solved: 1.6220
- total_wall_clock: 8761.94s

## Environment isolation

- workspace_root: `/var/tmp/ao-bench/1000/ws`
- grading_root: `/var/tmp/ao-bench/1000/_ao_bench_grading`
- env_contaminated: no
- baseline fingerprint: `62181309c7b1`
- user config: `fa4bfaf5ac45` {'model': 'opus[1m]', 'effortLevel': 'xhigh'}
