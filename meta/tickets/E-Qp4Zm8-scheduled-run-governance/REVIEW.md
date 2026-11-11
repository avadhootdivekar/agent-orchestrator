# REVIEW — E-Qp4Zm8 governance code as landed (Opus code review, unit `w29-01-a2-governance-code-review`)

By: reviewer (Opus) · Role: reviewer · Date: 2026-11-11
Branch `ad/enhancements-10nov`, tip `69dd4c0`. Read-only on source: no `src/`, `tests/` or `ui/` file was edited.

## Scope
Governance code as landed: `service/governance_gate.py` (quota + period-cost gate, reservation, prompt read),
`service/usage_ledger.py` (charge rules, high-water mark, readonly views), `service/capped_resume.py`
(pause/resume/expiry, stranded-resume recovery), `service/schedule_engine.py` (`drain_resume_requests`,
`_finalize_governance_once` retire hook incl. `run_confidence`), `service/schedule_launch.py` (fail-closed cap argv,
`launch_capped_resume`), `service/fire_store.py` (`ReadOnlyFireStore`), `engine.py` run cost-cap gate
(`cost_cap_reached`, `_trip_cost_cap`, `_retry_hits_cost_cap`), `cli.py` AC17 trailer `xN`, `schedules/governance.py`
(D-2 model), `schedules/usage_view.py` + `schedules/cli.py` (`usage`, `resume`, limit flags), `ui/app.py` +
`ui/service.py` (`usage`, `paused-runs`, `governance-events`, `POST …/resume-capped`).

## Summary
Design health is good. The gate is pure and clock-injected. Reservation and the `intended` write share one flock and
one atomic write. Launch fails closed when the cap argv is missing (checked before and after spawn). The engine cap is
sticky and non-latching, and the boundary check trips only while work remains. The drain is the single launch
authority, and the dashboard resume body is a bounded `add_usd` with `extra="forbid"`. D-1..D-5 are respected (D-2:
`LimitsSpec.declares_any_limit`, no opt-out; D-3: skip+record, no notifications; D-4: calendar windows in the binding
tz; D-5: `on_cap: pause|abort`, default pause, sticky on resume).

The open problems are in the seams around a resume: a dashboard writer that is still unlocked (R-2c, already open on
T-Se4Bk5 and confirmed again here), and a resume reservation the ledger cannot see until the child starts. There is
also one DRY drift: the dashboard usage body is not the shared `build_usage_doc`, which is why the dashboard has no
overshoot display. No finding is Critical.

Evidence: 13 governance suites run at tip `69dd4c0`: **496 passed, 0 failed** (`test_capped_resume`,
`test_governance_gate`, `test_governance_security_gate_ltw88y`, `test_usage_ledger`, `test_engine_cost_cap`,
`test_e2e_cli_cost_cap`, `test_run_options_cost_cap`, `schedules/test_windows`, `test_governance_model`,
`test_governance_e2e`, `test_cli_governance_e2e`, `ui/test_schedule_usage_api`, `test_tripped_breaker_trailer`).
Two throwaway probes (in `/tmp`, not committed) confirmed G-1 and G-2 below.

## Findings (numbered, severity-ranked)

### High
**G-1 (High) — FIXED (w31-01). The dashboard resume path persists fire-record writes without a lock (R-2c is still open).** `DashboardService.request_capped_resume` now uses `readonly_fire_store(sdir)` for both `fires` and the ledger; test `test_resume_capped_request_never_writes_fire_records` (byte-equality + store type). Closes T-Se4Bk5 R-2c.
- Where: `src/agent_orchestrator/ui/service.py:1506` (`fires = FireIntentStore(sdir)`) and `:1517`
  (`ledger=UsageLedger(fires, …)`).
- Observation: `check_resume` → `_remaining` → `ledger.usage` → `charge` → `_raise_mark` →
  `fires.update_governance(...)`. That is an unlocked read-modify-write of *every* fire in the original fire's
  windows. The `check_resume` docstring (`capped_resume.py:250-253`) and `FireIntentStore.update_governance`
  (`fire_store.py:283-288`) both say that a non-engine caller must use `readonly_fire_store`. The CLI resume path was
  fixed this way (R-2b, `schedules/cli.py` `resume_cmd`). The dashboard route was not. A probe asserted that the
  method still constructs a writable store; it passes.
- Why it matters: a dashboard request can land between the engine's `get` and its persist (retire hook writing
  `cap_state=paused` / `final` / `cost_confidence`, or the drain writing `resumed`). The dashboard's stale merge then
  rolls that write back. A paused run can lose `pause_expires_at`, and a finalised run can lose `final`. Either one
  corrupts governance state (resume-safety). The dashboard also runs in the service process on another thread, so
  "same process" does not make this safe.
- Next step: use `readonly_fire_store(sdir)` for both `fires` and the ledger in `DashboardService.request_capped_resume`.
  Add the regression test already sketched in T-Se4Bk5 REVIEW R-2c (record bytes unchanged after a dashboard resume
  request). Optionally make `ReadOnlyFireStore` enforce this (see G-4).

### Medium
**G-2 (Medium) — FIXED (w31-02). A resume's new reservation is invisible to the ledger until the resumed child marks `state.json` running.** `UsageLedger.charge` now treats `cap_state=="resumed" and not final` like a running run (`mark + max(0, reserved - mark)`); tests `test_g2_*` in `tests/service/test_usage_ledger.py` (probe shape: reserved 4, state failed, $2 spent -> actual 2 / reserved 2) and `test_g2_fire_refused_while_resume_pending` in `tests/service/test_governance_gate.py`. Both fail without the fix.
- Where: `src/agent_orchestrator/service/usage_ledger.py:388-394` (`charge`). The reservation is held only when
  `st.status == "running"`. `capped_resume.py:435-442` writes `cap_state="resumed"` and `reserved_usd=new_cap` under
  the flock, then launches outside it.
