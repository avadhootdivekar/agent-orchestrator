# TASK: T-nI4DBf-capped-run-pause-resume

## Metadata
- Task ID: `T-nI4DBf-capped-run-pause-resume`
- Epic ID: `E-Qp4Zm8-scheduled-run-governance`
- Owner: developer agent
- Created: 2026-11-10
- Last Updated: 2026-11-11
- Status: Done (reconciled 2026-11-11, w24-07; see STATUS.md)
- Estimate: 2 days

## Requirements Mapping
- Requirement IDs: FR-6, NFR-1 (see `../EPIC.md`); owner decision D-5 (resume/expiry)

## Description
The lifecycle of a run that hit its cost cap: `paused → resumed → (paused again | done)`, `paused → expired`, and
`aborted` (final). This task builds three pieces:
- the operator resume request (`request_capped_resume`), written as an E-Sc9Rt4 drop-directory request of a new kind;
- the engine-side drain that performs the resume as the single launch authority (ADR-0014 D10);
- the TTL expiry sweep.

Read HLD §9.4, §9.5 (normative), §12 and ADR-0025 D6. Read the **merged** code of `T-PpE5lf` (retire hook, lock),
E-Sc9Rt4 `T-Ev3Qm5` (`drain_run_now_requests`, `REQUEST_TTL_SECONDS`) and `T-Cl6Jn9` (in-process run-now path).

Files you own:
- `src/agent_orchestrator/service/capped_resume.py` (new): `request_capped_resume()`, `ResumeRefused` (typed, with
  `reason ∈ {not_paused, expired, aborted, no_headroom, bad_amount, schedule_not_found, overlap}`), `max_add()`,
  `sweep_pause_expiry()`, the drop-request model `ResumeCappedRequest`.
- `src/agent_orchestrator/service/schedule_engine.py` (edit): the request drain dispatches on `kind`
  (`run_now` default | `resume_capped`), and calls `sweep_pause_expiry()` once per tick inside the tick budget.
- `src/agent_orchestrator/service/schedule_launch.py` (edit): `launch_capped_resume(rec, new_cap)` →
  `ProcessSupervisor.launch_resume(run_id, options={"cost_cap_usd": new_cap}, ...)`, with the same argv verification
  as `T-PpE5lf`.
- `tests/service/test_capped_resume.py` (new).

Do NOT touch: `engine.py`, `cli.py`, `governance_gate.py` (import its lock helper), `usage_ledger.py` (use
`update_governance`), `schedules/`, `ui/`.

## Acceptance Criteria
1. `request_capped_resume(ws, schedule_id, run_id, add_usd=None, by)` validates in this order and raises
   `ResumeRefused(reason)`: schedule exists, record found and belongs to the schedule, `cap_state == "paused"`
   (`aborted` ⇒ `aborted`, `expired` ⇒ `expired`), `now < pause_expires_at`, `0 < add <= max_add` (`bad_amount`), and
   `add <= remaining` in the **original fire's windows** (`no_headroom`, message carries remaining). The default `add`
   is `per_run_usd`, or `MIN_RESERVATION_USD` when absent.
2. A valid request writes one drop-request file (`kind: "resume_capped"`, `new_cap = engine_cost_usd + add` (L3: the engine's metric from `compute_run_usage_totals`, **not** `cost_observed_usd`, which
   also includes `summary_cost_usd` and would hand the engine `add + summary_cost` of real headroom), `add`,
   `by`, `requested_at`, `request_id`) atomically. When no daemon is live, the in-process path (same function E-Sc9Rt4
   uses for run-now) performs the drain immediately.
3. The engine drain re-checks every condition under the governance flock. If the schedule has another active run
   and `overlap != "allow"`, it refuses (`overlap`). Otherwise it updates the record (`cap_state="resumed"`,
   `reserved_usd=new_cap`, `run_cost_cap_usd=new_cap`, `final=false`, append to `resumes[]`), calls
   `launch_capped_resume`, re-adds the fire key to `active_fire_keys`, and emits `schedule.governance.resumed`.
   Refusals emit `schedule.governance.resume_refused`. Requests older than `REQUEST_TTL_SECONDS` are discarded
   (E-Sc9Rt4 rule).
