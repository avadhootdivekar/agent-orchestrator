# STATUS

- ID: `T-Ws7Iso-bench-env-isolation`
- Updated At: 2026-10-24
- State: **Done**

## This update
- By: Claude · Role: developer · Date: 2026-10-24 · Comment: **Landed** on `ad/enhancements-20oct` (all six ACs,
  see TASK.md "As built"). New `bench/envcheck.py`; workspaces under `${AO_BENCH_WORKSPACE_ROOT:-/var/tmp/ao-bench/<uid>}/ws`,
  grading under the sibling `_ao_bench_grading`; init-event fingerprint + per-run baseline probe + user-config
  fingerprint; grading-access audit → `suspect`; `CLAUDE_CONFIG_DIR` kept (recorded, not relocated); default
  bundle disables claude.ai connectors and auto-memory; operator-session env scrubbed. Evidence:
  `make bench-gen-smoke` 2/2 from `/var/tmp/ao-bench/1000/ws`; no-cost shim run of both real adapters (incl.
  `uv run ao run`) found all transcripts and flagged a planted grading access; real haiku `dev-core`
  `bugfix-off-by-one` per subject ($0.013 total): both solved, both subjects' init fingerprint == baseline
  `62181309c7b1`, no repo/control-plane skill or agent, `mcp_servers` empty, auto-memory off, ao = 2 init events,
  comparison clean. Legacy `gen-services-mini` report still renders (marked "not fingerprinted"). NFR-2 gate:
  five pre-epic bench tests declared (mechanical `BENCH_WORKSPACE_ROOT` → `bench_workspace_root()` rename).
  Owner decision left open: whether the default bundle should also pin effort (`CLAUDE_CODE_EFFORT_LEVEL`) — today
  the user `effortLevel` (`xhigh` on this host) applies to every subject and is only recorded.
- By: Claude · Role: manager · Date: 2026-10-24 · Comment: created from the owner's question "does running the
  bench from the same branch/workspace contaminate scores?". Verified against the 2026-10-23 transcripts: the
  workspace root inside the checkout leaks both ancestor `CLAUDE.md` files and all repo/control-plane skills and
  agents into every subject (see TASK.md). Must land before any ranking campaign; the 2026-10-23 numbers stay
  valid only as a pipeline proof.
