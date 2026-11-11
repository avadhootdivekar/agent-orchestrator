# STATUS

- ID: `T-lTW88y-security-and-review-gate`
- Updated At: 2026-11-11
- State: Partial — early + final gate run, all Critical/High closed; NFR-6 delta vs baseline and ADR acceptance open
- Owner: reviewer (gate `w20-03`, fixes `w21-02`, re-review `w22-03`, Opus)

## Reconciliation 2026-11-11 (By: developer · Role: developer · unit `w24-07-a1-task-status-sync` · docs only)
- Checked against branch `ad/enhancements-10nov` at HEAD `1d5c6f9`. Any older line below that says "not started", "uncommitted" or "in the working tree" is superseded by this block where they disagree.
- State: **Partial — early + final gate run, all Critical/High closed; NFR-6 delta vs baseline and ADR acceptance open**
- Done: early gate (design review w01-07), final gate on committed HEAD (w20-03), fixes (G-1, H-1, H-2, W-1, F-1..F-3, N-1, M1, M5) and a clean-clone re-review (w22-03) with mutation evidence; checklist AC2 (a)-(h) PASS at the committed tip. C-1 and all High findings are closed.
- **Unmet / open**: **AC4** NFR-6 full-suite delta vs the epic baseline (no pre-epic baseline fixture committed; the only whole-tree numbers are the T-Se4Bk5 gate on `4b9d6a3`: 12155 passed, 5 failed outside this epic); open Info: W-4, I-1, I-6, I-7, I-9; ADR-0025 stays **Proposed** until the epic owner accepts it. The gate must be re-run after T-8knqVe's resume route lands.

## Re-review on the committed tip (w22-03, Opus)
- By: reviewer agent · Role: reviewer · Date: 2026-11-11
- Comment: re-ran the gate on committed code only (fresh `git clone` of the branch, never the shared working tree).
  The H-1/H-2/W-1 fixes (`4b9d6a3`) were verified by mutation. Every open lower finding and every w01-07 Medium (M1–M6)
  got an individual verdict. Four more small, clear gaps were fixed with tests in `884c167` and `5f51452`. Each new test
  is mutation-checked: it fails with the fix removed or on the pre-fix commit.

### Mutation evidence for the w21-02 fixes (clone of `4b9d6a3`)
| Fix | Mutant | Killing test |
|---|---|---|
| H-1 | `_retry_hits_cost_cap` break disabled | `test_engine_cost_cap.py::test_h1_retry_stops_when_run_cap_reached_between_attempts` |
| W-1 | per-attempt `blind_attempts += 1` removed | `test_engine_cost_cap.py::test_w1_blind_attempt_after_actuals_attempt_is_counted` |
| H-2 (409) | `_check_cost_cap_option` dropped from `resume_run` | `ui/test_cost_cap_resume_guard_ltw88y.py::test_governed_run_resume_rejects_cost_cap` |
| H-2 (bound) | `MAX_COST_CAP_USD` check disabled | `ui/test_cost_cap_resume_guard_ltw88y.py::test_validate_cost_cap_has_upper_bound` |
| F-3 | `usage?at=` year guard disabled | `ui/test_schedule_usage_api.py::test_usage_at_out_of_range_is_400_not_500` |
| F-1 | `min(.., MAX_USD)` removed | **none at `4b9d6a3`** (440 passed). New test `test_f1_max_add_without_any_cost_cap_is_bounded_by_max_usd` kills it |
| F-2 | request text bound disabled | **none at `4b9d6a3`** (440 passed). New test `test_f2_unbounded_or_unsafe_request_text_is_rejected` kills it |

