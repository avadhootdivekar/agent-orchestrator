# EPIC: E-Rc4Hk8-cross-run-result-cache

## Metadata
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Title: `Double-opt-in, content-addressed cross-run result cache (reuse identical successful task results instead of re-dispatching agents)`
- Owner: `manager` (execution) · design by `architect`
- Created: `2026-10-04`
- Last Updated: `2026-10-05`
- Status: `Draft` — **Design complete (Rev 3) / Not started**: the HLD/LLD, ADR-0019 and 20 task
  tickets are ready; no task has started.

## Summary
- **Goal.** A task in any run of a workspace can reuse the declared output files of an earlier,
  successful, *identical* task (content-addressed key) instead of dispatching the agent again.
  This saves real money on re-runs, clean-checkout runs and fresh-run retries.
- **Off by default, double opt-in.**
  - The **operator** sets the mode with `--cache/--no-cache`, `AO_CACHE` or `cache.enabled`.
    Modes: `on`, `shadow` (measure only), `off`.
  - The **workflow author** opts tasks in with `defaults.cache` or `tasks[].cache`. The default
    is one named constant, `DEFAULT_TASK_CACHE_POLICY = False`, the single flip point if the
    parent prefers "operator enabled ⇒ every eligible task cached".
- **Safety and no-op.** Safe under parallelism, resume, concurrent runs and hostile cache data; a
  planted symlink is never followed. With the mode off, the engine executes and imports no cache
  code, and the read sides import `cache.report` only for runs that have records.
- **Scope In:**
  - **New package `agent_orchestrator.cache`:** constants, `safeio`, types/contracts, settings,
    hashing, repo_state, fingerprint, keys, eligibility, `LocalFsCacheStore`, restore/capture,
    coordinator, records, report, and the `ao cache` CLI (`ls`, `stats`, `show`,
    `rm <key|prefix>`, `prune`, `clear`, read-only `verify`).
  - **Behaviour-identical extraction** of `build_claude_argv` in `executors/claude_cli.py`.
  - **Additive hooks** in `models.py`, `specs/workflow.schema.json`, `project_config.py`,
    `engine.py` (net ≤ +110 formatted lines, ≤ 12 added lines inside existing functions),
    `cli.py`, `runstate.py`, `usage.py`, `outcomes.py`, `ui/runs.py`, `ui/files.py` plus 2
    frontend files, `bench/subjects.py`, and one CI step in `.github/workflows/ci.yml`.
  - **Tests:** unit, integration, e2e, adversarial, the hostile corpus, tripwires, a no-op proof,
    and a hard coverage gate (package ≥ 85%, core modules ≥ 90%).
  - **The G0 protocol and tooling hand-off** (`docs-md/result-cache-g0-protocol.md`).
  - **Docs:** the HLD, ADR-0019 and the docs refresh.
- **Scope Out:**
  - **Executing G0** on a real consumer workflow: a post-merge follow-up owned by the parent or
    operator (finplan, with consent). It does not block epic closure.
  - **Deferred in Rev 3:** the `refresh` mode, `ao cache rm --run/--task`, `ao cache verify
    --repair`.
  - **Non-MVP (HLD §2.3):** remote/shared/S3 backends; HMAC entries; `dir_fd`-walking I/O; the
    executor-level `CachingExecutor` (ALT-7); `--reuse-from` (ALT-8); caching under isolation,
    active integration or hooks; directory and dynamic outputs; dependency-aware invalidation and
    warming; resolved-model recording; user-level context fingerprinting; hash memoization;
    per-entry hit counters; the `ao validate` warning; `ao cache explain`; dashboard launch
    controls; the separate `dispatch_cycle` bug ticket.

## Design
- HLD + LLD: [`docs-md/cross-run-result-cache-hld.md`](../../../docs-md/cross-run-result-cache-hld.md), §0–§25, Rev 3
- ADR: [`docs-md/adr/ADR-0019-cross-run-result-cache.md`](../../../docs-md/adr/ADR-0019-cross-run-result-cache.md), Rev 3, decisions D1–D35, alternatives ALT-1…ALT-8
- Decision log: HLD §7.6 (D1–D35). Threat model: HLD §7.7 (M-1…M-16 plus residuals).
- Reviews: Phase 4 in HLD §23.3–§23.4 (61 dispositions); Rev 3 early-gate review in HLD §23.5.
- Merge notes for sibling epics E-Ag7Pw3 and E-Da5Tn9: HLD §24.2
- Golden key vector GV-1 (Rev 2, unchanged in Rev 3): `6646469e94a695fe1a994d35f54ca74e007262911255552b03c54ce2e5d0319f` (HLD §8.2.7)

