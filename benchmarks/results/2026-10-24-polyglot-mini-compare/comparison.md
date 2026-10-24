# Bench comparison — polyglot-mini

- generated_at: 2026-10-24T14:49:20.507484+00:00

## Per-subject

| Subject | Solved/Total | Solve Rate | Total Cost($) | Cost/Solved($) | Total Wall(s) |
| --- | --- | --- | --- | --- | --- |
| ao-overseer-sonnet | 25/29 | 86.2% | 40.5496 | 1.6220 | 8761.94 |
| claude-haiku | 22/29 | 75.9% | 0.4043 | 0.0184 | 2212.19 |
| claude-opus | 29/29 | 100.0% | 5.9030 | 0.2036 | 1272.47 |
| claude-sonnet | 25/29 | 86.2% | 2.4021 | 0.0961 | 713.54 |

## Per-task (solved / cost)

| Task | ao-overseer-sonnet | claude-haiku | claude-opus | claude-sonnet |
| --- | --- | --- | --- | --- |
| ex-cpp-clock | yes / 1.5327 | yes / 0.0151 | yes / 0.3295 | yes / 0.1056 |
| ex-cpp-diamond | yes / 1.3760 | no / 0.0129 | yes / 0.2020 | yes / 0.0759 |
| ex-cpp-linked-list | yes / 1.4277 | yes / 0.0148 | yes / 0.3066 | yes / 0.0925 |
| ex-cpp-phone-number | yes / 1.7386 | no / 0.0153 | yes / 0.3062 | no / 0.1035 |
| ex-cpp-sublist | yes / 1.4066 | no / 0.0125 | yes / 0.2236 | yes / 0.0851 |
| ex-cpp-zebra-puzzle | yes / 1.4658 | yes / 0.0129 | yes / 0.1942 | yes / 0.0912 |
| ex-go-bottle-song | yes / 1.2849 | yes / 0.0068 | yes / 0.1410 | yes / 0.0649 |
| ex-go-connect | yes / 1.6661 | no / 0.0129 | yes / 0.2270 | yes / 0.0887 |
| ex-go-pig-latin | yes / 1.3209 | yes / 0.0103 | yes / 0.1564 | yes / 0.0695 |
| ex-go-tree-building | yes / 1.3031 | yes / 0.0108 | yes / 0.1685 | yes / 0.0670 |
| ex-go-trinary | no / 1.3273 | no / 0.0081 | yes / 0.1642 | no / 0.0876 |
| ex-go-variable-length-quantity | yes / 1.3718 | yes / 0.0088 | yes / 0.1611 | yes / 0.0733 |
| ex-javascript-beer-song | yes / 1.3557 | yes / 0.0106 | yes / 0.2178 | yes / 0.0749 |
| ex-javascript-book-store | yes / 1.2688 | yes / 0.0249 | yes / 0.2106 | yes / 0.0913 |
| ex-javascript-meetup | yes / 1.3894 | yes / 0.0143 | yes / 0.1679 | yes / 0.0789 |
| ex-javascript-parallel-letter-frequency | yes / 1.3488 | yes / 0.0095 | yes / 0.2156 | yes / 0.0764 |
| ex-javascript-pig-latin | yes / 1.2628 | yes / 0.0140 | yes / 0.1524 | yes / 0.0700 |
| ex-python-affine-cipher | yes / 1.3015 | yes / 0.0089 | yes / 0.1572 | yes / 0.0975 |
| ex-python-bowling | yes / 1.3399 | yes / 0.0206 | yes / 0.1893 | yes / 0.0827 |
| ex-python-dominoes | no / 1.2489 | yes / 0.0119 | yes / 0.1543 | yes / 0.0641 |
| ex-python-grade-school | yes / 1.4759 | no / 0.0057 | yes / 0.1400 | yes / 0.0684 |
| ex-python-pov | no / 1.3889 | yes / 0.0112 | yes / 0.1911 | no / 0.0719 |
| ex-python-proverb | yes / 1.2121 | yes / 0.0053 | yes / 0.1367 | yes / 0.0588 |
| ex-rust-nucleotide-codons | yes / 1.6738 | yes / 0.0171 | yes / 0.2719 | yes / 0.0973 |
| ex-rust-ocr-numbers | yes / 1.3907 | yes / 0.0233 | yes / 0.1693 | yes / 0.1076 |
| ex-rust-scale-generator | no / 1.4706 | no / 0.0370 | yes / 0.2283 | no / 0.0929 |
| ex-rust-word-count | yes / 1.3024 | yes / 0.0085 | yes / 0.1575 | yes / 0.0660 |
| ex-rust-wordy | yes / 1.4013 | yes / 0.0139 | yes / 0.1848 | yes / 0.0783 |
| ex-rust-xorcism | yes / 1.4966 | yes / 0.0262 | yes / 0.3781 | yes / 0.1204 |

