# HANDOFF: T-o95l1M-cache-cli-wiring

- Task: `T-o95l1M-cache-cli-wiring`
- State: `Draft` (handoff not yet available)
- From: `developer` (Dev B)
- To: T-JCOAsq (e2e), T-nPMuz4 (G0 protocol smoke), T-bdQZW4 (docs)

## What will be handed over
- `ao run --cache` and `ao resume --cache` end to end, with the banner (and warnings), the
  summary line and the `report-usage` lines.

## Frozen names / contracts
- The banner and summary-line texts (HLD §8.1.7, §8.8.3).

## Verification the receiver should run
- `pytest -q tests/cache/test_cli_result_cache_wiring.py tests/cache/test_noop_proof.py`

## Comments
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Handoff stub created (Rev 2).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: warnings and lazy
  imports. State `Draft` mirrors `TASK.md` and `STATUS.md`.