### Verdicts, all findings
| ID | Verdict | Evidence |
|---|---|---|
| C-1 | **Closed** (`3e2870b`) | `--cost-cap-usd` wired. `test_e2e_cli_cost_cap.py` runs end to end |
| G-1 | **Closed** | `O_NOFOLLOW`. 3 tests in `test_governance_security_gate_ltw88y.py` |
| H-1 | **Closed**, residual I-6 | Mutation above. The bound is now ≈ P × C + one attempt per lane |
| H-2 | **Closed**, residual I-7 | Mutation above. `start_run` and `resume_run` validate the cap; a governed resume gets 409 |
| W-1 | **Closed** | Mutation above |
| W-2 | **Info, accepted** | Unchanged since `3e2870b`. Resume read-modify-writes run under the `usage.lock` flock, on the engine thread |
| W-3 | **Accepted, documented** | HLD §13.3 ("Agents can write `state.json`" / "The engine's in-run gate trusts `state.json`") records the residual and the ADR-0016 D9 acceptance. The high-water mark and the reservation floor bound it. No code change |
| W-4 | **Open, Info** | `FireIntentStore.write` is still `exists()` then replace. `b93b576` moved it to `atomic_write_bytes(durable=True)` with a unique tmp file, but the final step is still `os.replace`, not an exclusive create. Safe under `usage.lock` and a single engine |
| I-1 | **Open, Info** | `FireIntentStore.prune` is still not called in `src/`. The only `.prune(` hit is the unrelated `cache/cli_ops.py:538` |
| I-2 | **Accepted** | Unchanged (directory-component race; the attacker already has workspace write access) |
| I-3 | **Accepted** | Unchanged (catch-up fires are charged to their scheduled window by design) |
| I-4 | **Closed** (export artefact) | `_build_info.py` is gitignored (`.gitignore:227`), so `ruff format --check .` in a git checkout skips it. It only showed up because the earlier gate ran on a `git archive` export |
| F-1/F-2/F-3 | **Closed** | F-1/F-2 had no test. Tests added (above). F-3 was already tested |
| **N-1** | Warning, **fixed** `884c167` | `_run_with_retries` returned a fresh `TaskResult(status="cancelled")` when a cancel was seen between attempts. That dropped the cumulative actuals of the attempts that had already run: probe on `4b9d6a3`, $0.30 spent → `cumulative_cost_usd 0.0`, `actuals_available False`, counted blind. The cancelled result now carries the cumulative fields. Test `test_n1_cancel_between_attempts_keeps_spent_cost` |
| **I-6** | Info, accepted | A blind executor (no `cost_usd`) still retries past the cap: probe, 4/4 attempts at cap $0.01. The engine cannot see blind spend. The ledger charges `max(observed, reserved)`. Document next to HLD §9.3 (`T-Hq3vXd`) |
| **I-7** | Info | The template instantiate-and-start route (`ui/service.py` `create_run(start=True)`, `options=` at ~l.653) skips `_check_cost_cap_option`. An out-of-range cap is still refused by `ao run` (exit 2, in the run log) rather than with a 400. The run is new, so not governed. No bypass |
| **I-8** | Info, **closed by `f4f0fc8`** (another unit) | At `5f51452`, `ruff check .` on a clean clone gave 75 errors, all in the committed `output/E-Sc9Rt4-scheduler-triggers/se4bk5-probes/*.py`, and `ruff format --check .` flagged the same 5 files. `src`/`tests` were clean. `f4f0fc8` (T-Se4Bk5) made the probes lint/format clean |
| **I-9** | Info, pre-existing | `mypy src` on a fresh clone (no generated `_build_info.py`): 4 `no-redef` errors in `_version.py:26`. In a built checkout it is clean (233 files). Suggest a `# type: ignore[no-redef]` or a `TYPE_CHECKING` split |

