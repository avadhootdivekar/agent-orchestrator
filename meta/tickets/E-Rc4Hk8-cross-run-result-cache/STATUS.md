# STATUS

- ID: `E-Rc4Hk8-cross-run-result-cache`
- Updated At: `2026-10-05`
- State: `In Progress`. Design phase complete (Rev 2); implementation not started.
- Owner: `manager` (execution) · `architect` (design)

## This update

### Rev 2 design package
- [`docs-md/cross-run-result-cache-hld.md`](../../../docs-md/cross-run-result-cache-hld.md): HLD +
  LLD, §0–§25 (26 top-level sections).
- [`docs-md/adr/ADR-0019-cross-run-result-cache.md`](../../../docs-md/adr/ADR-0019-cross-run-result-cache.md):
  decisions D1–D32 and alternatives ALT-1…ALT-8.
- 20 task tickets. Each has `TASK.md` and `STATUS.md`; every task except T-bdQZW4 (the last task,
  with no downstream) also has a `HANDOFF.md`.

### Phase-4 consultation
All five reviews ran against the code at `bb6d8a0`. Their 61 findings are dispositioned one by one
in HLD §23.4.

| Reviewer | Verdict | How it was resolved |
|----------|---------|---------------------|
| developer | feasible with fixes | All findings adopted. |
| reviewer | approve with changes | All findings adopted. |
| tester | conditional pass | All findings adopted. |
| dev-security | sound but not G1-ready | Adopted, except HMAC and `dir_fd` walking, which are deferred with reasons. |
| dev-critic | **no-go until value is validated** | Answered with shadow mode, the G0 gate and alternatives ALT-7/ALT-8. Escalated to the parent. |

### Changes relative to the manager analysis
| Item | Change |
|------|--------|
| A | Double opt-in. The author default is False. |
| B/H | The engine owns the hit settle. `dispatch_cycle` keeps its increment, `attempts` is unchanged, and a stale budget charge is reversed on a hit. |
| D | The HEAD guard is always on, plus a tracked-worktree guard. Git is detected from the filesystem. |
| E | Runtime unknown-field rules for the task, the workflow and the defaults. Command-basename and resolved-model rules. Sensitive outputs. |
| F | Ownership checks, per-operation component checks, and versioned `entries/v1`. |
| G | Bounded inline prune. `clear` fixed. |
| H | Records are bound to the hit's `ended_at`. |
| I | Modes, `rm`, and bounds on config and records. |

New in Rev 2:
- argv (via the extracted `build_claude_argv`) and an executor fingerprint are part of the key;
- hostile cache data goes through total parsing;
- an error boundary disables the cache for the rest of the run on an unexpected error.

### Readiness
- Execution Readiness Gate (HLD §21): **PASS**.

### Task status rollup (must match each task's `STATUS.md`)

| Task | Sprint | Owner | Est | State |
|------|--------|-------|-----|-------|
| T-FJH6LI-cache-contracts | S1 | Dev A | 16 h | Draft |
| T-28J9oR-cache-spec-config-surface | S1 | Dev B | 16 h | Draft |
| T-OeRYSO-executor-argv-builder | S1 | Dev C | 6 h | Draft |
| T-8tr1H4-cache-hashing | S1 | Dev C | 14 h | Draft |
| T-uoYW6b-cache-key-builder | S1 | Dev A | 20 h | Draft |
| T-QgQy08-cache-eligibility | S1 | Dev B | 12 h | Draft |
| T-ZTxN1x-bench-cache-force-off | S1 | Dev B | 4 h | Draft |
| T-U7ckfd-cache-store-core | S1 | Dev C | 16 h | Draft |
| T-u3jG8F-cache-restore-capture | S2 | Dev C | 16 h | Draft |
| T-eyn5UG-cache-reporting | S2 | Dev B | 16 h | Draft |
| T-HjxNQ0-cache-store-maintenance | S2 | Dev C | 16 h | Draft |
| T-gDNjN2-cache-coordinator | S2 | Dev A | 20 h | Draft |
| T-XpF1pF-cache-engine-integration | S2 | Dev A | 16 h | Draft |
| T-o95l1M-cache-cli-wiring | S2 | Dev B | 8 h | Draft |
| T-bLpoze-cache-dashboard-surface | S2 | Dev B | 10 h | Draft |
| T-6tRKml-cache-cli-commands | S2 tail → S3 | Dev C | 20 h | Draft |
| T-JCOAsq-cache-test-hardening | S1–S3 | Tester | 24 h | Draft |
| T-fXWbqg-cache-review-gates | S1–S3 (G1a/G1b/G2) | reviewer + dev-security | 24 h | Draft |
| T-nPMuz4-cache-shadow-value-check | S3 | manager + tester | 6 h + window | Draft |
| T-bdQZW4-cache-docs-refresh | S3 (last) | Dev B + architect | 8 h | Draft |

