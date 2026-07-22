# ADR-0009 — Benchmark tiers, external-suite import (SWE-bench Verified), USD budget enforcement, and parallel bench runner

- Status: **Proposed**
- Date: 2026-07-22
- Deciders: Avadhoot Divekar (user), Claude (architect role)
- Related: builds directly on **ADR-0008** (thin `ao`-native harness with explicit `Subject`/`Grader`/`Suite` import seams reserved for SWE-bench/inspect-ai) · epic [`E-Bt4Xk9-complex-benchmark-tiers`](../../meta/tickets/E-Bt4Xk9-complex-benchmark-tiers/EPIC.md) · design [`benchmarking-framework-hld.md`](../benchmarking-framework-hld.md) · survey [`benchmark-landscape-survey.md`](../benchmark-landscape-survey.md) · ADR-0007 (core parallel wave/barrier scheduler — a *different* parallelism from the one decided here) · ADR-0003 (settings precedence — bench knobs ride the invocation chain)

## Context

The MVP benchmark harness (ADR-0008, epic `E-9Qk4Zt`) shipped and its `dev-core` suite was run for real against 5 subjects. The Phase-2 finding: **`dev-core` is saturated** — every subject solves 6/6, so it measures only cost/latency, not capability. Every task is a "spot fix" solvable by one bare `claude -p` turn, which is exactly where an orchestrated multi-agent workflow *cannot* show value (no headroom for a verify/retry loop to pay for itself).