- Observation: between the drain's write and the child's first `state.json` save, the run's state is still `failed`
  (paused). `charge` therefore returns `actual=mark, reserved=0`. Probe: record `cap_state=resumed`,
  `reserved_usd=4.0`, state `failed` with $2.00 spent → `WindowUsage(actual_usd=2.0, reserved_usd=0.0)`. The $2.00
  top-up is not held. The same gap stays open permanently if the child dies before writing state; that case is
  under-reserved, not overspent.
- Why it matters: a concurrent pre-fire gate for the same schedule (`overlap: allow`, or a resume and a fire in the
  same tick) can admit a new run against headroom the resume has already claimed. Both runs then spend up to their
  caps, so the period cap (FR-4) can be exceeded by up to `add_usd`. This breaks NFR-1's "reservation written before
  launch is honoured" for the resume path.
- Next step: in `charge`, treat `g.cap_state == "resumed" and not g.final` like the running branch, i.e. charge
  `mark + max(0, reserved - mark)`. Add a ledger test with exactly the probe's shape, plus a gate test that a fire is
  refused while a resume is pending.

**G-3 (Medium, DRY / AC drift) — FIXED (w31-01). The dashboard usage API re-implements the usage document instead of using the shared builder.** `schedule_usage` now maps `build_usage_doc` window rows (UI keys unchanged, skip counts kept); `paused_runs` uses `paused_records` + `usage_view.paused_row` (renamed from `_paused_row`) and exposes `overshoot_usd`, shown in `PausedRuns.tsx`. Tests: `test_paused_runs_and_usage_share_the_cli_paused_row_with_overshoot`. G-8 (`max_add_usd` ceiling) fixed in w32-04.
- Where: `src/agent_orchestrator/ui/service.py:1327-1380` (`schedule_usage`) and `:1461-1495` (`paused_runs`), against
  `src/agent_orchestrator/schedules/usage_view.py` `build_usage_doc` / `paused_records` / `_paused_row`.
- Observation: `usage_view.py`'s module docstring says its dict "IS the dashboard API body … so the two surfaces never
  disagree". The dashboard builds its own rows with different keys (`window_key`/`runs_used`/`runs_max` against
  `key`/`runs`/`max_runs`) and its own paused list. The paused list omits `overshoot_usd`, which is the T-8knqVe AC5
  gap ("dashboard shows none; CLI only"). Two shapes now need two drift guards.
- Why it matters: CLI and dashboard numbers and fields can diverge; FR-5 observability is split.
- Next step: have `schedule_usage`/`paused_runs` delegate to `build_usage_doc` (with a readonly ledger) and map into
  the UI type. Use `_paused_row` for the paused list; that also closes AC5.

### Warnings
**G-4 (Warning) — FIXED (w32-01). `ReadOnlyFireStore` is read-only in name only.** `ReadOnlyFireStore` now raises `PermissionError` from `write`, `write_suppressed`, `update`, `_persist`, `_delete`, `prune` and `reconcile_orphans`; reads and the in-memory `update_governance` are unchanged. Test: `test_readonly_store_refuses_every_mutator_and_reads_still_work` (`tests/service/test_resume_queue_readonly.py`).
- Where: `src/agent_orchestrator/service/fire_store.py:410-425`.
- Observation: it overrides only `update_governance`. `write`, `write_suppressed`, `update` and `_persist` still
  persist.
- Why it matters: the safety of every view caller (G-1 class) depends on that caller never reaching another mutator.
  A future view helper that calls `update` would silently write.
- Next step: override the other mutators to raise (`PermissionError`/`RuntimeError`), and add a unit test
  that each one refuses.

**G-5 (Warning) — FIXED 2026-11-11 (w32-02). The retire hook labels a partly blind run `actual` when the binding has no cost reservation.**
- Where: `src/agent_orchestrator/service/schedule_engine.py:1571` (`confidence = "actual" if observed > 0 …`). The
  `run_confidence` override applies only inside `if g.reserved_usd is not None and (…)` (`:1578-1584`).
- Observation: on quota-only bindings (`reserved_usd is None`), a run with `attempts_without_actuals > 0` is persisted
  `actual` once its `state.json` is gone. The ledger, which derives from state, says `partial`. The Q-1 fix
  (`884c167`) only covers bindings that reserve.
- Why it matters: `usage` and the dashboard confidence flags under-report uncertainty after state cleanup. Charging is
  unaffected, since quota-only bindings have no cost cap.
- Fix: `_finalize_governance_once` now adopts a conservative `run_confidence` (partial) for non-killed runs outside the reservation branch; test `test_g5_quota_only_binding_partly_blind_run_is_persisted_partial`.
- Next step (was): set `confidence = conf if conf != "actual" else ("actual" if observed > 0 else g.cost_confidence)`
  outside the reservation branch, and add a quota-only partial test.

**G-6 (Warning, DRY). Two cost metrics in the engine gate.** — **FIXED** (2026-11-11, `w32-03`): `compute_run_usage_totals` accepts a `RunState` or an iterable of task states; new `engine.settled_run_cost_usd` (worker-thread-safe `list()` snapshot) is used by both `cost_cap_reached` and `_retry_hits_cost_cap`; agreement test `test_g6_both_gates_share_one_cost_computation` in `tests/test_engine_cost_cap.py`.
- Where: `src/agent_orchestrator/engine.py:3019` (`_retry_hits_cost_cap` sums `ts.cumulative_cost_usd` itself) against
  `engine.py:403-417` (`cost_cap_reached` → `compute_run_usage_totals`).