**Counts:** 20 tasks: 20 Draft, 0 In Progress, 0 Blocked, 0 Done. Total 288 focus hours.

### Gate tracker
| Gate | When | State |
|------|------|-------|
| G1a | end of S1 | not started |
| G1b | end of S2 | not started |
| G2 | S3 | not started |
| G0 (value; business go/no-go) | S3 | not started; the parent confirms the thresholds (OQ-6) |

## Evidence
- **Code baseline studied.** `main` @ `bb6d8a0`:
  - `engine.py`: `_prepare_and_maybe_dispatch`, `_settle_completed_task`, `_RunContext`;
  - `runstate.py`, `models.py`, `artifacts.py`;
  - `executors/{prompt,claude_cli,fake}.py`;
  - `cli.py`, `project_config.py`, `usage.py`, `outcomes.py`, `budget.py`;
  - `isolation/git.py` (`GitRepo` constructor, `status_porcelain`, `resolve_empty_hooks_dir`);
  - `bench/subjects.py`;
  - `ui/runs.py`, `ui/files.py`, `ui/activity.py`;
  - `tests/test_nfr2_regression_gate.py`.
- **Golden vector GV-1, Rev 2.** Recomputed on 2026-10-05 by running the real `build_prompt` and
  the real `claude_cli` argv helpers at `bb6d8a0`:
  - key: `6646469e94a695fe1a994d35f54ca74e007262911255552b03c54ce2e5d0319f`;
  - component digests: in HLD §8.2.7;
  - the Rev 1 value `536533b0…` is superseded.
- **Test baseline.** Measured on 2026-10-04: 5041 passed / 8 skipped / 2 failed. Both failures
  are the pre-existing
  `tests/bench/test_dev_{core,medium}_suite.py::test_fake_subject_full_suite_run_produces_valid_run_json_and_summary`.

## Risks / Blockers
- **No implementation blockers.**
- **Strategic decision for the parent (R-14).** Either:
  - **(a)** proceed as planned, with G0 deciding whether to *recommend* mode `on`; or
  - **(b)** pivot to ALT-8 (`--reuse-from`).
- **Assumption A-1:** the parent accepts the double opt-in (friction for authors).
- **Assumption A-9:** the env allowlist must be verified against the installed CLI. This is a
  TODO in T-uoYW6b.
- **Merge with E-Ag7Pw3.** The approval field must be classified RULED, and the approval check
  must run before the lookup seam (HLD §24.2).
- **Open questions.** OQ-1…OQ-6 (HLD §23.2) all have defaults. Only OQ-6 (the G0 thresholds)
  needs the parent.
- **Highest risks** (HLD §23.1):
  - R-1: stale hits from ambient state;
  - R-7: merge conflicts;
  - R-9: security-sensitive code written by juniors;
  - R-14: unproven value.

## Next actions
1. **Parent:** confirm direction (a) or (b), and the G0 thresholds (OQ-6).
2. **`manager`:** start Sprint 1 Wave 1, following HLD §22.3:
   - T-FJH6LI: `constants.py` first, then `safeio.py`;
   - T-OeRYSO and T-28J9oR in parallel;
   - the tester captures the base golden (T-JCOAsq Part 1) **before** any engine-touching task
     merges.
3. **Gates:** schedule G1a for the end of S1 and G1b for the end of S2.
4. **Separate bug ticket (recommended):** the missing-inputs branch resets `dispatch_cycle`
   (engine.py ~1174).
5. **Follow-up for E-Da5Tn9 or security:** the pre-existing `ui/files.read_file` FIFO open.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Epic STATUS initialized (Rev 1,
  16 tasks, all Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan: 20 tasks, all
  `Draft`. This rollup matches every task's `STATUS.md`, `TASK.md` and `HANDOFF.md`, and the epic
  `EPIC.md`.
