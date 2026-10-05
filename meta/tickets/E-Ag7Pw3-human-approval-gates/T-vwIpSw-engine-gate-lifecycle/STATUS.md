# STATUS

- ID: `T-vwIpSw-engine-gate-lifecycle`
- Updated At: 2026-10-05
- State: Draft — Not started
- Owner: developer

## This update
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Ticket created from HLD §9.10.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Gate 1 revision: `_open_ready_gates` pre-pass
  (R-01), per-poll re-hash budget with round-robin (S-09), `approval_driver_factory` replacing
  `approval_key_store` and a fail-closed `_require_approvals` instead of asserts (suggestion S-05), the
  breaker extraction as its own first commit (suggestion S-10), `is_resume = run_state is not None`
  (S-01), `settings.py` folded into `engine_glue.py` (CUT 5), and the interim resume path fails closed
  until `T-otHPGB` lands (new concern NC-4). Still 3 days.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Gate 2 revision (rev 3, frozen): the driver
  factory takes `is_resume` and probes the gated marker on resumed runs instead of `lstat`-ing
  `<run_dir>/approvals` (S-11, S-12); the fresh-run branch signs the policy, saves `state.json`, then writes
  the marker, and creates no directory (R-11); one consume-time re-hash per poll replaces the byte budget
  (R-10); `_is_gate` (persisted `approval` or `effective_approval`) at both detection sites (R-14); engine
  fixtures move to `tests/approvals/engine_helpers.py` (R-09). New ACs 4, 7, 8 and the resumed gate-free
  NFR-1 probe test. Estimate unchanged (3 d).

## Evidence
- None yet.

## Risks / Blockers
- Waits for `T-pfJiXw` (and `T-1MgGb4`/`T-drPIif` through it).

## Next actions
1. Record the NFR-1 goldens from the unmodified engine, then land the extraction as the first commit.
2. Driver + factory, then the engine edits, then the lifecycle tests.