### w01-07 High/Medium, re-verified one by one (AC5)
| ID | Verdict | Evidence |
|---|---|---|
| H1 | Implemented | `_has_remaining_work` gates the boundary trip. `test_serial_two_tasks_halts_at_boundary_with_second_pending` |
| H2 | Implemented | `TaskRunState.actuals_available` / `attempts_without_actuals`, now per attempt (W-1). `usage_ledger.run_confidence` |
| M1 | **Was partial, fixed `884c167`** | The ledger charged a partly blind run `max(observed, reserved)`, but the retire hook (`_finalize_governance_once`) persisted `cost_confidence=actual` at the observed cost. That was the strict xfail pinned by w21-06. Once `state.json` is gone, the window under-charged. The hook now uses `run_confidence` on a real `RunState`: partial/unknown → `max(observed, reserved)`, with the ledger's confidence. The xfail is removed and `test_retire_hook_confidence_matches_ledger_for_partly_blind_run` passes |
| M2 | Implemented | `queued_scheduled_for` (`fire_store.py`, `schedule_engine.py:~1191`). `test_queued_fire_over_quota_is_burned_in_original_window`, `test_queued_fire_within_quota_drains` |
| M3 | Implemented | Trip-after-latest-resume rule (L3 included). `test_retire_hook_ignores_stale_trip_after_resume_and_honours_fresh_one`, `test_l3_unparseable_trip_time_after_resume_fails_closed_and_equal_time_is_stale` |
| M4 | Implemented | The cost-cap halt goes through `DispatchPrep/SettleResult(signal="halt")` and ends `failed` with pending work (`test_serial_two_tasks_halts_at_boundary_with_second_pending`, AC16 tests in `test_engine_cost_cap.py`) |
| M5 | **Was partial, fixed `5f51452`** | (a) `Option.sticky_on_resume` + `SOURCE_STATE` were present (`test_run_options_cost_cap.py`). (c) The dashboard 409 was present (H-2). The CLI WARNING on `ao resume --cost-cap-usd` for a governed run (T-et7Edf AC14c) was **missing**. It is now added as `cli._warn_if_schedule_governed`: it warns and never refuses. Test `test_m5_resume_with_explicit_cap_warns_only_for_a_governed_run[True/False]` (the governed case fails on `4b9d6a3`) |
| M6 | Implemented (Info) | `FakeExecutor.token_outputs[..]["cost_usd"]` (`executors/fake.py:285`), used by the CLI e2e tiers (`test_e2e_cli_cost_cap.py`, `schedules/test_cost_cap_cli_e2e.py`). The AC's named `fake_cost_script` fixture in `tests/conftest.py` does not exist. Each suite patches `DispatchExecutor` locally instead. Cosmetic |

### Checklist AC2 at the committed tip
(a) **PASS**: H-2 is closed and `run_args.cost_cap_usd` is rejected. The CLI escape hatch now warns (M5). (b) **PASS**:
F-1 ceiling, with a test now. (c) PASS. (d) PASS (W-3 accepted). (e) PASS. (f) PASS. (g) **PASS**: `usage` and
`governance-events` are GET-only and authenticated. There is no resume POST, so the Origin gate is N/A (w21-02). (h) PASS.
Related: production bug **B-1** (the `ao schedule resume` shims dropped `launch_capped_resume`, found by w21-03) is
fixed at `b93b576`: both shims forward it, and `tests/service/test_deferred_launcher.py` covers it.

### Flake: `test_parallel_siblings_are_drained_not_killed`
**Not a real race.** In `run()`, the FILL pass submits both `a` and `b` before the first DRAIN (`engine.py`, the FILL
loop, then `as_completed`), so `b` always runs, whatever the thread timing. Settle order varies, and the assertions
already accept both orders (max trip 0.9). The test uses `max_attempts=1`, so the H-1 worker-thread path is never
reached. Stress result: **400/400 passed** under 24 busy-loop CPU hogs plus a concurrent full suite. The one failure
w21-02 saw was in the shared working tree while other units' edits were in flight, and that unit briefly ran
`git stash push -- src`. That environmental cause is the most likely explanation. No change.

### AC4 / NFR-6 gate numbers (committed tip)
No governance-specific baseline delta exists. Whole-tree numbers are the T-Se4Bk5 gate on `4b9d6a3` (same branch tip as the fixes above): `pytest -q` 12155 passed, 5 failed (2 install-script tests that need a `.git`, auth openapi baseline fixed by `0f7dac0`, 2 bench fake-pass suites outside this epic), 35 skipped, 2 xfailed; `mypy src` 0 errors; `ruff check .`/`ruff format --check .` clean at the later tip after `f4f0fc8`. AC4's delta vs a pre-epic baseline is **not** met (see Reconciliation).

### What is still open
- W-4, I-1, I-6, I-7, I-9 (all Info, owners above). ADR-0025 stays **Proposed** until the epic owner accepts it.

