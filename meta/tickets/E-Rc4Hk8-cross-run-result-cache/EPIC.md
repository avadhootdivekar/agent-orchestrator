# EPIC: E-Rc4Hk8-cross-run-result-cache

## Metadata
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Title: `Double-opt-in, content-addressed cross-run result cache (reuse identical successful task results instead of re-dispatching agents)`
- Owner: `manager` (execution) · design by `architect`
- Created: `2026-10-04`
- Last Updated: `2026-10-05`
- Status: `In Progress`
  - The design phase is complete (Rev 2): the HLD/LLD, ADR-0019 and 20 task tickets are ready.
  - Implementation has not started.
  - One strategic decision is escalated to the parent (G0; see below).

## Summary
- **Goal.** A task in any run of a workspace can reuse the declared output files of an earlier,
  successful, *identical* task (content-addressed key) instead of dispatching the agent again.
  This saves real money on re-runs, clean-checkout runs and fresh-run retries.
- **Off by default, double opt-in.**
  - The **operator** sets the mode with `--cache/--no-cache`, `AO_CACHE` or `cache.enabled`.
    Modes: `on`, `shadow`, `refresh`, `off`.
  - The **workflow author** opts tasks in with `defaults.cache` or `tasks[].cache`.
- **Safety and no-op.** It is safe under parallelism, resume, concurrent runs and hostile cache
  data. With the mode off it is a no-op: the engine executes and imports no cache code.
- **Scope In:**
  - **New package `agent_orchestrator.cache`:**
    - constants, `safeio`, types/contracts;
    - settings, hashing, repo_state, fingerprint, keys, eligibility;
    - `LocalFsCacheStore`, restore/capture, coordinator, records, report;
    - the `ao cache` CLI.
  - **Behaviour-identical extraction** of `build_claude_argv` in `executors/claude_cli.py`.
  - **Additive hooks** in `models.py`, `specs/workflow.schema.json`, `project_config.py`,
    `engine.py` (≤ 80 formatted lines, ≤ 8 inside existing functions), `cli.py`, `runstate.py`,
    `usage.py`, `outcomes.py`, `ui/runs.py`, `ui/files.py` plus 2 frontend files, and
    `bench/subjects.py`.
  - **Tests:** unit, integration, e2e, adversarial, the hostile corpus, tripwires, and a no-op
    proof.
  - **The G0 shadow-mode value check.**
  - **Docs:** the HLD, ADR-0019 and the docs refresh.
- **Scope Out (non-MVP; HLD §2.3):**
  - **Backends and integrity:** remote, shared or S3 backends; HMAC-authenticated entries;
    `dir_fd`-walking I/O.
  - **Alternative designs:** the executor-level `CachingExecutor` (ALT-7); `--reuse-from`
    (ALT-8).
  - **Unsupported task shapes:** caching under isolation, under active integration, or with
    hooks; directory and dynamic outputs.
  - **Cache behaviour:** dependency-aware invalidation and warming; resolved-model recording;
    user-level context fingerprinting; memoizing hashes; per-entry hit counters.
  - **Tooling:** the `ao validate` warning; `ao cache explain`; dashboard launch controls.
  - **Separate bug ticket:** the missing-inputs `dispatch_cycle` reset.

## Design
- HLD + LLD: [`docs-md/cross-run-result-cache-hld.md`](../../../docs-md/cross-run-result-cache-hld.md), §0–§25, Rev 2
- ADR: [`docs-md/adr/ADR-0019-cross-run-result-cache.md`](../../../docs-md/adr/ADR-0019-cross-run-result-cache.md), Rev 2, alternatives ALT-1…ALT-8
- Decision log: HLD §7.6, D1–D32, including the disposition of manager analysis A–K
- Threat model: HLD §7.7, M-1…M-16 plus residuals
- Phase-4 consultation: HLD §23.3 (summary) and §23.4 (61 findings, each with a disposition)
- Merge notes for sibling epics E-Ag7Pw3 and E-Da5Tn9: HLD §24.2
- Golden key vector GV-1 (Rev 2): `6646469e94a695fe1a994d35f54ca74e007262911255552b03c54ce2e5d0319f` (HLD §8.2.7)