## Requirements
The full table with verification methods is in HLD §1.3.

- FR-1: The run-level **mode** (`on|shadow|off`) is resolved `--cache/--no-cache` > `AO_CACHE` >
  `cache.enabled` + `cache.mode` > off, through ONE helper shared by `ao run` and `ao resume`.
  Any other env value (including `refresh`) means off, with a warning.
- FR-2: Author opt-in: `task.cache` > `defaults.cache` > `DEFAULT_TASK_CACHE_POLICY` (False). An
  injected task's `true` is ignored.
- FR-3: Key schema v1 (agent projection, argv, executor fingerprint, prompt, content digests,
  `[{path, prior}]` outputs, repo heads); task id excluded; GV-1 pins it.
- FR-4: Fail-closed eligibility with tripwires and runtime unknown-field rules for `TaskSpec`,
  **`AgentSpec`**, `WorkflowSpec` and `WorkflowDefaults`.
- FR-5: Lookup after skip, isolation, join, missing-inputs and dynamic-input steps; before the
  budget gate; the engine settles a hit itself.
- FR-6: Store only a final settled success, behind three guards; guard 3 is lazy (storable
  outcomes only) and its failure makes the outcome not storable, never ineligible.
- FR-7: Provisional `CacheStore` + `CacheAdmin` ABCs and `LocalFsCacheStore`; unsafe paths are
  never followed or evicted.
- FR-8: Safe restore and capture.
- FR-9: Accounting: `saved_*` separate; hits touch no `cumulative_*`, budget counters (except
  reversing a stale previous-cycle charge) or breakers; usage keeps a hit's real carried spend.
- FR-10: Resume keeps a restored task `succeeded`.
- FR-11: Observability with the exact fields `hit`, `key`, `saved_cost_usd`, `saved_tokens`,
  `saved_seconds` inside `result_cache` objects.
- FR-12: `ao cache ls|stats|show|prune|clear|verify|rm` with `--json`; `verify` read-only.
- FR-13: Dashboard tag and tile; the file browser refuses `.orchestrator/cache`.
- FR-14: `ao-bench` forces the cache off.
- FR-15: Config knobs with bounds; `ao init` template.
- FR-16: Shadow mode.
- FR-17: `ao cache rm <key|prefix>`.
- NFR-1: No-op when off, literally (no cache module beyond `cache` + `cache.constants` in a
  cache-off engine process).
- NFR-2: `engine.py` stays content-free. NFR-3: deterministic. NFR-4: bounded work (inline prune
  bounded by entries and bytes). NFR-5: cross-process safe. NFR-6: compatible. NFR-7: no magic
  literals. NFR-8: small additive edits, ≤ 100 columns. NFR-9: coverage gates. NFR-10: threat
  mitigations M-1…M-16. NFR-11: hostile data never raises.

## Task List

The manager runs the tasks **sequentially through agents**, grouped by cohesion (HLD §22.3). The
staffed sprint equivalent is in HLD §22.1. File scopes are exclusive; tasks that share a file are
ordered by dependency (HLD §22.2).

