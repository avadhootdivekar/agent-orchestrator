# TASK: T-Ph2Cap-path-history-unbounded-growth

## Metadata
- Task ID: `T-Ph2Cap-path-history-unbounded-growth`
- Epic ID: `E-Rr5Lnc-overseer-run-resilience`
- Owner: `unassigned`
- Created: `2026-11-11`
- Last Updated: `2026-11-11`
- Status: `Draft`
- Priority: `P0`
- Estimate: `< 3 days (re-estimate after the re-analysis below)`

## READ FIRST: re-analysis required before implementing
This ticket was filed from a log-level RCA of past failed runs (2026-11-11). The root cause
below is evidence-backed, but the **proposed fix is a starting hypothesis, not a decided
design.** Whoever picks this task up must, before writing code:
1. Re-read the cited evidence (run logs / hook stderr) and re-confirm the root cause against
   the CURRENT code (the overseer tool and engine change often; line numbers here will drift).
2. Look for other runs/workspaces with the same failure signature since this was filed.
3. Write down at least two fix options with trade-offs (safety, determinism, cost, contract
   impact on `overseer-contract.md` / instructions / existing runs on disk) and pick one,
   recorded in this ticket's STATUS.md, before implementing.
4. Add a regression test that reproduces the original failure first (fails before the fix).
5. Check compatibility with in-flight runs: per-run copies of `tools/overseer_tool.py` are
   frozen at scaffold time, so a template fix does not reach existing runs.

## Summary
outputs/overseer/path-history.json grows without bound and crashes ckpt-prep at 1 MiB. Impact: 1 run stop (deterministic for every long run).

## Evidence
- `ao-runner-ao` o-6w86nm-sched-governance ck-40 `pre_hook/stderr.txt`:
  `overseer_tool internal error: OversizeInputError('.../outputs/overseer/path-history.json is
  1070826 bytes, exceeds the 1048576 byte cap')` (85 ms after start, no LLM involved).
- The file held 39 per-checkpoint entries, each ~34.6 KB once MAX_TRACKED_PATHS=200 is reached.
- Mitigated in that run only by a LOCAL patch of its per-run tool copy (rolling window of 8
  entries) and a trimmed history file; backups in the run's `resume-backup-20261111/`.

## Root cause (as analysed 2026-11-11)
`update_path_history()` appends one entry per checkpoint and never prunes; it then reads the
file with `read_json_bounded(path, JSON_MAX_BYTES)` (1 MiB), a cap meant for AGENT-authored
JSON, not the tool's own state. With 200 absolute paths x 64-char hashes per entry the file
crosses 1 MiB at ~30 checkpoints. `_read_optional_json` (used by the signal detectors) swallows
the same oversize error silently, so oscillation signals would also silently disappear.

## Proposed fix (hypothesis; evaluate before implementing)
- Bound the file: rolling window of the last N checkpoints (N >= what `content_oscillation`
  needs), or a compact per-path structure (last K distinct hashes per path).
- Store repo-relative paths instead of absolute ones; consider hashing to shorter digests.
- Give tool-owned state files their own (larger) cap or no cap; keep the 1 MiB cap for
  agent-authored JSON only. Audit every other tool-maintained, append-only file (ledger,
  digests, state) for the same pattern.
- Make the oversize case in `_read_optional_json` loud (signal or warning), not silent.

## Acceptance Criteria
1. A test simulating 150 checkpoints x 200 tracked paths keeps path-history.json under a fixed bound and ckpt-prep succeeds.
2. content_oscillation still detects a return-to-earlier-hash within the retained window (existing tests pass).
3. No tool-maintained file is read with the agent-JSON cap (audit result recorded in STATUS.md).
4. Re-analysis notes and the chosen option (with rejected alternatives) are recorded in STATUS.md.
5. `pytest -q`, `ruff check .`, `mypy src` green; template change also reflected in
   `overseer-contract.md.tmpl` / instructions / README where relevant.

## Dependencies
- See the epic's Task List for ordering.

## Artifacts
- Docs/comments: `meta/tickets/E-Rr5Lnc-overseer-run-resilience/T-Ph2Cap-path-history-unbounded-growth/`

## Comments
- By: Claude (Opus 5.5, interactive session) · Role: architect · Date: 2026-11-11 · Comment: Filed from the cross-workspace failure RCA in the epic. Fix not evaluated in depth.
