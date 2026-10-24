# Bench comparison — polyglot-bv

- generated_at: 2026-10-24T14:49:21.072584+00:00

## Per-subject

| Subject | Solved/Total | Solve Rate | Total Cost($) | Cost/Solved($) | Total Wall(s) |
| --- | --- | --- | --- | --- | --- |
| ao-overseer-sonnet | 48/48 | 100.0% | 61.5757 | 1.2828 | 12089.35 |
| claude-haiku | 48/48 | 100.0% | 0.4529 | 0.0094 | 2059.89 |
| claude-opus | 48/48 | 100.0% | 8.1336 | 0.1695 | 1363.98 |
| claude-sonnet | 48/48 | 100.0% | 3.8131 | 0.0794 | 1029.74 |

## Per-task (solved / cost)

| Task | ao-overseer-sonnet | claude-haiku | claude-opus | claude-sonnet |
| --- | --- | --- | --- | --- |
| ex-cpp-all-your-base | yes / 1.3133 | yes / 0.0094 | yes / 0.1852 | yes / 0.0853 |
| ex-cpp-allergies | yes / 1.2291 | yes / 0.0095 | yes / 0.2282 | yes / 0.0933 |
| ex-cpp-binary-search-tree | yes / 1.3184 | yes / 0.0114 | yes / 0.1903 | yes / 0.0957 |
| ex-cpp-complex-numbers | yes / 1.3580 | yes / 0.0086 | yes / 0.2290 | yes / 0.1198 |
| ex-cpp-crypto-square | yes / 1.2289 | yes / 0.0083 | yes / 0.1839 | yes / 0.0755 |
| ex-cpp-dnd-character | yes / 1.3110 | yes / 0.0100 | yes / 0.1776 | yes / 0.0903 |
| ex-cpp-grade-school | yes / 1.3364 | yes / 0.0067 | yes / 0.1721 | yes / 0.0878 |
| ex-cpp-knapsack | yes / 1.3370 | yes / 0.0064 | yes / 0.1574 | yes / 0.0760 |
| ex-cpp-queen-attack | yes / 1.2644 | yes / 0.0096 | yes / 0.1713 | yes / 0.0839 |
| ex-cpp-robot-name | yes / 1.2888 | yes / 0.0119 | yes / 0.1711 | yes / 0.0861 |
| ex-go-beer-song | yes / 1.1618 | yes / 0.0072 | yes / 0.1816 | yes / 0.0867 |
| ex-go-food-chain | yes / 1.4944 | yes / 0.0066 | yes / 0.1575 | yes / 0.0696 |
| ex-go-forth | yes / 1.2233 | yes / 0.0161 | yes / 0.1891 | yes / 0.0848 |
| ex-go-kindergarten-garden | yes / 1.2327 | yes / 0.0069 | yes / 0.1890 | yes / 0.0819 |
| ex-go-pov | yes / 1.2345 | yes / 0.0076 | yes / 0.1832 | yes / 0.0860 |
| ex-go-react | yes / 1.2383 | yes / 0.0074 | yes / 0.1824 | yes / 0.0937 |
| ex-go-robot-simulator | yes / 2.0198 | yes / 0.0297 | yes / 0.2799 | yes / 0.1462 |
| ex-go-say | yes / 1.4806 | yes / 0.0044 | yes / 0.1271 | yes / 0.0659 |
| ex-javascript-forth | yes / 1.2313 | yes / 0.0107 | yes / 0.2103 | yes / 0.0975 |
| ex-javascript-list-ops | yes / 1.2855 | yes / 0.0088 | yes / 0.1316 | yes / 0.0724 |
| ex-javascript-phone-number | yes / 1.1542 | yes / 0.0064 | yes / 0.1275 | yes / 0.0696 |
| ex-javascript-queen-attack | yes / 1.3100 | yes / 0.0095 | yes / 0.1343 | yes / 0.0714 |
| ex-javascript-resistor-color-trio | yes / 1.2962 | yes / 0.0068 | yes / 0.1360 | yes / 0.0666 |
| ex-javascript-simple-linked-list | yes / 1.1988 | yes / 0.0073 | yes / 0.1276 | yes / 0.0667 |
| ex-javascript-transpose | yes / 1.1493 | yes / 0.0068 | yes / 0.1232 | yes / 0.0664 |
| ex-javascript-two-bucket | yes / 1.2691 | yes / 0.0091 | yes / 0.1587 | yes / 0.0767 |
| ex-javascript-wordy | yes / 1.2438 | yes / 0.0081 | yes / 0.1498 | yes / 0.0688 |
| ex-javascript-zebra-puzzle | yes / 1.2284 | yes / 0.0082 | yes / 0.1624 | yes / 0.0689 |
| ex-python-bottle-song | yes / 1.1880 | yes / 0.0041 | yes / 0.1300 | yes / 0.0528 |
| ex-python-forth | yes / 1.2571 | yes / 0.0127 | yes / 0.2052 | yes / 0.0888 |
| ex-python-go-counting | yes / 1.1741 | yes / 0.0072 | yes / 0.1549 | yes / 0.0735 |
| ex-python-phone-number | yes / 1.2048 | yes / 0.0048 | yes / 0.1514 | yes / 0.0719 |
| ex-python-pig-latin | yes / 1.1712 | yes / 0.0088 | yes / 0.1261 | yes / 0.0594 |
| ex-python-poker | yes / 1.4853 | yes / 0.0056 | yes / 0.1626 | yes / 0.0583 |
| ex-python-scale-generator | yes / 1.2172 | yes / 0.0079 | yes / 0.1410 | yes / 0.0591 |
| ex-python-transpose | yes / 1.0865 | yes / 0.0046 | yes / 0.1217 | yes / 0.0677 |
| ex-python-variable-length-quantity | yes / 1.1486 | yes / 0.0042 | yes / 0.1318 | yes / 0.0578 |
| ex-python-zipper | yes / 1.4796 | yes / 0.0068 | yes / 0.1894 | yes / 0.0776 |
| ex-rust-accumulate | yes / 1.1365 | yes / 0.0056 | yes / 0.1102 | yes / 0.0596 |
| ex-rust-acronym | yes / 1.2052 | yes / 0.0075 | yes / 0.1130 | yes / 0.0580 |
| ex-rust-book-store | yes / 1.2230 | yes / 0.0050 | yes / 0.1342 | yes / 0.0624 |
| ex-rust-doubly-linked-list | yes / 1.3484 | yes / 0.0340 | yes / 0.3724 | yes / 0.1382 |
| ex-rust-forth | yes / 1.3016 | yes / 0.0266 | yes / 0.2455 | yes / 0.1081 |
| ex-rust-grade-school | yes / 1.2091 | yes / 0.0051 | yes / 0.1315 | yes / 0.0692 |
| ex-rust-luhn-from | yes / 1.4290 | yes / 0.0052 | yes / 0.1320 | yes / 0.0628 |
| ex-rust-parallel-letter-frequency | yes / 1.1616 | yes / 0.0061 | yes / 0.1566 | yes / 0.0595 |
| ex-rust-react | yes / 1.4396 | yes / 0.0235 | yes / 0.2590 | yes / 0.1275 |
| ex-rust-two-bucket | yes / 1.2720 | yes / 0.0083 | yes / 0.1479 | yes / 0.0670 |