### 1. Core set, then gate G1a (key, store and restore core)
- [ ] `T-FJH6LI-cache-contracts`: constants (commit 1), `safeio` (commit 2), types/contracts/ABCs/errors/fakes, corpus, AST guard (commit 3). 16 h. Dev A. Deps: none.
- [ ] `T-28J9oR-cache-spec-config-surface`: spec, config, record model (derived `hit`/`saved_tokens`), settings with the named default, CLI flag (resolution half), `ao cache` group. 16 h. Dev B. Deps: T-FJH6LI commit 1.
- [x] `T-OeRYSO-executor-argv-builder`: pure `build_claude_argv` extraction. 6 h. Dev C. Deps: none (may run any time; independent file scope). **Done** (2026-10-05) Commit ac35e73.
- [ ] `T-QgQy08-cache-eligibility`: allowlist predicate, tripwires, runtime rules incl. `unknown_agent_field`. 13 h. Dev B. Deps: T-28J9oR, T-FJH6LI.
- [ ] `T-8tr1H4-cache-hashing`: bounded hashing; repository detection bounded by the workspace root; HEAD reader; worktree probe. 14 h. Dev C. Deps: T-FJH6LI (commit 3).
- [ ] `T-uoYW6b-cache-key-builder`: fingerprint, keys, GV-1. 20 h. Dev A. Deps: T-FJH6LI, T-OeRYSO, T-8tr1H4.
- [ ] `T-U7ckfd-cache-store-core`: `LocalFsCacheStore(CacheStore)`, checks before every operation, `CacheUnsafePathError`, `is_expired`. 17 h. Dev C. Deps: T-FJH6LI.
- [ ] `T-u3jG8F-cache-restore-capture`: staged, verified restore and safe capture. 16 h. Dev B. Deps: T-FJH6LI.
- [ ] `T-HjxNQ0-cache-store-maintenance`: adds `CacheAdmin`; streaming iteration, prune, inline enforcement bounded by entries and bytes, `clear`, read-only `verify`, race test. 15 h. Dev C. Deps: T-U7ckfd.
- [ ] Gate **G1a** (`T-fXWbqg`).

### 2. Engine set, then gate G1b (coordinator, engine, CLI wiring, reporting)
- [ ] `T-JCOAsq-cache-test-hardening` **Part 1** (golden, I-2, I-1; base code only; may run any time before T-XpF1pF). 6 h. Tester.
- [ ] `T-gDNjN2-cache-coordinator`: `ResultCache` (shadow, lazy guard 3, unsafe-path handling, boundary) and records builders. 20 h. Dev A. Deps: T-uoYW6b, T-QgQy08, T-U7ckfd, T-u3jG8F, T-8tr1H4, T-28J9oR.
- [ ] `T-XpF1pF-cache-engine-integration`: engine seams, shared `_reverse_stale_charge`, integration tests. 16 h. Dev A. Deps: T-gDNjN2, T-JCOAsq Part 1.
- [ ] `T-eyn5UG-cache-reporting`: report helpers; `status.json`, usage (both sites, `result_cache` object) and outcomes hooks; lazy imports. 18 h. Dev B. Deps: T-28J9oR, T-FJH6LI.
- [ ] `T-o95l1M-cache-cli-wiring`: CLI construction, banner and warnings, summary lines, `report-usage` lines. 8 h. Dev B. Deps: T-XpF1pF, T-eyn5UG, T-gDNjN2.
- [ ] `T-ZTxN1x-bench-cache-force-off`: bench argv and env, plus a regression test. 4 h. Dev B. Deps: T-28J9oR.
- [ ] Gate **G1b** (`T-fXWbqg`).

### 3. Surfaces
- [ ] `T-nPMuz4-cache-shadow-value-check`: **G0 protocol and tooling hand-off** (procedure, report template, smoke validation); does not execute G0. 6 h. Tester (+ manager sign-off). Deps: T-o95l1M, T-eyn5UG.
- [ ] `T-6tRKml-cache-cli-commands`: `ao cache ls|stats|show|rm|prune|clear|verify`. 17 h. Dev C. Deps: T-HjxNQ0, T-28J9oR.
- [ ] `T-bLpoze-cache-dashboard-surface`: payload fields, file-browser deny, tag, tile; bundle rebuilt as a separate commit. 10 h. Dev C. Deps: T-eyn5UG.

### 4. Hardening, gate G2, docs
- [ ] `T-JCOAsq-cache-test-hardening` **Parts 2–3**: integration and adversarial (Part 2; deps T-XpF1pF, T-u3jG8F, T-HjxNQ0); e2e, CI coverage step, full suite (Part 3; deps Part 2, T-o95l1M, T-6tRKml, T-ZTxN1x, T-bLpoze). 18 h. Tester.
- [ ] `T-fXWbqg-cache-review-gates`: G1a, G1b and **G2**. 24 h in total. reviewer + dev-security.
- [ ] `T-bdQZW4-cache-docs-refresh`: post-implementation docs reconciliation (mandatory, last). 8 h. Dev B + architect sign-off. Deps: G2 PASS, T-nPMuz4.

