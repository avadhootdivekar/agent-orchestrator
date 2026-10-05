# STATUS

- ID: `E-Rc4Hk8-cross-run-result-cache`
- Updated At: `2026-10-05`
- State: `In Progress` — Design complete (Rev 3); implementation under way (manager execution, 2026-10-05).
- Owner: `manager` (execution) · `architect` (design)

## This update

### Rev 3 design package
- [`docs-md/cross-run-result-cache-hld.md`](../../../docs-md/cross-run-result-cache-hld.md): HLD +
  LLD, §0–§25 (26 top-level sections), Rev 3.
- [`docs-md/adr/ADR-0019-cross-run-result-cache.md`](../../../docs-md/adr/ADR-0019-cross-run-result-cache.md):
  decisions D1–D35, alternatives ALT-1…ALT-8, Rev 3.
- 20 task tickets (`TASK.md` + `STATUS.md`; `HANDOFF.md` for every task except T-bdQZW4, the last
  task). Rev 3 rewrote 18 of them; **T-OeRYSO and T-ZTxN1x are unchanged** (no Rev 3 item affects
  their scope).

### Independent early-gate review of `94dac52` (verdict GO-WITH-FIXES)
Every item is dispositioned in HLD §23.5. Summary:

| Item | Resolution |
|------|------------|
| A1 dependency graph | `LocalFsCacheStore(CacheStore)` in T-U7ckfd, `CacheAdmin` added by T-HjxNQ0; T-XpF1pF depends on T-JCOAsq Part 1 only; one T-FJH6LI commit numbering (T-8tr1H4 needs commit 3); `types.py` references `ResultCacheRecord` only under `TYPE_CHECKING` (no cycle); T-bdQZW4 depends on T-nPMuz4; T-JCOAsq parts re-split; critical path recomputed (120 h). |
| A2 repo detection | `find_git_toplevel` stops at the workspace root; a nested workspace is treated as non-git with a banner warning. |
| A3 AgentSpec | `unknown_agent_field` runtime rule. |
| A4 unsafe evict | Checks before every store operation; `CacheUnsafePathError`; never evicted. |
| A5 G0 | Re-scoped to a protocol and tooling hand-off; execution is post-merge. |
| B scope | `refresh`, `rm --run/--task`, `verify --repair` deferred; lazy guard 3; `GIT_OPTIONAL_LOCKS=0`; byte-bounded inline prune; version memo by binary identity; named policy default; exact brief fields. |
| C should-fix | Both usage sites; literal NFR-1 with lazy imports; `budget.resume_reverse` on the hit path; owned test ids; cost-reporting test executor; non-vacuous concurrency; simulated `EACCES`; skip markers; key-hygiene notes. |
| D nits | Honest engine budget (net ≤ +110 lines); copy-ready code ≤ 100 columns; public APIs only; CI step; separate bundle commit; example spec; release note. |

**One deviation (B7):** the would-hit rate for G0 comes from `ao report-usage --json`
(`result_cache` object), not from `ao cache stats --json`, because the store keeps no per-lookup
history; `ao cache stats --json` supplies the store-growth fields.

### Readiness
- Execution Readiness Gate (HLD §21): **PASS** (re-checked in Rev 3).

### Task status rollup (must match each task's `STATUS.md`)