- Observation: today they are the same sum. The retry gate re-implements it inline so it can iterate a snapshot from
  a worker thread.
- Why it matters: if `compute_run_usage_totals` ever adds a term (e.g. sentinel lane cost), the retry gate and the
  dispatch/boundary gate will disagree about whether the cap is reached.
- Next step: let `compute_run_usage_totals` accept an iterable of task states (or a snapshot helper), and call it
  from both sites.

**G-7 (Warning) — FIXED 2026-11-11 (w32-02). Overshoot mixes metrics.**
- Where: `src/agent_orchestrator/schedules/usage_view.py:114` and `schedule_engine.py` (`run_cost_capped`
  `overshoot_usd`). Both compute `cost_observed_usd − run_cost_cap_usd`.
- Observation: `cost_observed_usd` includes the summariser's cost and, for blind/partial runs, the conservative
  `max(observed, reserved)` charge. The cap is the engine metric (L3, `UsageLedger.engine_cost_usd`).
- Why it matters: the reported overshoot is inflated by summary cost, and for a blind run whose charge is set to
  `reserved` it can be ~0 or misleading. D-5's "overshoot measured per fire and displayed" should use one metric.
- Fix: new optional `FireGovernance.engine_cost_usd` persisted at finalisation; `run_cost_capped` event gains `engine_cost_usd` and `overshoot_usd` uses it; `paused_row` overshoot uses it (legacy records fall back to `cost_observed_usd`) and exposes `engine_cost_usd` next to `cost_usd` (the charge). Tests `test_g7_*`, `test_paused_row_overshoot_uses_engine_cost_metric`. Followup: `usage_ledger.Charge.overshoot_usd` still uses `observed` (usage_ledger.py was out of scope for this unit).
- Next step (was): compute overshoot from `engine_cost_usd` (the ledger already has `Charge.overshoot_usd` doing this from
  `observed`), and label summary cost separately.

### Suggestions / Info
**G-8 (Low) — FIXED (w32-04).** `paused_runs` reports `max_add_usd = max_add(binding, inf)` (`ui/service.py:1488`). On bindings with
no `per_run_usd` that is `MAX_USD` ($100 000), so the Resume control shows a meaningless ceiling. The POST re-checks,
so this is not a security issue. Next step: compute headroom with a readonly ledger, or show "bounded by period
headroom".

Fix: `paused_runs` now returns `max_add_usd: null` on bindings without `per_run_usd` (per-run ceiling otherwise); `PausedRuns.tsx` shows "bounded by period headroom" and no ceiling. POST re-check unchanged. Tests: `test_paused_runs_max_add_is_per_run_bound_only_when_per_run_usd_is_set`, vitest `paused-runs.test.tsx`.

**G-9 (Low, NFR-5).** `sweep_pause_expiry` (`capped_resume.py:502`) scans `fires.list(status="launched")` across all
workspaces on every tick. It is O(all records), not O(window). Next step: index paused records (or go through the
ledger index), and add a perf counter test next to the 200-schedule tick test.

**G-10 (Low).** A period-only cap (no `per_run_usd`) reserves the whole remaining period budget for one fire
(`governance_gate.py:196-197`). Under `overlap: allow` a second fire in the same window is then refused
`cost_cap_reached` even when the first spends little. This is already tracked as "period cost-cap polish" (T-PpE5lf).
It is listed here so the reviewer verdict on it is explicit: acceptable for MVP, must be documented in the guide.

**G-11 (Info).** `read_regular_file` uses `O_NOFOLLOW` on the last component only (`governance_gate.py:223`). An
intermediate directory swapped for a symlink between `resolve()` and `open` can still redirect the read. The exposure
is a same-user workspace (the threat model accepts workspace-content control). Recorded for T-lTW88y Info, no action
required. A future hardening is `openat` walking with `O_NOFOLLOW|O_DIRECTORY`.

## Security checks (resume route)
- **Auth:** `POST /api/schedules/{id}/runs/{run_id}/resume-capped` is AUTHENTICATED by deny-by-default (absent from
  `DASHBOARD_ROUTE_POLICIES`, `ui/app.py:575`). `SecurityMiddleware` applies Host allowlist, Origin and Content-Type
  CSRF checks to mutating methods. OK.
- **Body:** `ResumeCappedRequest` has `extra="forbid"`, `0 < add_usd <= MAX_USD` and `allow_inf_nan=False`. It is
  validated by hand, so NaN cannot cause a 500 (`ui/app.py:107-113, 649-660`). No workflow, argv or absolute cap can
  ride along. OK.
- **Path params:** `schedule_id` and `run_id` are only lookup keys (`find_by_run_id` plus a `rec.schedule_id ==
  schedule_id` check). They are never joined into a filesystem path by the route. The request file name is a uuid,
  and `ResumeCappedRequest.from_json` bounds `requested_by` and validates `request_id`. The drain re-checks everything
  under the flock. OK.
- **Generic resume bypass:** `_check_cost_cap_option` refuses `cost_cap_usd` on a governed run with a 409. OK.
- **Writes:** see **G-1**. This is the one open security/integrity item on this route.