## Winners

- Highest solve rate: ao-overseer-sonnet (100.0% of attempted, tied with 3 others)
- Highest mean score: ao-overseer-sonnet (1.000 over attempted, tied with 3 others)
- Lowest total cost: claude-haiku ($0.4529)
- Lowest cost per solved: claude-haiku ($0.0094)
- Fastest total wall-clock: claude-sonnet (1029.74s)

## Cost & efficiency

Per-task figures cover attempted runs (repeats included); spend counts every run.

| Subject | Spend($) | Runs | Solved | Mean score | $/task (mean) | $/task (median) | $/solved | $/score-pt | Wall/task(s) | Out tokens/task |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ao-overseer-sonnet | 61.5757 | 48 | 48 | 1.000 | 1.2828 | 1.2410 | 1.2828 | 1.2828 | 251.9 | 29736 |
| claude-haiku | 0.4529 | 48 | 48 | 1.000 | 0.0094 | 0.0075 | 0.0094 | 0.0094 | 42.9 | 7093 |
| claude-opus | 8.1336 | 48 | 48 | 1.000 | 0.1695 | 0.1581 | 0.1695 | 0.1695 | 28.4 | 1930 |
| claude-sonnet | 3.8131 | 48 | 48 | 1.000 | 0.0794 | 0.0729 | 0.0794 | 0.0794 | 21.5 | 1723 |