| Task | Phase | Owner | Est | State |
|------|-------|-------|-----|-------|
| T-FJH6LI-cache-contracts | core set | Dev A | 16 h | Done |
| T-28J9oR-cache-spec-config-surface | core set | Dev B | 16 h | Done |
| T-OeRYSO-executor-argv-builder | core set (any time) | Dev C | 6 h | Done |
| T-QgQy08-cache-eligibility | core set | Dev B | 13 h | Done |
| T-8tr1H4-cache-hashing | core set | Dev C | 14 h | Done |
| T-uoYW6b-cache-key-builder | core set | Dev A | 20 h | Done |
| T-U7ckfd-cache-store-core | core set | Dev C | 17 h | Done |
| T-u3jG8F-cache-restore-capture | core set | Dev B | 16 h | Done |
| T-HjxNQ0-cache-store-maintenance | core set | Dev C | 15 h | Done |
| T-gDNjN2-cache-coordinator | engine set | Dev A | 20 h | Done |
| T-XpF1pF-cache-engine-integration | engine set | Dev A | 16 h | Done |
| T-eyn5UG-cache-reporting | engine set | Dev B | 18 h | Done |
| T-o95l1M-cache-cli-wiring | engine set | Dev B | 8 h | Done |
| T-ZTxN1x-bench-cache-force-off | engine set | Dev B | 4 h | Done |
| T-nPMuz4-cache-shadow-value-check | surfaces | Tester (+ manager) | 6 h | Done |
| T-6tRKml-cache-cli-commands | surfaces | Dev C | 17 h | Done |
| T-bLpoze-cache-dashboard-surface | surfaces | Dev C | 10 h | Done |
| T-JCOAsq-cache-test-hardening | Part 1 before T-XpF1pF; Parts 2–3 hardening | Tester | 24 h | In Progress (Part 1 done) |
| T-fXWbqg-cache-review-gates | G1a / G1b / G2 | reviewer + dev-security | 24 h | Draft |
| T-bdQZW4-cache-docs-refresh | last | Dev B + architect | 8 h | Draft |

**Counts:** 20 tasks: 2 Draft, 1 In Progress (T-JCOAsq, Part 1 done), 0 In Review, 0 Blocked, 17 Done, 0 Deferred. Total 288 focus hours.

### Gate tracker
| Gate | When | State |
|------|------|-------|
| G1a | after the core set | **PASS** (2026-10-05): reviewer PASS (0 MUST-FIX); dev-security FAIL on SEC-01 -> fixed in `763375f`, re-verified PASS (0 open MUST-FIX; full suite 6081 passed / 10 skipped / 0 failed) |
| G1b | after the engine set | **PASS** (2026-10-05): reviewer PASS (0 MUST-FIX, 5 SHOULD-FIX, 7 NIT) + dev-security PASS (0 MUST-FIX, 5 SHOULD-FIX, 10 NIT); fixable items remediated in `6ba90ba`; deferred items recorded in T-fXWbqg STATUS. Hard G2 exit items: dashboard deny-list for `.orchestrator/cache` (T-bLpoze), approval-ordering check |
| G2 | after T-JCOAsq Part 3 | not started |
| G0 (value; business go/no-go) | **post-merge**, parent or operator | G0 protocol shipped; execution is a post-merge follow-up (owner: parent/operator); not run in this epic. |

## Evidence
- **Code baseline studied:** `main` @ `bb6d8a0` (engine seams, `runstate`, `usage` sites ~409
  and ~472, `outcomes`, `budget`, `isolation/git.py` public API and `probe`, `executors`,
  `bench/subjects.py`, `ui/runs.py`, `ui/files.py`, `.github/workflows/ci.yml`, the NFR-2 gate).
- **GV-1 (Rev 2)** re-run on 2026-10-05 with the real `build_prompt` and argv helpers:
  `6646469e94a695fe1a994d35f54ca74e007262911255552b03c54ce2e5d0319f`; Rev 3 does not change
  the key.
- **Engine seam size:** the §8.7.1 snippets run through `ruff format --line-length 100` with no
  diff and no E501; about 123 lines added and 20 moved out (net ≈ +103).
- **Example workflow** (HLD §17) validated with `jsonschema` against `specs/workflow.schema.json`
  plus the planned `cache` properties.
- **Test baseline** (2026-10-04): 5041 passed / 8 skipped / 2 failed (the two pre-existing
  `tests/bench/test_dev_{core,medium}_suite.py::test_fake_subject_full_suite_run_produces_valid_run_json_and_summary`).

