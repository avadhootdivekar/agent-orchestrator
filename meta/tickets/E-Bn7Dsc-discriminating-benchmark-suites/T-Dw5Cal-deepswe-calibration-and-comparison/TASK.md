# TASK: T-Dw5Cal-deepswe-calibration-and-comparison

## Metadata
- Task ID: `T-Dw5Cal-deepswe-calibration-and-comparison`
- Epic ID: `E-Bn7Dsc-discriminating-benchmark-suites`
- Owner: Claude (manager) for avadhoot
- Created: 2026-11-11
- Last Updated: 2026-11-11
- Status: In Progress (Phase 0)
- Estimate: Phase 0-1 < 1 day; Phase 2-3 sized after Phase 1
- Follows: `T-Hb3Adp-bench-harbor-external` (the Harbor harness this task uses)

## Why
Owner question (2026-11-11): does an external benchmark actually discriminate for the CURRENT Claude
generation, and does ao (overseer-runner) add anything over plain Claude Code on the same model?

Findings that set the direction:
- Terminal-Bench 2.1 is near saturated for current models: Opus 5.5 87.6 %, Sonnet 5.5 83.2 % (Vals,
  Terminus 2 harness, pass@1, 2026-10-01; Vals has archived TB 2.1 in favour of TB 4.0). The three TB 2.1 tasks
  first picked for the pilot were chosen from TB 2.0 per-task rates dominated by OLDER Claude models (a
  selection mistake); plain Sonnet 5.5 solved `dna-assembly` in 1.9 min for $0.25. TB 2.1 is kept only for
  tasks that our own calibration shows Sonnet 5.5 fails.
- DeepSWE v1.1 (113 tasks, 91 repos, 5 languages, 90 min/task) still has headroom: best public pass@1 75.2 %,
  Opus 5.5 (max effort) 72.3 % (Mercor); no published Sonnet 5.5 number. Its reward is binary (all fail-to-pass
  tests pass AND no pass-to-pass test breaks); `partial` is a diagnostic only.
- SWE-Marathon (20 tasks, 4-10 h, 5 GPU-only) has the most headroom (Opus 5.5 ~46 % reported) but is costly; it
  comes after DeepSWE is verified end-to-end.
- No public source says WHICH tasks Sonnet 5.5 fails, so we calibrate ourselves.

## Goal
1. A DeepSWE **regression set** (tasks plain Sonnet 5.5 solves: ao must not lose them) and a **grading set**
   (tasks plain Sonnet 5.5 fails: where ao could add value), measured on this host with the Harbor harness.
2. A verified, working plain-vs-ao comparison on DeepSWE (both arms, same model/effort/cap/timeout/prompt,
   `harbor-parity` clean).
3. Then the same on SWE-Marathon (CPU tasks).

## Roadmap

### Phase 0 - ao arm plumbing (gate; no DeepSWE spend)
- [x] Oracle (reference solution) passes on the 5 pilot tasks (TB 2.1 x3, DeepSWE x2).
- [x] Plain arm end-to-end: `pilot1-tb21-dna-cc-sonnet` reward 1.0, $0.25.
- [x] ao arm fixes: bench schemas uploaded into the container (wheel does not package them); no-git
  local-workspace mode (shared prompt line + `init_git: false` + explicit `branch_policy`); wheel built from a
  clean `git archive` of a pinned commit (`AO_WHEEL_REF`) so an ao run working in the same checkout never
  leaks into the ao under test; task dir moved into the ao workspace with a symlink left at its path (ao
  confines repos to the workspace root) and moved back before grading.
- [ ] ao arm end-to-end on one TB 2.1 task (`pilot3-tb21-dna-ao-sonnet-nogit`): reward recorded, `ao-usage.json`
  cost, overseer run state in `ao-orchestrator.tgz`, `ao-bench harbor-parity` vs the plain job, no token in job
  files, secrets file deleted. Commit + push the adapter changes only after this passes.

### Phase 1 - DeepSWE verification + calibration (owner budget: **$100 total**, list-price equivalent)
Budget split (worst case = tasks x per-task cap, because `run.sh` has no job-level running-total stop):
- 1a Cost probe, plain Sonnet 5.5 on the 2 oracle-validated tasks (`sqlfmt-create-table-ddl-formatting`,
  `koota-pair-relation-tracking`), cap $8/task -> measured $/attempt and minutes. Worst case $16.
