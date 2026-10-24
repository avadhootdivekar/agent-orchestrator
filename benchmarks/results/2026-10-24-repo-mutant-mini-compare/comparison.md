# Bench comparison — repo-mutant-mini

- generated_at: 2026-10-24T05:57:22.466832+00:00

## Per-subject

| Subject | Solved/Total | Solve Rate | Total Cost($) | Cost/Solved($) | Total Wall(s) |
| --- | --- | --- | --- | --- | --- |
| claude-haiku | 4/4 | 100.0% | 0.0366 | 0.0092 | 203.95 |
| claude-sonnet | 4/4 | 100.0% | 0.3760 | 0.0940 | 121.39 |
| claude-opus | 4/4 | 100.0% | 0.7998 | 0.1999 | 195.44 |
| ao-overseer-sonnet | 4/4 | 100.0% | 5.2638 | 1.3159 | 1240.11 |

## Per-task (solved / cost)

| Task | claude-haiku | claude-sonnet | claude-opus | ao-overseer-sonnet |
| --- | --- | --- | --- | --- |
| attrs-excise-ne-validator | yes / 0.0156 | yes / 0.1337 | yes / 0.3590 | yes / 1.4866 |
| attrs-to-bool-shorthand | yes / 0.0075 | yes / 0.0714 | yes / 0.1201 | yes / 1.3360 |
| click-range-open-bounds | yes / 0.0060 | yes / 0.0889 | yes / 0.1675 | yes / 1.1965 |
| rich-rate-unit-boundary | yes / 0.0075 | yes / 0.0820 | yes / 0.1532 | yes / 1.2446 |

## Winners

- Highest solve rate: ao-overseer-sonnet (100.0% of attempted, tied with 3 others)
- Highest mean score: ao-overseer-sonnet (1.000 over attempted, tied with 3 others)
- Lowest total cost: claude-haiku ($0.0366)
- Lowest cost per solved: claude-haiku ($0.0092)
- Fastest total wall-clock: claude-sonnet (121.39s)

## Cost & efficiency

Per-task figures cover attempted runs (repeats included); spend counts every run.

| Subject | Spend($) | Runs | Solved | Mean score | $/task (mean) | $/task (median) | $/solved | $/score-pt | Wall/task(s) | Out tokens/task |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| claude-haiku | 0.0366 | 4 | 4 | 1.000 | 0.0092 | 0.0075 | 0.0092 | 0.0092 | 51.0 | 5039 |
| claude-sonnet | 0.3760 | 4 | 4 | 1.000 | 0.0940 | 0.0854 | 0.0940 | 0.0940 | 30.3 | 2174 |
| claude-opus | 0.7998 | 4 | 4 | 1.000 | 0.1999 | 0.1604 | 0.1999 | 0.1999 | 48.9 | 2748 |
| ao-overseer-sonnet | 5.2638 | 4 | 4 | 1.000 | 1.3159 | 1.2903 | 1.3159 | 1.3159 | 310.0 | 33294 |

Mean pass fraction per probe group (attempted runs):

| Subject |  |
| --- |  |
| claude-haiku |  |
| claude-sonnet |  |
| claude-opus |  |
| ao-overseer-sonnet |  |

### Observations (rule-based)

- Highest mean score: claude-haiku (1.000).
- Cheapest per score point: claude-haiku ($0.009); most expensive: ao-overseer-sonnet ($1.316), 143.8x.
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
| claude-haiku | 4 | 4 | 0 | 100.0% | 1.000 | 0.0092 | 0.0075 | 46.16 |
| claude-sonnet | 4 | 4 | 0 | 100.0% | 1.000 | 0.0940 | 0.0854 | 22.95 |
| claude-opus | 4 | 4 | 0 | 100.0% | 1.000 | 0.1999 | 0.1604 | 35.33 |
| ao-overseer-sonnet | 4 | 4 | 0 | 100.0% | 1.000 | 1.3159 | 1.2903 | 297.05 |

## Pairwise (paired bootstrap 95% CI, Wilcoxon p)

Per-task difference of the repeat-mean score, A minus B, over tasks both attempted; `CI excludes 0` is the staged-repeat stopping rule (HLD §6). Reported, not gated.

| A | B | Metric | Mean diff (A-B) | 95% CI | Wilcoxon p | Tasks | CI excludes 0 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| claude-haiku | claude-sonnet | score | +0.000 | [0.000, 0.000] | 1.000 | 4 | no |
| claude-haiku | claude-opus | score | +0.000 | [0.000, 0.000] | 1.000 | 4 | no |
| claude-haiku | ao-overseer-sonnet | score | +0.000 | [0.000, 0.000] | 1.000 | 4 | no |
| claude-sonnet | claude-opus | score | +0.000 | [0.000, 0.000] | 1.000 | 4 | no |
| claude-sonnet | ao-overseer-sonnet | score | +0.000 | [0.000, 0.000] | 1.000 | 4 | no |
| claude-opus | ao-overseer-sonnet | score | +0.000 | [0.000, 0.000] | 1.000 | 4 | no |

## Per family

Cells: mean score / solve rate (n = tasks attempted). Families weigh equally.

| Family | claude-haiku | claude-sonnet | claude-opus | ao-overseer-sonnet |
| --- | --- | --- | --- | --- |
| repo-mutation | 1.00 / 100.0% (n=4) | 1.00 / 100.0% (n=4) | 1.00 / 100.0% (n=4) | 1.00 / 100.0% (n=4) |

## Per language

Cells: mean score / solve rate (n = tasks attempted). Languages weigh equally.

| Language | claude-haiku | claude-sonnet | claude-opus | ao-overseer-sonnet |
| --- | --- | --- | --- | --- |
| python | 1.00 / 100.0% (n=4) | 1.00 / 100.0% (n=4) | 1.00 / 100.0% (n=4) | 1.00 / 100.0% (n=4) |

## Infra errors / suspects

Infra errors (`subject_status == error`) are excluded from the rates above. Suspects are unsolved non-error runs under $0.15 and 180s -- they look like aborts but stay IN the rates.

| Subject | Infra errors | Share | Infra tasks | Suspect tasks |
| --- | --- | --- | --- | --- |
| claude-haiku | 0 | 0.0% | — | — |
| claude-sonnet | 0 | 0.0% | — | — |
| claude-opus | 0 | 0.0% | — | — |
| ao-overseer-sonnet | 0 | 0.0% | — | — |