## Risks / Blockers
- **No implementation blockers.**
- **Parent decisions (non-blocking):** OQ-6 G0 thresholds and post-merge ownership; OQ-7 the
  container name `result_cache` (rationale D35); OQ-8 keep or flip `DEFAULT_TASK_CACHE_POLICY`.
- **Assumption A-9:** the env allowlist must be verified against the installed CLI (TODO in
  T-uoYW6b).
- **Merge with E-Ag7Pw3:** classify new fields (task/workflow as RULED; agent fields in
  `constants`), keep the approval check before the lookup seam, recapture the I-2 goldens if a
  sibling changed `status.json` or `ao run` output.
- **Highest risks** (HLD §23.1): R-1 stale hits; R-7 merge conflicts; R-9 junior-written
  security code; R-14 unproven value.

## Next actions
1. **`manager`:** start the core set (HLD §22.3): T-FJH6LI (commit 1, 2, 3), T-28J9oR, T-QgQy08,
   T-8tr1H4, T-uoYW6b, T-U7ckfd, T-u3jG8F, T-HjxNQ0, then gate G1a. T-OeRYSO may run in
   parallel at any time; T-JCOAsq Part 1 any time before T-XpF1pF.
2. **Parent:** answer OQ-6, OQ-7 and OQ-8; plan the post-merge G0 run.
3. **Separate bug ticket (recommended):** the missing-inputs branch resets `dispatch_cycle`
   (engine.py ~1174).
4. **Follow-up for E-Da5Tn9 or security:** the pre-existing `ui/files.read_file` FIFO open.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Epic STATUS initialized (Rev 1).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan: 20 tasks, all Draft.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the early-gate
  review and the manager's scope decisions. 20 tasks, all `Draft`; this rollup matches every
  task's `STATUS.md`, `TASK.md` and `HANDOFF.md`, and `EPIC.md`. Status wording changed to
  "Design complete / Not started".