4. A resume consumes **no** quota slot: quota usage is unchanged before and after.
5. A resumed run that caps again ⇒ `paused` with a **fresh** `pause_expires_at`. Two consecutive resumes plus a
   final cap ⇒ `len(resumes) == 2`, and the total charge stays within the original week's cap.
6. Expiry sweep: at `pause_expires_at - 1s`, the run is still `paused`. At `+0s`, it is `expired` with `final=true`,
   and one `schedule.governance.pause_expired` WARNING is emitted. A second sweep is a no-op (idempotent). The run's
   `state.json` is not modified.
7. `on_cap: abort` ⇒ resume refused `aborted` immediately. `pause_ttl_seconds: 0` ⇒ expires on the first sweep.
8. The scheduler never resumes automatically: no code path calls `launch_capped_resume` except the request drain
   (grep-level test).
9. Manual out-of-band `ao resume --cost-cap-usd 99` after expiry is still charged by the ledger. An integration test
   with a stub state.json raising the cost confirms the window usage rises.
10. ≥ 85 % coverage on `capped_resume.py`; ruff/format/mypy clean.
11. **(L6) Original launch inputs.** `launch_capped_resume` reuses the original launch's `workflow_path`, reposets and agents,
    looked up from `LaunchRecord.workflow_path` (`ui/processes.py:443`) via the fire's `launch_id` (`FireIntentRecord`
    holds only `launch_id`). A template **instance** directory is never re-rendered on resume. Test: resume of a
    template-instance fire runs against the same instance dir and the render function is not called.
12. **(M5) Governed runs resist generic resume.** Provide `is_governed_run(ws, run_id)` (a thin wrapper over
    `find_by_run_id`) for T-8knqVe's route guard (AC10 there) and for the CLI WARNING in T-et7Edf AC14c.

## Risks
- Two launch authorities would reappear if the CLI or dashboard called `launch_resume` directly. Both **must** go
  through the request (AC8).
- Charging resumes to the *original* window can surprise operators after a window has closed. The HLD documents it,
  and the refusal message must name the window it checked.

## Dependencies
- Upstream: `T-PpE5lf`; E-Sc9Rt4 `T-Cl6Jn9` (in-process request path) and `T-Ev3Qm5` (drain); `T-et7Edf`
  (`--cost-cap-usd` on resume).
- Downstream: `T-rwj4nV` (`ao schedule resume`), `T-8knqVe` (Resume button), `T-88wSLC`.

## Pseudocode / Algorithm
```text
HLD §9.5 request_capped_resume / drain_resume_capped are normative.
sweep_pause_expiry(now):
  for rec in fires.iter_governance(cap_state="paused"):
      if now >= rec.governance.pause_expires_at:
          update_governance(rec.fire_key, cap_state="expired", final=True)
          emit("schedule.governance.pause_expired", ...)
```

## Schemas / Interface Notes
- Interface: `service.capped_resume.{request_capped_resume, ResumeRefused, sweep_pause_expiry, ResumeCappedRequest}`.
- Spec / data schema: the drop request `{kind:"resume_capped", workspace_root, schedule_id, run_id, add_usd,
  new_cap_usd, requested_by, requested_at, request_id}`.
- Triggers / events: `schedule.governance.{resumed, resume_refused, pause_expired}`.
- Artifacts: `<state_dir>/schedules/requests/*.json`, fire records.

## Handoff Boundary
- Upstream: retire hook + lock (`T-PpE5lf`).
- Downstream: `ResumeRefused.reason` values map 1:1 to CLI exit code 3 messages and HTTP 409 bodies.

## Artifacts
- Docs/comments: `meta/tickets/E-Qp4Zm8-scheduled-run-governance/T-nI4DBf-capped-run-pause-resume/`
- Large outputs: N/A
