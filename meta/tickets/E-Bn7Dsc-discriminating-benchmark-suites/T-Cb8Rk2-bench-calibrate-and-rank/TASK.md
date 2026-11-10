# TASK: T-Cb8Rk2-bench-calibrate-and-rank

## Metadata
- Task ID: `T-Cb8Rk2-bench-calibrate-and-rank`
- Epic ID: `E-Bn7Dsc-discriminating-benchmark-suites`
- Owner: Claude (developer/manager) for avadhoot
- Created: 2026-10-24
- Last Updated: 2026-10-24
- Status: Done (2026-10-24)
- Estimate: < 1 day (wall clock dominated by agent runs)

## Description
Owner ask (2026-10-24): tune the generated-suite difficulty so grading discriminates, then run a ranking
campaign of `claude-haiku`, `claude-sonnet`, `claude-opus` and an `ao` overseer subject on Sonnet only;
produce a report with scores, $/task, $/score and insights. Budget: $20 per subject (ranking), $250 total
including calibration and smoke runs. Keep asking whether we are *tuning* (making the instrument able to
tell subjects apart) or *fitting* (shaping the instrument until it shows a ranking we expect).

## Pre-registered protocol (written before any calibration spend)
Fixed for the whole task — not tuned on results:
- Grader: `blackbox` defaults (weights core 0.5 / trap 0.35 / perf 0.15, `pass_threshold` 0.8,
  `solved` also needs every core probe). Parity bundle `benchmarks/parity/default` (max_turns 30,
  bypassPermissions, and — amended before any spend — `CLAUDE_CODE_EFFORT_LEVEL=high` so the operator's
  user-level `effortLevel: xhigh` no longer silently applies to every subject). Subject configs as committed. Suite timeout 3600 s.
- Language: Python only. The agent's host has Node 18 (no `node:sqlite`) and no Go toolchain, so TS/Go
  tasks would measure working around a missing toolchain, not capability (known limit).
- Families: `crud-relational` (http) + `job-queue` (jsonl), equal counts.

The ONLY tuned knob is the global difficulty vector (`entities,traps,concurrency,scale`), applied to every
instance alike:
1. Anchors are `claude-haiku` and `claude-opus` (bare). `claude-sonnet` and the `ao` subject are never run
   during calibration, so the vector cannot be fitted to them.
2. Calibration instances use seeds 7301+ (`gen-calib-v<n>` suites); ranking instances use seeds 9511+,
   generated only after the vector is frozen and never inspected or filtered by result.
3. Accept a vector when, over the calibration instances, the pooled anchor pass rate is in [0.2, 0.8] OR the
   anchors' mean scores differ by >= 0.10 with neither above 0.95 (continuous discrimination). Too easy ->
   raise the axes toward their max; too hard -> lower `traps` first. At most 3 rounds; if the families
   saturate at the maximum vector, say so instead of changing the grader.
4. No per-instance selection on the ranking suite; every generated ranking instance is run and reported.
5. Fitting checks reported with the results: which probe group (core/trap/perf) carries the differences
   (perf is timing-noisy), repeat variance vs between-subject differences where repeats exist, whether
   any knob was moved after seeing a non-anchor subject, and per-family consistency of the ordering.

## Acceptance Criteria
1. Calibration rounds recorded (vector, instances, anchor scores, cost, decision) in STATUS.md.
2. Ranking suite generated at the frozen vector with fresh seeds; campaign of the four subjects within
   budget; comparison committed under `benchmarks/results/`.
3. Report (README results entry + STATUS) with score, solve rate, $/task, $/solved, $/score point,
   wall/task, per-probe-group scores, pairwise CIs and the fitting-vs-tuning assessment.
4. Skills to repeat this (`bench-tune`, `bench-campaign-report`) committed.

## Dependencies
- `T-Ov3Sbj-bench-ao-template-subject` (the overseer subject) before the ranking campaign.

## Amendment 1 (2026-10-24, after calibration rounds 1-2, before any non-anchor subject ran)
Rounds 1 (3,3,2,2) and 2 (3,3,3,2 = max valid; scale 3 is not gradeable for crud) saturated: 16/16 anchor runs
scored 1.0 on every probe group. Per rule 3 the generated families are reported as saturated and the grader is
not touched. Changes:
- The generated ranking suite is still run (fresh seeds 9511+, max valid vector); it measures cost/time, and any
  quality difference there is a bonus, not the design.
- `repo-mutant-mini` (Tier 2: injected bugs / excised feature in pinned click/attrs/rich, hidden tests) is added as a
  second instrument: reference self-check (copy-reference) first, then the anchors at r=1. It is included in the
  ranking as a whole suite if the anchors do not both saturate (no per-instance selection). Its 4 instances are
  fixed, so the haiku-vs-opus contrast on it is not independent of the inclusion decision (declared); sonnet and
  the `ao` subject never influence it. All runs use `--parity benchmarks/parity/default`.
