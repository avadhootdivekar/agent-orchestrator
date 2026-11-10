# Bench comparison — polyglot-b

- generated_at: 2026-10-24T14:49:20.783964+00:00

## Per-subject

| Subject | Solved/Total | Solve Rate | Total Cost($) | Cost/Solved($) | Total Wall(s) |
| --- | --- | --- | --- | --- | --- |
| claude-haiku | 28/48 | 58.3% | 0.7602 | 0.0271 | 3392.07 |
| claude-opus | 46/48 | 95.8% | 10.7745 | 0.2342 | 2696.33 |
| claude-sonnet | 37/48 | 77.1% | 4.1423 | 0.1120 | 1236.99 |

## Per-task (solved / cost)

| Task | claude-haiku | claude-opus | claude-sonnet |
| --- | --- | --- | --- |
| ex-cpp-all-your-base | no / 0.0286 | yes / 0.3142 | no / 0.1000 |
| ex-cpp-allergies | yes / 0.0120 | yes / 0.2072 | yes / 0.0848 |
| ex-cpp-binary-search-tree | no / 0.0286 | yes / 0.3328 | yes / 0.1216 |
| ex-cpp-complex-numbers | yes / 0.0297 | yes / 0.3936 | yes / 0.1043 |
| ex-cpp-crypto-square | no / 0.0148 | yes / 0.2755 | yes / 0.0964 |
| ex-cpp-dnd-character | yes / 0.0129 | yes / 0.2002 | yes / 0.0935 |
| ex-cpp-grade-school | yes / 0.0103 | yes / 0.3205 | yes / 0.0895 |
| ex-cpp-knapsack | yes / 0.0118 | yes / 0.2334 | yes / 0.0816 |
| ex-cpp-queen-attack | yes / 0.0290 | yes / 0.2046 | yes / 0.0860 |
| ex-cpp-robot-name | no / 0.0176 | yes / 0.3310 | no / 0.0967 |
| ex-go-beer-song | no / 0.0089 | yes / 0.1874 | no / 0.0910 |
| ex-go-food-chain | no / 0.0137 | yes / 0.1652 | yes / 0.0687 |
| ex-go-forth | no / 0.0146 | yes / 0.3472 | no / 0.0898 |
| ex-go-kindergarten-garden | no / 0.0161 | yes / 0.2027 | yes / 0.0748 |
| ex-go-pov | yes / 0.0115 | yes / 0.2243 | yes / 0.0833 |
| ex-go-react | yes / 0.0128 | yes / 0.2499 | yes / 0.0804 |
| ex-go-robot-simulator | no / 0.0323 | no / 0.4475 | no / 0.2072 |
| ex-go-say | yes / 0.0052 | yes / 0.1493 | yes / 0.0669 |
| ex-javascript-forth | no / 0.0161 | no / 0.2335 | yes / 0.0808 |
| ex-javascript-list-ops | yes / 0.0195 | yes / 0.1919 | yes / 0.0843 |
| ex-javascript-phone-number | no / 0.0092 | yes / 0.1538 | no / 0.0689 |
| ex-javascript-queen-attack | yes / 0.0403 | yes / 0.1913 | no / 0.0788 |
| ex-javascript-resistor-color-trio | no / 0.0076 | yes / 0.2482 | no / 0.0796 |
| ex-javascript-simple-linked-list | no / 0.0221 | yes / 0.1795 | yes / 0.0692 |
| ex-javascript-transpose | no / 0.0071 | yes / 0.1538 | yes / 0.0634 |
| ex-javascript-two-bucket | yes / 0.0365 | yes / 0.1727 | no / 0.0788 |
| ex-javascript-wordy | no / 0.0124 | yes / 0.1780 | yes / 0.0941 |
| ex-javascript-zebra-puzzle | yes / 0.0097 | yes / 0.1490 | yes / 0.0704 |
| ex-python-bottle-song | yes / 0.0074 | yes / 0.1383 | yes / 0.0766 |
| ex-python-forth | yes / 0.0124 | yes / 0.2050 | yes / 0.0890 |
| ex-python-go-counting | no / 0.0160 | yes / 0.1641 | no / 0.1288 |
| ex-python-phone-number | no / 0.0115 | yes / 0.1732 | yes / 0.0741 |
| ex-python-pig-latin | yes / 0.0098 | yes / 0.1326 | yes / 0.0619 |
| ex-python-poker | yes / 0.0117 | yes / 0.2154 | yes / 0.0710 |
| ex-python-scale-generator | yes / 0.0104 | yes / 0.1949 | yes / 0.0782 |
| ex-python-transpose | yes / 0.0102 | yes / 0.1348 | yes / 0.0750 |
| ex-python-variable-length-quantity | yes / 0.0068 | yes / 0.1448 | yes / 0.0633 |
| ex-python-zipper | yes / 0.0114 | yes / 0.1780 | yes / 0.0723 |
| ex-rust-accumulate | yes / 0.0055 | yes / 0.1430 | yes / 0.0627 |
| ex-rust-acronym | no / 0.0068 | yes / 0.1356 | yes / 0.0664 |
| ex-rust-book-store | yes / 0.0119 | yes / 0.2242 | yes / 0.0803 |
| ex-rust-doubly-linked-list | yes / 0.0402 | yes / 0.5187 | yes / 0.1347 |
| ex-rust-forth | no / 0.0238 | yes / 0.2785 | no / 0.1129 |
| ex-rust-grade-school | no / 0.0087 | yes / 0.1566 | yes / 0.0692 |
| ex-rust-luhn-from | yes / 0.0094 | yes / 0.1540 | yes / 0.0639 |
| ex-rust-parallel-letter-frequency | yes / 0.0100 | yes / 0.1948 | yes / 0.0692 |
| ex-rust-react | yes / 0.0280 | yes / 0.4815 | yes / 0.1335 |
| ex-rust-two-bucket | yes / 0.0173 | yes / 0.1679 | yes / 0.0743 |

