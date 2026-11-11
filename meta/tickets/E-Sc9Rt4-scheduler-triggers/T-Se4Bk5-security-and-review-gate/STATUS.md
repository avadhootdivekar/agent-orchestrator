# STATUS

- ID: `T-Se4Bk5-security-and-review-gate`
- Updated At: 2026-11-11
- State: In Progress (S-1..S-10 closed or limitation; REVIEW.md AC6/AC8-AC13 done with R-2 (high) open; AC7, AC14 UI/coverage, AC16 not done)
- Owner: reviewer agent (overseer w20-02)

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