## AC walk per task
| Task | Verdict from code | Notes |
|---|---|---|
| T-lJu9oZ window math | Meets ACs | pure, tz/DST/week-start; seeded property test present |
| T-tPlK1T binding model | Meets ACs | D-2 enforced, strict numbers, per_run ≤ smallest period, digest canonical |
| T-Uz0Uol usage ledger | Partial (as ticketed) + **G-2**, G-5 | event sink unwired (`event_sink=None` default in production) |
| T-PpE5lf pre-fire gate | Partial (as ticketed) + G-10 | flock + single intended write + fail-closed argv OK |
| T-et7Edf run cost cap | Meets core ACs; G-6 | sticky, re-armed, boundary-only-with-work, retry H-1, trailer xN (AC17) OK |
| T-nI4DBf pause/resume | Done, but **G-1** (via route) and **G-2** affect resume integrity | stranded-resume recovery OK; B-2 known |
| T-rwj4nV CLI limits/usage | Meets AC1-11 on read; readonly views correct | dedicated review now done (this unit) |
| T-8knqVe dashboard | Partial; **G-1**, **G-3**, G-8 (fixed) | overshoot display missing (G-3 is the root) |
| T-88wSLC e2e | Partial (as ticketed) | missing: test for G-1/G-2 shapes |
| T-lTW88y gate | Partial; G-1 must close before PASS on the UI half | |
| T-Hq3vXd docs | out of code scope | |

## Testing notes
- Add: a ledger test for `cap_state=resumed` with non-running state (G-2); a dashboard-route byte-equality test for
  fire records (G-1); `ReadOnlyFireStore` mutators refuse (G-4); a quota-only partial confidence test (G-5); a
  dashboard paused-row `overshoot_usd` test (G-3).
- Mock seams already exist: `UsageLedger(run_state_loader=…, clock=…, fs_now_ns=…)`,
  `drain_resume_capped(launcher=…, other_run_active=…)`.

## Checklist
- [x] Scope confirmed (governance modules listed above, tip `69dd4c0`)
- [x] Three alignment levels checked (project: resume safety and determinism; epic: FR/NFR/D-1..D-5; code: docstring
  contracts, e.g. G-1 and G-3 contradict their own docstrings)
- [x] Dimensions walked: SOLID/KISS OK (gate pure); DRY G-3/G-6; magic literals: none new found (named Finals
  throughout); pluggability OK (`LedgerPort`, `CappedResumeLauncher`, `EventSink`); spec/schema OK (schema-vs-pydantic
  parity tested); determinism OK (injected clocks); errors/logging OK (drain never raises, fail-closed gate);
  testability OK; concurrency G-1/G-2/G-4
- [x] Every finding has location, observation, why and next step
- [x] Severity bucketed (no Critical; 1 High, 2 Medium, 4 Warning, 4 Low/Info)
- [x] Testing notes included
- [x] No source edits

---

# Re-review — fixes for G-1..G-8 (Opus, unit `w34-01-a2-gov-rereview`)

By: reviewer (Opus) · Role: reviewer · Date: 2026-11-11
Branch `ad/enhancements-10nov`, tip `e1b1264`. Read-only on source: no `src/`, `tests/` or `ui/` file was edited. The
only edits are this section and one line in the epic `STATUS.md`.

## Scope
Fix commits: `55878de` (G-1, G-3), `845247b` (G-2, `usage_ledger` fix), `a50a3ce` (G-8), `5677e98` (G-6), `d88df5f`
(G-4; it also carries the `FireGovernance.engine_cost_usd` field that G-7 uses) and `e5011ea` (G-5, G-7). I also
checked for regressions in the quota gate, the cost-cap gates and pause/resume.

## Method
1. **Suites at tip `e1b1264`:** all 20 governance and cost-cap test files (`tests/**` matching
   `governance_|cost_cap|capped|usage_ledger|usage_view|schedule_usage|windows|tripped|resume_queue`) gave
   **547 passed, 0 failed**. This is up from 496 at `69dd4c0`. Vitest `ui/src/test/paused-runs.test.tsx` gave 8/8 passed.
2. **Mutation check:** I used a throwaway git worktree at `/tmp/w34rr`, now removed. For each fix I reverted only its
   `src/` hunk (with `git apply -R` or a one-line edit), kept the new tests, and ran them. The question was whether
   the tests fail without the fix.
3. **Probes:** I ran one throwaway pytest probe in the same worktree. It was never committed. It confirmed G-12 below.