The user's ask: **long-duration, complex-task benchmarks** where `ao`'s multi-agent orchestration is the differentiator, with a spend envelope (≤$10 per small run, ≤$100 per model, ≤$800 per whole large run), an xlarge tier defined but disabled, and the existing small suite kept intact. Verified environment facts that constrain this (treated as ground truth from the orchestrator's probing today): Docker 28.3.0 works (32 cores / 62 GB RAM) but **only ~37 GB free disk** (a full 500-instance SWE-bench eval is infeasible; a 10–30-instance subset with aggressive image cleanup is feasible); the `swebench` pip package and `princeton-nlp/SWE-bench_Verified` dataset resolve; per-image Docker pulls are ~1 GB+ / >4 min; the runner is currently **serial**, so a large tier (N instances × several subjects × 5–20 min each) is intractable without task-level parallelism.

This ADR records four coupled decisions that this epic delivers. All preserve **SI-1** (nothing in core imports `bench/`) and the smallest-correct-change bias (extend `runner.py`/`spec.py`/`workspace.py`, do not rewrite).

---

## D1 — Tier model: an optional `tier` field on suites + a committed `benchmarks/tiers.json` of per-tier defaults

**Decision.** Add an **optional** `tier` enum (`small` | `medium` | `large` | `xlarge`, default `small`) to the suite schema, and a single committed declarative config `benchmarks/tiers.json` mapping each tier → default caps and knobs: `enabled`, `cost_budget_usd_per_run`, `cost_budget_usd_per_subject`, `default_max_parallel`, `default_timeout_seconds`.

- `dev-core` stays **byte-unchanged** — `tier` defaults to `small`, so no edit to the committed small suite (satisfies "keep the older smaller versions — those are required").
- Per-tier defaults live in JSON, not Python constants (repo rule: all orchestration metadata in structured files). The runner/CLI read `tiers.json`; precedence is **CLI flag > tier default (from `tiers.json`) > builtin fallback**.
- `xlarge.enabled = false`; a suite whose resolved tier is disabled refuses to run without an explicit `--enable-xlarge` (or `enabled:true` edit).

**Reason over alternatives.** A `tier` *label* + external defaults table beats (a) hard-coding tier caps in code (violates the config-driven rule, and re-tuning a cap should not be a code change/release), and (b) putting caps inline on every suite (duplicates the policy across suites; a suite says *what tier it is*, the tier says *what that costs*). Optional-with-default keeps every existing suite and test valid.

**Consequences.** One new tiny module (`bench/tiers.py`) loads/validates `tiers.json`; a new committed data file; a one-line optional schema field. No behavior change for existing runs.

## D2 — External suite: import a **pinned subset of SWE-bench Verified** (not Aider-polyglot) for the large tier

**Decision.** The large tier is a **pinned N-instance subset of `princeton-nlp/SWE-bench_Verified`** (dataset revision pinned; instance-id list committed to git as `benchmarks/suites/swe-verified-mini/instances.json`). An importer materializes each selected instance into ao-bench task shape (workspace = the instance's repo checked out at `base_commit`; `problem_statement` → the task instruction), and grading uses the **official `swebench` evaluation harness in Docker** on the patch extracted from the mutated workspace (`git diff`). "Solved" = the official `resolved` status. `swebench` + `datasets` are **optional dependencies** (extras group), lazy-imported, so core install and the network-free CI job are untouched.

**Options evaluated.**
- **SWE-bench Verified subset (chosen).** Human-validated, MIT, the de-facto agentic-dev leaderboard; its *fixture-repo + repo's-own-test-suite-as-grader* shape maps 1:1 onto our `PytestGrader`/fixture model (already the D2 seam target named in ADR-0008); the official harness gives an authoritative `resolved` verdict we do not have to re-derive. Its cost is Docker/disk-heavy, but a **small pinned subset with delete-after-each-instance cleanup fits ~37 GB**, and the *full* 500-instance run is what the disabled xlarge tier is for.
- **Aider polyglot (rejected as the primary large suite).** Also test-graded and cost-aware (a good conceptual match), but importing it *for signal* needs **six language toolchains** (C++, Go, Java, JS, Python, Rust) installed on the host — a heavier, flakier setup than SWE-bench's per-instance Docker images, and it measures whole-file edit correctness on self-contained exercises rather than real repo-scale issue resolution. A handful of its Python exercises remain a fine *future medium-tier* import, but it is not the large-tier headline.
- **terminal-bench / inspect-ai SWE-bench bridge (rejected for now).** Both are Docker/framework-heavy and add a second harness to own; the official `swebench` harness is the smallest path to an authoritative verdict.
- **Bigger self-authored suite instead of any external one (rejected).** We keep authoring a *medium* tier (D-adjacent, see epic T-Md7Vc3) for tasks we fully control, but an external, contamination-audited, independently-leaderboarded suite is the credible capability signal the user is asking for; self-authoring 500 hard tasks is not viable.

**Reason.** SWE-bench Verified is the lowest-friction path to a real, externally-credible "can the agent resolve a genuine repo issue" number, it reuses the exact seam ADR-0008 reserved, and — critically for this environment — it is the only heavyweight option that degrades cleanly to a *tiny pinned subset* under a hard disk cap.

**Consequences.** New optional deps and a Docker dependency for the large tier only. A `WorkspaceProvider` seam (D-below via the epic) is needed because the "fixture" is now a git checkout, not a committed dir. Disk must be actively managed (image cleanup after every instance). Determinism is bounded: dataset revision + instance list + base commits are pinned, but agent scores remain LLM-stochastic (ADR-0008 A5) and Docker/host variance exists (documented in the SWE-bench ecosystem).

## D3 — USD budget enforcement: a new cost budget distinct from the existing token `budget_total`

**Decision.** Introduce **USD cost budgets** enforced by the harness itself, kept strictly separate from the pre-existing `budget_total` (a *token* budget that the bench merely threads into `ao run --budget-total`). Two levels:
1. **Per-`ao-bench run` (one suite × one subject) cost cap** — `run_suite` accumulates each task's `SubjectResult.cost_usd`; **before scheduling a new task**, if cumulative recorded cost ≥ the cap, it stops scheduling and records each remaining task with a distinct `subject_status = "skipped_budget"` (solved=false, score=0, cost=0). Default cap = the tier's `cost_budget_usd_per_subject`; CLI `--cost-budget-usd` overrides. This is the primitive that enforces "$100 per model".
2. **Whole-run (all subjects) cost cap** — a new thin `ao-bench campaign` command runs a suite against a *list* of subjects, tracks cumulative spend across subjects, and stops launching further subjects once the tier's `cost_budget_usd_per_run` is reached. This enforces "$800 per whole large run" / "$10 per small run".

Semantics: **check-before-schedule**, so overshoot is bounded to at most one in-flight task per worker (documented, not zero). `skipped_budget` is **not** a harness failure — it never trips the CLI's non-zero exit (`_HARNESS_FAILURE_STATUSES` excludes it). **Resume interaction:** on a resume, cumulative cost is recomputed from existing *non-skipped* records, and any `skipped_budget` records (plus never-run tasks) are re-attempted under the current cap — so raising the cap and re-running finishes the suite. `cost_budget_usd` (and `max_parallel`, D4) are **excluded from `config_fingerprint`** — they are control-flow (how many tasks run / how fast), not config that changes what a subject *does* per task, so raising the cap and resuming must not trip the W4 fingerprint-mismatch guard.

**Reason over alternatives.** Enforcing a hard *dollar* stop in the harness (vs. relying only on the token `budget_total` passed to `ao run`, which does not exist for the bare-`claude` subject and does not aggregate across a suite/campaign) is the only way to guarantee the user's spend envelope for *every* subject type. A distinct `skipped_budget` status (vs. reusing `error`) keeps budget stops out of the failure-signal so a partially-run, budget-capped suite is a clean, resumable outcome, not a red run.

**Consequences.** A new status string threads through `SubjectResult`/`TaskMetric`/CLI exit logic. The two-level split (per-run primitive + campaign aggregate) maps directly onto the user's two numbers without a monolithic new orchestrator.

## D4 — Parallel bench runner: bounded thread pool, `--max-parallel` (default 1 = today's serial behavior)

**Decision.** Add opt-in **task-level parallelism** to `run_suite` via a bounded `ThreadPoolExecutor` (`--max-parallel N`, default 1 = byte-identical to today's serial path). Tasks are independent (no DAG — unlike the core engine's ADR-0007 wave/barrier scheduler, which this deliberately does **not** reuse; bench tasks are embarrassingly parallel). Per-task workspaces are already disjoint (`<run>/<subject>/<task-id>/`), so there is no filesystem contention. Shared mutable state — the `tasks_dict`, the running cost total, and the atomic `run.json` persist — is guarded by a single lock; the budget check (D3) is performed under that lock before each dispatch, so it stays correct under concurrency. Persisted output stays deterministic (the `tasks` list is always sorted by id before writing), even though completion *order* is not. For the SWE-bench/large tier, the `SweBenchGrader` serializes its Docker-eval step behind a module-global lock (disk safety on ~37 GB), so agent execution can parallelize while grading stays one-at-a-time.

**Reason over alternatives.** Threads (not processes) because each task is subprocess/IO-bound (`claude`/`ao`/Docker spawn — the GIL is released during the wait), so threads give the concurrency with trivially shared state and no pickling. Default 1 preserves the current serial contract exactly (no surprise behavior change; opt-in per ADR-0007's own "default off" precedent). Reusing the core wave/barrier scheduler was rejected — it solves DAG dependencies the bench does not have, and importing it would couple `bench/` to engine internals (SI-1 risk) for no benefit.

**Consequences.** One new concurrency path in `runner.py`; a lock and a documented bounded-overshoot/nondeterministic-which-tasks-get-skipped interaction with D3. Wall-clock for medium/large tiers drops from intractable to hours.

---

## Positioning (anti-feature-creep — extends ADR-0008's)
```
MATCH  SWE-bench Verified as an externally-credible, contamination-audited capability
       signal — imported behind the ADR-0008 Subject/Grader/Suite seam, not re-implemented.
BEAT   every harness in cost-bounded, apples-to-apples "ao workflow vs bare claude -p"
       comparison — now on tasks HARD ENOUGH that a multi-agent verify/retry loop can pay
       for itself (medium tier) and on real repo issues (large tier), with a HARD dollar
       budget the harness itself enforces per-model and per-whole-run.
AVOID  the full 500-instance SWE-bench run (xlarge — defined, DISABLED, infeasible on 37 GB
       here), a second heavyweight harness (inspect/terminal-bench), six-language toolchains
       (Aider-polyglot), and any core-engine coupling for parallelism. External deps stay
       OPTIONAL; core install and the network-free CI job are untouched.
```

## Consequences summary
- **+** A discriminating medium tier and an externally-credible large tier turn the harness from a cost/latency meter into a capability meter — the differentiator the user asked for.
- **+** Hard, harness-enforced dollar budgets make expensive runs safe to launch (auto-stop at the cap, resumable).
- **+** Parallelism makes medium/large tiers finish in hours, not days.
- **+** SI-1 preserved; `swebench`/`datasets` optional; CI stays network-free; `dev-core` untouched.
- **−** The large tier owns real operational burden (Docker, disk cleanup, image-pull time, host variance) — the price of a credible external signal; contained to the large tier and its optional deps.
- **−** Budget × parallelism introduces bounded, documented nondeterminism in *which* tasks get `skipped_budget` near the cap (scores were already stochastic per A5).
- **Follow-ons (still non-MVP):** inspect-ai bridge; Aider/EvalPlus Python-exercise medium imports; a held-out-test `grading_overlay` framework feature if the medium suite's per-fixture `.grading/` honor-system proves gameable; multi-seed/pass@k statistical rigor; a results dashboard; nightly cron.
