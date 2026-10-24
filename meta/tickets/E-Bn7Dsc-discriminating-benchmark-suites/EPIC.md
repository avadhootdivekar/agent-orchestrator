# EPIC: E-Bn7Dsc-discriminating-benchmark-suites

## Metadata
- Epic ID: `E-Bn7Dsc-discriminating-benchmark-suites`
- Title: Discriminating benchmark suites (behavioural-spec + large-codebase tiers, parity, stats)
- Owner: Claude (architect/manager) for the owner (avadhoot)
- Created: 2026-10-23
- Last Updated: 2026-10-24 (6/6 tracks done; follow-on `T-Ws7Iso` done)
- Status: Done (P1–P3 as-built; follow-ons listed in HLD §12) · follow-on `T-Ws7Iso-bench-env-isolation` **Done** (2026-10-24) — ranking campaigns unblocked

## Summary
- Goal: make `ao-bench` able to tell subjects apart on speed, cost and correctness at a cost a single developer
  can afford to repeat — current suites saturate (6/6 everywhere) or have n=10/r=1 resolution.
- Scope In: parity bundle (shared instructions/skills/env/limits for every subject), grader-owned hidden
  assets (`grading_dir`), `blackbox` grader + Docker sandbox + probe DSL, generated task families (Tier 1,
  Python/TS/Go), Tier-2 repo-mutation + PR-mined providers with `pytest_targeted` grader, paired statistics /
  staged repeats / family roll-ups / infra table, `ao-bench gen|calibrate|images|import-prs`, suites + skill.
- Scope Out: LLM-judge grading, non-dev domains, leaderboard UI, replacing `dev-core`/`swebench` provider.

## Requirements
- FR-1 Parity: every subject in a campaign runs under one fingerprinted bundle; comparison refuses mixed parity.
- FR-2 Hidden assets never under the agent-visible workspace.
- FR-3 Behaviour-only grading of generated tasks (core/trap/perf groups, α scaling + reference ratio) in Docker.
- FR-4 ≥2 generator families end-to-end (reference passes its own probes) in P1; 8 by P4.
- FR-5 Tier 2: synthetic bug injection + feature excision on pinned public repos; post-cutoff PR mining.
- FR-6 Stats: paired bootstrap CIs, Wilcoxon, staged repeats, infra-error separation, family roll-up.
- NFR-1 All CI tests fake-tier (no LLM, no network); Docker tests gated by `AO_E2E_DOCKER=1`.
- NFR-2 Existing suites/results load unchanged (additive schema; `total`/`solve_rate` semantics kept).

## Task List
- [x] `T-Cs1Ab2-core-seams` — spec/schema extensions, grading_dir, parity module + bundle, infra-aware aggregate,
      FakeSubject copy-reference, CLI stubs (this commit).
- [x] `T-Bx2Cd3-blackbox-sandbox` — sandbox.py (docker/process), blackbox grader, probe DSL evaluator, perf/α/ref ratio, `ao-bench images`.
- [x] `T-Gn3Ef4-generators` — bench/gen: base, traps, wordlists, skeletons (py/ts/go), crud-relational + job-queue, generated provider, `ao-bench gen`, `gen-services-mini` suite.
- [x] `T-St4Gh5-stats-campaign` — stats.py, comparison parity check + repeats + CIs + family/infra tables, campaign staged repeats, `ao-bench calibrate`.
- [x] `T-T2-5Ij6-tier2-providers` — repo_mutation + pr_mined providers, pytest_targeted grader, import-prs, recipes, dev-medium `.grading/` migration.
- [x] `T-Dk6Lm7-docs-skill-smoke` — README/HLD reconcile, bench skill, memories, real haiku smoke on gen-services-mini.
- [x] `T-Ws7Iso-bench-env-isolation` — workspace root outside the checkout, environment fingerprint from the transcript `init` event, hidden-asset access audit, held-out seeds policy (finding 2026-10-24, HLD §12).
- [x] `T-Ov3Sbj-bench-ao-template-subject` — `ao_workflow` template mode (`ao new … --run`) + `init_git`; `ao-overseer-sonnet` subject.
- [x] `T-Cb8Rk2-bench-calibrate-and-rank` — pre-registered difficulty calibration (anchors haiku/opus) and a haiku/sonnet/opus/ao-overseer-sonnet ranking campaign with report; skills `bench-tune`, `bench-campaign-report`.

## Risks and Dependencies
- Docker availability on dev machines (fallback: process sandbox, tagged).
- Generator bias: mitigated by seed rotation + family roll-up; calibration against ≥2 model tiers before ranking.
- Tier-2 recipes need network to validate against real repos; unit tests use a committed mini fixture repo.
- **Environment leak (found 2026-10-24):** workspaces under `<repo>/playground/.tmp/bench` inherit both ancestor `CLAUDE.md` files and every repo/control-plane skill and agent (verified in the 2026-10-23 `init` events). Parity held but §2b's "same instructions" is violated — fixed by `T-Ws7Iso` (HLD §2c).

## Links
- Design doc: `docs-md/benchmark-discriminating-suites-hld.md`, `docs-md/bench-probe-dsl.md`
- Prior epics: `E-9Qk4Zt-agent-benchmark-harness`, `E-Bt4Xk9-complex-benchmark-tiers`