Mean pass fraction per probe group (attempted runs):

| Subject |  |
| --- |  |
| ao-overseer-sonnet |  |
| claude-haiku |  |
| claude-opus |  |
| claude-sonnet |  |

### Observations (rule-based)

- Highest mean score: ao-overseer-sonnet (1.000).
- Cheapest per score point: claude-haiku ($0.009); most expensive: ao-overseer-sonnet ($1.283), 135.9x.
- Every subject scores >= 0.95: the suite is at its ceiling and does not separate these subjects on quality -- repeats will not change that; a harder instrument would. Compare them on cost and time.

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
| ao-overseer-sonnet | 48 | 48 | 0 | 100.0% | 1.000 | 1.2828 | 1.2410 | 250.38 |
| claude-haiku | 48 | 48 | 0 | 100.0% | 1.000 | 0.0094 | 0.0075 | 35.99 |
| claude-opus | 48 | 48 | 0 | 100.0% | 1.000 | 0.1695 | 0.1581 | 26.98 |
| claude-sonnet | 48 | 48 | 0 | 100.0% | 1.000 | 0.0794 | 0.0729 | 16.11 |

## Pairwise (paired bootstrap 95% CI, Wilcoxon p)

Per-task difference of the repeat-mean score, A minus B, over tasks both attempted; `CI excludes 0` is the staged-repeat stopping rule (HLD §6). Reported, not gated.

| A | B | Metric | Mean diff (A-B) | 95% CI | Wilcoxon p | Tasks | CI excludes 0 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ao-overseer-sonnet | claude-haiku | score | +0.000 | [0.000, 0.000] | 1.000 | 48 | no |
| ao-overseer-sonnet | claude-opus | score | +0.000 | [0.000, 0.000] | 1.000 | 48 | no |
| ao-overseer-sonnet | claude-sonnet | score | +0.000 | [0.000, 0.000] | 1.000 | 48 | no |
| claude-haiku | claude-opus | score | +0.000 | [0.000, 0.000] | 1.000 | 48 | no |
| claude-haiku | claude-sonnet | score | +0.000 | [0.000, 0.000] | 1.000 | 48 | no |
| claude-opus | claude-sonnet | score | +0.000 | [0.000, 0.000] | 1.000 | 48 | no |

## Per family

Cells: mean score / solve rate (n = tasks attempted). Families weigh equally.

| Family | ao-overseer-sonnet | claude-haiku | claude-opus | claude-sonnet |
| --- | --- | --- | --- | --- |
| exercism | 1.00 / 100.0% (n=48) | 1.00 / 100.0% (n=48) | 1.00 / 100.0% (n=48) | 1.00 / 100.0% (n=48) |

## Per language

Cells: mean score / solve rate (n = tasks attempted). Languages weigh equally.

| Language | ao-overseer-sonnet | claude-haiku | claude-opus | claude-sonnet |
| --- | --- | --- | --- | --- |
| cpp | 1.00 / 100.0% (n=10) | 1.00 / 100.0% (n=10) | 1.00 / 100.0% (n=10) | 1.00 / 100.0% (n=10) |
| go | 1.00 / 100.0% (n=8) | 1.00 / 100.0% (n=8) | 1.00 / 100.0% (n=8) | 1.00 / 100.0% (n=8) |
| javascript | 1.00 / 100.0% (n=10) | 1.00 / 100.0% (n=10) | 1.00 / 100.0% (n=10) | 1.00 / 100.0% (n=10) |
| python | 1.00 / 100.0% (n=10) | 1.00 / 100.0% (n=10) | 1.00 / 100.0% (n=10) | 1.00 / 100.0% (n=10) |
| rust | 1.00 / 100.0% (n=10) | 1.00 / 100.0% (n=10) | 1.00 / 100.0% (n=10) | 1.00 / 100.0% (n=10) |

## Infra errors / suspects

Infra errors (`subject_status == error`) are excluded from the rates above. Suspects are unsolved non-error runs under $0.15 and 180s -- they look like aborts but stay IN the rates.

| Subject | Infra errors | Share | Infra tasks | Suspect tasks |
| --- | --- | --- | --- | --- |
| ao-overseer-sonnet | 0 | 0.0% | — | — |
| claude-haiku | 0 | 0.0% | — | — |
| claude-opus | 0 | 0.0% | — | — |
| claude-sonnet | 0 | 0.0% | — | — |
