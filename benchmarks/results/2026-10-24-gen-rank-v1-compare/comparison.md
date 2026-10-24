# Bench comparison — gen-rank-v1

- generated_at: 2026-10-24T05:57:22.218324+00:00

## Per-subject

| Subject | Solved/Total | Solve Rate | Total Cost($) | Cost/Solved($) | Total Wall(s) |
| --- | --- | --- | --- | --- | --- |
| claude-haiku | 6/6 | 100.0% | 0.6686 | 0.1114 | 1848.82 |
| claude-sonnet | 6/6 | 100.0% | 1.2880 | 0.2147 | 439.62 |
| claude-opus | 6/6 | 100.0% | 3.7478 | 0.6246 | 1272.72 |
| ao-overseer-sonnet | 6/6 | 100.0% | 12.6668 | 2.1111 | 4359.38 |

## Per-task (solved / cost)

| Task | claude-haiku | claude-sonnet | claude-opus | ao-overseer-sonnet |
| --- | --- | --- | --- | --- |
| crud-relational-py-s9511 | yes / 0.1175 | yes / 0.2529 | yes / 0.8906 | yes / 2.0304 |
| crud-relational-py-s9512 | yes / 0.2025 | yes / 0.3174 | yes / 0.9383 | yes / 2.3000 |
| crud-relational-py-s9513 | yes / 0.2486 | yes / 0.2726 | yes / 0.6858 | yes / 2.1518 |
| job-queue-py-s9511 | yes / 0.0335 | yes / 0.1510 | yes / 0.3833 | yes / 2.1217 |
| job-queue-py-s9512 | yes / 0.0329 | yes / 0.1326 | yes / 0.4035 | yes / 1.9400 |
| job-queue-py-s9513 | yes / 0.0336 | yes / 0.1614 | yes / 0.4463 | yes / 2.1229 |

## Winners

- Highest solve rate: ao-overseer-sonnet (100.0% of attempted, tied with 3 others)
- Highest mean score: ao-overseer-sonnet (0.975 over attempted, tied with 2 others)
- Lowest total cost: claude-haiku ($0.6686)
- Lowest cost per solved: claude-haiku ($0.1114)
- Fastest total wall-clock: claude-sonnet (439.62s)

## Cost & efficiency

Per-task figures cover attempted runs (repeats included); spend counts every run.

| Subject | Spend($) | Runs | Solved | Mean score | $/task (mean) | $/task (median) | $/solved | $/score-pt | Wall/task(s) | Out tokens/task |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| claude-haiku | 0.6686 | 6 | 6 | 0.975 | 0.1114 | 0.0756 | 0.1114 | 0.1143 | 308.1 | 58660 |
| claude-sonnet | 1.2880 | 6 | 6 | 0.975 | 0.2147 | 0.2072 | 0.2147 | 0.2202 | 73.3 | 10341 |
| claude-opus | 3.7478 | 6 | 6 | 0.950 | 0.6246 | 0.5660 | 0.6246 | 0.6575 | 212.1 | 15935 |
| ao-overseer-sonnet | 12.6668 | 6 | 6 | 0.975 | 2.1111 | 2.1223 | 2.1111 | 2.1653 | 726.6 | 74609 |

Mean pass fraction per probe group (attempted runs):

| Subject | core | perf | trap |
| --- | --- | --- | --- |
| claude-haiku | 1.000 | 0.833 | 1.000 |
| claude-sonnet | 1.000 | 0.833 | 1.000 |
| claude-opus | 1.000 | 0.667 | 1.000 |
| ao-overseer-sonnet | 1.000 | 0.833 | 1.000 |

### Observations (rule-based)

- Highest mean score: claude-haiku (0.975).
- Cheapest per score point: claude-haiku ($0.114); most expensive: ao-overseer-sonnet ($2.165), 18.9x.
- Probe group 'perf' separates subjects by 0.17 (claude-haiku 0.83 vs claude-opus 0.67); perf is timing-sensitive, weigh it lightly.
- Every subject scores >= 0.95: the suite is at its ceiling and does not separate these subjects on quality -- repeats will not change that; a harder instrument would. Compare them on cost and time.

## Parity

- parity_fingerprint: `e8f9c4cbc31fc64108b7546547ca7c6f109a40117dc6faf4a58605916449f0a1` (shared by every run)
- repeats per subject: claude-haiku=1, claude-sonnet=1, claude-opus=1, ao-overseer-sonnet=1