**Totals.**
- **288 focus hours** across 20 tasks: developers 234, tester 30, review gates 24.
- **Dependency critical path:** T-FJH6LI → T-8tr1H4 → T-uoYW6b → T-gDNjN2 → T-XpF1pF → T-JCOAsq
  Part 2 → Part 3 → G2 → T-bdQZW4 = **120 focus hours** (about 25 working days).
- **Staffed plan (3 developers + 1 tester):** about 134 focus hours (about 28 working days),
  inside 3 sprints with about 2 days of slack. Capacity math in HLD §22.1.

## Strategic flag (parent decision)
- `dev-critic` judged the full build **no-go until value is validated**. The design answers with
  `shadow` mode, the **G0 protocol** (T-nPMuz4; HLD §22.5) and alternatives ALT-7/ALT-8.
- **G0 runs after the merge**, owned by the parent or operator, on a real consumer workflow
  (finplan, with consent). It decides whether to *recommend* `on`; it does not block epic
  closure. The parent confirms the thresholds (OQ-6), the container name `result_cache` (OQ-7)
  and the author-policy default (OQ-8).

## Risks and Dependencies
- **R-1: stale hits from ambient state** (high impact). Double opt-in; HEADs, priors, argv and
  the fingerprint in the key; three store guards; `shadow`; `ao cache rm`; TTL; the guide.
- **R-4: poisoning by a deliberate same-uid writer.** Not defended in MVP; `--no-cache` for
  untrusted workflows.
- **R-7: merge conflicts with E-Ag7Pw3 and E-Da5Tn9.** Small additive hunks (HLD §24.2); the UI
  bundle is a separate commit, never hand-merged; the approval gate goes before the lookup seam;
  new task, workflow or agent fields must be classified; I-2 goldens may need recapturing.
- **R-9: security-sensitive code written by juniors.** Gates G1a, G1b, G2; the hostile corpus;
  the AST guard.
- **R-14 (STRATEGIC): unproven value.** G0 protocol shipped; G0 runs post-merge.
- **R-15 (STRATEGIC): tension with isolation-by-default.** Migration path ALT-7.
- **R-19: nested workspace treated as non-git** (banner warning). **R-20: dirty tracked edits
  before the lookup are not in the key** (accepted). **R-21: noise files cause false misses.**
- **Dependencies.** No hard dependency on the sibling epics; coordination at merge time.

## Links
- Design doc: `docs-md/cross-run-result-cache-hld.md`
- ADR: `docs-md/adr/ADR-0019-cross-run-result-cache.md`
- Sprint plan and execution order: HLD §22
- Output artifacts: `output/E-Rc4Hk8-cross-run-result-cache/` (gate reports and the G0 smoke
  evidence will land there; none yet)

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Epic created (Rev 1).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 after the Phase-4
  consultation: 20 tasks, 288 h; 61 dispositions in HLD §23.4.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: **Rev 3** after the independent
  early-gate review of `94dac52` (GO-WITH-FIXES) and the manager's scope decisions; every item is
  dispositioned in HLD §23.5.
  - **Must-fixes applied:** a buildable dependency graph (store class split, T-XpF1pF depends on
    T-JCOAsq Part 1 only, one commit numbering, no T-FJH6LI→T-28J9oR cycle, T-bdQZW4 after
    T-nPMuz4, re-split test parts); repository detection stops at the workspace root;
    `unknown_agent_field`; unsafe cache paths never followed or evicted; G0 re-scoped to a
    post-merge protocol hand-off.
  - **Scope:** `refresh`, `rm --run/--task` and `verify --repair` deferred; lazy guard 3 with
    `GIT_OPTIONAL_LOCKS=0`; inline prune bounded by bytes; named policy default; exact brief
    fields in `result_cache` objects.
  - **Plan:** totals stay 288 h; estimates and owners rebalanced; critical path recomputed to
    120 h. T-OeRYSO and T-ZTxN1x are unchanged by Rev 3.
