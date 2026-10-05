# G0 protocol smoke validation (T-nPMuz4)

- By: developer · Role: developer · Date: 2026-10-05
- Protocol under test: `docs-md/result-cache-g0-protocol.md` (this repo; after the smoke only HTML-comment
  markers outside the fenced blocks were added, for the guard test; the bash blocks are unchanged).
- **This validates the tooling, not the value.** The fake executor reports zero cost, so
  `avoidable_cost_usd` is 0 by construction. No G0 measurement was made.

## Setup and honesty notes

- **Real CLI, real executor boundary.** The runs are real `ao run` subprocesses of this worktree's
  venv entry point (`<worktree>/.venv/bin/ao`, via a one-line `exec` wrapper `aow` so the commands
  could be issued from the sandboxed shell; the same code `ao-beta` would carry). The agent uses
  the registered `fake` executor (`agents: {"ag": {"executor": "fake"}}`), the same one the e2e
  tests use through `CliRunner`; nothing is mocked. `AO_CACHE=shadow` comes from the environment.
- **Documented commands run verbatim.** `smoke_doc.sh` extracts each fenced bash block of the
  protocol with a regex and executes it unmodified; only the Step 0 variables are exported and the
  `<workflow.json>`/`<reposets.json>`/`<agents.json>` placeholders of Step 4 are substituted.
- Fixture (temp dir under the session scratchpad, a git repo with one commit; `wf.json`: tasks `a`
  and `b` opted in with `cache: true` and default `skip_if_outputs_exist: true`; `c` not opted in;
  `d` opted in but with no outputs, so ineligible). Outputs under `$WS/out` are deleted between
  runs; with `skip_if_outputs_exist: true` this is what makes the second run reach the lookup
  instead of being skipped (HLD EC-13).
- **Third run (beyond the required two)**: `instructions/a.md` was edited and the run repeated, only
  to produce a real miss for the Step 6 miss-component script (two runs of unchanged content have no
  miss to diff). The acceptance numbers below are the two-run window.
- Stable install untouched: `install.sh` was not run.

```text
$ command -v ao; ao --version        # before AND after the whole smoke (identical)
/home/avadhoot/.local/bin/ao
ao 0.1.0 (a10ebc5.dirty) built 2026-10-03T16:50:49Z
$ <worktree>/.venv/bin/ao --version  # the build under test
ao 0.1.0 (d02816f.dirty) built 2026-10-05T11:55:56Z
```

## Commands and results (transcript of `smoke_doc.sh`, abridged only by the long fixture paths)

`WS=<scratchpad>/g0smoke/fx/ws`, `WF_ID=g0-smoke`, `WINDOW_START=19700101T000000Z`, `DAYS=7`,
`AO=<worktree>/.venv/bin/ao`.

### Step 3 (start state; `exists: false`, nothing stored yet)

```text
2026-10-05T12:12:36Z
entries=0 expired_entries=0 bytes.total=0 oldest_created_at=None newest_created_at=None
```

### Run 1 and Run 2 (Step 4)

```text
$ AO_CACHE=shadow $AO run --workflow $SPECS/wf.json --reposets $SPECS/rs.json --agents $SPECS/ag.json
Result cache: shadow (source=env), 3 of 4 static task(s) opted in, at $WS/.orchestrator/cache
Run:    g0-smoke-20261005T121237Z          <- run 1 (R1)
Result cache: hits=0 (saved ~$0.0000 est., ~0 tokens, ~0s) would_hits=0 misses=2 stored=2 ineligible=1
$ rm -rf "$WS/out"
$ AO_CACHE=shadow $AO run ...              (same command)
Result cache: shadow (source=env), 3 of 4 static task(s) opted in, at $WS/.orchestrator/cache
Run:    g0-smoke-20261005T121239Z          <- run 2 (R2)
Result cache: hits=0 (saved ~$0.0000 est., ~0 tokens, ~0s) would_hits=2 misses=0 stored=2 ineligible=1
```

### `result_cache` object per run (`ao report-usage --workspace $WS --run-id <id> --json`)

Run 1 (`g0-smoke-20261005T121237Z`):

```json
{"hits": 0, "saved_cost_usd": 0.0, "saved_tokens": 0, "saved_seconds": 0.0, "lookups": 2, "would_hits": 0, "misses": 2, "ineligible": 1, "avoidable_cost_usd": 0.0, "miss_reasons": {"not_found": 2}, "store_skip_reasons": {}}
```

