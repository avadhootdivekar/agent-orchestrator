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
**G-4 (Warning). `ReadOnlyFireStore` is read-only in name only.**
- Where: `src/agent_orchestrator/service/fire_store.py:410-425`.
- Observation: it overrides only `update_governance`. `write`, `write_suppressed`, `update` and `_persist` still
  persist.
- Why it matters: the safety of every view caller (G-1 class) depends on that caller never reaching another mutator.
  A future view helper that calls `update` would silently write.
- Next step: override the other mutators to raise (`PermissionError`/`RuntimeError`), and add a unit test
  that each one refuses.

**G-5 (Warning). The retire hook labels a partly blind run `actual` when the binding has no cost reservation.**
- Where: `src/agent_orchestrator/service/schedule_engine.py:1571` (`confidence = "actual" if observed > 0 …`). The
  `run_confidence` override applies only inside `if g.reserved_usd is not None and (…)` (`:1578-1584`).
- Observation: on quota-only bindings (`reserved_usd is None`), a run with `attempts_without_actuals > 0` is persisted
  `actual` once its `state.json` is gone. The ledger, which derives from state, says `partial`. The Q-1 fix
  (`884c167`) only covers bindings that reserve.
- Why it matters: `usage` and the dashboard confidence flags under-report uncertainty after state cleanup. Charging is
  unaffected, since quota-only bindings have no cost cap.
- Next step: set `confidence = conf if conf != "actual" else ("actual" if observed > 0 else g.cost_confidence)`
  outside the reservation branch, and add a quota-only partial test.

**G-6 (Warning, DRY). Two cost metrics in the engine gate.**
- Where: `src/agent_orchestrator/engine.py:3019` (`_retry_hits_cost_cap` sums `ts.cumulative_cost_usd` itself) against
  `engine.py:403-417` (`cost_cap_reached` → `compute_run_usage_totals`).
- Observation: today they are the same sum. The retry gate re-implements it inline so it can iterate a snapshot from
  a worker thread.
- Why it matters: if `compute_run_usage_totals` ever adds a term (e.g. sentinel lane cost), the retry gate and the
  dispatch/boundary gate will disagree about whether the cap is reached.
- Next step: let `compute_run_usage_totals` accept an iterable of task states (or a snapshot helper), and call it
  from both sites.

**G-7 (Warning). Overshoot mixes metrics.**
- Where: `src/agent_orchestrator/schedules/usage_view.py:114` and `schedule_engine.py` (`run_cost_capped`
  `overshoot_usd`). Both compute `cost_observed_usd − run_cost_cap_usd`.
- Observation: `cost_observed_usd` includes the summariser's cost and, for blind/partial runs, the conservative
  `max(observed, reserved)` charge. The cap is the engine metric (L3, `UsageLedger.engine_cost_usd`).
- Why it matters: the reported overshoot is inflated by summary cost, and for a blind run whose charge is set to
  `reserved` it can be ~0 or misleading. D-5's "overshoot measured per fire and displayed" should use one metric.
- Next step: compute overshoot from `engine_cost_usd` (the ledger already has `Charge.overshoot_usd` doing this from
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