- 1b ao arm verification, ao + Sonnet 5.5 on the same 2 tasks, same cap -> proves the comparison path on
  DeepSWE (reward, cost, parity, ao run state, no git changes). Worst case $16.
- 1c Calibration pool, plain Sonnet 5.5, 1 attempt per task, stratified random sample across the 5 languages
  (fixed seed, recorded), N = floor(remaining budget / cap) with cap re-chosen from 1a's measurement (~$60 left
  -> e.g. 10-15 tasks). Concurrency moderate (`-n 3`): the subscription is shared with live ao runs.
- Stop rule: after 1a, if the measured mean $/attempt x planned N exceeds the remaining budget, shrink N; never
  exceed $100 without the owner.
- The per-task cap used here is THE cap for the later comparison (an attempt that fails because it hit the cap
  is "failed under the comparison's conditions", which is what the grading set must mean). Record it.

Classification (per task, 1c + 1a):
- `solved` -> regression-set candidate.
- `failed` -> grading-set candidate ("failed once", not "unsolvable": one attempt cannot tell hard from unlucky).
- infra error (image build, verifier crash reward -1, API/auth) -> excluded and re-run, never classified.
- Record per task: reward, `partial`, f2p/p2p counts, $ and minutes, language, repo. Output:
  `benchmarks/harbor/sets/deepswe-v1.1-sonnet-5.5.json` (+ the job dirs under `/var/tmp/ao-harbor/jobs/`).

### Phase 2 - Comparison (budget proposed to the owner after Phase 1)
- FRESH attempts for both arms (selection-bias guard: never reuse the calibration attempts that defined the
  sets; tasks picked because plain Sonnet failed once would otherwise regress towards solved and flatter ao).
- Grading set + a sample of the regression set, ~3 attempts per task per arm, same `AO_WHEEL_REF`, cap,
  effort, timeout, prompt; `harbor-parity` on the first pair.
- Report: per-arm solve rate with CI, pairwise both / ao-only / plain-only / neither, $ per solved task, wall
  time; ao regressions on the regression set called out. No claim with fewer than ~10 discordant tasks.
- Optional: Opus 5.5 arms if budget allows.

### Phase 3 - SWE-Marathon (after Phase 2 works end-to-end)
- 15 CPU tasks (skip the 5 GPU tasks; host GPU is a T400 4 GB); 16-32 GB RAM each -> concurrency 2; dataset
  timeouts 4-10 h. Same calibration -> sets -> comparison flow; budget proposed separately.

## Acceptance Criteria
1. Phase 0 ao arm end-to-end on one task with parity checked; adapter changes committed and pushed.
2. Phase 1 spend <= $100 (list-price equivalent), recorded per job in STATUS.md.
3. `deepswe-v1.1-sonnet-5.5.json` lists every attempted task with class and evidence; infra errors excluded.
4. ao arm verified on at least 2 DeepSWE tasks (reward, cost, parity, no git changes in the task dir).
5. Phase 2 budget proposal (tasks x attempts x arms x measured $/attempt) written for the owner.
6. Results + spend in this STATUS, the epic STATUS, `benchmarks/README.md` and the KB.

## Risks
- Per-task cap too low -> "failed" means "ran out of budget"; mitigated by measuring in 1a and using one cap
  for calibration and comparison alike.
- DeepSWE images are large: keep 60+ GB free under the Docker data-root; check `docker system df` between jobs.
- Subscription rate limits and the host token (~8 h; `run.sh --min-token-hours`) shared with live ao runs.
- ao arm overhead counts against the 90 min dataset timeout (by design: same wall clock for both arms).
- The task-dir move (symlink) could affect a verifier that inspects real paths or a service left running in
  the task dir; check DeepSWE reports when the ao arm fails where the plain arm passed.
- ao's overseer still runs an LLM git-branch-off stage in no-git mode (deterministic fix: E-Rr5Lnc
  `T-Ng1Wsp`); if it aborts, that is a harness defect, excluded and fixed, not a model result.

## Dependencies
- `T-Hb3Adp-bench-harbor-external` (harness), host Claude login (`run.sh --auth host`).

## Artifacts
- Jobs: `/var/tmp/ao-harbor/jobs/<job>`; sets: `benchmarks/harbor/sets/`; docs: `benchmarks/harbor/README.md`.
