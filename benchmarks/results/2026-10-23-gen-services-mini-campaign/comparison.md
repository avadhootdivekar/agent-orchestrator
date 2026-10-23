# Bench comparison — gen-services-mini

- generated_at: 2026-10-23T15:41:28.715456+00:00

## Per-subject

| Subject | Solved/Total | Solve Rate | Total Cost($) | Cost/Solved($) | Total Wall(s) |
| --- | --- | --- | --- | --- | --- |
| claude-haiku | 2/2 | 100.0% | 0.2292 | 0.1146 | 452.63 |
| ao-epic-haiku | 2/2 | 100.0% | 0.2454 | 0.1227 | 632.11 |

## Per-task (solved / cost)

| Task | claude-haiku | ao-epic-haiku |
| --- | --- | --- |
| crud-relational-py-s4242 | yes / 0.1943 | yes / 0.1840 |
| job-queue-py-s4242 | yes / 0.0348 | yes / 0.0614 |

## Winners

- Highest solve rate: ao-epic-haiku (100.0% of attempted, tied with 1 other)
- Highest mean score: ao-epic-haiku (1.000 over attempted, tied with 1 other)
- Lowest total cost: claude-haiku ($0.2292)
- Lowest cost per solved: claude-haiku ($0.1146)
- Fastest total wall-clock: claude-haiku (452.63s)

## Parity

- parity_fingerprint: `06aaf5ad1df7f6fc7534259a337e4fec4a9bc9785aea42e8079b2742e8dcec16` (shared by every run)
- repeats per subject: claude-haiku=1, ao-epic-haiku=1

## Attempted-based rates (infra excluded)

Rates average per-task values over the tasks a subject actually attempted (infra errors and budget skips are not capability evidence); each task counts once however many repeats it had.

| Subject | Task runs | Attempted | Infra errors | Solve rate | Mean score | Cost/score-pt($) | Median cost/task($) | Median wall/task(s) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| claude-haiku | 2 | 2 | 0 | 100.0% | 1.000 | 0.1146 | 0.1146 | 226.32 |
| ao-epic-haiku | 2 | 2 | 0 | 100.0% | 1.000 | 0.1227 | 0.1227 | 316.06 |

## Pairwise (paired bootstrap 95% CI, Wilcoxon p)

Per-task difference of the repeat-mean score, A minus B, over tasks both attempted; `CI excludes 0` is the staged-repeat stopping rule (HLD §6). Reported, not gated.

| A | B | Metric | Mean diff (A-B) | 95% CI | Wilcoxon p | Tasks | CI excludes 0 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| claude-haiku | ao-epic-haiku | score | +0.000 | [0.000, 0.000] | 1.000 | 2 | no |

## Per family

Cells: mean score / solve rate (n = tasks attempted). Families weigh equally.

| Family | claude-haiku | ao-epic-haiku |
| --- | --- | --- |
| crud-relational | 1.00 / 100.0% (n=1) | 1.00 / 100.0% (n=1) |
| job-queue | 1.00 / 100.0% (n=1) | 1.00 / 100.0% (n=1) |

## Per language

Cells: mean score / solve rate (n = tasks attempted). Languages weigh equally.

| Language | claude-haiku | ao-epic-haiku |
| --- | --- | --- |
| python | 1.00 / 100.0% (n=2) | 1.00 / 100.0% (n=2) |

## Infra errors / suspects

Infra errors (`subject_status == error`) are excluded from the rates above. Suspects are unsolved non-error runs under $0.15 and 180s -- they look like aborts but stay IN the rates.

| Subject | Infra errors | Share | Infra tasks | Suspect tasks |
| --- | --- | --- | --- | --- |
| claude-haiku | 0 | 0.0% | — | — |
| ao-epic-haiku | 0 | 0.0% | — | — |