## Earlier update (w20-03 gate)
- By: reviewer agent · Role: reviewer · Date: 2026-11-11
- Comment: I ran the final security and review gate on **committed HEAD** (`a3d0195`, exported with `git archive` so
  uncommitted carry-over work could not leak in). I reviewed `governance_gate.py`, `usage_ledger.py`, `fire_store.py`,
  `schedule_launch.py`, `schedule_engine.py` (`_finalize_governance*`, sweep, adopt), `schedules/governance.py`, the
  engine cost-cap diff (`1c0bfeb`) and `_run_with_retries`. I landed one fix (G-1) with tests. Everything else is
  recorded below as a follow-up for the owning task.
- **Not reviewed (uncommitted at review time, owned by carry-over units):** `capped_resume.py` (pause/resume, `add_usd`,
  the resume POST and `ao schedule resume`), the cli.py `--cost-cap-usd` wiring, the `/api/schedules/{id}/usage` and
  `governance-events` routes, and `schedules/usage_view.py`. Their checklist items are marked **deferred**. The gate
  must be re-run on them once they are committed.
- **Re-check on `3e2870b`** (landed by concurrent units while this gate ran; it adds pause/resume and the
  `--cost-cap-usd` wiring): **C-1 resolved**, since `_render_options({'cost_cap_usd': 1.5})` now gives
  `['--cost-cap-usd', '1.5']`. **H-2 still open**: `ui/service.py`/`ui/app.py` have no `cost_cap_usd` guard and
  `validate_cost_cap_usd` has no upper bound. **W-2 downgraded to Info**: `capped_resume` re-checks and rewrites under
  the `usage.lock` flock and is drained inside the engine tick (same thread as the ledger and finaliser). Only the
  rollback and the expiry sweep write outside the lock, and both run on that same thread. The full
  `capped_resume.py` audit is still owed: checklist (b) and (g) and AC5 stay deferred.

## Fix landed (this unit)
- **G-1 (Low, fixed):** `governance_gate._read_regular_file` now opens with `O_NOFOLLOW`. `read_prompt` resolves and
  root-checks the path, then opens it. A symlink swapped into the last component between those two steps used to be
  followed, which could pull ≤256 KiB of a file outside the workspace into the run's prompt. Now the open fails with
  `ELOOP` and the fire is refused as `prompt_missing/unreadable`. Legitimate in-root symlinks still work, because their
  resolved target is what gets opened. Tests: `tests/service/test_governance_security_gate_ltw88y.py` (3 tests). They are
  mutation-checked: 2 of them fail on HEAD without the fix.

