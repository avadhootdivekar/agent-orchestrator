# TASK: T-otHPGB-engine-resume-policy

## Metadata
- Task ID: `T-otHPGB-engine-resume-policy`
- Epic ID: `E-Ag7Pw3-human-approval-gates`
- Owner: developer
- Created: 2026-10-04
- Last Updated: 2026-10-05
- Status: Draft
- Estimate: 3 days (on the critical path; the pure helpers it calls are built and unit-tested by `T-1MgGb4`)

## Requirements Mapping
- Requirement IDs: FR-6, FR-7 (routes/loops/emit at engine level), FR-11 (resume events), NFR-4
- Design: HLD §9.10.6 (resume integrity, full pseudocode and refusal messages), §9.10.3 (re-entry
  branches), §9.9 (policy, evidence, clone re-derivation, derivable `not_taken`), §9.1.5–§9.1.7,
  §7.4 TM-5/6/7/19/24/29/30/33/34, §12.2, §12.4, §12.7, §12.8; Gate 1 S-01, S-02, S-03, R-03, R-04

## Description
Complete the resume semantics of the driver (no new engine edits beyond `T-vwIpSw`'s):

1. `begin_session` for every resumed session (and any session with gate evidence), exactly per HLD §9.10.6:
   `check_policy` → `missing` + evidence → fail closed (`missing_with_evidence`); `missing` without
   evidence + gates → record the policy (`approval.policy_recorded`, `on_resume: true`); `key_changed` /
   `bad_signature` / `drift` → fail closed with the documented message; `ok` with additions → re-sign
   (`approval.policy_updated`); then `rederive_loop_clones` with the engine's bound `_clone_body`
   (mismatch → `clone_mismatch`); then `normalise_gate_statuses` (gates and gate ancestors: `succeeded`
   needs `approved_and_verified`; `skipped` gate → `pending`; non-derivable `not_taken` → `pending`;
   `approval.unverified_on_resume` with the reason). Remove `T-vwIpSw`'s interim fail-closed stub and its
   temporary test.
2. `open_gate` re-entry branches: request signature or binding invalid → void + supersede
   (`approval.request_invalid`, `approval.superseded`); `spec_digest` mismatch against the
   **effective** (static base) spec → fail + halt (`approval.policy_violation`, `spec_digest`); valid
   pending → re-enter (`approval.reentered`, same `request_id` and `expires_at`; the pre-pass polls at
   once); `rejected`/`expired`/`void` → new request with `supersedes`, `previous`, and the old span appended
   to `closed_waits`.
3. Integration tests for resume integrity, routes, loops, manifests and boot-resume.

Files — changed: `approvals/engine_glue.py`; new tests `tests/approvals/test_engine_resume.py`,
`tests/approvals/test_engine_loops_routes.py`. Shared: none. **Exclusive files during stage E** (HLD
§22.3): exactly these; never in parallel with `T-vwIpSw`.

## Acceptance Criteria
1. Re-entry: resume of a run halted with a pending gate re-enters the **same** `request_id` and
   `expires_at` (`approval.reentered`); `test_decision_recorded_while_down_consumed_before_dispatch` — with
   every `max_parallel` slot claimable by an independent ready task, a record written while the engine was
   down with `decided_at < expires_at` is applied in the first loop pass before anything else is dispatched,
   even if the resume happens after `expires_at`; with no record and `now >= expires_at` the gate expires on
   the first poll.
2. Resume detection (S-01): `test_truncated_spec_sessions_still_treated_as_resume` (`spec_sessions` set to
   `[]` in `state.json`: the policy is checked, the tampered gate below is still re-asked);
   `test_fresh_library_state_records_policy_without_refusal` (`run_state=store.new_run(wf)` on a gated
   workflow: policy recorded with `on_resume: true`, nothing refused);
   `test_create_policy_refuses_to_overwrite_existing`.
3. Status flips (S-02): `state.json` edited to mark a gate `succeeded` (no accepted record, or one that fails
   verification), `skipped`, or `not_taken` (not derivable), or a gate **ancestor** `not_taken` → after
   resume the task is `pending` → re-asked with a new request (`supersedes` set where there was one);
   `approval.unverified_on_resume` at ERROR with the reason; a gate legitimately `not_taken` through an
   unselected route stays `not_taken` (`test_legit_route_not_taken_gate_stays_not_taken`). Same for a loop
   clone gate (`test_state_tamper_clone_gate_succeeded_is_reasked`).
4. Pending-request tampering: removed `approvers` → `approval.request_invalid` → a fresh request built from
   the static spec (approvers restored); a validly signed request copied from another task →
   `bad_binding` → superseded (`test_request_copied_from_other_task_superseded`).
5. Gate-scoped policy (R-03) through `ao resume` (CliRunner): removing the gate, removing
   `depends_on: [gate]` from a dependent (or every path to it), renaming or removing a dependent, and
   changing `approvers`/`require_*`/`timeout_seconds`/`review`/`message` each exit 1 with
   `ApprovalPolicyError` naming the change, `state.status == "failed"`, nothing dispatched;
   `test_unrelated_spec_edit_resumes` (a new unrelated task, an instruction/model/agent change, an output
   path outside every closure, `max_iterations`) resumes normally; adding a downstream task resumes and
   logs `approval.policy_updated`.
6. Missing policy (S-03): `test_policy_deleted_with_evidence_refuses_resume` — one case per evidence kind
   (approvals entry, non-pending gate, awaiting task, invalid policy present, `approvals/` directory) →
   `missing_with_evidence`, run failed; `test_gate_added_to_gate_free_run_records_policy`;
   `test_gate_removal_refused_gate_addition_allowed`; key rotated (new key dir) → `key_changed`.
7. Loop clones (R-04): `test_weakened_clone_gate_uses_static_spec` (approvers cleared on an unopened
   `gate__iter2` in `injected_tasks` → `clone_mismatch`; and the request, if it were opened, would carry
   the base spec), `test_clone_gate_turned_into_agent_task_refuses_resume`,
   `test_downstream_clone_detached_refuses_resume`.
8. Reject → `ao resume` → new request with `previous.status == "rejected"`, `previous.reason` containing
   the comment, and a `closed_waits` span for the rejected request.
9. Boot-resume: `service/boot_resume.scan_resumable_runs` lists a dashboard-launched gated run with a dead
   PID; an in-process resume of it re-enters the pending request.
10. Routes/loops/manifests: a gate in an unselected route cone never gets an `approvals` entry or audit
    line; a loop-body gate over 3 iterations yields 3 distinct `request_id`s keyed `gate`, `gate__iter2`,
    `gate__iter3`, and `test_loop_gate_iteration2_waits_for_in_body_producer` proves each clone opens only
    after that iteration's producer settled; an emitter whose manifest contains `approval` fails with
    `manifest_error` and the run halts.
11. Targeted tests green; ruff/mypy clean (full suite at the stage-E checkpoint); a `reviewer`-agent
    review is recorded.

## Risks
- Re-verification must not re-hash files (approved content may legitimately change later); AC3 + a test
  that edits an approved artifact after approval and resumes (gate stays succeeded) guard this.
- Over-strict normalisation would re-run legitimately skipped work: only gates and gate ancestors are
  normalised, and only from tampered or underivable states (AC3's legit-route case guards it).

## Dependencies
- `T-vwIpSw` (driver, engine edits); `T-1MgGb4` (policy, evidence, clone and derivation helpers).

## Pseudocode / Algorithm
```text
HLD §9.10.6 begin_session / normalise_gate_statuses / approved_and_verified / fail_closed;
§9.10.3 open_gate re-entry; §9.9.3 check_policy; §9.9.5 rederive_loop_clones; §9.9.6 derivable_not_taken.
```

## Schemas / Interface Notes
- `ApprovalPolicyError(OrchestratorError)` with the messages of HLD §9.10.6; events of HLD §15
  (`policy_recorded`, `policy_updated`, `policy_violation`, `reentered`, `superseded`, `request_invalid`,
  `unverified_on_resume`).

## Verification
```
WT=/usr/avadhoot/mounted/agent-orchestrator/.claude/worktrees/agent-a513b4d78ac3b20bb
PY=/usr/avadhoot/mounted/agent-orchestrator/.venv/bin/python
cd $WT && $PY -m pytest -o pythonpath=src -p no:cacheprovider -q tests/approvals tests/service/test_boot_resume.py tests/test_resume_replay.py
cd $WT && $PY -m ruff check src/agent_orchestrator/approvals tests/approvals && $PY -m mypy src/agent_orchestrator/approvals src/agent_orchestrator/engine.py
```

## Handoff Boundary
- Upstream: `T-vwIpSw`.
- Downstream: `T-pdLR96`.

## Artifacts
- Code as listed; evidence in STATUS.md.
