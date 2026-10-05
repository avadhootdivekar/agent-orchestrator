# HANDOFF: T-nPMuz4-cache-shadow-value-check

- Task: `T-nPMuz4-cache-shadow-value-check` (G0 protocol and tooling hand-off)
- State: `Done`
- From: `tester` (+ `manager` sign-off)
- To: T-bdQZW4 (links the protocol); the parent or operator (executes G0 after the merge)

## What was delivered (commit `edc3c18`)
- `docs-md/result-cache-g0-protocol.md`: the runnable procedure and the report template.
- `output/E-Rc4Hk8-cross-run-result-cache/g0-protocol-smoke.md`: the smoke-validation evidence.
- The epic `STATUS.md` G0 line ("protocol shipped; execution post-merge").
- `tests/cache/test_g0_protocol_doc.py`: guards the documented field names and bash blocks against the real CLI.

## Frozen names / contracts
- The §22.5 decision rule (a recommendation; thresholds pending OQ-6) and the measurement fields
  of §13.4 and §13.6.

## Verification the receiver should run
- Re-run the smoke commands from the evidence file; the second run shows `would_hits > 0`.
- `pytest -q -p no:cacheprovider tests/cache/test_g0_protocol_doc.py`

## For T-bdQZW4 and the G0 executor
- The doc is machine-checked through HTML-comment markers (`g0-fields:*`, `g0-cmd:*`): keep them if the doc is edited.
- Real CLI facts the protocol encodes: `ao report-usage` has `--workspace` (no `-w`) and scans every run
  unless `--run-id` is repeated; `lookups = hits + would_hits + misses` (ineligible excluded); shadow
  re-stores a would-hit key (refreshes `created_at`), so `entries` counts distinct keys; the miss
  `reason` is almost always `not_found`, the information is in `components` (diffed in Step 6);
  ineligible reasons are only in `cache.skip` run.log events.

## Comments
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Handoff stub created (Rev 2).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: re-scoped deliverables;
  G0 execution is not handed over to an epic task. State `Draft` mirrors `TASK.md` and
  `STATUS.md`.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> In Review; handoff available (commit `edc3c18`). Manager sign-off pending.