## Findings (Critical / High / Warning / Info)
| ID | Sev | Where | Finding | Owner / next step |
|---|---|---|---|---|
| C-1 | Critical (usable bar) — **resolved at `3e2870b`** | HEAD `run_options.py:376-395`, `cli.py` | At HEAD `cost_cap_usd` is not in `OPTIONS` and `ao run`/`ao resume` have no `--cost-cap-usd`. Verified on the HEAD export: `_render_options({'cost_cap_usd': 1.5}) == []` and `ao run --cost-cap-usd 1.5` gives "No such option". As a result **every governed fire that reserves money fails closed** as `launch_failed: cost_cap_not_forwarded`. That is safe, but the A2 usable bar ("cost cap enforcement works end to end") is not met on HEAD. The working tree has the wiring, uncommitted. | `T-et7Edf`: commit the cli.py + `OPTIONS` wiring (carry-over unit) |
| H-1 | High | `engine.py` `_run_with_retries` (`for attempt in range(1, max_attempts+1)`, ~l.5192) | The in-task retry loop never re-checks the run cap between attempts. The cap is only consulted at dispatch prep and at the settle boundary, so once one task has been dispatched it can make `max_attempts` paid attempts past the cap. The real overshoot bound is ≈ `P × C × max_attempts`, not `P × C`. HLD §9.3's "`max_parallel: 1` makes the bound one task" recipe is wrong whenever retries > 1. | `T-et7Edf` follow-up: before attempt k>1, stop when `run settled cost + cum_cost_usd ≥ state.cost_cap_usd`. This needs an AC3 scope exception (a fifth engine site). Fix the HLD §9.3 bound text in `T-Hq3vXd` |
| H-2 | High (materialises with C-1) | `ui/app.py` `POST /api/runs/{id}/resume`, `POST /api/runs`; `run_options.validate_cost_cap_usd` | Once `cost_cap_usd` is registered, the generic dashboard resume route forwards `{"options": {"cost_cap_usd": N}}` to `ao resume --cost-cap-usd N`. That **replaces** a scheduled run's sticky cap with an absolute, unbounded value (no upper limit), bypassing the bounded `add_usd` resume. Checklist (a) fails. The HLD §16 accepts this for the explicit CLI escape hatch, not for a route. Mitigation: the ledger still charges the spend, so later fires skip. | `T-nI4DBf`/`T-8knqVe`: reject `cost_cap_usd` in the generic resume options when `FireIntentStore.find_by_run_id` finds a governed record (route it to the schedule resume). Consider an upper bound (`MAX_USD`) in `validate_cost_cap_usd` |
| W-1 | Warning | `engine.py` `_accumulate` + `_run_with_retries` | `TaskResult.actuals_available` is cumulative across attempts. A killed or blind attempt that follows an attempt with actuals in the same call does not bump `attempts_without_actuals`. The ledger then sees `actual` even though that attempt's spend is lost (the M1/H2 case). | `T-et7Edf` follow-up: count blind attempts inside the loop, not from the cumulative result |
| W-2 | Info (downgraded at `3e2870b`) | `fire_store.update` / `update_governance` | These are lock-free read-modify-writes. That is safe at HEAD: webhook only enqueues, the engine is single-threaded, and `engine.lock` keeps it a singleton. But uncommitted `capped_resume.py` writes governance from the resume POST/CLI, which risks lost updates (status/pid regress, or an on-disk high-water mark regress). | `T-nI4DBf`: take `usage.lock` (or a per-record lock) around the resume RMW, and re-review |
| W-3 | Warning | `usage_ledger` / `state.json` | The high-water mark lives outside the workspace and only rises, but it is only sampled at gate/finalise probes. A run that rewrites its own (workspace-writable) `state.json` before finalisation can under-report. | Document in `T-Hq3vXd`; optionally take the max with the summariser/event-log cost at finalise |
| W-4 | Info | `FireIntentStore.write` | Existence check followed by `os.replace` is not an exclusive create. It is safe today under `usage.lock` and the single engine, but becomes fragile if a second writer appears. | Use `os.link`/`O_EXCL` on the tmp→final step when convenient |
| I-1 | Info | `FireIntentStore.prune` | Not called anywhere in `src/`. If it gets wired, the count-based cut (10 000 records, suppressed ones included) could delete in-window records and refund quota. | Keep it unwired, or exclude records in any open window |
| I-2 | Info | `read_prompt` | G-1 covers the last path component only. A directory-component swap race remains, which would need `openat`-walking. It is low impact: the attacker would already have workspace write access. | Accept |
| I-3 | Info | windows by `scheduled_for` | Catch-up fires are charged to their scheduled window (by design, HLD §10). Around midnight, one wall-clock day can run more than `max_runs`. | Accept, documented |
| I-4 | Info | HEAD `ruff format --check` | `_build_info.py` (generated) would be reformatted. | Exclude it from format or regenerate it formatted |

Counts at `a3d0195`: Critical 1, High 2, Warning 3, Info 4 (+ G-1 Low, fixed). Open at `3e2870b`: High 2, Warning 2, Info 5. Not yet mirrored into ADR-0025: that file has
uncommitted edits by another unit, and its "Early-gate corrections" section should take these counts when it is
committed (AC1 sync is open).

## Follow-up update (w21-02, 2026-11-11)
- By: developer agent · Role: developer · Date: 2026-11-11
- Comment: fixed the open High/Warning findings and audited the previously deferred code. Test counts are in the run report.

