# TASK: T-vwIpSw-engine-gate-lifecycle

## Metadata
- Task ID: `T-vwIpSw-engine-gate-lifecycle`
- Epic ID: `E-Ag7Pw3-human-approval-gates`
- Owner: developer
- Created: 2026-10-04
- Last Updated: 2026-10-05 (rev 3)
- Status: Draft
- Estimate: 3 days (on the critical path; rev 3 replaced the per-file re-hash budget with one re-hash per
  poll and added the fresh-run marker write and `_is_gate`, net unchanged)

## Requirements Mapping
- Requirement IDs: FR-2, FR-3, FR-6 (fresh-run policy + marker), FR-11 (engine events), NFR-1, NFR-2,
  NFR-4, NFR-9
- Design: HLD §9.10.1–§9.10.5, §9.10.6 (fresh-run branch and `record_policy` order only),
  §9.10.7–§9.10.9, §9.2.2, §9.3.4 (marker writer), §9.6.5 (one re-hash per poll), §15, §26 rows 10–17;
  Gate 1 R-01 (pre-pass), S-09, suggestions S-05 (factory, no asserts) and S-10 (extraction first);
  Gate 2 S-11, S-12, R-10, R-11, R-14

## Description
Wire approval gates into the scheduler with the smallest possible `engine.py` diff.

