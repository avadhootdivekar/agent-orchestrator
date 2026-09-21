# STATUS

- ID: `T-J1b0FN-timing-profiling`
- Updated At: 2026-09-21
- State: `In Progress` (B2.1 Done, B2.2 delegated/in progress)
- Owner: `dev-epic` agent (B2.1), delegated `developer` agent (B2.2)

## Early-gate outcome (2026-09-21)
- Architect finding: split into B2.1 (top-N slowest, MVP, trivially correct) and B2.2
  (activity breakdown, flagged as the epic's only heuristic-based MVP item — a hardcoded
  build/test keyword list matched against free-form Bash text). Kept B2.2 in MVP per the
  original epic request (feasibility explicitly confirmed via real captured transcripts before
  scoping), but hardened per the architect's stated conditions: named module-level constants
  (no magic literals), a `Literal`/enum for categories, explicit approximation caveat in the
  docstring, and a COMMITTED fixture transcript under `tests/` (not the gitignored
  `playground/.tmp/` path the design doc's evidence originally cited — architect finding B-8).

## This update
- **B2.1 (done, implemented directly)**: `reporting.py::top_n_slowest_tasks` +
  `cache_effectiveness`/`run_cache_effectiveness` (shared with B4) + `ao report-timing
  --run-id <id> [--top N]` CLI command. 10 unit tests (`tests/test_reporting.py`), 1 e2e test
  (`tests/test_e2e_cli_cost_caching.py`, shared evidence with B3). `ruff`/`mypy` clean.
- **B2.2 (delegated to `developer`, in progress)**: `task_activity_breakdown` — parses a
  committed fixture `transcript.jsonl` via the EXISTING `claude_cli.py::parse_transcript_events`
  (read text, do not change that function's signature), buckets by tool-name-derived activity
  category (table in design doc §2.2), wires into `ao report-timing --task <task_id>`.

## Evidence
- `pytest tests/test_reporting.py -q` → 10 passed.
- `pytest tests/test_e2e_cli_cost_caching.py -q` → 1 passed (`ao report-timing` output
  asserted as part of the shared e2e proof).

## Risks / Blockers
- None for B2.1 (done). B2.2 pending delegated developer's fixture commit + implementation.

## Next actions
1. Developer: commit a small fixture transcript under `tests/fixtures/` (or `tests/bench/data/`
   sibling convention), implement `task_activity_breakdown`, wire `--task` into
   `ao report-timing`, tests, ruff/mypy.
2. Roll into `T-UJElTR` late-gate verification once landed.
