# EPIC: E-Bt4Xk9-complex-benchmark-tiers

## Metadata
- Epic ID: `E-Bt4Xk9-complex-benchmark-tiers`
- Title: Long-duration / complex-task benchmark tiers — small/medium/large/xlarge, USD budget enforcement, parallel bench runner, SWE-bench Verified subset import
- Owner: architect agent (design) → developer/tester agents (delivery)
- Created: 2026-07-22
- Last Updated: 2026-07-22
- Status: **In Progress** (10/11 delivered; PLAN Run 1 medium done $17.05 all-6/6-saturated; large campaign executing; T-Dc1Yg7 docs pending)
- Predecessor: `E-9Qk4Zt-agent-benchmark-harness` (MVP harness, Done 2026-07-22)

## Summary
- **Goal:** extend the shipped `ao-bench` harness from a saturated cost/latency meter into a **capability** meter, by adding harder, longer, discriminating benchmark tiers where `ao`'s multi-agent orchestration can show value — while keeping the existing small suite intact, keeping every expensive run inside a hard, harness-enforced dollar budget, and making large runs finish in hours via bounded task parallelism.
- **Scope In:**
  1. **Tier model** — optional `tier` (small|medium|large|xlarge, default small) on suites + committed `benchmarks/tiers.json` of per-tier caps/knobs; `dev-core` stays byte-unchanged = the small tier.
  2. **USD budget enforcement** — per-`ao-bench run` (per-model) cost cap in the runner (`skipped_budget` status; resume-aware) + a whole-run (all-subjects) cap via a new `ao-bench campaign`.
  3. **Parallel bench runner** — opt-in `--max-parallel` (default 1 = today's serial), bounded thread pool, lock-guarded shared state + budget accounting, deterministic persisted output.
  4. **Workspace-provider seam** — `WorkspaceProvider` ABC + registry; `fixture` (default, = today's behavior) + `swebench` (git checkout at pinned `base_commit`).
  5. **Large tier** — pinned N-instance subset of SWE-bench Verified: importer + committed instance list + pinned dataset revision + a `swebench` grader (official Docker eval on the extracted patch, `resolved` = solved) with active disk cleanup. `swebench`/`datasets` are **optional deps**.
  6. **Medium tier** — a self-authored `dev-medium` suite (4–8 genuinely longer multi-file tasks, pytest/command-graded, no Docker) designed for discrimination, plus a harder 4-agent `ao-epic-plus` (plan→implement→review→fix) workflow subject.
  7. **XL tier** — defined only, `enabled:false`, documented infeasible here (disk).
  8. **Docs + ADR** — `benchmarks/README.md` tiers/budgets/how-to-run + HLD update + ADR-0009.
  9. **Run plan execution** — the concrete Phase-2 capability comparison on medium + a bounded large-tier run (see PLAN below).
- **Scope Out (still non-MVP):** inspect-ai bridge; Aider/EvalPlus imports; LLM-judge grader; a `grading_overlay` held-out-test *framework* feature (medium uses a per-fixture `.grading/` honor-system convention instead, matching `dev-core`'s `check.py` precedent); running the full 500-instance SWE-bench Verified (xlarge); multi-seed/pass@k statistical rigor; a results dashboard; nightly cron.

Design: ADR [`docs-md/adr/ADR-0009-benchmark-tiers-external-suites-budgets-parallelism.md`](../../../docs-md/adr/ADR-0009-benchmark-tiers-external-suites-budgets-parallelism.md) · HLD [`docs-md/benchmarking-framework-hld.md`](../../../docs-md/benchmarking-framework-hld.md) (to be extended by T-Dc1Yg7) · survey [`docs-md/benchmark-landscape-survey.md`](../../../docs-md/benchmark-landscape-survey.md) · predecessor ADR-0008.

## Invariants (must hold across every task)
- **SI-1:** nothing in core imports `bench/`; `ao run` works whether or not `bench/` exists. Grep-verified per task.
- **Optional external deps:** `swebench`/`datasets` live in a `[project.optional-dependencies] swebench` extra, lazy-imported inside the swebench provider/grader only. Core install + the network-free CI bench job stay unaffected.
- **Determinism:** pinned dataset revision + pinned instance list + fixed clocks in tests; persisted `run.json` task order stays id-sorted regardless of parallelism.
- **Smallest correct change:** extend `runner.py`/`spec.py`/`workspace.py`/`registries.py`; do not rewrite. `dev-core` and all existing results stay byte-unchanged.
- **Budget vs token-budget naming:** the new USD cap is `cost_budget_usd` everywhere; the pre-existing token `budget_total` is never renamed or conflated.

## Requirements (with traceability)

| ID | Requirement | Task(s) | Verify |
|----|-------------|---------|--------|
| FR-1 | Optional `tier` enum on the suite schema (default `small`); `benchmarks/tiers.json` of per-tier `{enabled, cost_budget_usd_per_run, cost_budget_usd_per_subject, default_max_parallel, default_timeout_seconds}`; `bench/tiers.py` loader; precedence CLI > tier > builtin. `dev-core` unedited. | T-Tr1Km8 | unit: default resolves to small; tiers.json validates; disabled tier refused |
| FR-2 | Per-`ao-bench run` USD cap in `run_suite`: accumulate `cost_usd`, stop scheduling at cap, record `subject_status="skipped_budget"`; resume re-attempts skipped; `cost_budget_usd` excluded from `config_fingerprint`; `--cost-budget-usd` CLI flag. | T-Bg2Wq4 | unit + integration (fake subject w/ scripted costs) |
| FR-3 | Bounded task-level parallelism in `run_suite` (`--max-parallel`, default 1); lock-guarded `tasks_dict`/cost-total/persist; budget correct under concurrency; persisted order id-sorted; `max_parallel` excluded from fingerprint. | T-Pl3Rx7 | unit (fixed-worker, injected costs) + integration |
| FR-4 | `WorkspaceProvider` ABC + `WORKSPACE_PROVIDER_REGISTRY`; `fixture` provider = current copy behavior (default when no provider declared); optional task-level `source`/`workspace_provider` field; `fixture` optional when a provider is given. | T-Wp4Nz5 | unit: fixture provider identical to today; unknown provider rejected |
| FR-5 | SWE-bench importer → committed `benchmarks/suites/swe-verified-mini/{instances.json, suite.json}` (pinned dataset revision + instance ids, tier `large`); `swebench` WorkspaceProvider checks out repo@base_commit into `ws/repo`; `swebench`/`datasets` optional extra. | T-Sw5Hd9 | unit (mocked dataset) + opt-in real (1 instance checkout) |
| FR-6 | `SweBenchGrader` (`grader.type="swebench"`): `git diff` → predictions file → official `swebench.harness.run_evaluation` in Docker → parse `resolved` → solved; per-instance timeout; **Docker-eval lock** (disk safety); delete image + prune after each instance. | T-Sg6Jf2 | unit (mocked harness) + opt-in real (1 instance solved via golden patch) |
| FR-7 | `dev-medium` suite: 4–8 multi-file, 30–90-min-of-agent-work tasks (feature-across-a-package / cross-cutting refactor / misleading-symptom bug), pytest/command-graded with per-fixture `.grading/` held-out harness; **not** saturable by one trivial `claude -p` turn; tier `medium`. | T-Md7Vc3 | validate + fake-solution green / no-op red; ≥2 subjects diverge on a real haiku smoke |
| FR-8 | `ao-epic-plus` 4-agent subject (plan→implement→review→fix) workflow/agents/reposet templates + `ao-epic-plus-sonnet.json`; uniform-model (model-clobber defect avoided). | T-Ep8Lq6 | `ao-bench validate --subject`; opt-in real run on one medium task |
| FR-9 | `ao-bench campaign --suite S --subject J ...` (whole-run USD cap from tier, stops launching subjects at cap; per-tier `make bench-medium`/`bench-large` recipes; `--enable-xlarge` gate). | T-Cm9Tb4 | CliRunner (fake subjects, scripted costs) + Make dry-run |
| FR-10 | Tests for tier/budget/parallel/provider/campaign (deterministic, network-free); swebench tests behind `swebench` marker + optional dep; CI bench job stays network-free; coverage of new `bench/` code ≥80%. | T-Ts0Xn5 | `uv run pytest tests/bench -m "not real_llm and not swebench"` |
| FR-11 | `benchmarks/README.md` (tiers/budgets/how-to-run-each-tier/spend expectations) + HLD extended + ADR-0009 → Accepted (post-impl) + learnings; reconciled to as-built incl. deviations. | T-Dc1Yg7 | docs match shipped code (grep/spot-check) |
| NFR-1 | SI-1 preserved; `swebench`/`datasets` optional + lazy; core install + network-free CI unaffected. | all | regression gate + grep + `uv run --no-extra swebench` import check |
| NFR-2 | Determinism/safety: pinned revision/instances/seeds; disk bounded + cleaned on large tier; per-task timeout; workspaces repo-local gitignored. | T-Sw5Hd9, T-Sg6Jf2, T-Bg2Wq4 | audit + opt-in real run leaves disk clean |

## HLD (condensed — full extension lands in T-Dc1Yg7)
```
ao-bench run --suite S --subject J [--max-parallel N] [--cost-budget-usd U]
  runner.run_suite:
    tier = resolve_tier(suite.tier, tiers.json)              # T-Tr1Km8
    cap  = cost_budget_usd or tier.cost_budget_usd_per_subject
    pool = ThreadPool(max_parallel or tier.default_max_parallel)   # T-Pl3Rx7; default 1 = serial
    for task in id_sorted(suite.tasks) not already recorded (or skipped_budget):
        with lock: if running_cost >= cap: record skipped_budget; continue   # T-Bg2Wq4
        ctx = materialize_workspace(task)   # WORKSPACE_PROVIDER_REGISTRY[task.source.type or "fixture"]  # T-Wp4Nz5
        sr  = SUBJECT_REGISTRY[subject.type].run(task, ctx)
        gr  = GRADER_REGISTRY[task.grader.type].grade(task.grader, ctx)   # "swebench" grader → Docker eval  # T-Sg6Jf2
        with lock: tasks_dict[task.id]=metric; running_cost += sr.cost_usd or 0; persist run.json (id-sorted)

ao-bench campaign --suite S --subject J1 --subject J2 ...   # T-Cm9Tb4
  whole_cap = tier.cost_budget_usd_per_run
  for subject in subjects:
      if cumulative_spend >= whole_cap: mark subject skipped_budget; continue
      rec = run_suite(S, subject, cost_budget_usd = min(remaining, tier.per_subject))
      cumulative_spend += rec.aggregate.total_cost_usd
  report → comparison.{json,md}
```
`bench/` imports core helpers read-only; nothing in core imports `bench/` (SI-1). SWE-bench = a `WorkspaceProvider` + a `Grader` behind the ADR-0008 seam; its deps are an optional extra.

## Sprint plan & capacity
Team profile: developers with <4 yrs experience; 2-week sprints (5-day weeks); 40% overhead. `team_size = 2` (one developer + one tester, matching this repo's epic staffing and the predecessor epic).
```
GrossHoursPerSprint      = 2 * 10 * 8        = 160
NetFocusHoursPerSprint   = 160 * 0.60        = 96      (12 focus-days)
CommitmentHoursPerSprint = 96 * (0.70..0.85) = 67.2 .. 81.6
```
Task sizing (person-days → hours at 8h/d):
T-Tr1Km8 1.5(12) · T-Bg2Wq4 2.5(20) · T-Pl3Rx7 2.5(20) · T-Wp4Nz5 2.0(16) · T-Sw5Hd9 3.0(24) · T-Sg6Jf2 3.0(24) · T-Md7Vc3 3.0(24) · T-Ep8Lq6 1.5(12) · T-Cm9Tb4 2.0(16) · T-Ts0Xn5 2.5(20) · T-Dc1Yg7 1.5(12) = **25.0 person-days ≈ 200 gross hours**.

**Recommendation: three 2-week sprints**, justified — 200h of task work ÷ ~75h commitment/sprint ≈ 2.7 sprints, so three (the last lightly loaded, holding docs + the buffer for SWE-bench Docker/disk flakiness and the real run-plan execution). Two sprints (134–163h capacity) cannot hold 200h, especially with the novel Docker/disk risk in the large tier.
- **Sprint 1 (foundation, ~10.0 d):** T-Tr1Km8 → {T-Bg2Wq4 ∥ T-Wp4Nz5 ∥ T-Ep8Lq6} → T-Pl3Rx7. The tier model + budget + parallel runner + provider seam + the (independent) ao-epic-plus subject. Everything the two tiers below build on.
- **Sprint 2 (tiers, ~9.0 d):** T-Sw5Hd9 → T-Sg6Jf2 (large tier, highest risk — front-loaded in this sprint) ∥ T-Md7Vc3 (medium suite, independent dir). 
- **Sprint 3 (integrate + ship + run, ~6.0 d + run):** T-Cm9Tb4 → T-Ts0Xn5 → T-Dc1Yg7, then execute the PLAN run matrix. Contingency: if the large-tier real run is disk/flaky, its blocking part is "importer + grader pass their mocked/opt-in-single-instance tests"; the full 10-instance run trails into buffer.

## Task List (dependency-ordered; each ≤3 days)
- [x] `T-Tr1Km8-tier-model-budgets-config` (1.5d) — `tier` schema field + `benchmarks/tiers.json` + `bench/tiers.py`. **Deps: none.** Parallel-safe with: T-Ep8Lq6.
- [x] `T-Bg2Wq4-usd-budget-enforcement` (2.5d) — per-run USD cap + `skipped_budget` + resume rule in `runner.py`/`subjects.py`/`cli.py`. **Deps: T-Tr1Km8.** Parallel-safe with: T-Wp4Nz5, T-Ep8Lq6.
- [x] `T-Pl3Rx7-parallel-bench-runner` (2.5d) — `--max-parallel` thread pool + lock-guarded budget/state in `runner.py`/`cli.py`. **Deps: T-Bg2Wq4 (same file; must be concurrency-correct over budget).**
- [x] `T-Wp4Nz5-workspace-provider-seam` (2.0d) — `WorkspaceProvider` ABC+registry + `fixture` provider + `source` field. **Deps: T-Tr1Km8 (spec.py/schema sequencing).** Parallel-safe with: T-Bg2Wq4.
- [x] `T-Sw5Hd9-swebench-import-provider` (3.0d) — importer + pinned instances + `swebench` provider + optional extra. **Deps: T-Wp4Nz5, T-Tr1Km8.** Parallel-safe with: T-Md7Vc3, T-Pl3Rx7.
- [x] `T-Sg6Jf2-swebench-grader-docker` (3.0d) — `SweBenchGrader` (Docker eval + cleanup + predictions bridge + Docker lock). **Deps: T-Wp4Nz5 (spec KNOWN_GRADER_TYPES/registries sequencing), T-Sw5Hd9 (optional extra).** Parallel-safe with: T-Md7Vc3, T-Pl3Rx7.
- [x] `T-Md7Vc3-dev-medium-suite` (3.0d) — `benchmarks/suites/dev-medium/` 4–8 discriminating tasks. **Deps: T-Tr1Km8.** Parallel-safe with: T-Sw5Hd9, T-Sg6Jf2, T-Pl3Rx7 (new dir, no code overlap).
- [x] `T-Ep8Lq6-ao-epic-plus-subject` (1.5d) — 4-agent plan→implement→review→fix `ao_workflow` subject. **Deps: none** (all new files). Parallel-safe with: everything.
- [x] `T-Cm9Tb4-campaign-tier-recipes` (2.0d) — `ao-bench campaign` (whole-run cap) + `make bench-medium/bench-large` + `--enable-xlarge`. **Deps: T-Bg2Wq4, T-Pl3Rx7, T-Tr1Km8.**
- [x] `T-Ts0Xn5-tier-budget-parallel-tests` (2.5d) — tests for tier/budget/parallel/provider/campaign; swebench tests behind marker+extra; CI stays network-free. **Deps: T-Cm9Tb4 (+ all features it tests).**
- [ ] `T-Dc1Yg7-docs-adr0009-reconcile` (1.5d) — README/HLD/ADR-0009→Accepted/learnings, reconciled to as-built (**post-implementation docs-refresh, mandatory**). **Deps: all.**

### Parallel-safe execution waves (for parallel subagents — file-ownership is disjoint within a wave)
- **Wave A:** T-Tr1Km8, T-Ep8Lq6.
- **Wave B (after T-Tr1Km8):** T-Bg2Wq4 (runner/subjects/cli), T-Wp4Nz5 (workspace/spec/registries), T-Md7Vc3 (new suite dir). Disjoint files → safe in parallel.
- **Wave C:** T-Pl3Rx7 (after T-Bg2Wq4, same runner.py), T-Sw5Hd9 (after T-Wp4Nz5), T-Sg6Jf2 (after T-Wp4Nz5). T-Sw5Hd9 & T-Sg6Jf2 both touch `registries.py`/`spec.py` closed-lists → **sequence T-Sw5Hd9 → T-Sg6Jf2** (or coordinate); both are parallel-safe with T-Pl3Rx7.
- **Wave D:** T-Cm9Tb4 (after Wave C, edits cli.py after T-Pl3Rx7).
- **Wave E:** T-Ts0Xn5, then T-Dc1Yg7.

## PLAN — concrete Phase-2 run matrix (execute after the framework lands; T-Cm9Tb4/T-Dc1Yg7)

> Estimates are engineering forecasts anchored to the real `dev-core` numbers (bare-`claude` sonnet $0.20/task, opus $0.26/task; `ao-epic`-sonnet $0.48/task on *trivial* tasks) scaled for task complexity and turn count. Actuals will vary (A5 stochasticity); the **harness-enforced caps are the hard ceiling** — the plan is designed to land well under them, not to spend the cap.

### Run 1 — Medium tier (the headline capability comparison)
Suite: `dev-medium` (6 tasks, 30–90 min agent-work each). Subjects: **`claude-sonnet`, `claude-opus`, `ao-epic-sonnet`** (the user-required minimum) **+ `ao-epic-plus-sonnet`** (4-agent, to show orchestration headroom). `--max-parallel 4`.

| Subject | Est. $/task | Est. total (6 tasks) | Per-subject cap (medium) |
|---|---|---|---|
| `claude-sonnet` | ~$1.5 | ~$9 (range $6–15) | $50 |
| `claude-opus` | ~$3 | ~$18 (range $12–36) | $50 |
| `ao-epic-sonnet` (2-agent) | ~$3 | ~$18 (range $12–36) | $50 |
| `ao-epic-plus-sonnet` (4-agent) | ~$5 | ~$30 (range $18–54) → capped $50 | $50 |
| **Medium whole-run** | — | **~$75 (range $48–141)** | run cap $150 |

Expected wall-clock: with `--max-parallel 4`, ~1.5–2 h/subject; 4 subjects run sequentially (rate-limit hygiene) ≈ **6–8 h**. What we learn: whether the multi-agent workflows lift **solve rate** (not just cost) once tasks are hard enough that a single `claude -p` turn misses edge cases — the claim `dev-core` could not test.

### Run 2 — Large tier (external credible signal, bounded)
Suite: `swe-verified-mini` (**N = 10** pinned SWE-bench Verified instances; mixed repos, biased to smaller images / faster test suites). Subjects: **`claude-sonnet`, `claude-opus`, `ao-epic-sonnet`**. Grading cost = **$0** (Docker, no LLM). `--max-parallel 3` for agent execution; Docker eval serialized.

| Subject | Est. $/instance | Est. total (10) | Per-subject cap (large) |
|---|---|---|---|
| `claude-sonnet` | ~$2.5 | ~$25 (range $15–50) | $100 |
| `claude-opus` | ~$7 | ~$70 (range $40–120) → capped $100 | $100 |
| `ao-epic-sonnet` | ~$6 | ~$60 (range $40–120) → capped $100 | $100 |
| **Large whole-run** | — | **~$155 (range $95–270)**, hard ceiling $300 (3×$100) / run cap $800 | run cap $800 |

Expected wall-clock: agent ~10 min/instance (parallel×3) + Docker eval ~10 min/instance (serialized, disk safety) + one-time image pulls (~1–4 min each, distinct instances) ≈ **~8–12 h** for the full 3-subject run. Disk: peak ≈ 1 live image + build cache (~3–5 GB) + repo cache (~few GB), cleaned per instance → stays under ~37 GB.

### Total recommended run-plan spend
**~$230 expected** (Run 1 ~$75 + Run 2 ~$155), hard-ceilinged far below the $800/whole-large-run and $100/model envelopes. We deliberately do **not** spend the cap — N and subject count are tuned for signal, not for burn. Scaling levers if the user wants more signal: raise large-tier N to ~15 (adds ~$75–150) or add `haiku`/`ao-epic-plus` subjects (each within its own $100 cap).

## Risks and Dependencies
- **R1 — Disk exhaustion on the large tier (37 GB).** Mitigation: serial Docker eval behind a lock; `docker image rm` + build-cache prune after every instance; N pinned small; xlarge disabled. Opt-in real test asserts disk returns to baseline. (T-Sg6Jf2)
- **R2 — SWE-bench harness / Docker flakiness & host variance.** Mitigation: pinned dataset revision + instance list; per-instance timeout; `resolved` status is authoritative; a failed/errored eval records `subject_status` distinctly and never poisons other instances (per-task isolation already in the runner). (T-Sg6Jf2, T-Sw5Hd9)
- **R3 — Budget × parallelism correctness.** Mitigation: budget check under the same lock as the running-cost total; check-before-schedule; bounded overshoot (≤ max_parallel in-flight) documented; fixed-worker deterministic unit tests with injected costs. (T-Bg2Wq4, T-Pl3Rx7)
- **R4 — Medium suite saturates anyway (both subjects 6/6) or is trivially gameable.** Mitigation: multi-file fixtures + misleading-symptom bug + per-fixture `.grading/` held-out harness the instruction forbids touching; gate authoring on a real haiku smoke showing ≥1 task where subjects diverge before committing. (T-Md7Vc3)
- **R5 — Optional-dep leakage breaks core/CI.** Mitigation: `swebench`/`datasets` imported only inside the swebench provider/grader function bodies; a CI/import test asserts `agent_orchestrator` (and `ao-bench validate` on a non-swebench suite) work with the extra NOT installed; SI-1 grep. (T-Sw5Hd9, T-Ts0Xn5)
- **R6 — Model-override clobber (known core defect).** `ao-epic-plus` agents set **no** per-agent `model`; the subject's `model` flows uniformly via `AO_MODEL` (same sidestep as `ao-epic`). (T-Ep8Lq6)
- **Reuses (import-only, unchanged):** `executors.claude_cli.parse_usage_and_429`, `models.compute_run_usage_totals`, `runstate`, `logging_setup`; existing `bench/` modules. **No** engine/core-schema edit (SI-1/NFR-1).

## Open questions (need user/orchestrator resolution)
- **OQ-1 (medium tier caps):** architect set medium `cost_budget_usd_per_run=$150`, `per_subject=$50` (user specified only small $10 / large $800+$100). Confirm or adjust.
- **OQ-2 (large N + subject set):** architect recommends N=10 instances × {sonnet, opus, ao-epic-sonnet} (~$155 expected). Confirm N and whether to include `ao-epic-plus-sonnet` / `haiku` on the large tier (each stays within its own $100 cap; adds cost + wall-clock).
- **OQ-3 (agent inside Docker vs host checkout):** design runs the *agent* on a host checkout and uses Docker for *grading only* (official harness on the extracted `git diff`). Running the agent *inside* the per-instance container (closer to canonical SWE-bench agent harnesses) is a larger lift (mount `claude`/auth) and out of scope — confirm host-checkout grading is acceptable.
- **OQ-4 (SWE-bench install auth/network):** the large tier needs network for the one-time dataset + image pulls; confirm the run host has outbound access at run time (CI stays network-free regardless).

## Links
- ADR: [`docs-md/adr/ADR-0009-benchmark-tiers-external-suites-budgets-parallelism.md`](../../../docs-md/adr/ADR-0009-benchmark-tiers-external-suites-budgets-parallelism.md)
- HLD (extended by T-Dc1Yg7): [`docs-md/benchmarking-framework-hld.md`](../../../docs-md/benchmarking-framework-hld.md)
- Predecessor epic: [`meta/tickets/E-9Qk4Zt-agent-benchmark-harness/EPIC.md`](../E-9Qk4Zt-agent-benchmark-harness/EPIC.md)
- Output artifacts (produced by the PLAN run): committed under `benchmarks/results/` (`dev-medium-*`, `swe-verified-mini-*`)
