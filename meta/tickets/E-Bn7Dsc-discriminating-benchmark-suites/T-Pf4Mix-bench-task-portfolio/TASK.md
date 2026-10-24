# TASK: T-Pf4Mix-bench-task-portfolio

## Metadata
- Task ID: `T-Pf4Mix-bench-task-portfolio`
- Epic ID: `E-Bn7Dsc-discriminating-benchmark-suites`
- Owner: Claude (architect/developer/manager) for avadhoot
- Created: 2026-10-24
- Status: In Progress
- Budget: $300 of agent spend for this task (owner, 2026-10-24)

## Description
Owner ask (2026-10-24): after the saturated T-Cb8Rk2 campaign, broaden the benchmark into a portfolio that discriminates
across a wide range — easy → very hard, short → long context, traps, many problem areas (CRUD, queues, algorithms/data
structures, in-memory engines, parsing, rule engines, debugging, brownfield) and languages — without biasing it towards
the long/complex tasks where `ao` is expected to shine. Design: `docs-md/benchmark-task-portfolio.md`.

## Acceptance Criteria
1. Agents get Go and Node 22 on the host via the parity bundle (done: `bench/toolchains.py`).
2. `exercism` provider + per-language test grader + importer; seeded stratified sample suite; reference/stub checks.
3. New generated families with intrinsic depth: `order-book` (in-memory engine), `rules-engine` (long spec); a
   `bug-hunt` mode (debug planted faults in generated code).
4. Per-band reporting (difficulty, area, language) in the comparison.
5. Anchor calibration (haiku + opus) for new generated families with disjoint seeds; one portfolio campaign over
   haiku / sonnet / opus / ao-overseer-sonnet; report with per-band tables and the tuning-vs-fitting statement.
6. Everything committed + pushed as it lands; spend ledger kept; total agent spend <= $300.

## Pre-registration (2026-10-24, before any spend on the new generated families)
Same rules as T-Cb8Rk2 (anchors haiku + opus only, disjoint seeds, grader frozen, accept when pooled anchor pass
rate in [0.2, 0.8] or anchor mean scores differ by >= 0.10 with neither > 0.95, max 3 rounds, report saturation
rather than fit). Every suite passes `make bench-refcheck` (reference 1.0 everywhere, do-nothing solves none) first.
- `order-book`: start at `entities=3,traps=3,concurrency=2,scale=2`; calibration seeds 7401+, ranking seeds 9611+.
- `bug-hunt`: start at `entities=3,traps=2` (5 planted faults), `concurrency=2,scale=1`; calibration seeds 7451+,
  ranking seeds 9651+.
- `polyglot-mini` (external, fixed sample drawn by seed 20261024 before any agent ran) is not tuned at all.
- Ranking languages for generated build tasks: python + typescript + go (toolchains on the agent host since
  `b2dc911`); bug-hunt is Python only.
- `rules-engine` (added before any spend on it): start at `entities=3,traps=3,concurrency=2,scale=1`; calibration
  seeds 7501+, ranking seeds 9701+.