**Run 2 (`g0-smoke-20261005T121239Z`): `lookups = 2 > 0`, `would_hits = 2 > 0`.**

```json
{"hits": 0, "saved_cost_usd": 0.0, "saved_tokens": 0, "saved_seconds": 0.0, "lookups": 2, "would_hits": 2, "misses": 0, "ineligible": 1, "avoidable_cost_usd": 0.0, "miss_reasons": {}, "store_skip_reasons": {}}
```

### Step 5 collect (the documented `RUNIDS` filter + window object), Step 7 rate

```text
--run-id g0-smoke-20261005T121237Z --run-id g0-smoke-20261005T121239Z
{ "hits": 0, "saved_cost_usd": 0.0, "saved_tokens": 0, "saved_seconds": 0.0,
  "lookups": 4, "would_hits": 2, "misses": 2, "ineligible": 2, "avoidable_cost_usd": 0.0,
  "miss_reasons": {"not_found": 2}, "store_skip_reasons": {} }
lookups=4 would_hits=2
would_hit_rate=0.500
avoidable_usd_per_week=0.00
```

Text form: `Result cache (shadow): 2 would-hit(s) of 4 lookup(s), ~$0.0000 avoidable (est.)`.

### Stats before / after (`ao cache stats --json`, the five documented fields)

| Point | entries | bytes.total | expired_entries | oldest_created_at | newest_created_at |
|-------|---------|-------------|-----------------|-------------------|-------------------|
| start (before run 1) | 0 | 0 | 0 | null | null |
| end of window (after run 2) | 2 | 2762 | 0 | 2026-10-05T12:12:39.061238+00:00 | 2026-10-05T12:12:39.071145+00:00 |
| (after the extra run 3) | 3 | 4125 | 0 | 2026-10-05T12:12:39.061238+00:00 | 2026-10-05T12:12:42.412568+00:00 |

Observation recorded in the protocol: run 2 re-stored the same two keys (shadow refreshes an entry
it would have hit, ADR-0019 D26 / HLD 12.3), so `entries` stayed 2 and `oldest_created_at` moved to
run 2's time. `entries` counts distinct keys (3 after run 3: task `a` got a new key).

### Step 6 miss components (after the extra run 3), real `run.log` events

```text
miss reasons: {'not_found': 3}
components that changed vs the same task last miss: {'instruction': 1}
cache.skip (phase/reason): {'lookup/no_outputs': 3}
```

This matches what was done (only `a.md` changed, only task `a` missed again; task `d` is ineligible
`no_outputs` in each of the three runs). The `cache.miss` event fields it reads are `event`, `ts`,
`task_id`, `reason`, `components`; the `cache.skip` fields are `phase`, `reason` (all verified in
the real `run.log`, e.g. `{"event": "cache.miss", "reason": "not_found", "key": "...",
"components": {"key_schema": "6b86b273ff34", "agent": ..., "instruction": ..., "repo_heads": ...}}`).

## Acceptance (T-nPMuz4 AC-4, AC-5)

- Exact commands, run ids and both `result_cache` objects: above. Second run: `lookups = 2`,
  `would_hits = 2`.
- Every collection step of the protocol ran and produced the documented fields (Steps 3, 5, 6, 7).
- `ao --version` and `command -v ao` unchanged before and after (above).
- Guard test (cheap): `tests/cache/test_g0_protocol_doc.py` (10 tests) re-runs the same sequence
  through `CliRunner` on a tiny fixture and executes the doc's collection blocks verbatim.

## Findings

- No defect found: shadow mode produced would-hits on the repeat, and the stats/usage fields
  match HLD 13.4 and 13.6 exactly.
- Doc-relevant behaviours (now in the protocol): `ao report-usage` has no `-w` short option and
  scans every run unless `--run-id` is given; `lookups` excludes `ineligible`; run ids are second
  granular; shadow refreshes `created_at` of a would-hit entry.
- Limit: the fake executor cannot show non-zero `avoidable_cost_usd` through the CLI (that needs an
  executor that reports cost; `tests/cache/test_cli_result_cache_wiring.py` covers a non-zero case
  with a cost-reporting wrapper).