- By: developer · Role: developer · Date: 2026-10-05 · Comment: T-FJH6LI -> Done (commits 9b24194, 5628556, d1c1d08). Rollup row and counts updated (18 Draft, 2 Done); `EPIC.md` checkbox ticked; matches the task `TASK.md`, `STATUS.md` and `HANDOFF.md`.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: T-28J9oR -> Done (commit 458c472) and T-QgQy08 -> Done (commit f9d4f70). Rollup rows and counts updated (16 Draft, 4 Done); `EPIC.md` checkboxes ticked; matches each task's `TASK.md`, `STATUS.md` and `HANDOFF.md`. Gate G1a not started (core set incomplete).
- By: developer · Role: developer · Date: 2026-10-05 · Comment: T-8tr1H4 -> Done (commit 00b9a3c). Rollup row and counts updated (15 Draft, 5 Done); `EPIC.md` checkbox ticked; matches the task `TASK.md`, `STATUS.md` and `HANDOFF.md`. Gate G1a not started (core set incomplete).
- By: developer · Role: developer · Date: 2026-10-05 · Comment: T-uoYW6b -> Done (commit 95dab70). Rollup row and counts updated (14 Draft, 6 Done); `EPIC.md` checkbox ticked; matches the task `TASK.md`, `STATUS.md` and `HANDOFF.md`. Full suite after T-8tr1H4 + T-uoYW6b: 5787 passed, 10 skipped, 0 failed (baseline 5583). Gate G1a not started (core set incomplete: T-U7ckfd, T-u3jG8F, T-HjxNQ0 remain).
- By: developer · Role: developer · Date: 2026-10-05 · Comment: T-U7ckfd -> Done (commit 181bbbb). Rollup row and counts updated (13 Draft, 7 Done); `EPIC.md` checkbox ticked; matches the task `TASK.md`, `STATUS.md` and `HANDOFF.md`. Gate G1a not started (core set incomplete: T-u3jG8F, T-HjxNQ0 remain).
- By: developer · Role: developer · Date: 2026-10-05 · Comment: T-u3jG8F -> Done (commit 87814ea). Rollup row and counts updated (12 Draft, 8 Done); `EPIC.md` checkbox ticked; matches the task `TASK.md`, `STATUS.md` and `HANDOFF.md`. Gate G1a not started (core set incomplete: T-HjxNQ0 remains).
- By: developer · Role: developer · Date: 2026-10-05 · Comment: T-HjxNQ0 -> Done (commit 6753c71). Rollup row and counts updated (11 Draft, 9 Done); `EPIC.md` checkbox ticked; gate G1a is ready to start (core set complete); matches the task `TASK.md`, `STATUS.md` and `HANDOFF.md`. Full suite after the core-set group (T-U7ckfd + T-u3jG8F + T-HjxNQ0): 6027 passed, 10 skipped, 0 failed (5787 after the previous group; baseline 5583).
- By: developer · Role: developer · Date: 2026-10-05 · Comment: G1a remediation committed in `763375f` (SEC-01 MUST-FIX and the SHOULD-FIX set fixed; the rest deferred with rationale in `T-fXWbqg-cache-review-gates/STATUS.md`). Gate row updated; G1a is not marked PASS until the manager re-verifies.
- By: manager · Role: agent · Date: 2026-10-05 · Comment: Gate G1a PASS after remediation `763375f` and independent dev-security re-verification (report `output/E-Rc4Hk8-cross-run-result-cache/review-g1a-security.md`, Re-verification section). Open for G2: SEC-15 sensitive-path list decision (`.gitlab-ci.yml`, `.githooks/*`, `Jenkinsfile`); optional NIT RV-1 (unbudgeted inline trash cleanup).
- By: developer · Role: developer · Date: 2026-10-05 · Comment: T-JCOAsq Part 1 reworked and done (commit `8972149`; the first delivery was rejected as vacuous). Task row -> In Progress (Part 1 done); counts 9 Draft, 1 In Progress, 10 Done; `EPIC.md` Part 1 checkbox ticked; matches the task `TASK.md`, `STATUS.md` and `HANDOFF.md`. Finding for the parent: `cli.py:44` eagerly imports `cache.cli` (outside HLD 8.7.5's CLI allowance); see the task `STATUS.md`. T-XpF1pF's Part 1 prerequisite is satisfied.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: T-gDNjN2 -> Done (commit `d14f07d`). Rollup row and counts updated (10 Draft, 10 Done); `EPIC.md` checkbox ticked; matches the task `TASK.md`, `STATUS.md` and `HANDOFF.md`. 107 new tests (coordinator 97, records 10), coordinator and records line coverage 100%; `tests/cache` + spawn-provenance: 1045 passed. Full suite not run (new files only, no shared module touched). Gate G1b not started (engine set incomplete: T-XpF1pF, T-eyn5UG, T-o95l1M, T-ZTxN1x remain).
- By: developer · Role: developer · Date: 2026-10-05 · Comment: T-XpF1pF -> Done (commit `b7ca9c7`). Rollup row and counts updated (8 Draft, 1 In Progress, 11 Done); `EPIC.md` checkbox ticked; matches the task `TASK.md`, `STATUS.md` and `HANDOFF.md`. Engine diff net +103 formatted lines, 12 added lines inside existing functions. Full suite: 6249 passed, 10 skipped, 1 failed (a pre-existing thread-timing flake in `tests/test_wave_scheduler.py`, passes in isolation). Gate G1b not started (T-eyn5UG, T-o95l1M, T-ZTxN1x remain).
- By: developer · Role: developer · Date: 2026-10-05 · Comment: T-eyn5UG -> Done (commit `cbf9152`). Rollup row and counts updated (7 Draft, 1 In Progress, 12 Done); `EPIC.md` checkbox ticked; matches the task `TASK.md`, `STATUS.md` and `HANDOFF.md`. 52 new tests; existing runstate/usage/outcomes tests unedited and green. Gate G1b not started (T-o95l1M, T-ZTxN1x remain).
- By: developer · Role: developer · Date: 2026-10-05 · Comment: T-o95l1M -> Done (commit `e882b31`). Rollup row and counts updated (6 Draft, 1 In Progress, 13 Done); `EPIC.md` checkbox ticked; matches the task `TASK.md`, `STATUS.md` and `HANDOFF.md`. 27 new wiring tests; CLI/e2e modules 417 passed unedited. Decision (manager): `agent_orchestrator.cache.cli` is allowed on the CLI path (HLD E-2 deviation, recorded in the task `HANDOFF.md` for T-bdQZW4; T-6tRKml must keep `cache/cli.py` import-light). Gate G1b not started (T-ZTxN1x remains).
- By: developer · Role: developer · Date: 2026-10-05 · Comment: T-ZTxN1x -> Done (commit `15a659d`). Rollup row and counts updated (5 Draft, 1 In Progress, 14 Done); `EPIC.md` checkbox ticked; matches the task `TASK.md`, `STATUS.md` and `HANDOFF.md`. 7 new tests; `tests/bench` 417 passed, 4 skipped. Gate G1b ready to start (engine set complete). Full suite after T-eyn5UG + T-o95l1M + T-ZTxN1x: 6333 passed, 10 skipped, 0 failed (655 s); last known 6249 passed, 10 skipped, 1 failed (the load-dependent wave-scheduler flake did not recur).
- By: manager · Role: agent · Date: 2026-10-05 · Comment: Gate G1b PASS (reports `output/E-Rc4Hk8-cross-run-result-cache/review-g1b.md`, `review-g1b-security.md`). Engine diff net +105 / 12 in-function lines (budget +110 / 12). Next: surfaces (T-nPMuz4, T-6tRKml, T-bLpoze), then T-JCOAsq Parts 2-3, G2, T-bdQZW4.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: T-6tRKml -> Done (commit `033dd79`). Rollup row and counts updated (4 Draft, 1 In Progress, 15 Done); `EPIC.md` checkbox ticked; matches the task `TASK.md`, `STATUS.md` and `HANDOFF.md`. 191 new tests (138 e2e CliRunner, 32 restore-sweep unit, 21 safeio); cache/cli.py, cli_ops.py and restore_sweep.py at 100% line coverage; full suite 6572 passed, 10 skipped (6582 collected).
- By: developer · Role: developer · Date: 2026-10-05 · Comment: T-nPMuz4 -> In Review (commit `edc3c18`), manager sign-off pending (AC-6). Rollup row and counts updated (3 Draft, 1 In Progress, 1 In Review, 15 Done); `EPIC.md` line annotated (box not ticked until sign-off); matches the task `TASK.md`, `STATUS.md` and `HANDOFF.md`. G0 gate line: protocol shipped, execution post-merge, not run. Evidence `output/E-Rc4Hk8-cross-run-result-cache/g0-protocol-smoke.md`; `tests/cache` + spawn-provenance + NFR-2 gate: 1301 passed. No `src/` change, full suite not run.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: T-bLpoze -> Done (commits `faf8f57` source + tests, `4e61e68` bundle, separate). Rollup row and counts updated (2 Draft, 1 In Progress, 17 Done; T-nPMuz4 is Done per the manager sign-off in `c8a91dc`); `EPIC.md` checkbox ticked; matches the task `TASK.md`, `STATUS.md` and `HANDOFF.md`. The G1b hard G2 exit item (dashboard deny-list for `.orchestrator/cache`) is delivered. Surfaces set complete; next T-JCOAsq Parts 2-3, G2, T-bdQZW4. Bundle: drop and rebuild after merging sibling dashboard epics.
