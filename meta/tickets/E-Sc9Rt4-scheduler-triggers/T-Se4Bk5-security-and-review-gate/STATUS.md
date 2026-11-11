# STATUS

- ID: `T-Se4Bk5-security-and-review-gate`
- Updated At: 2026-11-11
- State: In Progress — NOT Done (gate evidence incomplete; no high finding open)
- Owner: reviewer agent (overseer w20-02)

## Reconciliation 2026-11-11 (By: developer · Role: developer · unit `w24-07-a1-task-status-sync` · docs only)
- Checked against branch `ad/enhancements-10nov` at HEAD `1d5c6f9`. Any older line below that says "not started", "uncommitted" or "in the working tree" is superseded by this block where they disagree.
- State: **In Progress — NOT Done (gate evidence incomplete; no high finding open)**
- Closed since the older entries below: **R-2** (view-path fire-record writer; `ReadOnlyFireStore` in `fire_store.py`, tests `test_r2_*` + `test_usage_api_never_writes_fire_records`, committed), **R-3/R-4** (`0f2a99b`), S-1/S-8/S-10, R-1, AC7 (threat-model delta), AC16 (EPIC/ADR sync), TEST-QUALITY TQ-1..14 and FU-1..FU-7 (FU-4 via R-1).
- **Unmet / open**: **AC14** `schedules/usage_view.py` coverage 78 % < 80 % floor (follow-up unit w24-04 in flight when this was written); **whole-tree gate** (pytest/ruff/mypy) not re-run on a clean tree after the fixes (REVIEW.md and its "not Done" verdict pre-date R-2's fix); TEST-QUALITY FU-8, FU-10, FU-11 deferred and FU-9 partial (w24-03 in flight); S-7 (github-scheme replay across restart) stays an accepted limitation.
- Do not mark Done until the gate is re-run on a clean tree and `REVIEW.md` is updated to say R-2 is closed.

## This update
- By: reviewer agent (w20-02-a1-security-gate) · Role: reviewer · Date: 2026-11-11
- Comment: I ran the security pass on the four areas in this unit's brief: HMAC/scheme/replay,
  allow-lists, file locking and path handling. It ran against committed HEAD `a3d0195`, with probes on a
  clean `git archive` tree. Full write-up: [`SECURITY.md`](SECURITY.md); probes:
  `output/E-Sc9Rt4-scheduler-triggers/se4bk5-probes/`.
- I landed one small fix, **`b92f427`** (S-3, partial): a NUL-byte `prompt_file` now gets the named refusal
  `prompt_missing/invalid_path` in `governance_gate.read_prompt`. Tests:
  `tests/service/test_security_gate_se4bk5.py`, 31 parametrized cases, including path-shape fuzz over
  `workflow`, `prompt_file` and `template`.

## Findings summary (counts match SECURITY.md)
- Blocking: 1.
  - **S-1**: the HEAD webhook accepts either signature header, so an X-AO delivery can be replayed with
    no time bound in GitHub form. The fix is **pending** as uncommitted w19-01 N1 (`webhook.scheme`) and
    must land before this task closes.
- Should-fix: 5.
  - S-2: FIFO/unbounded read in the ungoverned `_read_prompt` (latent; handed over).
  - S-3: NUL at load and in the webhook secret path. Fixed in part here; the rest is handed over.
  - S-4: `secret_file` TOCTOU.
  - S-5: duplicate fsync/atomic-write idiom with a silent `except OSError: pass` in `webhook.py`.
  - S-6: magic `10` fallback for the rate limit.
- Limitations: 4.
  - S-7: no time bound on github-scheme replay across a restart.
  - S-8: pending N2 header deadline depends on uvicorn internals.
  - S-9: `ScheduleStore` follows symlinks.
  - S-10: pending `cost_cap_usd` in `ALLOWED_OPTIONS`; re-verify AC2 when it lands.
- I did not fix the handed-over findings here, because the files they live in (`webhook.py`,
  `schedules/models.py`, `schedule_launch.py`) had live uncommitted edits from carry-over units.

## Evidence
- Passed at HEAD, with details in SECURITY.md:
  - `compare_digest` is used and there is no `==` on any signature;
  - unknown and bad-signature 401s are byte-identical;
  - a real **421** shows the middleware is mounted;
  - no secret or signature appears in any log line or response;
  - run_args and binding allow-lists hold;
  - every escaping path shape is rejected at load;
  - no subprocess call in the schedule modules;
  - flock on the store and the governance lock.
- `uv run pytest -q tests/service tests/schedules`: 1195 passed, 1 failed
  (`test_hld_event_table_is_complete`). Its subject files, `fire_store.py` and the HLD, have carry-over
  uncommitted edits.

## Risks / Blockers
- S-1 is blocking until the w19-01 N1 change is committed. After that, re-run
  `se4bk5-probes/p1_downgrade.py` against the new HEAD.

## Next actions (unfinished — this was the last work wave)
1. Re-verify S-1 and S-8 against HEAD once the w19-01 webhook commit lands, and S-10 once the w18-04
   commit lands.
2. Hand off S-2, S-3(a)(b), S-4, S-5 and S-6 to their owning tasks (T-Wh9Kv1, T-PpE5lf/T-Ap1Xs3,
   T-Sd1Kq7), with the exact changes given in SECURITY.md.
3. Not started: AC6 (run-id guard), AC7 (threat-model delta paragraph), AC8–AC13 (`REVIEW.md`
   architecture pass), AC14 (whole-tree quality gate), AC16 (EPIC/ADR sync).

## w21 update
- By: developer agent (w21-01-a1-security-fixes) · Role: developer · Date: 2026-11-11
- S-1 blocking: the scheme pin was already committed; probe p1 re-run prints `wrong_scheme` for the downgrade. S-2..S-5 fixed with regression tests, S-6 already gone. Details: SECURITY.md "w21 update".
- Evidence: `uv run pytest -q tests/service tests/schedules`: 1248 passed + 2 failed before I fixed one of my own w20 tests (it loaded a NUL path through the validator that now rejects it); the other failure is `tests/schedules/test_cost_cap_cli_e2e.py::test_prototype`, an untracked file from another unit's in-progress cost-cap work, not mine. After the fix, the two security test files pass (45).
- Still NOT done: AC6, AC7, AC8-AC13 (REVIEW.md), AC14 whole-tree gate (tree currently has another unit's uncommitted edits: ruff/mypy/format failures in `schedules/cli.py` and `test_cost_cap_cli_e2e.py`, and `tests/auth` openapi test failing), AC16. S-8 and S-10 re-verify pending.

## w21 test-quality review
- By: reviewer agent (w21-05-a1-test-quality-review, Opus) · Role: reviewer · Date: 2026-11-11
- Comment: I ran the mandatory test-quality review of the scheduler-triggers tests (24 files, about 10.8k lines). Full findings with severity and evidence are in [`TEST-QUALITY.md`](TEST-QUALITY.md).
- **Fixed in `b5fe677`** (test-only, mutation-checked): 6 High, 7 Medium, Low batch (TQ-1..TQ-14). The High ones are tests that could not fail:
  - webhook raw-bytes and missing-signature tests that were refused before the HMAC ran;
  - replay-window edges;
  - the F2 zombie test, which reaped the child itself;
  - the `.orchestrator` self-trigger guard, whose glob never matched;
  - the store `mutate` flock.
- **Open follow-ups**: FU-1..FU-11. The three High ones:
  - FU-1: the nonce-eviction test doesn't test eviction, and at 12.9 s it is the slowest test;
  - FU-2: **no cron or non-UTC timezone/DST coverage through the engine**;
  - FU-3: the integration opt-in test is vacuous because of the real `monotonic`, which needs a src seam in `build_schedule_engine`.
  - There is also a durability gap (FU-4): the fire store has no directory fsync, and a test pins the wrong fsync count.
- Evidence:
  - baseline: 950 passed, 1 xfailed;
  - committed state in a clean worktree: 959 passed, 1 failed (`test_capped_resume.py::test_only_the_resume_drain_calls_launch_capped_resume`, already failing at HEAD `47afab8` in a file this unit did not touch), 1 xfailed;
  - ruff clean.
- Still NOT done for this task: everything listed under the w21 update above (AC6, AC7, AC8-AC13, AC14, AC16), plus FU-1..FU-11.

## w22 Opus re-review
- By: reviewer agent (w22-02-a1-security-gate-rereview, Opus) · Role: reviewer · Date: 2026-11-11
- Comment: I re-verified the security gate on committed tip `4b9d6a3` (clean `git archive` tree) and did the
  architecture pass. Full write-up: [`REVIEW.md`](REVIEW.md).
- Re-verified by re-running the probes: S-1..S-5 closed, S-6 n/a.
  - **S-8 closed**: the real-socket contract test `tests/service/test_webhook_listener.py` guards the uvicorn internals.
  - **S-10 closed**: `cost_cap_usd` in `ALLOWED_OPTIONS` is deliberate and guarded; `run_id` is not
    forwardable. The stale "CLI only" help is fixed in `b93b576`.
- AC6 (run-id guard) holds. New probe `p6_run_id.py`: traversal shapes and symlink reuse are refused, and
  2 processes × 20 trials give exactly one winner each time.
- Findings:
  - **R-1** (fire store: fixed tmp name, no directory fsync; also TEST-QUALITY FU-4): **fixed in `b93b576`**,
    with tests that fail on the old code.
  - **R-2 (high, open)**: dashboard/CLI usage views persist fire-record cost marks with no lock, so they can roll
    back the engine's concurrent update. Handed to E-Qp4Zm8 (`usage_ledger.py` owner).
  - R-3 (private test patches, non-injected stores) and R-4 (duplicate root guards): open, handed.
  - Suggestions: R-5, R-7. New limitation: R-6.
  - The committed probes broke whole-tree ruff; fixed in `f4f0fc8`.
- Gate on `4b9d6a3`: `pytest -q` **12155 passed, 5 failed**, 35 skipped, 2 xfailed. The failures:
  - 2 install-script tests: environment (no `.git` in the archive); they pass in the checkout;
  - the auth openapi baseline: already fixed by `0f7dac0`;
  - 2 bench fake-pass suites: they reproduce in the checkout and are outside this epic.
- Other tools on `4b9d6a3`: `mypy src` 0 errors; `ruff check .` 75 errors and `ruff format --check .` 5 files,
  all in the probes. At the new tip both are clean. `pytest tests/service tests/schedules` at the new tip: 1283 passed.
- Still NOT done: R-2 fix (blocks Done), AC7 threat-model paragraph, AC14 `make ui-typecheck`/`ui-test` and
  coverage, AC16 (EPIC/ADR-0014 sync, including the late-gate note that AC2's "ALLOWED_OPTIONS unchanged" is
  superseded by `cost_cap_usd`).

### 2026-11-11 — R-2 view-path fix (By: developer unit w23-01 · Role: developer · Date: 2026-11-11)
- Comment: `ReadOnlyFireStore` / `readonly_fire_store` moved from `schedules/usage_view.py` to `service/fire_store.py`
  (in-memory `update_governance`, never persists). The CLI views already used it; the dashboard usage API
  (`ui/service.py` `schedule_usage`) built a writable `UsageLedger` and now uses it too. Governance semantics unchanged;
  the engine's own ledger still persists marks. `ao resume` (a write path) is unchanged.
- Regression tests (each fails with the view ledger on a writable store, passes with the fix):
  `tests/service/test_usage_ledger.py::test_r2_*` (2), `tests/ui/test_schedule_usage_api.py::test_usage_api_never_writes_fire_records`.
- R-2 closed. Remaining open: AC7, AC14 UI gates/coverage, AC16 (unchanged).

- 2026-11-11 (tester, w23-03): TEST-QUALITY FU-1 and FU-2 closed (commit 5e118bc). Engine DST tests: tests/service/test_schedule_engine_dst.py. FU-3..FU-11 remain open.
- 2026-11-11 (tester, w23-04): TEST-QUALITY FU-5, FU-6, FU-7 done; FU-9 partly (concurrency, secret_file oversize/FIFO, listener recv); FU-8, FU-10, FU-11 deferred with reasons in TEST-QUALITY.md. FU-3/FU-4 still open.

## w23-05 closeout
- By: developer agent (w23-05-a1-security-gate-closeout) · Role: developer · Date: 2026-11-11 · Docs only, no code changed.
- **AC7 done.** Threat-model delta paragraph and the AC7a webhook-vs-dashboard-auth confirmation are in
  [`SECURITY.md`](SECURITY.md) ("Threat-model delta") and in ADR-0014 "Late-gate corrections (2026-11-11, T-Se4Bk5 closeout)".
  It names workflow hooks, agent tool permissions and the `schedules` opt-in flag, and confirms `ao service add` as the
  trust boundary, with one correction: the new exposure class is unattended execution of already-trusted workflow content.
- **AC14 UI + coverage evidence (real runs, this tree, 2026-11-11):**
  - `make ui-typecheck` (`tsc -b --noEmit`): exit 0, no errors.
  - `make ui-test` (vitest): 72 files, **915 passed**, 0 failed.
  - `uv run pytest -q tests/service tests/schedules --cov=agent_orchestrator.schedules --cov=agent_orchestrator.service`:
    first run **1289 passed, 4 failed** (3 in `test_schedule_engine_dst.py`, 1 in `test_service_schedule_integration.py`); the
    tree had other units' uncommitted edits in `src/` (R-2 area: `fire_store`, `usage_view`, `schedule_engine`, `webhook`) while
    it ran. Re-running those two files alone: **40 passed**. I read the 4 failures as transient interference from
    concurrent edits, not as a defect I could attribute, but I did not isolate the cause.
  - Coverage of that run (floor 80 %, 90 % for `webhook.py`): `webhook.py` 97 %, `schedule_engine.py` 95 %,
    `fire_store.py` 99 %, `schedule_launch.py` 96 %, `watch.py` 96 %, `statefile.py` 96 %, `schedules/cli.py` 94 %,
    `schedules/models.py` 98 %, `schedules/store.py` 93 %, `schedules/usage_view.py` **78 % (below the 80 % floor; follow-up)**.
    Aggregate for the two packages 96 %.
  - Whole-tree `pytest -q`, `ruff` and `mypy src` were **not** re-run by this unit: the shared tree is dirty with other
    units' edits, so a whole-tree number would not describe committed code. The last clean numbers are the w22 ones above.
- 2026-11-11 (developer, w24-02): REVIEW R-3 renames closed in 7e99458: `governance_gate.read_regular_file`, `supervisor.slug_for_root`, public CLI clock `schedules.cli.now_utc` (was `_now`; `now` avoided: shadowed by locals); call sites/tests/HLD updated, no behaviour change. ruff/mypy clean on touched files; tests/service + tests/schedules 1314 passed. Untouched (not from this branch): format drift in `_build_info.py`; E501 in another unit's new `tests/service/test_schedule_engine_fu8.py`.
- **AC16 synced:** EPIC.md and the epic STATUS.md updated; ADR-0014 gained a late-gate section. AC2's wording
  "`ALLOWED_OPTIONS` unchanged" is **superseded**: `cost_cap_usd` was added to the registry-derived `ALLOWED_OPTIONS` by the
  governance epic. It is deliberate and guarded (still not in `SCHEDULE_RUN_ARG_NAMES`, so a schedule file cannot set it).
- **Still open:** R-2 (high; fire-record cost-mark writes from dashboard/CLI usage views are unlocked) — fix owned by
  unit **w23-01**; re-verify after it lands, then this task can be marked Done. Also open: R-3, R-4 (handed), FU-2..FU-11
  residue, `usage_view.py` coverage 78 %, whole-tree gate re-run on a clean tree.
- 2026-11-11 (developer, w23-02): TEST-QUALITY FU-3 and REVIEW R-3 (clock seam + injectable stores) / R-4 (webhook + governance gate use LocalFsArtifactStore.resolve) closed. `build_schedule_engine(monotonic=)`, `ScheduleEngine.clock/.monotonic`, optional `fire_store/state_store/event_log`. Opt-in integration test now ticks twice past the rate limit with an opted-in control; mutation-checked (dropping the seam or forcing opt-in fails). R-3 renames (`read_regular_file`, `slug_for_root`, public CLI clock) remain open.