## Winners

- Highest solve rate: claude-opus (95.8% of attempted)
- Highest mean score: claude-opus (0.977 over attempted)
- Lowest total cost: claude-haiku ($0.7602)
- Lowest cost per solved: claude-haiku ($0.0271)
- Fastest total wall-clock: claude-sonnet (1236.99s)

## Cost & efficiency

Per-task figures cover attempted runs (repeats included); spend counts every run.

| Subject | Spend($) | Runs | Solved | Mean score | $/task (mean) | $/task (median) | $/solved | $/score-pt | Wall/task(s) | Out tokens/task |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| claude-haiku | 0.7602 | 48 | 28 | 0.776 | 0.0158 | 0.0122 | 0.0271 | 0.0204 | 70.7 | 12492 |
| claude-opus | 10.7745 | 48 | 46 | 0.977 | 0.2245 | 0.1949 | 0.2342 | 0.2298 | 56.2 | 4358 |
| claude-sonnet | 4.1423 | 48 | 37 | 0.881 | 0.0863 | 0.0800 | 0.1120 | 0.0979 | 25.8 | 2777 |

Mean pass fraction per probe group (attempted runs):

| Subject |  |
| --- |  |
| claude-haiku |  |
| claude-opus |  |
| claude-sonnet |  |

### Observations (rule-based)

- Highest mean score: claude-opus (0.977).
- Cheapest per score point: claude-haiku ($0.020); most expensive: claude-opus ($0.230), 11.3x.
- Pairs whose 95% CI excludes 0: claude-haiku vs claude-opus (-0.201, n=48), claude-haiku vs claude-sonnet (-0.105, n=48), claude-opus vs claude-sonnet (+0.095, n=48).

## Parity

- parity_fingerprint: `7f60b7d7cd0b86cbe7384d2ba9d18037f6e0cd9830e1c279d8fee3adb17376ed` (shared by every run)
- repeats per subject: claude-haiku=1, claude-opus=1, claude-sonnet=1

## Environment isolation

| Subject | Fingerprint | Baseline | User config | Contaminated | Foreign | Grading-access tasks (excluded) |
| --- | --- | --- | --- | --- | --- | --- |
| claude-haiku | `62181309c7b1` | `62181309c7b1` | `fa4bfaf5ac45` | no | — | — |
| claude-opus | `62181309c7b1` | `62181309c7b1` | `fa4bfaf5ac45` | no | — | — |
| claude-sonnet | `62181309c7b1` | `62181309c7b1` | `fa4bfaf5ac45` | no | — | — |