## Requirements
The full table with verification methods is in HLD §1.3.

- FR-1: The run-level **mode** (`on|shadow|refresh|off`) is resolved `--cache/--no-cache` >
  `AO_CACHE` > `cache.enabled` + `cache.mode` > off, through ONE helper shared by `ao run` and
  `ao resume`. An unknown env value means off, with a warning.
- FR-2: Author opt-in (double opt-in) is `task.cache` > `defaults.cache` > **False**. An injected
  task's `true` is ignored.
- FR-3: Key schema v1. Canonical JSON over the agent projection, argv, executor fingerprint,
  prompt, content digests, `[{path, prior}]` outputs and repo heads. The task id is excluded. GV-1
  pins it.
- FR-4: Fail-closed eligibility allowlist. Every spec field is classified, with tripwires and
  runtime unknown-field rules. Command-basename and resolved-model rules.
- FR-5: The lookup runs after the skip, isolation, join, missing-inputs and dynamic-input steps,
  and before the budget gate. The engine settles a hit itself.
- FR-6: Only a final settled success is stored, behind three guards: key recompute, HEAD moved,
  tracked-worktree changed.
- FR-7: Provisional `CacheStore` + `CacheAdmin` ABCs, and `LocalFsCacheStore`. Atomic,
  deduplicated, concurrency-safe and versioned, with bounded maintenance.
- FR-8: Safe restore and capture. Destinations are spec-derived and re-validated; sensitive paths
  are refused; data is verified before commit; modes are masked.
- FR-9: Accounting. `saved_*` is kept separate. A hit never touches `cumulative_*`, budget
  counters (except reversing a stale charge) or breakers.
- FR-10: A restored task survives resume. Records stay consistent across sessions where the cache
  is on and off.
- FR-11: Observability: `cache.*` events (including miss components), `status.json`, the summary
  line, `report-usage` and `settle_reason: cached`.
- FR-12: `ao cache ls|stats|show|prune|clear|verify|rm`, each with `--json` and exit codes.
- FR-13: Dashboard: a `cached` tag and a "Result cache" tile. The file browser does not serve
  `.orchestrator/cache`.
- FR-14: `ao-bench` forces the cache off.
- FR-15: Config knobs with bounds and clamping, plus the `ao init` template.
- FR-16: Modes. `shadow` measures and never restores; `refresh` re-rolls and overwrites.
- FR-17: Single-entry invalidation: `ao cache rm`.
- NFR-1: No-op when off (HLD §8.7.5 claim and evidence).
- NFR-2: `engine.py` stays content-free.
- NFR-3: Deterministic and replayable.
- NFR-4: Bounded work and memory.
- NFR-5: Safe under cross-process concurrency.
- NFR-6: Backward and forward compatible.
- NFR-7: No magic literals.
- NFR-8: Small, additive edits to shared files.
- NFR-9: At least 85% coverage of `agent_orchestrator.cache`.
- NFR-10: Threat-model mitigations M-1…M-16.
- NFR-11: Hostile data never raises past the parse boundary. An unexpected bug disables the cache
  for the rest of the run and never kills it.

## Task List

The execution order and safe parallelism are in HLD §22.3. File scopes are exclusive; tasks that
share a file are ordered by dependency (HLD §22.2).