## Per-finding verdict
| ID | Verdict | Evidence |
|---|---|---|
| G-1 (High) | **PASS (fixed)** | `ui/service.py:1516`: `request_capped_resume` passes `readonly_fire_store(sdir)` for `fires` and the ledger. With G-4, every mutator on that store refuses, so a dashboard resume can no longer write a fire record. Reverting `55878de`+`a50a3ce` makes `test_resume_capped_request_never_writes_fire_records` fail. One weakness in the test, see **G-15**: only its `isinstance` asserts catch the regression. The byte-equality assert also passes against a writable store. |
| G-2 (Medium) | **PASS (fixed)** | `usage_ledger.py:387-392`: `live = running or cap_state == "resumed"`, so a resumed run still holds `reserved - mark` until it is final. Reverting `845247b` makes `test_g2_resumed_pending_child_keeps_topup_reserved` and `test_g2_fire_refused_while_resume_pending` fail. Lifecycle checked: the reservation is released when (a) the retire hook sets `final=True` after the child ends (`resumed` is not in `GOVERNANCE_CAPPED_STATES`, and a stale trip at or before `resumed_at` is not treated as a cap), (b) the hook re-pauses on a fresh trip, or (c) `recover_stranded_resume` rolls back an unspawned child. In none of these paths is the reservation held for ever. `test_g2_resumed_then_final_releases_reservation` passes with or without the fix, so it is not a regression guard (it is harmless). |
| G-3 (Medium) | **PASS (fixed)** | `schedule_usage` (`ui/service.py:1341`) now maps the window rows of `build_usage_doc`. `paused_runs` uses `paused_records` and `paused_row`, and exposes `overshoot_usd`, so T-8knqVe AC5 is closed. Reverting the fix makes `test_paused_runs_and_usage_share_the_cli_paused_row_with_overshoot` fail. Leftovers: G-13 (display) and G-16 (wasted work). |
| G-4 (Warning) | **PASS (fixed)** | `fire_store.py:432-454`: `_persist`, `_delete`, `write`, `update`, `write_suppressed`, `prune` and `reconcile_orphans` each raise `PermissionError`. `_persist` and `_delete` are the two disk chokepoints (`fire_store.py:270/281/302/394/399`). Reverting the fix makes `test_readonly_store_refuses_every_mutator_and_reads_still_work` fail. No current caller of a readonly store reaches a mutator: the CLI `resume` drains through `engine.drain_resume_requests`, which uses the engine's own writable store. The full suites still pass. |
| G-5 (Warning) | **PARTIAL: open for killed runs** | Fixed for runs that are not killed: reverting the 3-line block makes `test_g5_quota_only_binding_partly_blind_run_is_persisted_partial` fail. The block is gated `and not killed` (`schedule_engine.py:1579`), though, and the reservation branch that relabels killed runs does not run on a binding without `reserved_usd`. A probe on a quota-only binding (`limits(quota_day=5)`, reservation-less) used a `cancelled` run with one reporting task and one attempt without actuals. `run_confidence(state)` gives `partial`, but the record is persisted `cost_confidence="actual"` and `final=True`. This is the same G-5 class, so see **G-12**. The new test also models "quota-only" as a cost binding (`limits(per_run=2, week=50)`) with `reserved=None`, not as a true quota-only binding. |
| G-6 (Warning) | **PASS (fixed)** | `engine.py:403-411` adds `settled_run_cost_usd` (a `list()` snapshot passed to `compute_run_usage_totals`). Both `cost_cap_reached` (`:423`) and `_retry_hits_cost_cap` (`:3029`) call it, and the snapshot keeps the retry gate's worker-thread safety. No behavioural test can fail without this fix, because before the fix the two sums were identical. `test_g6_both_gates_share_one_cost_computation` fails without it only through `ImportError`. That is acceptable for a pure DRY change. |
| G-7 (Warning) | **PASS (fixed), with display and dead-field leftovers** | Finalisation persists `engine_cost_usd`. The `run_cost_capped` event and `paused_row` compute overshoot on that metric, and legacy records fall back to the charge. Reverting the 2 lines makes `test_g7_overshoot_uses_engine_cost_not_the_reserved_charge` and `test_paused_row_overshoot_uses_engine_cost_metric` fail. Leftovers are **G-13** (the "spent / cap (overshoot)" text no longer reconciles) and **G-14** (`Charge.overshoot_usd` is still on `observed`). |
| G-8 (Low) | **PASS (fixed)** | `paused_runs` returns `max_add_usd: null` unless the binding has `per_run_usd`. The POST re-check is unchanged. Reverting `a50a3ce` makes 2 UI-API tests fail. Vitest `paused-runs.test.tsx` passes 8/8. |
| G-9..G-11 | not in fix scope | Unchanged and still open as recorded (Low/Info). |

**Regression check (quota gate, cost-cap gates, pause/resume):** I found no regression. The quota gate counts
`runs=1` per non-suppressed record, as before. The only charging change is G-2, which only adds reservation (the
fail-safe direction). The cost-cap dispatch, boundary and retry gates now share one metric. Pause/resume works the
same way: the drain writes under the flock, launches outside it, and rolls back on failure. Stranded-resume recovery
and the retire hook's stale-trip rule are unchanged. All 547 governance-suite tests pass.

## New findings (numbered, severity-ranked; none fixed by this unit)

### Warnings
**G-12 (Warning) — FIXED 2026-11-11 (w35-01): `_finalize_governance_once` no longer gates the conservative label on `not killed`; test `test_g12_killed_quota_only_run_is_persisted_conservative`. G-5 residual: a killed (`cancelled`) partly blind run on a reservation-less binding is persisted
`actual`.**
- Where: `src/agent_orchestrator/service/schedule_engine.py:1579` (`if conf in _CONSERVATIVE_CONFIDENCES and not
  killed`), and `:1582-1588`, the only branch that relabels killed runs, which requires `g.reserved_usd is not None`.
- Observation: with a quota-only binding and a `cancelled` run (task a reported $0.40; task b has
  `attempts_without_actuals=1`), the ledger with state gives `partial`, but the persisted value is `actual`
  (`final=True`). Probe output: `ledger-with-state: partial persisted: actual final: True`.
- Why it matters: once `state.json` is cleaned up, `usage` and the dashboard confidence flag say `actual` for a run
  whose spend was partly invisible. This is exactly the under-reporting G-5 described. Charging is unaffected,
  because quota-only bindings have no cost cap.
- Next step: drop `and not killed` for the label only (`confidence = conf`) when `g.reserved_usd is None`. For killed
  runs, `unknown` is also defensible and matches the reservation branch. Add a test that uses a true quota-only
  binding (`limits(quota_day=…)`) and a `cancelled` partly blind state.

### Suggestions / Low
**G-13 (Low) — the paused row's "spent X / cap Y (overshoot Z)" no longer reconciles.**
- Where: `src/agent_orchestrator/schedules/usage_view.py:264` (CLI text: `cost_usd` "of" cap), and
  `ui/src/components/PausedRuns.tsx:73-74` (`spent_usd` = `paused_row.cost_usd`).
