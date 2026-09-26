# TASK: T-ABDjSj-tool-state-ledger-budget

## Metadata
- Task ID: `T-ABDjSj-tool-state-ledger-budget`
- Epic ID: `E-YAAGhk-overseer-runner-template`
- Owner: developer
- Created: 2026-09-26
- Last Updated: 2026-09-26
- Status: Draft
- Estimate: 3 days (24 h)

## Requirements Mapping
- Requirement IDs: FR-4, FR-5, FR-7 (charter lock verify), FR-8, FR-11, FR-16, FR-18, FR-19, NFR-1..6
- Design: `docs-md/overseer-runner-hld.md` §8.2, §8.3 (integrity model), §8.4 M1, §13.4

## Description
Create `src/agent_orchestrator/templates/builtin/overseer-runner/tools/overseer_tool.py`. It is
stdlib only and Python ≥ 3.11. Implement the CLI shell and module M1:
- argparse subcommands: `intake-prep`, `ckpt-prep` (the M1 half, calling M2's
  `detect_signals`/`progress` through a stub until T-C6uQJW lands), and `unit-gate`
- hook-context reader (`AO_HOOK_CONTEXT_PATH`)
- bounded JSON/JSONL IO with atomic writes
- `load_config` (CFG-0..3)
- `load_state` (the `state.json` subset, ST-1/ST-2)
- wave-unit selection
- hash-chained ledger: `append_chained`, `verify_ledger_chain` (INT-3), idempotent ingest
- path history (breadcrumb paths ∪ git-derived paths, with confinement)
- budget stage machine with latch and override validation (FR-16)
- cadence caps (time cap from median unit duration and `wave_max_minutes`; budget cap)
- `must_close`, `allowed_decisions`
- hold gate (HOLD / INT-2 / INT-4)
- charter-lock verification (INT-1)
- unit gate (BUDGET / FANOUT)
- digest assembly (§13.4)
- the operator subcommand `request-closeout` (FR-19)

Keep all pure functions (budget math, cadence, chain hashing) in a clearly separated **no-I/O
section**, which is ADR-0016 D4's extraction readiness requirement. The source must contain no
`{{ ident }}` sequences, because it is rendered via `_render`.

- Inputs: rendered `overseer-config.json`, `state.json`, briefs, breadcrumbs, the previous digest,
  `control/*`, and hook `context.json`
- Outputs: `outputs/ledger.jsonl`, `outputs/overseer/path-history.json`,
  `outputs/checkpoints/ck-KK/digest.json`, `prep-result.json`, and exit codes 0/1/2

## Acceptance Criteria
1. `tests/test_overseer_tool_budget.py` is table-driven over §8.2. It includes at least these rows:
   - (a) the $2000 example from §12.2 gives stages converge, then stabilize, then closeout at the
     stated spends
   - (b) no settled units, so the defaults are used
   - (c) all settled unit costs are 0.0, so the zeros are used and not the defaults
   - (d) the projection crosses a threshold while actual spend has not, so the stage escalates
   - (e) the latch: a lower later spend never de-escalates
   - (f) an override honored when `breaker_overrides["run-budget-backstop"] ≥ amount`, which
     un-latches
   - (g) an override **refused** without a matching extension
   - (h) a lower override value, which may jump the stage to closeout
   - (i) `TAIL_TASKS` is 2 when `final_push` is false
   - (j) `must_close` is set at `K == max_waves` and at `stabilize_passes ≥ max_stabilize_passes`
   - (k) `allowed_decisions` per stage matches the §8.2 table exactly
2. Cadence tests: `allowed_wave_size = max(1, min(wave_size, time_cap, budget_cap))` for
   explore/converge, `max(1, min(stabilize_wave_size, cap_to_100))` for stabilize, and `0` for
   closeout. A null `started_at`/`ended_at` is excluded from the median.
3. Ledger tests:
   - re-running `ckpt-prep` on the same inputs appends **zero** new `unit` lines (idempotent)
   - chain verification passes on an untouched ledger
   - editing any byte of an earlier line fails with exit 2 and `OV-INT-3`
   - a missing breadcrumb for a failed unit is synthesized as `{outcome: failed, verdict: fail}`
   - a breadcrumb with a mismatched `unit_id` gives `OV-BC-1`