## Winners

- Highest solve rate: claude-opus (100.0% of attempted)
- Highest mean score: claude-opus (1.000 over attempted)
- Lowest total cost: claude-haiku ($0.4043)
- Lowest cost per solved: claude-haiku ($0.0184)
- Fastest total wall-clock: claude-sonnet (713.54s)

## Cost & efficiency

Per-task figures cover attempted runs (repeats included); spend counts every run.

| Subject | Spend($) | Runs | Solved | Mean score | $/task (mean) | $/task (median) | $/solved | $/score-pt | Wall/task(s) | Out tokens/task |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ao-overseer-sonnet | 40.5496 | 29 | 25 | 0.947 | 1.3983 | 1.3760 | 1.6220 | 1.4771 | 302.1 | 35731 |
| claude-haiku | 0.4043 | 29 | 22 | 0.827 | 0.0139 | 0.0129 | 0.0184 | 0.0169 | 76.3 | 12213 |
| claude-opus | 5.9030 | 29 | 29 | 1.000 | 0.2036 | 0.1893 | 0.2036 | 0.2036 | 43.9 | 3699 |
| claude-sonnet | 2.4021 | 29 | 25 | 0.926 | 0.0828 | 0.0789 | 0.0961 | 0.0894 | 24.6 | 2486 |

Mean pass fraction per probe group (attempted runs):

| Subject |  |
| --- |  |
| ao-overseer-sonnet |  |
| claude-haiku |  |
| claude-opus |  |
| claude-sonnet |  |

### Observations (rule-based)

- Highest mean score: claude-opus (1.000).
- Cheapest per score point: claude-haiku ($0.017); most expensive: ao-overseer-sonnet ($1.477), 87.6x.
- Pairs whose 95% CI excludes 0: ao-overseer-sonnet vs claude-haiku (+0.119, n=29), ao-overseer-sonnet vs claude-opus (-0.053, n=29), claude-haiku vs claude-opus (-0.173, n=29), claude-haiku vs claude-sonnet (-0.099, n=29), claude-opus vs claude-sonnet (+0.074, n=29).
- 1 pair(s) not distinguishable at this n (CI straddles 0): add tasks or repeats before ranking them.

## Parity

- parity_fingerprint: `7f60b7d7cd0b86cbe7384d2ba9d18037f6e0cd9830e1c279d8fee3adb17376ed` (shared by every run)
- repeats per subject: ao-overseer-sonnet=1, claude-haiku=1, claude-opus=1, claude-sonnet=1

## Environment isolation

| Subject | Fingerprint | Baseline | User config | Contaminated | Foreign | Grading-access tasks (excluded) |
| --- | --- | --- | --- | --- | --- | --- |
| ao-overseer-sonnet | `62181309c7b1` | `62181309c7b1` | `fa4bfaf5ac45` | no | — | — |
| claude-haiku | `62181309c7b1` | `62181309c7b1` | `fa4bfaf5ac45` | no | — | — |
| claude-opus | `62181309c7b1` | `62181309c7b1` | `fa4bfaf5ac45` | no | — | — |
| claude-sonnet | `62181309c7b1` | `62181309c7b1` | `fa4bfaf5ac45` | no | — | — |

## Attempted-based rates (infra excluded)

Rates average per-task values over the tasks a subject actually attempted (infra errors and budget skips are not capability evidence); each task counts once however many repeats it had.