- Observation: since G-7, `overshoot_usd` is `engine_cost_usd − cap`, but "spent" still shows the charge
  (`cost_observed_usd`). The charge includes summary cost and can be inflated by the reservation. The G-7 test shape
  renders as "spent $5.00 / cap $2.00 (overshoot $0.30)".
- Why it matters: an operator deciding how much to add on resume sees numbers that contradict each other. D-5's
  intent is a single, clear overshoot display.
- Next step: show `engine_cost_usd` as "spent". If the charge is shown at all, label it "charged". In the dashboard,
  map `engine_cost_usd` into the UI type.

**G-14 (Low) — `Charge.overshoot_usd` is still computed on `observed`, and nothing in production reads it.**
- Where: `src/agent_orchestrator/service/usage_ledger.py:396` (`over = max(0.0, observed - cap)`), where `observed`
  includes summary cost.
- Observation: this is the third overshoot computation, on a different metric from the G-7 one. It is only asserted
  in `tests/service/test_usage_ledger.py:153,164`. The w32-02 STATUS note already lists it as a followup.
- Why it matters: it is latent drift. The next consumer that picks it up will disagree with the event and with
  `paused_row`.
- Next step: compute it from the engine metric (`_observed` already returns it as the first tuple element), or
  delete the field and its two asserts.

**G-15 (Low, test strength) — the byte-equality half of the G-1 regression test is vacuous.**
- Where: `tests/ui/test_schedule_usage_api.py:401-424`.
- Observation: I restored a writable `FireIntentStore` in `request_capped_resume` and removed the two `isinstance`
  asserts. The test still passes, because the fixture's `cost_observed_usd` already equals the observed state cost,
  so `_raise_mark` never persists. Only the structural `isinstance` checks guard G-1.
- Why it matters: a refactor that keeps a readonly-looking wrapper but writes some other way would pass the test.
- Next step: make the fixture's state report a cost of at least `cost_observed_usd + HWM_PERSIST_STEP_USD` (or a
  confidence change) so that a writable store would persist. Then the byte-equality assert actually discriminates.

**G-16 (Low, NFR-5) — the dashboard `schedule_usage` builds and throws away paused rows and events, and reads the
event log twice.**
- Where: `src/agent_orchestrator/ui/service.py:1341-1351`.
- Observation: `build_usage_doc` also runs `paused_records` (a schedule scan) and `governance_events` (an event-log
  read up to `EVENT_SCAN`). `schedule_usage` uses only `windows`, then reads the event log again in
  `_governance_skips`.
- Why it matters: every usage GET does extra I/O. This is not a correctness problem.
- Next step: split a `usage_windows(...)` helper out of `build_usage_doc` (both the CLI and the dashboard call it),
  or let the dashboard reuse `shared["events"]`.

### Info
**G-17 (Info).** `ui/service.py:845` (`_check_cost_cap_option`) and `cli.py:921` still build a writable
`FireIntentStore` for pure lookups (`is_governed_run` / `find_by_run_id`). Neither reaches the ledger, so nothing is
written today. For consistency with ADR-0025 and G-4, use `readonly_fire_store` there as well.

**Process note (not numbered):** `d88df5f` (G-4) also carries the G-7 `FireGovernance.engine_cost_usd` field. The
`e5011ea` message says so. Bisecting G-7 on its own is therefore not clean. No action is needed.

## Verdict
G-1, G-2, G-3, G-4, G-6, G-7 and G-8 are **fixed**, and each fix has a test that fails without it (G-6 only through
the import, as expected for a DRY change). G-5 is **partially fixed**: it is still open for killed runs, see **G-12**
(Warning). I found no regression in the quota gate, the cost-cap gates or pause/resume. New findings: 1 Warning
(G-12), 4 Low (G-13..G-16) and 1 Info (G-17). None blocks merge. G-12 should be fixed before T-Uz0Uol / T-nI4DBf are
called Done on confidence reporting.

## Checklist
- [x] Scope confirmed: the six fix commits listed above, tip `e1b1264`.
- [x] Three alignment levels checked. Project: resume safety (G-1/G-2) and observability (G-5/G-7/G-13). Epic:
  FR-4/FR-5, NFR-1, NFR-5 and D-5. Code: docstring contracts, e.g. `paused_row` claims "overshoot" but sits beside a
  mismatched "spent".
- [x] Dimensions walked. SOLID/KISS: OK. DRY: G-14, G-16. Magic literals: none new. Pluggability: OK. Spec/schema:
  `engine_cost_usd` is optional with a legacy fallback, which is expand–contract safe. Determinism/resume: G-2 OK.
  Errors: `PermissionError` carries context. Testability: G-15. Concurrency: G-1/G-4 OK.
- [x] Every finding has a location, an observation, why it matters, and a next step.
- [x] Severity bucketed: 0 Critical, 0 High, 0 Medium, 1 Warning, 4 Low, 1 Info.
- [x] Testing notes: G-12 (true quota-only binding with a `cancelled` state) and G-15 (fixture must make a
  writable-store write observable).
- [x] No source edits.

