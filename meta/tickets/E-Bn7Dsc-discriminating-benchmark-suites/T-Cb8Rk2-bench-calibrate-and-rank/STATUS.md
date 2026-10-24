# STATUS

- ID: `T-Cb8Rk2-bench-calibrate-and-rank`
- Updated At: 2026-10-24
- State: **Done**

## This update
- By: Claude · Role: manager · Date: 2026-10-24 · Comment: **Done.** Full write-up in `benchmarks/README.md`
  (results entry "calibration + 4-subject ranking (2026-10-24, T-Cb8Rk2)"); comparisons
  `benchmarks/results/2026-10-24-{gen-rank-v1,repo-mutant-mini}-compare/`.
  - Calibration (anchors haiku+opus): round 1 (3,3,2,2) first exposed two grader validity bugs (reference failed 4/4),
    then saturated 8/8; round 2 (3,3,3,2, max valid; scale 3 not gradeable for crud) saturated 8/8;
    `repo-mutant-mini` saturated 8/8. Rule 3 applied: saturation reported, grader untouched (amendment 1).
  - Ranking (10 tasks: gen-rank-v1 seeds 9511-9513 + repo-mutant-mini): haiku / sonnet / opus / ao-overseer-sonnet all
    10/10 solved; mean score 0.985 / 0.985 / 0.970 / 0.985 — the only gaps are one generator-invalid perf group shared by
    everyone (s9512, fixed in `b70515e`) and one perf grading flake for opus (s9513; re-grade 1.0 twice). Cost per task
    $0.071 / $0.166 / $0.455 / $1.793; wall per task 205 / 56 / 147 / 560 s.
  - ao finding: `run-budget-backstop` stopped 5/6 overseer gen runs at $2 before close-out; the template's staging
    heuristics (`default_unit_cost_usd` 8, `default_ckpt_cost_usd` 5) do not degrade gracefully at small budgets.
  - Validity fixes (model-blind, each from a reference/harness failure): jsonl any-JSON lines; HTTP via container IP +
    SO_LINGER 0 (ephemeral-port exhaustion through docker-proxy); grader error keeps subject cost; init_git on existing
    repos; crud perf load label `{{i}}`; refcheck requires 1.0.
  - Spend $32.75 total (incl. $0.80 estimated) of $250; ranking per subject <= $20 (overseer $17.93).
  - Open: perf-phase ConnectionRefused flake under parallel grading (1 of 40 gradings); quality signal needs a harder
    instrument (remaining families, TS/Go with host toolchains, recent pr-mined instances).
- By: Claude · Role: manager · Date: 2026-10-24 · Comment: created; protocol pre-registered in TASK.md before
  any calibration spend. Go sandbox base bumped to `golang:1.26-alpine` (the latest `modernc.org/sqlite` needs
  Go >= 1.26; the 1.23 image failed to build) — TS/Go still excluded from ranking (host toolchains).