| Subject | Task runs | Attempted | Infra errors | Solve rate | Mean score | Cost/score-pt($) | Median cost/task($) | Median wall/task(s) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ao-overseer-sonnet | 29 | 29 | 0 | 86.2% | 0.947 | 1.4771 | 1.3760 | 285.47 |
| claude-haiku | 29 | 29 | 0 | 75.9% | 0.827 | 0.0169 | 0.0129 | 61.22 |
| claude-opus | 29 | 29 | 0 | 100.0% | 1.000 | 0.2036 | 0.1893 | 37.76 |
| claude-sonnet | 29 | 29 | 0 | 86.2% | 0.926 | 0.0894 | 0.0789 | 24.00 |

## Pairwise (paired bootstrap 95% CI, Wilcoxon p)

Per-task difference of the repeat-mean score, A minus B, over tasks both attempted; `CI excludes 0` is the staged-repeat stopping rule (HLD §6). Reported, not gated.

| A | B | Metric | Mean diff (A-B) | 95% CI | Wilcoxon p | Tasks | CI excludes 0 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ao-overseer-sonnet | claude-haiku | score | +0.119 | [0.003, 0.247] | 0.102 | 29 | yes |
| ao-overseer-sonnet | claude-opus | score | -0.053 | [-0.138, -0.001] | 0.125 | 29 | yes |
| ao-overseer-sonnet | claude-sonnet | score | +0.020 | [-0.042, 0.105] | 0.625 | 29 | no |
| claude-haiku | claude-opus | score | -0.173 | [-0.308, -0.047] | 0.016 | 29 | yes |
| claude-haiku | claude-sonnet | score | -0.099 | [-0.205, -0.003] | 0.156 | 29 | yes |
| claude-opus | claude-sonnet | score | +0.074 | [0.003, 0.177] | 0.125 | 29 | yes |

## Per family

Cells: mean score / solve rate (n = tasks attempted). Families weigh equally.

| Family | ao-overseer-sonnet | claude-haiku | claude-opus | claude-sonnet |
| --- | --- | --- | --- | --- |
| exercism | 0.95 / 86.2% (n=29) | 0.83 / 75.9% (n=29) | 1.00 / 100.0% (n=29) | 0.93 / 86.2% (n=29) |

## Per language

Cells: mean score / solve rate (n = tasks attempted). Languages weigh equally.

| Language | ao-overseer-sonnet | claude-haiku | claude-opus | claude-sonnet |
| --- | --- | --- | --- | --- |
| cpp | 1.00 / 100.0% (n=6) | 0.50 / 50.0% (n=6) | 1.00 / 100.0% (n=6) | 0.83 / 83.3% (n=6) |
| go | 0.83 / 83.3% (n=6) | 0.70 / 66.7% (n=6) | 1.00 / 100.0% (n=6) | 0.83 / 83.3% (n=6) |
| javascript | 1.00 / 100.0% (n=5) | 1.00 / 100.0% (n=5) | 1.00 / 100.0% (n=5) | 1.00 / 100.0% (n=5) |
| python | 0.92 / 66.7% (n=6) | 0.98 / 83.3% (n=6) | 1.00 / 100.0% (n=6) | 0.99 / 83.3% (n=6) |
| rust | 0.99 / 83.3% (n=6) | 0.99 / 83.3% (n=6) | 1.00 / 100.0% (n=6) | 0.99 / 83.3% (n=6) |

## Infra errors / suspects

Infra errors (`subject_status == error`) are excluded from the rates above. Suspects are unsolved non-error runs under $0.15 and 180s -- they look like aborts but stay IN the rates.

| Subject | Infra errors | Share | Infra tasks | Suspect tasks |
| --- | --- | --- | --- | --- |
| ao-overseer-sonnet | 0 | 0.0% | — | — |
| claude-haiku | 0 | 0.0% | — | ex-cpp-diamond, ex-cpp-phone-number, ex-cpp-sublist, ex-go-connect, ex-go-trinary, ex-python-grade-school, ex-rust-scale-generator |
| claude-opus | 0 | 0.0% | — | — |
| claude-sonnet | 0 | 0.0% | — | ex-cpp-phone-number, ex-go-trinary, ex-python-pov, ex-rust-scale-generator |
