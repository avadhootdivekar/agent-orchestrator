# TASK: T-HPJcc6-tool-structural-checkers

## Metadata
- Task ID: `T-HPJcc6-tool-structural-checkers`
- Epic ID: `E-YAAGhk-overseer-runner-template`
- Owner: developer
- Created: 2026-09-26
- Last Updated: 2026-09-26
- Status: Draft
- Estimate: 3 days (24 h)

## Requirements Mapping
- Requirement IDs: FR-2, FR-3, FR-7, FR-18 (OV-R9), NFR-4, NFR-6
- Design: `docs-md/overseer-runner-hld.md` §8.4 M3, §13.3, ADR-0016 D9

## Description
Implement the structural half of M3 in `tools/overseer_tool.py`. The subcommands are `intake-check`
(post-hook of `intake`) and the structural part of `ckpt-check`, sharing one rule engine, plus
`--dry-run --task-id` support. The rules:

- **OV-R1** strict JSON
- **OV-R2** field whitelist per entry class (unit, next-ckpt, tail, expander)
- **OV-R3** id patterns, uniqueness, no collision with `state.json` ids
- **OV-R4** unit count ≤ `allowed_wave_size` (from the digest; at intake use `min(wave_size, …)`
  from config)
- **OV-R5** expanders ≤ `max_expanders_per_wave`
- **OV-R6** brief exists and is schema-valid, `ask_ids ⊆ charter`, and `agent`/`instruction`
  **exactly** equal `kind_map[kind]` (dev-security CRITICAL #1)
- **OV-R7** outputs == [report, breadcrumb], inputs ∋ brief
- **OV-R8** `depends_on` ∋ emitter and ⊆ {emitter} ∪ manifest ids
- **OV-R9** exactly `pre_hook: {"use": "ov-unit-gate"}`, no `post_hook/model/max_turns`, and an
  effort enum
- **OV-R10** exactly one of {next ckpt, tail}, with exact shapes: hooks, `emit_tasks`, manifest
  path, outputs, effort, `model` iff `overseer_model` is set, `depends_on == all unit ids` or
  `[emitter]`, and inputs ⊇ all unit breadcrumbs; `final-push` iff `final_push`
- **OV-R15** 1 MiB limit and schema v1

Charter checks at `intake-check`: schema-valid, `prompt_sha256 == sha256(prompt.md)`, 1–12 asks,
each with ≥1 acceptance criterion and ≥1 usable-bar entry. On success (not `--dry-run`) write
`outputs/overseer/charter.lock.json` and append the chained event `charter_locked`. Write
`check-result.json` (`{ok, violations:[{rule, message, path}]}`) on every non-dry run. Print one
stderr line per violation, prefixed with the `OV-` rule id. Exit 2 on violations.

- Inputs: manifest, briefs, charter, config, digest (ckpt), `state.json`
- Outputs: exit code, `check-result.json`, `charter.lock.json` + ledger event (intake)

## Acceptance Criteria
1. For each rule OV-R1…R10 and R15 there is at least one passing fixture and one failing fixture.
   The failing run exits 2 and stderr contains that exact rule id (parametrized test).
2. Specific cases:
   - a trailing comma gives R1
   - a duplicate id within the manifest gives R3
   - an id that already exists in `state.json` gives R3
   - a dangling `depends_on` gives R8
   - a depends_on on a previous wave's unit gives R8
   - a unit with `instruction` pointing at `outputs/waves/w01/w01-01-x.md` gives R6
   - a unit carrying `post_hook` gives R9
   - `model` on a unit gives R9
   - `{"tasks": []}` gives R10
   - a manifest with both a next ckpt and the tail gives R10
   - `final-push` present when `final_push` is false gives R10
   - a `ck-*` missing a unit breadcrumb input gives R10
   - an 1.5 MiB manifest gives R15
3. Intake: a wrong `prompt_sha256` fails. A valid run writes the lock and the event, and a second
   run on unchanged inputs doesn't duplicate the event. `--dry-run` writes nothing.
4. The shapes the checker accepts are exactly the contract's example shapes. A test feeds the JSON
   blocks extracted from the rendered contract (T-ltBLUY) and they pass.
5. Checker-accepted entries always construct valid `TaskSpec`s, and every referenced hook name
   exists in the rendered `workflow.json` (a property-style test over the fixtures).
6. ≥ 90% line coverage for the checker section. `ruff`/`mypy` are clean. A `reviewer`-agent review
   is recorded in STATUS.md.

## Risks
- Over-strict rules could reject legitimate agent output. Mitigation: the rejection message names
  the exact fix; T-23yMMB measures the rejection rate.

## Dependencies
- T-ABDjSj (IO helpers, state loader, ledger append), T-ltBLUY (contract shapes and rule ids).

## Pseudocode / Algorithm
```text
See design §8.4 M3 ckpt_check (the structural steps). The rule engine is a list of (rule_id, fn)
over a parsed context. Every fn returns a list of Violation(rule, message, path) and never raises
on bad input.
```

## Schemas / Interface Notes
- CLI: `overseer_tool.py {intake-check|ckpt-check} … [--dry-run --task-id <id>]`
- `check-result.json`: `{schema:"ao.overseer.check/v1", task_id, ok, violations:[{rule, message, path}]}`

## Handoff Boundary
- Upstream: T-ABDjSj, T-ltBLUY.
- Downstream: T-tAKBBB (adds the semantic rules to the same engine), T-WruPiv, T-3FlD46.

## Artifacts
- Tests: `tests/test_overseer_tool_checker_structural.py`