## Environment isolation

| Subject | Fingerprint | Baseline | User config | Contaminated | Foreign | Grading-access tasks (excluded) |
| --- | --- | --- | --- | --- | --- | --- |
| claude-haiku | `62181309c7b1` | `62181309c7b1` | `874197f82a89` | no | — | — |
| claude-sonnet | `62181309c7b1` | `62181309c7b1` | `874197f82a89` | no | — | — |
| claude-opus | `62181309c7b1` | `62181309c7b1` | `874197f82a89` | no | — | — |
| ao-overseer-sonnet | `62181309c7b1` | `62181309c7b1` | `874197f82a89` | no | — | — |

## Attempted-based rates (infra excluded)

Rates average per-task values over the tasks a subject actually attempted (infra errors and budget skips are not capability evidence); each task counts once however many repeats it had.

| Subject | Task runs | Attempted | Infra errors | Solve rate | Mean score | Cost/score-pt($) | Median cost/task($) | Median wall/task(s) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| claude-haiku | 6 | 6 | 0 | 100.0% | 0.975 | 0.1143 | 0.0756 | 240.49 |
| claude-sonnet | 6 | 6 | 0 | 100.0% | 0.975 | 0.2202 | 0.2072 | 68.91 |
| claude-opus | 6 | 6 | 0 | 100.0% | 0.950 | 0.6575 | 0.5660 | 134.81 |
| ao-overseer-sonnet | 6 | 6 | 0 | 100.0% | 0.975 | 2.1653 | 2.1223 | 611.91 |

## Pairwise (paired bootstrap 95% CI, Wilcoxon p)

Per-task difference of the repeat-mean score, A minus B, over tasks both attempted; `CI excludes 0` is the staged-repeat stopping rule (HLD §6). Reported, not gated.

| A | B | Metric | Mean diff (A-B) | 95% CI | Wilcoxon p | Tasks | CI excludes 0 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| claude-haiku | claude-sonnet | score | +0.000 | [0.000, 0.000] | 1.000 | 6 | no |
| claude-haiku | claude-opus | score | +0.025 | [0.000, 0.075] | 1.000 | 6 | no |
| claude-haiku | ao-overseer-sonnet | score | +0.000 | [0.000, 0.000] | 1.000 | 6 | no |
| claude-sonnet | claude-opus | score | +0.025 | [0.000, 0.075] | 1.000 | 6 | no |
| claude-sonnet | ao-overseer-sonnet | score | +0.000 | [0.000, 0.000] | 1.000 | 6 | no |
| claude-opus | ao-overseer-sonnet | score | -0.025 | [-0.075, 0.000] | 1.000 | 6 | no |

## Per family

Cells: mean score / solve rate (n = tasks attempted). Families weigh equally.

| Family | claude-haiku | claude-sonnet | claude-opus | ao-overseer-sonnet |
| --- | --- | --- | --- | --- |
| crud-relational | 0.95 / 100.0% (n=3) | 0.95 / 100.0% (n=3) | 0.90 / 100.0% (n=3) | 0.95 / 100.0% (n=3) |
| job-queue | 1.00 / 100.0% (n=3) | 1.00 / 100.0% (n=3) | 1.00 / 100.0% (n=3) | 1.00 / 100.0% (n=3) |

## Per language

Cells: mean score / solve rate (n = tasks attempted). Languages weigh equally.

| Language | claude-haiku | claude-sonnet | claude-opus | ao-overseer-sonnet |
| --- | --- | --- | --- | --- |
| python | 0.97 / 100.0% (n=6) | 0.97 / 100.0% (n=6) | 0.95 / 100.0% (n=6) | 0.97 / 100.0% (n=6) |

## Infra errors / suspects

Infra errors (`subject_status == error`) are excluded from the rates above. Suspects are unsolved non-error runs under $0.15 and 180s -- they look like aborts but stay IN the rates.

| Subject | Infra errors | Share | Infra tasks | Suspect tasks |
| --- | --- | --- | --- | --- |
| claude-haiku | 0 | 0.0% | — | — |
| claude-sonnet | 0 | 0.0% | — | — |
| claude-opus | 0 | 0.0% | — | — |
| ao-overseer-sonnet | 0 | 0.0% | — | — |
