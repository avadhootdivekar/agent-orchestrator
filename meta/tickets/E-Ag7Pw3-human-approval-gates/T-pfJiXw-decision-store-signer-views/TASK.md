# TASK: T-pfJiXw-decision-store-signer-views

## Metadata
- Task ID: `T-pfJiXw-decision-store-signer-views` (rev 1 slug `decision-store-audit-signer`; renamed at
  Gate 1 because `audit.py` moved to `T-drPIif` and `views.py` moved in)
- Epic ID: `E-Ag7Pw3-human-approval-gates`
- Owner: developer
- Created: 2026-10-04
- Last Updated: 2026-10-05 (rev 3)
- Status: Draft
- Estimate: 3 days (on the critical path; rev 3 simplifies the scan and adds the shared test fixtures)

## Requirements Mapping
- Requirement IDs: FR-4 (store, consume order, deferral, refusals), FR-8/FR-9 (shared signer and read
  model), NFR-3, NFR-4
- Design: HLD §9.6 (store), §9.12.1 (signer), §9.12.2 (views), §9.14.3 (refusal mapping), §7.4
  TM-9/10/16/23/24/25/31; Gate 1 R-05, R-08, S-04 (state loads), S-09 (re-hash bound), suggestion S-06; Gate 2 R-09 (shared fixtures), R-10 (one re-hash per poll)
  (`already_decided`)

## Description
- `approvals/store.py`: layout constants, `ensure_real_dir`, `publish_record` (O_EXCL temp + atomic
  `rename`, 0600), V0 through `read_bounded_nofollow`, `scan_request(..., rehash: OncePerPollRehash,
  may_rehash: bool)` returning accepted / none / **needs_rehash** (rev 3, Gate 2 R-10: V1–V11 via
  `verify_decision(check_hashes=False)`, then V12 via `check_review_hashes` only if this request's set was
  already hashed in this poll or `may_rehash` is true; otherwise `needs_rehash` and this and later records
  stay in place), `OncePerPollRehash` (memoized per request, exposes `done`), `refuse(...)`
  moving records to `.refused/` with the per-request audit cap (no flood/suppression events — Gate 1
  CUT 4), and read-only `find_valid_record(...)` for signers.
- `approvals/signer.py`: `DecisionTarget`, `PreparedDecision`, `CommittedDecision`, `prepare_decision`,
  `commit_decision`, `decidability(...)` exactly per HLD §9.12.1 — every refusal is an
  `ApprovalSignerError` subclass carrying `reason: RefusalReason`, `detail_code`, `extra`; `task_id`
  validated with `TASK_ID_PARAM_PATTERN`; `state.json` load errors (`OSError`, `ValueError` incl. pydantic
  `ValidationError`, `RecursionError`) → `not_found` / `state_unreadable`; request binding check
  (`bad_binding`); `already_decided` when `find_valid_record` finds a valid record (before publishing, and
  re-checked in step 4); `comment_required` / `comment_too_long` / `review_set_mismatch` /
  `review_unavailable` / `stale_hashes` as distinct reasons; audit of refusals (MAC'd after the key is
  loaded, unauthenticated before).
- `approvals/views.py` (Gate 1 R-05): `scan_pending`, `build_task_view` (can_decide through
  `signer.decidability`, never re-implemented), `task_view_json` — the one read model for CLI and
  dashboard.

- Shared test fixtures (rev 3, Gate 2 R-09): append `fake_clock`, `make_gated_workflow(...)` and
  `human_decides(...)` (through the real `prepare_decision`/`commit_decision`) to
  `tests/approvals/conftest.py`, below `T-AGO2L6`'s autouse fixtures (HLD §18.2).

Files — new: `approvals/{store,signer,views}.py`; tests `tests/approvals/test_{store,signer,views}.py`.
Changed (new-in-epic): `tests/approvals/conftest.py` (append only). Shared files of §26: none.
**Exclusive files during stage C** (HLD §22.3): exactly these, including `conftest.py` (stage C runs
alone).

## Acceptance Criteria
1. `publish_record`: the final file appears atomically (a test that stops after the temp write leaves only a
   dot-file, which `scan_request` ignores); file name matches `DECISION_FILE_PATTERN`; mode 0600; a
   symlinked `approvals/`, `decisions/` or `decisions/<rid>/` raises `ApprovalTamperError` and nothing is
   written outside.