## Fix status — G-13..G-17 (w35-02, developer, 2026-11-11)
- **G-13 fixed.** CLI `paused:` line renders `engine_cost_usd` as "spent" (`usage_view.py` `render_usage`); dashboard `paused_runs.spent_usd` = `paused_row["engine_cost_usd"]` (so `PausedRuns.tsx` needed no change, only the `PausedRun.spent_usd` doc comment in `types.ts`). The charge stays in the JSON as `cost_usd`. Tests: `test_paused_runs_spent_is_the_engine_cost_that_overshoot_is_measured_on`, `test_render_usage_paused_lines` (charge 9.0 vs engine 3.0).
- **G-14 fixed.** `Charge.overshoot_usd` is computed from the engine metric (`usage_ledger.py`); `test_row_terminal_overshoot_is_on_the_engine_metric_not_summary_inclusive`.
- **G-15 fixed.** The G-1 test now writes a running `state.json` costing 1.5 (> `cost_observed_usd` 1.0 + `HWM_PERSIST_STEP_USD`). Mutation check: writable store in `request_capped_resume` with the `isinstance` asserts removed now FAILS the byte-equality assert (it passed before).
- **G-16 fixed.** New `usage_view.usage_windows(...)`, used by `build_usage_doc` and the dashboard `schedule_usage`; the dashboard no longer scans paused records or the event log inside the builder. Test: `test_usage_get_does_not_scan_paused_or_events_via_build_usage_doc`.
- **G-17 fixed** (trivial): `ui/service.py` `_check_cost_cap_option` and `cli.py` `_warn_if_schedule_governed` use `readonly_fire_store`. No dedicated test (the lookups are covered by existing governed-resume tests, which still pass).

# Re-review — fixes for G-13..G-17 (Opus, unit `w38-01-a2-gov-g13-rereview`)

By: reviewer (Opus) · Role: reviewer · Date: 2026-11-11
Branch `ad/enhancements-10nov`, fix commit `ce8f24d`, reviewed at tip `d5ec21a` (`84e71a8` is docs only). Read-only on
source: no `src/`, `tests/`, `ui/` or `docs-md/` file was edited. The only edit is this section.

## Method
1. **Suites at `d5ec21a`:** in a throwaway detached worktree (`/tmp/w38rr`, now removed), run with `PYTHONPATH=<worktree>/src`.
   All 18 tracked governance and cost-cap test files (`tests/**` matching
   `governance_|cost_cap|capped|usage_ledger|usage_view|schedule_usage|windows|tripped|resume_queue`) gave **551 passed,
   0 failed**. Vitest `ui/src/test/paused-runs.test.tsx` gave **8/8 passed**. `ruff check` and `ruff format --check` on the 4
   changed source files and 3 changed test files are clean. `mypy` reports 4 errors, all in `_version.py` (`no-redef`).
   They were already there before the fix (`ce8f24d^`) and are unrelated.
2. **Mutation check:** for each fix I reverted only its `src/` hunk in the worktree, kept the new tests, and ran them.
   The question was whether the tests fail without the fix.

## Per-finding verdict
| ID | Verdict | Evidence |
|---|---|---|
| G-13 (Low) | **PASS (fixed)** | The CLI `paused:` line now renders `engine_cost_usd` (`usage_view.py:274`). The dashboard has `spent_usd = paused_row["engine_cost_usd"]` (`ui/service.py:1490`), and `PausedRuns.tsx:73-74` renders it next to the cap and the overshoot. Spent, cap and overshoot now use one metric. Legacy records fall back to the charge (`usage_view.py:111`). This is also the metric that `capped_resume.py:298` uses for `new_cap`, so the operator's "add" maths agrees with the server. Mutations: reverting the CLI line makes `test_render_usage_paused_lines` fail. Reverting the dashboard line makes `test_paused_runs_spent_is_the_engine_cost_that_overshoot_is_measured_on` fail. |
| G-14 (Low) | **PASS (fixed)** | `usage_ledger.py:396-397`: `over = max(0.0, engine - cap)`, where `engine` is the first element of `_observed`. Reverting it to `observed` makes `test_row_terminal_overshoot_is_on_the_engine_metric_not_summary_inclusive` fail (charge 3.5, overshoot 0.5). All three overshoot computations (the event, `paused_row` and `Charge`) now agree. |
| G-15 (Low) | **PASS (fixed)** | `tests/ui/test_schedule_usage_api.py:453-456` writes a running `state.json` with cost 1.5, above `cost_observed_usd` 1.0 plus `HWM_PERSIST_STEP_USD`. Mutation: I put a writable `FireIntentStore` in `request_capped_resume` (`ui/service.py:1514`) and deleted both `isinstance` asserts. The test then **fails on the byte-equality assert**, because the fire record `k-r1.json` differs. I also reverted the `_live_state` line, and the same mutation then passes. So the new fixture is exactly what makes the assert discriminate. |
| G-16 (Low) | **PASS (fixed)** | The new `usage_view.usage_windows` (`usage_view.py:161-174`) is used by `build_usage_doc` and by the dashboard `schedule_usage` (`ui/service.py:1340-1348`). The dashboard no longer imports `build_usage_doc`. The output does not change: rows and `limits is None → []` behave as before. Reverting `ui/service.py` makes `test_usage_get_does_not_scan_paused_or_events_via_build_usage_doc` fail. One weakness in that test, see **G-19**. |
| G-17 (Info) | **PASS (fixed), no guard** | `cli.py:921` and `ui/service.py:844` now use `readonly_fire_store`. Both lookups (`is_governed_run` and `find_by_run_id`) are read-only, so no mutator can raise. Reverting both makes **no test fail** (551 still pass). That is acceptable for an Info item, but nothing would catch a regression, see **G-20**. |

**Regression check (quota gate, cost-cap gate, usage view and ledger, dashboard usage API):** I found none.
- **Quota gate and charging:** `Charge.actual_usd` and `reserved_usd` are unchanged. Only the informational
  `overshoot_usd` moved to the engine metric, and no gate reads it (callers: `usage_view` and tests).
- **Cost-cap gate:** not touched by `ce8f24d`. The engine's `settled_run_cost_usd` and the `run_cost_capped` event are
  unchanged.
