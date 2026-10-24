# STATUS

- ID: `T-Ws7Iso-bench-env-isolation`
- Updated At: 2026-10-24
- State: **Open**

## This update
- By: Claude · Role: manager · Date: 2026-10-24 · Comment: created from the owner's question "does running the
  bench from the same branch/workspace contaminate scores?". Verified against the 2026-10-23 transcripts: the
  workspace root inside the checkout leaks both ancestor `CLAUDE.md` files and all repo/control-plane skills and
  agents into every subject (see TASK.md). Must land before any ranking campaign; the 2026-10-23 numbers stay
  valid only as a pipeline proof.
