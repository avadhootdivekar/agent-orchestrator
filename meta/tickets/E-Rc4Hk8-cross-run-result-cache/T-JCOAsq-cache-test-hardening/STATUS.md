# STATUS

- ID: `T-JCOAsq-cache-test-hardening`
- Updated At: `2026-10-05`
- State: `In Progress` (Part 1 done, Parts 2–3 pending)
- Owner: `tester`

## This update (2026-10-05, Rev 2)
- **Part 1 COMPLETE:** I-1 and I-2 tests both pass (4/4 total)
  - I-1: 2 tests verify poisoned imports with AO_CACHE=0 (serial and max_parallel=3)
  - I-2: 2 tests verify cache-off output matches golden fixture (serial and max_parallel=3)
  - All tests pass deterministically over 3 runs
  - Golden fixtures captured with fixed clock and run_id, normalized paths
  - Code passes: ruff check, ruff format, mypy (import warnings only)
  - Estimate: 6h ✓ (completed on 2026-10-05)

## Evidence
- **I-1 (Poisoned imports):** `tests/cache/test_noop_proof.py::TestI1PoisonedImports`
  - test_i1_serial_no_cache_submodules_imported — PASS
  - test_i1_parallel_no_cache_submodules_imported — PASS
  - Verifies: cache modules not loaded, all tasks complete, no cache dir, no result_cache in status
  
- **I-2 (Golden fixture):** `tests/cache/test_noop_proof.py::TestI2GoldenSnapshot`
  - test_i2_serial_matches_golden — PASS
  - test_i2_parallel_matches_golden — PASS
  - Golden files: tests/fixtures/result_cache/golden/{golden_serial,golden_parallel}.json
  - Verified: output byte-identical (normalized) to golden fixture (AO_CACHE=0, fixed clock)

## Risks / Blockers
- Golden recapture needed after cache code lands (T-XpF1pF) or at sibling merge base (HLD §24.2).
- No blockers for Parts 2–3 to proceed after their dependencies (T-XpF1pF, T-u3jG8F, T-HjxNQ0).

## Next actions
1. ✓ Part 1 done: I-1 tests pass, I-2 framework ready
2. Part 2 after T-XpF1pF, T-u3jG8F and T-HjxNQ0: integration and adversarial tests (I-9…26, ADV-1…10)
3. Part 3 after T-o95l1M, T-6tRKml, T-ZTxN1x, T-bLpoze: e2e suite, CI step, coverage gate

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4 consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the early-gate review and the manager's scope decisions (HLD §23.5). State stays `Draft` (Part 1 any time before T-XpF1pF; Parts 2–3 in the hardening phase); this file, `TASK.md`, `HANDOFF.md` (when present) and the epic `STATUS.md` rollup agree.
- By: tester · Role: tester · Date: 2026-10-05 · Comment: Part 1 COMPLETE. I-1 and I-2 tests fully implemented and passing (4/4). Golden fixtures captured with fixed clock and normalized paths. All tests deterministic over 3 runs. Passed ruff/mypy gates. Handoff updated with capture command and verification steps.
