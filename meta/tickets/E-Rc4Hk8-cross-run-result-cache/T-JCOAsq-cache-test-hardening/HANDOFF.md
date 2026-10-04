# HANDOFF: T-JCOAsq-cache-test-hardening

- Task: `T-JCOAsq-cache-test-hardening`
- State: `Draft` (handoff not yet available)
- From: `tester`
- To: T-XpF1pF (Part 1 is its prerequisite), T-fXWbqg (gate evidence), T-bdQZW4, the parent
  (golden recapture after merging)

## What will be handed over
- **Base golden** for the I-2 fixture (serial and `max_parallel=3`), captured from a temporary
  worktree of `bb6d8a0` with `PYTHONPATH` set to its `src`, a fixed clock, a fixed run id and
  `<WS>` normalization. The exact command is recorded here once captured.
- `tests/cache/test_noop_proof.py` (I-1, I-2), `tests/cache/test_adversarial.py`,
  `tests/cache/test_integration_hardening.py`, `tests/test_e2e_cli_result_cache.py`.
- The CI step in `.github/workflows/ci.yml` and the coverage numbers.
- The full-suite numbers.

## Frozen names / contracts
- The golden fixture paths; the NFR-1 evidence definitions (HLD §8.7.5); the coverage thresholds.

## Verification the receiver should run
- `pytest -q tests/cache tests/test_e2e_cli_result_cache.py`
- The CI step's commands (HLD §18).

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: re-split parts, CI step,
  golden recapture note. State `Draft` mirrors `TASK.md` and `STATUS.md`.
