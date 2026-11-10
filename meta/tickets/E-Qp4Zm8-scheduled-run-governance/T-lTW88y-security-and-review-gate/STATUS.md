# STATUS

- ID: `T-lTW88y-security-and-review-gate`
- Updated At: 2026-11-11
- State: In Progress (final gate run on HEAD `a3d0195`, then spot-rechecked on `3e2870b`; gate **not passed**: H-1/H-2 open)
- Owner: reviewer (run unit `w20-03-a2-security-gate`, Opus)

## This update
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
1. C-1 is committed (`3e2870b`). Fix H-2 before the branch ships: it is live now that the option is registered.
2. H-1 and W-1 in `T-et7Edf` (engine; needs an AC3 scope exception). W-2 in `T-nI4DBf`.
3. Re-run checklist items (a), (b) and (g), plus AC5's remaining items, on the committed pause/resume and dashboard code.
4. Sync the finding counts into ADR-0025's corrections section and the epic STATUS (AC1).