0. **First change, landed alone:** the pure-move extraction of `_settle_completed_task`'s breaker block into
   `_evaluate_boundary_breakers` (HLD §9.10.4), verified against the unedited breaker/monitor/emit suites
   before any gate code exists (so the cache epic's merge can replay it).
1. `approvals/engine_glue.py`: `resolve_approval_poll_seconds(explicit, environ)` (ctor > env > default,
   clamp/validation per §9.10.7); `ApprovalDriverFactory`; `ApprovalGateDriver.for_run(..., is_resume)` —
   the default factory returns `None` unless the spec can contain a gate, `state.approvals` or
   `state.approval_policy` exist, or (`is_resume` and `marker_exists(key_store.gated_marker_path(run_id))`)
   (rev 3: a fresh gate-free run does **no** added I/O; a resumed gate-free run resolves the key-directory
   path and `lstat`s one marker path; there is no `lstat` of `<run_dir>/approvals` any more — Gate 2 S-12);
   otherwise captures the static workflow, resolves the raw ctor poll value, loads/creates the key (key
   errors → run failed + saved + `approval.key_unavailable` + re-raise). Then `poll_seconds`,
   `effective_approval`, `has_pending`, `poll` (snapshot of awaiting ids rotated round-robin; **at most one
   consume-time re-hash per poll** across all gates — each scan gets `may_rehash` = "the poll's re-hash is
   unused", a `needs_rehash` gate is deferred, records untouched, no expiry decision, logged once per
   session as `approval.rehash_deferred`; `orphan` outcome), `open_gate` (open + re-entry happy path with
   the run/task binding check; the resume-specific branches — request signature failure, `spec_digest`
   mismatch — complete with `T-otHPGB`; until then they fail closed with a clear `ApprovalPolicyError`),
   `apply_outcome`, and `begin_session` with the **fresh-run branch only**: refuse an existing policy;
   refuse a run-id marker collision (`marker_collision`); then `record_policy` in the crash-safe order of
   Gate 2 R-11 — sign the gate-scoped policy → save `state.json` → write the gated marker
   (`write_marker`, `GatedMarkerUnwritable` → `marker_unwritable`, fail closed) — and **create no
   directory** (`<run_dir>/approvals/` appears lazily at the first `open_gate`). On any resumed session or
   with in-state gate evidence it raises `ApprovalPolicyError("resume of gated runs is completed by
   T-otHPGB")` after marking the run failed — fail closed in the interim (NC-4), replaced by `T-otHPGB`.
2. `engine.py` edits exactly per §9.10.2–§9.10.5: ctor params (`approval_poll_seconds` stored **raw**,
   `approval_driver_factory` defaulting to `ApprovalGateDriver.for_run`) + docstring; `_RunContext.approvals`;
   `run()` factory call (`is_resume=run_state is not None`) + `begin_session(workflow, state, graph=graph,
   cones=cones, is_resume=run_state is not None)` before `done` is seeded; loop-top
   `_settle_decided_approval_gates`; the `_open_ready_gates` pre-pass before FILL; termination with the
   idle `sleeper(poll)`; DRAIN through `_wait_or_settle_gates` only when gates are pending; `_ready_ids`
   excluded tuple; `_prepare_and_maybe_dispatch` `dispatch_cycle` guard + gate branch before the budget
   gate; the `_is_gate` helper (persisted `approval` **or** `effective_approval`, Gate 2 R-14) used at both
   detection sites (`_open_ready_gates` and `_prepare_and_maybe_dispatch`, incl. the `dispatch_cycle`
   guard); the fail-closed `_require_approvals` helper used everywhere instead of `assert`; new
   `_settle_decided_approval_gates`, `_settle_approval_gate`, `_wait_or_settle_gates`, `_open_ready_gates`.
3. Engine events of §15 for open/approve/reject/expire/refuse/defer/state-error/policy-recorded/
   marker-written (mirrored to `audit.jsonl`).
4. `tests/approvals/engine_helpers.py` (rev 3, Gate 2 R-09): `make_orchestrator(...)` (driver factory
   partial, poll `MIN_APPROVAL_POLL_SECONDS`), clock-advancing / deciding sleepers, Event-gated fake tasks —
   a plain helper module imported by this task's tests and later by `T-otHPGB`, `T-Mdk27e` and `T-nmL0HP`.
   This task does not edit `tests/approvals/conftest.py`.

Files — new: `approvals/engine_glue.py`; tests `tests/approvals/test_engine_gate_lifecycle.py`,
`tests/approvals/test_poll_settings.py`, `tests/approvals/engine_helpers.py`, golden fixtures under
`tests/approvals/fixtures/`. Shared: `engine.py` (§26 rows 10–17). **Exclusive files during stage D**
(HLD §22.3): exactly these; never in parallel with `T-otHPGB` (`engine_glue.py`).

## Acceptance Criteria
1. The extraction lands first and alone and, at that point, `tests/test_engine_breakers.py`,
   `tests/test_monitoring_breaker_consult.py`, `tests/test_mvp_breaker_conditions.py`,
   `tests/test_emit_settle_atomicity.py` and `tests/test_run_active_seconds_breaker.py` pass **unedited**
   (re-run at the end of the task too).
2. Gate pauses only its dependents: with `max_parallel` 1 and 2, an independent branch completes while the
   gate is `awaiting_approval`; the gate is never in `in_flight` (spy on `pool.submit`).
3. Pre-pass (Gate 1 R-01): `test_gate_opens_while_all_slots_busy`; `test_gates_never_enter_rank_wave_or_capacity`;
   `test_gate_waits_for_drain_when_integration_active`.
4. Detection (Gate 2 R-14): `test_nulled_clone_approval_still_treated_as_gate` — a loop clone of a static
   gate whose persisted `approval` is `None` is still opened as a gate by `_open_ready_gates` and never
   dispatched as an agent task by `_prepare_and_maybe_dispatch` (its `dispatch_cycle` stays 0).
5. Approve (through the real signer via `human_decides`, sharing the engine's fake clock) → gate
   `succeeded`, dependents run, run `succeeded`; reject → gate `failed`, run `failed`,
   `self_heal_enabled=True` with a spy monitor that is **never** consulted for the gate; retries never
   applied.
6. Timeout with the injected clock: `expired` → `failed`; a record signed before `expires_at` placed in the
   same poll still wins (scan before expiry check).
7. One re-hash per poll (Gate 2 R-10): `test_one_rehash_per_poll_round_robin` — three gates with candidate
   decisions in the same poll: exactly one review set is hashed per poll (spy), all three are eventually
   applied, none is refused, a deferred gate gets no expiry decision in the poll that deferred it,
   `approval.rehash_deferred` appears once per deferred gate.
8. Fresh-run policy and marker (Gate 2 S-11, R-11): `test_fresh_run_saves_policy_before_marker` — a spy
   records that `state.json` with the signed policy is saved **before** the marker is written, the marker's
   `policy_sha256` equals `policy_digest(state.approval_policy)`, and no `<run_dir>/approvals/` exists until
   the first gate opens; `test_marker_collision_refuses_fresh_run`; `test_marker_unwritable_fails_closed`
   (fresh run: policy saved, run failed with `marker_unwritable`).
9. A gate never increments `dispatch_cycle`, never gets `started_at`, keeps `attempts == 0`, gets
   `ended_at` on settle; a spy budget manager/estimator is never called for it.
10. A `stop_file` breaker whose file appears during the wait trips at the gate's settle boundary (before the
    dependent dispatches); a `run_active_seconds` breaker is not tripped by a long (fixed-clock) wait.
11. Cancel during the wait → run `cancelled`, request still `pending`, task `awaiting_approval` persisted.
12. NFR-1: `TestNoGateByteIdentical` — for a fresh gate-free fixture (fixed clocks), the `run.log` event
    names and the `status.json` bytes equal goldens **recorded from the unmodified engine at the start of
    this task**; the injected spy key store records zero calls; no `approvals/` directory exists.
    `test_gate_free_resume_probes_marker_only` — a resumed gate-free run calls only `gated_marker_path` (no
    key load), creates nothing and logs nothing; `test_gate_free_run_reads_no_approval_env`.
13. `test_driver_exists_whenever_a_gate_can_appear` and `test_missing_driver_fails_closed`.
14. `test_budget_blocked_task_not_reprepared_on_idle_poll_ticks`, `test_orphan_awaiting_task_fails_instead_of_idling`,
    and the temporary `test_resume_before_t_othpgb_fails_closed` (removed by `T-otHPGB`).
15. Tests use `approval_poll_seconds=MIN_APPROVAL_POLL_SECONDS` (0.05 s) through `engine_helpers.py` and
    assert outcomes, never poll counts or durations. Targeted tests green; ruff/mypy clean on `engine.py` and
    `approvals/` (full suite at the stage-D checkpoint); a `reviewer`-agent review is recorded in STATUS.md
    (focus: the extraction is a pure move; NFR-1; the pre-pass never changes gate-free scheduling).

## Risks
- `concurrent.futures.wait` uses real time: tests must assert outcomes, never poll counts or durations
  (determinism comes from the injected clock, file-name order and round-robin).
- Merge conflicts with the cache epic in `_prepare_and_maybe_dispatch`, the FILL/DRAIN and the ctor
  (HLD §16.4 CE-1/CE-5).
- The pre-pass must not change gate-free scheduling: it is guarded by `ctx.approvals is not None` (AC12).

## Dependencies
- `T-pfJiXw` (store/signer/views, shared fixtures), `T-1MgGb4` (policy helpers), `T-drPIif` (keys, audit,
  `gated_marker.py`), `T-AGO2L6` (models).

## Pseudocode / Algorithm
```text
HLD §9.10.3 open_gate and _is_gate, §9.10.4 _settle_approval_gate/_evaluate_boundary_breakers, §9.10.5 loop
edits (_open_ready_gates, _require_approvals, _wait_or_settle_gates), §9.10.1 poll (one re-hash per poll,
round-robin), §9.10.6 begin_session fresh-run branch + record_policy (policy saved, then marker).
```

## Schemas / Interface Notes
- `Orchestrator(..., approval_poll_seconds: float | None = None, approval_driver_factory: ApprovalDriverFactory | None = None)`.
- `ApprovalGateDriver` / `ApprovalDriverFactory` contract (incl. `is_resume`): HLD §9.10.1.
- Events: HLD §15.

## Verification
```
WT=/usr/avadhoot/mounted/agent-orchestrator/.claude/worktrees/agent-a513b4d78ac3b20bb
PY=/usr/avadhoot/mounted/agent-orchestrator/.venv/bin/python
cd $WT && $PY -m pytest -o pythonpath=src -p no:cacheprovider -q tests/approvals
cd $WT && $PY -m pytest -o pythonpath=src -p no:cacheprovider -q tests/test_engine_breakers.py tests/test_monitoring_breaker_consult.py tests/test_mvp_breaker_conditions.py tests/test_emit_settle_atomicity.py tests/test_run_active_seconds_breaker.py tests/test_wave_scheduler.py
cd $WT && $PY -m ruff check src/agent_orchestrator tests/approvals && $PY -m mypy src/agent_orchestrator/engine.py src/agent_orchestrator/approvals
```

## Handoff Boundary
- Upstream: `T-pfJiXw`.
- Downstream: `T-otHPGB` (completes `begin_session` and the re-entry branches; removes the interim
  fail-closed resume stub and its temporary test), `T-Mdk27e` (uses `engine_helpers.py`), `T-ZPGoSN`,
  `T-nmL0HP` (e2e).

## Artifacts
- Code as listed; golden fixtures; evidence in STATUS.md.
