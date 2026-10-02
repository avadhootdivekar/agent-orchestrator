# STATUS

- ID: `T-qifi3r-activity-reader`
- Updated At: 2026-10-02
- State: Done
- Owner: dev-epic

## This update
- By: dev-epic · Role: manager · Date: 2026-10-02 · Comment: Done.

## Evidence
- src/agent_orchestrator/ui/activity.py; tests/ui/test_activity.py (72 tests: fold/describe/parse/build, tailer partial/garbled/oversize/burst/cache/inode, path-safety incl. traversal ids, symlink escape, FIFO); fixture tests/fixtures/transcript_stream_real_shape.jsonl (redacted real claude stream-json). Finding: stream usage.output_tokens is first-chunk (~1% of final) -> estimate from chars + thinking_tokens events (61-87% of final over 60 real attempts); turns exclude sidechains.

## Next actions
1. None (Phase 1 shipped).