## Attempted-based rates (infra excluded)

Rates average per-task values over the tasks a subject actually attempted (infra errors and budget skips are not capability evidence); each task counts once however many repeats it had.

| Subject | Task runs | Attempted | Infra errors | Solve rate | Mean score | Cost/score-pt($) | Median cost/task($) | Median wall/task(s) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| claude-haiku | 48 | 48 | 0 | 58.3% | 0.776 | 0.0204 | 0.0122 | 59.44 |
| claude-opus | 48 | 48 | 0 | 95.8% | 0.977 | 0.2298 | 0.1949 | 36.29 |
| claude-sonnet | 48 | 48 | 0 | 77.1% | 0.881 | 0.0979 | 0.0800 | 21.63 |

## Pairwise (paired bootstrap 95% CI, Wilcoxon p)

Per-task difference of the repeat-mean score, A minus B, over tasks both attempted; `CI excludes 0` is the staged-repeat stopping rule (HLD §6). Reported, not gated.

| A | B | Metric | Mean diff (A-B) | 95% CI | Wilcoxon p | Tasks | CI excludes 0 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| claude-haiku | claude-opus | score | -0.201 | [-0.319, -0.089] | <0.001 | 48 | yes |
| claude-haiku | claude-sonnet | score | -0.105 | [-0.205, -0.014] | 0.021 | 48 | yes |
| claude-opus | claude-sonnet | score | +0.095 | [0.029, 0.176] | 0.007 | 48 | yes |

## Per family

Cells: mean score / solve rate (n = tasks attempted). Families weigh equally.

| Family | claude-haiku | claude-opus | claude-sonnet |
| --- | --- | --- | --- |
| exercism | 0.78 / 58.3% (n=48) | 0.98 / 95.8% (n=48) | 0.88 / 77.1% (n=48) |

## Per language

Cells: mean score / solve rate (n = tasks attempted). Languages weigh equally.

| Language | claude-haiku | claude-opus | claude-sonnet |
| --- | --- | --- | --- |
| cpp | 0.60 / 60.0% (n=10) | 1.00 / 100.0% (n=10) | 0.80 / 80.0% (n=10) |
| go | 0.77 / 37.5% (n=8) | 0.88 / 87.5% (n=8) | 0.85 / 62.5% (n=8) |
| javascript | 0.64 / 40.0% (n=10) | 0.99 / 90.0% (n=10) | 0.85 / 60.0% (n=10) |
| python | 0.89 / 80.0% (n=10) | 1.00 / 100.0% (n=10) | 0.90 / 90.0% (n=10) |
| rust | 0.97 / 70.0% (n=10) | 1.00 / 100.0% (n=10) | 1.00 / 90.0% (n=10) |

## Infra errors / suspects

Infra errors (`subject_status == error`) are excluded from the rates above. Suspects are unsolved non-error runs under $0.15 and 180s -- they look like aborts but stay IN the rates.

| Subject | Infra errors | Share | Infra tasks | Suspect tasks |
| --- | --- | --- | --- | --- |
| claude-haiku | 0 | 0.0% | — | ex-cpp-all-your-base, ex-cpp-binary-search-tree, ex-cpp-crypto-square, ex-cpp-robot-name, ex-go-beer-song, ex-go-food-chain, ex-go-forth, ex-go-kindergarten-garden, ex-go-robot-simulator, ex-javascript-forth, ex-javascript-phone-number, ex-javascript-resistor-color-trio, ex-javascript-simple-linked-list, ex-javascript-transpose, ex-javascript-wordy, ex-python-go-counting, ex-python-phone-number, ex-rust-acronym, ex-rust-forth, ex-rust-grade-school |
| claude-opus | 0 | 0.0% | — | — |
| claude-sonnet | 0 | 0.0% | — | ex-cpp-all-your-base, ex-cpp-robot-name, ex-go-beer-song, ex-go-forth, ex-javascript-phone-number, ex-javascript-queen-attack, ex-javascript-resistor-color-trio, ex-javascript-two-bucket, ex-python-go-counting, ex-rust-forth |