2. `scan_request`: in one poll, the earliest-named valid record wins and every later record is moved to
   `.refused/` with reason `already_decided`; across polls each file is evaluated exactly once; with 5,000
   garbage files plus one valid record the valid record is accepted within `ceil(5000/64)+1` polls;
   refusal audits stop at 100 per request (later refused records are still moved); the re-hash function is
   called at most once per request per poll; `test_needs_rehash_leaves_records_in_place` (`may_rehash=False`
   and a record that passes V0–V11 → `needs_rehash`, no record moved, refused or audited).
3. Signer: happy path for approve and reject; one test per refusal of §9.12.1, each asserting its `reason`
   and `detail_code` (`in_agent`, `not_found`, `state_unreadable` via a depth-bomb and a big-int
   `state.json` — `test_depth_bomb_state_is_not_found`, `request_invalid` for key mismatch, tampered
   request and `bad_binding`, `not_pending` for both sub-codes, `expired`, `unauthorized`,
   `require_dashboard`, `require_2fa`, `already_decided` — `test_second_racer_gets_already_decided`,
   `comment_required`, `comment_too_long`, `review_set_mismatch`, `review_unavailable`, `stale_hashes`
   when a file changes between prepare and commit, `store_error` via a failing publish seam); refusals
   after key load are MAC'd audit lines, earlier ones unauthenticated.
4. Views: `test_scan_pending_bounds_and_filters` (≤ 200 runs, ≤ 500 rows, `truncated`, running-only by
   default, `include_stopped`, `run_id` filter, unreadable `status.json` skipped);
   `test_task_view_json_contract` (exact keys of HLD §9.14.4 without `principal`);
   `test_can_decide_uses_signer_decidability` (spy: the same function, same verdicts); `test_big_int_state_is_not_found`.
5. Shared fixtures (Gate 2 R-09): `fake_clock`, `make_gated_workflow` and `human_decides` live in
   `tests/approvals/conftest.py` below the autouse fixtures, add no autouse behaviour, and are exercised
   by `test_signer.py` (`human_decides` round trip with the same fake clock as the signer).
6. Coverage ≥ 90% for the 3 modules; `ruff`/`mypy` clean; targeted tests green (full suite at the stage-C
   checkpoint).
7. A `reviewer`-agent review is recorded in STATUS.md.

## Risks
- Atomicity on network filesystems is out of scope (documented local-FS assumption).
- `decidability` must stay the single implementation of steps 6–11; a second copy in views would drift.

## Dependencies
- `T-drPIif` (canonical, keys, audit, accounting), `T-1MgGb4` (hashing, records, authz).

## Pseudocode / Algorithm
```text
HLD §9.6.2 ensure_real_dir, §9.6.3 publish_record + find_valid_record, §9.6.4 scan_request/refuse
(needs_rehash), §9.12.1 prepare_decision/commit_decision/decidability, §9.12.2 views.
```

## Schemas / Interface Notes
- Files: `<run_dir>/approvals/{audit.jsonl, decisions/<rid>/<name>.json, decisions/<rid>/.refused/}`.
- Record schema: HLD §9.5.1; refusal mapping: HLD §9.14.3; task view JSON: HLD §9.14.4.

## Verification
```
WT=/usr/avadhoot/mounted/agent-orchestrator/.claude/worktrees/agent-a513b4d78ac3b20bb
PY=/usr/avadhoot/mounted/agent-orchestrator/.venv/bin/python
cd $WT && $PY -m pytest -o pythonpath=src -p no:cacheprovider -q tests/approvals/test_store.py tests/approvals/test_signer.py tests/approvals/test_views.py
cd $WT && $PY -m pytest -o pythonpath=src -p no:cacheprovider -q --cov=agent_orchestrator.approvals --cov-report=term-missing tests/approvals
cd $WT && $PY -m ruff check src/agent_orchestrator/approvals tests/approvals && $PY -m mypy src/agent_orchestrator/approvals
```

## Handoff Boundary
- Upstream: `T-drPIif`, `T-1MgGb4`.
- Downstream: `T-vwIpSw` (engine uses `scan_request`), `T-nmL0HP` and `T-l43hCg` (both use the signer and
  the views).

## Artifacts
- Code as listed; evidence in STATUS.md.