### Sprint 1: contracts, surface, key building, store core (118 h)
- [ ] `T-FJH6LI-cache-contracts`: constants (commit 1), `safeio` (commit 2), types/contracts/ABCs/errors/fakes, corpus, AST guard. 16 h. Dev A. Deps: none.
- [ ] `T-28J9oR-cache-spec-config-surface`: spec, config, record model, settings, CLI flag (resolution half), `ao cache` group. 16 h. Dev B. Deps: T-FJH6LI commit 1.
- [ ] `T-OeRYSO-executor-argv-builder`: pure `build_claude_argv` extraction. 6 h. Dev C. Deps: none.
- [ ] `T-8tr1H4-cache-hashing`: bounded hashing, git marker, HEAD reader, worktree probe. 14 h. Dev C. Deps: T-FJH6LI commit 2.
- [ ] `T-uoYW6b-cache-key-builder`: fingerprint, keys, GV-1 Rev 2. 20 h. Dev A. Deps: T-FJH6LI, T-OeRYSO, T-8tr1H4.
- [ ] `T-QgQy08-cache-eligibility`: allowlist predicate, tripwires, runtime unknown-field rules. 12 h. Dev B. Deps: T-28J9oR.
- [ ] `T-ZTxN1x-bench-cache-force-off`: bench argv and env, plus a regression test. 4 h. Dev B. Deps: T-28J9oR.
- [ ] `T-U7ckfd-cache-store-core`: `LocalFsCacheStore` hot path, layout, checks, `is_expired`. 16 h. Dev C. Deps: T-FJH6LI.
- [ ] `T-JCOAsq-cache-test-hardening` Part 1: base golden, I-2 (active), I-1 (importorskip), ADV-9 harness. 6 h. Tester.
- [ ] Gate **G1a** (`T-fXWbqg`): 8 h.

### Sprint 2: restore, coordinator, engine, reporting, wiring, dashboard (128 h)
- [ ] `T-u3jG8F-cache-restore-capture`: staged, verified restore and safe capture. 16 h. Dev C. Deps: T-FJH6LI.
- [ ] `T-eyn5UG-cache-reporting`: report helpers, plus the `status.json`, `usage` and `outcomes` hooks. 16 h. Dev B. Deps: T-28J9oR.
- [ ] `T-HjxNQ0-cache-store-maintenance`: streaming iteration, prune, bounded inline enforcement, clear, verify, race test. 16 h. Dev C. Deps: T-U7ckfd.
- [ ] `T-gDNjN2-cache-coordinator`: `ResultCache` (modes, guards, boundary) and records builders. 20 h. Dev A. Deps: T-uoYW6b, T-QgQy08, T-U7ckfd, T-u3jG8F, T-8tr1H4.
- [ ] `T-XpF1pF-cache-engine-integration`: engine seams and integration tests; must pass I-1 and I-2. 16 h. Dev A. Deps: T-gDNjN2, T-JCOAsq Part 1.
- [ ] `T-o95l1M-cache-cli-wiring`: CLI construction, banner, summary lines, `report-usage` line. 8 h. Dev B. Deps: T-XpF1pF, T-eyn5UG.
- [ ] `T-bLpoze-cache-dashboard-surface`: payload fields, file-browser deny, tag, tile, bundle rebuild. 10 h. Dev B. Deps: T-eyn5UG.
- [ ] `T-6tRKml-cache-cli-commands`, first part: read-only commands. About 8 h of 20 h. Dev C. Deps: T-HjxNQ0.
- [ ] `T-JCOAsq-cache-test-hardening` Part 2: integration hardening. 10 h. Tester.
- [ ] Gate **G1b** (`T-fXWbqg`): 8 h.

### Sprint 3: commands, hardening, gates, G0, docs (42 h plus deliberate slack)
- [ ] `T-6tRKml-cache-cli-commands`, remainder: `rm`, `prune`, `clear`, `verify`. About 12 h. Dev C.
- [ ] `T-JCOAsq-cache-test-hardening` Part 3: e2e, coverage, full suite. 8 h. Tester.
- [ ] `T-fXWbqg-cache-review-gates`: gate **G2**. 8 h. reviewer + dev-security.
- [ ] `T-nPMuz4-cache-shadow-value-check`: **G0**, the shadow-mode value check and report. 6 h plus an observation window. manager + tester. Deps: T-o95l1M.
- [ ] `T-bdQZW4-cache-docs-refresh`: post-implementation docs reconciliation (mandatory, last). 8 h. Dev B + architect sign-off. Deps: G2 PASS.