- **Usage view:** `build_usage_doc` still produces the same doc. `paused[].cost_usd` keeps the charge in `--json`, so the
  JSON shape only gains the existing `engine_cost_usd` key. Only the human text line changed metric.
- **Dashboard usage API:** the same window rows with less I/O. `_governance_skips` still reads the event log once.
- **Read-only stores:** every dashboard and CLI path that is only a lookup now uses `readonly_fire_store`. No caller
  reaches a mutator, because the suites would raise `PermissionError` otherwise.
- **Display vs resume maths:** the display uses the **persisted** `engine_cost_usd`. `new_cap` uses the **live** engine
  cost from `state.json`. These are the same while a run is paused, because a paused run makes no progress. This is not
  a finding.

## New findings (numbered, severity-ranked; none fixed by this unit)

### Suggestions / Low
**G-18 (Low, docs drift) — the HLD still describes overshoot on the summary-inclusive metric, and says the dashboard does not show it.**
- Where: `docs-md/scheduled-run-governance-hld.md:701` (`overshoot_usd = max(0, observed − cap)`), `:837` (the §10.2
  pseudocode `overshoot=max(0, observed - g.run_cost_cap_usd)`), `:1023` ("the **overshoot** indicator is NOT shown in the
  dashboard (CLI only; §26.4)").
- Observation: since G-7/G-14, all three computations use `engine − cap`. `PausedRuns.tsx:74` does render
  `(overshoot $X)`. The `:990` example (`$2.07 of $2.00`) now means engine cost, but nothing says so.
- Why it matters: §26 says "where the design and code disagree, the code wins", but these lines are the ones a reader
  will check against the dashboard. They contradict the shipped D-5 behaviour.
- Next step: in §9.3 and §10.2, change `observed` to `engine` (the cap's own metric) and note that the charge stays
  summary-inclusive. Drop or rewrite the `:1023` "NOT shown" clause, and close the matching §26.4 item if it is still listed.

**G-19 (Low, test strength) — one of the three monkeypatches in the G-16 test does nothing.**
- Where: `tests/ui/test_schedule_usage_api.py:333` (`monkeypatch.setattr(usage_view, "build_usage_doc", boom)`).
- Observation: before the fix, `ui/service.py` imported `build_usage_doc` by name, so patching the `usage_view` module
  attribute never reached the dashboard. Mutation: I reverted `ui/service.py` and kept only the `build_usage_doc` patch.
  The test **passed**. Only the `paused_records` and `governance_events` patches (`:334-335`) catch the regression. They
  work because `build_usage_doc` looks those names up in its own module globals.
- Why it matters: the test name and its assertion message claim a guard that one of its lines does not give. A
  future builder that does not use those two helpers would get past it.
- Next step: patch `agent_orchestrator.ui.service` itself (e.g. `monkeypatch.setattr(ui_service, "build_usage_doc", boom,
  raising=False)`), or delete the inert line and reword the docstring so it names the two helpers that do the guarding.

### Info
**G-20 (Info, coverage) — nothing tests the G-17 read-only lookups.** Reverting `cli.py:921` and `ui/service.py:844` to
`FireIntentStore` leaves all 551 governance tests passing. Nothing writes on those paths today, so this is low risk.
If a guard is wanted, a spy test like the G-1 one (`isinstance(..., ReadOnlyFireStore)` on the `fires` argument
of `is_governed_run`) for `_check_cost_cap_option` and `_warn_if_schedule_governed` would cover it.

**G-21 (Info, DRY/literals).** The defaults `"UTC"` and `"monday"` now appear twice in one module:
`usage_view.py:169-170` (`usage_windows`) and `:193-194` (`build_usage_doc`, used for the doc header). Next step: one
small `_binding_tz(binding)` / `_binding_week_start(binding)` helper, or named constants, used by both.

## Verdict
**G-13, G-14, G-15, G-16 and G-17 all PASS (fixed).** For G-13..G-16, every fix has a test that fails without it, and
that was confirmed by mutation. G-17 is fixed but nothing tests it (G-20). I found no regression in the quota gate, the
cost-cap gate, the usage view or ledger, or the dashboard usage API. On this evidence, **the A2.7 fix-verification
item can be marked met.** New findings: 2 Low (G-18 docs drift, G-19 test strength) and 2 Info (G-20, G-21). None
blocks merge. Together with the earlier verdicts (G-1..G-8 fixed, G-12 fixed in `b04cdc9`), G-9..G-11 are the only
earlier findings still open, and they are Low/Info.

## Checklist
- [x] Scope confirmed: `ce8f24d`, i.e. G-13..G-17. Regressions checked in the quota gate, cost-cap gate, usage view,
  ledger and dashboard usage API.
- [x] Three alignment levels checked. Project: observability (one overshoot metric) and safe-by-default (read-only
  stores). Epic: D-5 overshoot display, NFR-5 (G-16 I/O) and FR-5 resume maths. Code: docstrings match behaviour,
  except the G-19 test docstring.
- [x] Dimensions walked. SOLID/KISS: `usage_windows` is a clean extraction. DRY: G-21. Magic literals: G-21. Pluggability:
  `LedgerPort` is still injected. Spec/schema: the JSON shape is unchanged and additive. Determinism/resume: unchanged.
  Errors/logging: unchanged. Testability: G-19, G-20. Concurrency: the read-only stores remove the write races.
- [x] Every finding has a location, an observation, why it matters, and a next step.
- [x] Severity bucketed: 0 Critical, 0 High, 0 Medium, 0 Warning, 2 Low, 2 Info.
- [x] Testing notes: G-19 (patch the consumer module, not the provider) and G-20 (spy on the store type for the lookups).
- [x] No source edits.