4. Hold tests:
   - a request with no answer gives exit 2 and stderr starting with `HOLD:`
   - an answer older than the request is still a HOLD
   - a valid answer is archived into `ck-KK/hold/`, both files are removed from `control/`, and a
     `hold_answered` event is appended
   - a hold decision event with the request file deleted gives `OV-INT-2`
   - a stray request without a hold decision gives `OV-INT-4`
5. Unit gate tests: `spent ≥ effective budget` gives exit 2 with a stderr message starting
   `BUDGET:` that names both options (`request-closeout`, or override + `--extend-breaker`);
   injected count > `max_injected_tasks` gives `FANOUT:`; otherwise exit 0 with no files written.
5b. `request-closeout` tests: it refuses with `OV-RUNNING` when `state.json.status == "running"`.
   On a failed run it writes a no_op report and breadcrumb for every *pending* unit (and never
   overwrites existing ones), and appends one chained `forced_closeout` event. The next `ckpt-prep`
   digest has `must_close: true` with reason `forced_closeout`, and `allowed_decisions ==
   ["closeout"]`. Ingest records the skipped units as `engine_status: skipped`, outcome `no_op`.
6. Path confinement tests: the following are excluded from hashing and recorded in the data that
   M2 turns into `breadcrumb_integrity`: an absolute path, a path containing `..`, an unknown
   `repo_id`, and a symlink resolving outside its repo root. Tracked paths are capped at
   `MAX_TRACKED_PATHS`.
7. The config tests give CFG-1 for bad percent ordering, CFG-3 when `max_injected_tasks` is below
   the computed need, and CFG-0 for non-numeric JSON.
8. `state.json` field contract test: building a real `RunState` via `models` and dumping it with
   `model_dump_json` gives a file the tool loads. `spent` equals
   `compute_run_usage_totals(state).cost_usd`.
9. Determinism: with a fixed fixture and `--now 2026-01-01T00:00:00+00:00`, running `ckpt-prep`
   twice produces byte-identical `digest.json`.
10. Coverage: the M1 lines of the tool are ≥ 90% covered (measured with `--cov` on the tools
    source path). Record the numbers in STATUS.md.
11. `ruff`/`mypy` are clean on the tool file. A `reviewer`-agent review is recorded in STATUS.md.

## Risks
- `state.json` is an internal dump. Mitigation: the AC 8 contract test.
- The git subprocess for tracked paths must be bounded (30 s) and skip non-git repos.

## Dependencies
- None upstream. It is consumed by T-C6uQJW, T-HPJcc6, T-tAKBBB, and T-eGXqXH (hook argv).

## Pseudocode / Algorithm
```text
See docs-md/overseer-runner-hld.md §8.4 M1 (load_config, load_state, ckpt_prep, hold_gate,
ingest_ledger, append_chained, verify_ledger_chain, effective_budget, unit_gate, compute_budget)
and §8.2 (the stage machine). Constants: STATE_MAX_BYTES = 16 MiB, JSON_MAX_BYTES = 1 MiB,
MAX_TRACKED_PATHS = 200, HASH_SKIP_BYTES = 50 MiB, GIT_TIMEOUT_S = 30.
```

## Schemas / Interface Notes
- CLI: `overseer_tool.py {intake-prep|ckpt-prep|unit-gate|request-closeout} --workspace-root <abs> --instance-dir <rel> [--now <iso>] [--dry-run --task-id <id>] [--reason <text>]`
- Exit codes: 0 pass, 2 violation/hold/budget, 1 internal error. Rule ids are printed with the `OV-` prefix.
- Schemas: config, ledger (unit/event, chained), path-history, and digest (§13.4).

## Handoff Boundary
- Upstream: the design (§13.4 schemas are frozen at the start of this task).
- Downstream: T-C6uQJW (reads the ledger/path-history formats), T-HPJcc6/T-tAKBBB (reuse the IO helpers and constants).

## Artifacts
- Code: `src/agent_orchestrator/templates/builtin/overseer-runner/tools/overseer_tool.py`
- Tests: `tests/test_overseer_tool_budget.py`, `tests/test_overseer_tool_ledger.py`, `tests/test_overseer_tool_gates.py`