**Totals.**
- **288 focus hours** across 20 tasks: developers 234, tester 24, gates 24, G0 6.
- **Team:** 3 developers + 1 tester.
- **Duration:** 3 sprints. The dependency critical path is 104 h (about 22 working days). The
  resource-constrained path is about 114 h (about 24 working days).
- The capacity math is in HLD §22.1.

## Strategic flag (parent decision)
- `dev-critic` judged the full build **no-go until value is validated**. Fail-closed keys,
  committing tasks and per-epic paths may make hits rare for the primary consumer.
- The design answers with:
  - `shadow` mode;
  - the **G0 gate** (T-nPMuz4; HLD §22.5);
  - alternatives ALT-7 and ALT-8 recorded in ADR-0019.
- **The parent decides** whether to:
  - **(a)** build as planned, with G0 deciding whether to *recommend* `on`; or
  - **(b)** pursue ALT-8 (`--reuse-from`) instead.

## Risks and Dependencies
- **R-1: stale hits from ambient state** (high impact).
  - Mitigations: double opt-in; HEADs, priors, argv and the fingerprint in the key; three store
    guards; `shadow`, `refresh` and `rm`; TTL; the authoring guide.
- **R-4: poisoning by a deliberate same-uid writer.** Not defended in MVP (HMAC is non-MVP).
  Recommend `--no-cache` for untrusted workflows.
- **R-7: merge conflicts with E-Ag7Pw3 and E-Da5Tn9.**
  - Hunks are small and additive; the merge notes are in HLD §24.2.
  - Never hand-merge the UI bundle.
  - The approval gate goes **before** the lookup seam.
  - The approval field must be classified RULED.
- **R-9: security-sensitive code written by juniors.** Mitigated by gates G1a, G1b and G2
  (0 MUST-FIX each), the hostile corpus and the AST guard.
- **R-10: eligibility drift.** Mitigated by tripwires plus runtime unknown-field rules.
- **R-14 (STRATEGIC): unproven value.** Addressed by G0; the parent decides.
- **R-15 (STRATEGIC): tension with isolation-by-default.** Migration path: ALT-7.
- **R-17: env allowlist drift.** Covered by the A-9 check in T-uoYW6b.
- **Dependencies.** There is no hard dependency on the sibling epics; coordination happens at
  merge time.

## Links
- Design doc: `docs-md/cross-run-result-cache-hld.md`
- ADR: `docs-md/adr/ADR-0019-cross-run-result-cache.md`
- Sprint plan: HLD §22
- Output artifacts: `output/E-Rc4Hk8-cross-run-result-cache/`. None yet; the gate reports and the
  G0 report will land there.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Epic created (Rev 1). The design
  package covered HLD/LLD §0–§25, ADR-0019 and 16 tasks.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: **Rev 2** after the Phase-4
  consultation (developer, reviewer, tester, dev-security, dev-critic). Every one of the 61
  findings is dispositioned in HLD §23.4.
  - **Changes relative to the manager analysis.**
    - A: double opt-in. The author default is False.
    - B/H: the engine owns the hit settle. `dispatch_cycle` keeps its increment, `attempts` is
      unchanged, and a stale budget charge is reversed.
    - D: the HEAD guard always applies, plus a tracked-worktree guard. Git is detected from the
      filesystem.
    - E: runtime unknown-field rules (task and workflow), the command-basename and
      resolved-model rules, and sensitive outputs.
    - F: per-operation ownership and component checks; versioned `entries/v1`.
    - G: bounded inline prune; `clear` fixed.
    - H: `ended_at` binding.
    - I: modes, `rm` and bounds.
  - **New in Rev 2:** argv and an executor fingerprint in the key; total parsing; an error
    boundary that disables the cache for the run.
  - **Plan:** 4 tasks added (T-OeRYSO, T-8tr1H4, T-o95l1M, T-nPMuz4), giving 20 tasks and 288 h.
  - **Readiness:** the Execution Readiness Gate (HLD §21) passes. The strategic G0 decision is
    escalated to the parent.