| ID | Status | Fix / evidence |
|---|---|---|
| H-1 | **Fixed** | `Orchestrator._retry_hits_cost_cap` is consulted between attempts in `_run_with_retries` (settled run cost + this call's cost so far >= cap stops further attempts). HLD §9.3 bound text corrected. Tests `tests/test_engine_cost_cap.py::test_h1_*` (mutation-checked: fail without the engine change). |
| H-2 | **Fixed** | `DashboardService._check_cost_cap_option`: `options.cost_cap_usd` is refused (409) on resume of a governed run (`is_governed_run`), and bounded (400) on resume/start. `validate_cost_cap_usd` has the upper bound `MAX_COST_CAP_USD` (100 000). Tests `tests/ui/test_cost_cap_resume_guard_ltw88y.py`. |
| W-1 | **Fixed** | `TaskResult.blind_attempts` counted per attempt in the loop; `_accumulate_actuals` adds it. Tests `test_w1_*` (mutation-checked). |
| F-1 | Low, **fixed** | `capped_resume.max_add` had no ceiling with no per-run cap and no period cap (`add_usd` unbounded -> unbounded new cap). Now `min(remaining, MAX_USD)`. |
| F-2 | Low, **fixed** | Drop request `request_id`/`requested_by` were unbounded free text stored in the fire record and events. `from_json` now requires `request_id` to match `[A-Za-z0-9_-]{1,64}` and `requested_by` <= 128 chars. |
| F-3 | Low, **fixed** | `GET /schedules/{id}/usage?at=9999-12-31...` raised `OverflowError` (500). The route now returns 400 for years outside 1970..8999. |
| I-5 | Info | A drop-request file is trusted as far as the state dir is (local user). The drain re-runs `check_resume` under the flock and recomputes `new_cap` itself, ignoring the file's `new_cap_usd`. No finding. |

- **Checklist (b)** PASS (per-resume ceiling `per_run_usd * MAX_RESUME_FACTOR`, headroom in the ORIGINAL fire's windows, re-check under the flock; F-1 closed the one gap). **(g)** PASS: `usage` and `governance-events` are GET-only, authenticated by omission from `DASHBOARD_ROUTE_POLICIES`, `limit` clamped 1..500, `id` max 128 and checked against the schedule file, redacted keys dropped, scoped to this workspace. There is **no resume POST route** in the dashboard (resume is the CLI drop-request), so the Origin gate for it is N/A; the existing POST routes keep the generic Origin/Content-Type gate.
- **AC5 drop-request schema:** PASS with F-2. **M1-M6 individually re-verified:** still not done (only H1/H2 were).
- **Still open:** W-3, W-4, I-1..I-4 (unchanged above). W-2 stays Info.
- **Not done:** the final gate has not been re-run on the committed tip by a reviewer; ADR-0025 stays Proposed.

## AC2 security checklist (evidence)
- (a) **FAIL / partial.** `run_args.cost_cap_usd` is rejected (`schedules/models.py:95`,
  `test_run_args_rejects_cost_cap_usd`). Workflow paths are root-guarded (`ScheduledLauncher._guarded_path`, tests in
  `test_schedule_launch.py`). Templates are by name only (`_find_template`). The launcher adds zero argv surface (the
  `_render_options` allow-list, `test_dropped_cost_cap_fails_closed_before_spawn`). The failure is the dashboard
  resume route (H-2). The resume POST / `ao schedule resume` are deferred (uncommitted).
- (b) **Deferred.** `add_usd` lives in uncommitted `capped_resume.py`. `MAX_RESUME_FACTOR` exists in
  `schedules/governance.py`.
- (c) **PASS.** `read_prompt` rejects absolute paths, `..` and symlink-out (`test_prompt_symlink_escaping_root_is_refused`).
  The read is bounded to `MAX_PROMPT_BYTES+1` (`test_prompt_matrix` too_large). FIFOs are refused without blocking
  (`test_l6_fifo_prompt_does_not_block_and_is_refused`). The TOCTOU symlink case is now covered by G-1 tests.
- (d) **PASS (W-3 caveat).** `cost_observed_usd` lives in `<state_dir>/schedules/fires/…` and is max-merged
  (`test_ac4_update_governance_refuses_to_lower_mark`).
- (e) **PASS.** `test_run_args_rejects_cost_cap_usd`.
- (f) **PASS at HEAD (code reference).** The reserved event carries `prompt_sha256[:12]` (`schedule_launch._reserve`).
  The skip detail carries only path and sha prefixes (`read_prompt` prompt_changed). No prompt text goes into events.
  The G-1 test also asserts that the refused fire's result does not contain the out-of-root content.
- (g) **Deferred.** The new routes (`usage`, `governance-events`, resume POST) are uncommitted in `ui/app.py`.
- (h) **PASS.** `test_gate_exception_fails_closed_and_feeds_quarantine`, `test_gate_failure_inside_launcher_fails_closed`,
  `test_dropped_cost_cap_fails_closed_before_spawn`.

Reservation/quota races: `check_and_reserve` re-gates under an exclusive flock on `usage.lock`. It invalidates the ledger
index, reads the prompt once and writes `intended` (reservation and prompt pin included) in one fsync'd write, then
unlocks before spawn. Quotas count `intended`/`launched`/`orphaned` but not `suppressed`/`launch_failed`. Running
fires hold `max(0, reserved − mark)`. Blind or killed runs are charged `max(observed, reserved)` both by the
finaliser and the ledger. Period-only cost reserves the whole remaining budget (one concurrent run per period, by
design). NaN/inf/negative limits are rejected at load (`allow_inf_nan=False`, `gt=0`). No race found at HEAD beyond W-2.

## AC3 scope
- `engine.py` (`1c0bfeb`): the four named sites are (a) `cost_cap_reached`, (b) the dispatch-prep call, (c) the boundary
  call (with `_has_remaining_work`) and (d) the `_start_llm_lane` min cap. The extra hunks are all justified, so none
  were reverted:
  - the constructor `cost_cap_usd` parameter and its assignment (plumbing for AC1);
  - the state stamp at start (sticky persist, AC2);
  - the `_trip_cost_cap` method shared by (b) and (c);
  - the `_accumulate` AC13 counter (owned by T-et7Edf H2);
  - the imports and `Final`.
- `evaluate()` order: not re-diffed hunk by hunk in this unit. It is covered by T-PpE5lf's AC1 order tests (green in the
  1066-test service+schedules run).

## AC4 / NFR-6 (actual numbers, HEAD `a3d0195` export)
- Full `pytest -q`: **11930 passed, 6 failed, 35 skipped** (22m31s). 4 of the 6 failures (`test_install_script` ×2,
  `test_blackbox_grader` HTTP ×2) pass in the git checkout, so they are artifacts of the non-git export. The other 2
  (`tests/bench/test_dev_{core,medium}_suite.py::test_fake_subject_full_suite_run…`) fail in both. They are bench-only
  and unrelated to governance. No epic-baseline count is recorded anywhere, so a delta cannot be stated.
- `tests/service` + `tests/schedules` with G-1: **1066 passed**.
- `ruff check .` clean. `ruff format --check .`: 1 file (`_build_info.py`, I-4). `mypy src`: clean (231 files).
- Coverage: `schedules/governance.py` 100 %, `schedules/windows.py` 100 %, `service/governance_gate.py` 99 %,
  `service/usage_ledger.py` 99 %.

## AC5 (early-gate dev-security follow-up)
- flock: PASS (above). Path guard on `prompt_file`: PASS plus G-1. Drop-request schema and the Origin gate on the POST:
  **deferred** (uncommitted). Re-review of every w01-07 High/Medium: H1/H2 are present at HEAD (`attempts_without_actuals`,
  boundary trip only with remaining work). M1–M6 were not individually re-verified in this unit.

## Next actions
1. ~~C-1~~ committed (`3e2870b`); ~~H-2~~ fixed in w21-02.
2. ~~H-1 and W-1~~ fixed in w21-02 (engine; this is the AC3 scope exception, a fifth engine site).
3. ~~Re-run checklist (a), (b), (g) and AC5 on the committed code~~ done in w22-03 (all PASS; M1/M5 completed).
4. ~~Sync counts into ADR-0025 and the epic STATUS~~ done in w22-03.
5. Remaining Info items: W-4, I-1, I-6 (doc in `T-Hq3vXd`), I-7, I-9. None blocks the gate.
