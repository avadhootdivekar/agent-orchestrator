# TASK: T-Hn4Rq8-agents-recommended-asset

## Metadata
- Task ID: `T-Hn4Rq8-agents-recommended-asset`
- Epic ID: `E-Vt6Lp2-template-cost-hygiene`
- Owner: dev-epic (implemented directly; early-gate reviewed by `architect`/`reviewer`)
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: In Progress
- Estimate: < 1 day

## Requirements Mapping
- Requirement IDs: FR-D1-1, FR-D1-2, NFR-D1-1, NFR-D1-2, NFR-D1-3

## Description
Add a new `assets:` entry to `routed-runner/template.yaml`:
`agents.recommended.json.tmpl` (template-dir-relative source) → `agents.recommended.json`
(workspace-root-relative target, `keep_existing: true`). This is materialized once per workspace
(confirmed via `templates/__init__.py`'s `instantiate()`: `assets:` targets are resolved against
`workspace_root`, not `instance_dir`, and `keep_existing` skips a target that already exists) —
so the FIRST `ao new routed-runner ...` in a fresh workspace seeds the file, and every later
`ao new` call (same or different instance) leaves an already-materialized/edited copy alone.

The seed file is a real, parseable, partial `agents.json`-shaped JSON document (not prose) naming
`command_template` (with `--autocompact <threshold>`) for the subset of `required_agents` that do
long, multi-turn agentic work: `architect`, `architect-opus`, `developer`, `full-tester`,
`manager`, `market-surveyor`, `reviewer`, `reviewer-opus`, `tester` (9 of 11). Excluded:
`git-operator`, `merge-resolver` — short-lived, few-turn mechanical dispatches per the epic
prompt's own hint and this task's judgment (git plumbing / a bounded conflict-resolution pass,
not open-ended agentic exploration that accumulates the kind of context growth the real 232-turn
example shows).

Also add a new `README.md` section documenting the recommended `command_template` snippet, why
these 9 roles and not the other 2, and how a workspace adopts it (copy fields from
`agents.recommended.json` into its own `agents.json`; the file is never wired into `ao run`
itself).

## Acceptance Criteria
1. `template.yaml`'s `assets:` list has 2 entries (was 1); the new entry's `source` is
   `agents.recommended.json.tmpl`, `target` is `agents.recommended.json`, `keep_existing: true`.
2. `agents.recommended.json.tmpl` is valid JSON containing a `command_template` array with
   `--autocompact` and a threshold for exactly the 9 named roles (not the other 2), each entry
   plausibly `AgentSpec`-shaped (`executor`, `command_template` at minimum).
3. `README.md` documents this under a clearly-named section, with the chosen `--autocompact`
   value and a one-line justification (full justification lives in the design doc, linked).
4. `tests/test_builtin_routed_runner_assets.py` extended: manifest asset-count/shape assertions
   updated for 2 assets, a new assertion the seed file parses as JSON and covers exactly the 9
   roles with `--autocompact` present, a README-section-presence test mirroring
   `test_readme_documents_parallel_isolation_section`.
5. `tests/test_e2e_builtin_routed_runner.py` (or `test_e2e_cli_templates.py`, whichever already
   drives a real `ao new` through `CliRunner`) extended with a real e2e case: `ao new` renders
   `agents.recommended.json` into the scratch workspace root; a second `ao new` (new instance, or
   `--force`/no-op re-run) after hand-editing that file does NOT clobber the edit
   (`keep_existing` proven end-to-end, not just at the unit level).
6. No regression in any pre-existing test in the 4 named suites (`test_templates.py`,
   `test_e2e_builtin_routed_runner.py`, `test_builtin_routed_runner_assets.py`,
   `test_e2e_cli_templates.py`) — full pytest run, pass/fail counts reported.
7. `ruff check`/`ruff format --check`/`mypy` clean on any touched `.py` files.

## Risks
- The `--autocompact` numeric default is an estimate from aggregate, not per-turn, real data —
  disclosed in the design doc, not silently presented as measured-optimal.
- Role-inclusion judgment (9 vs. 11) is a call, not a hard rule from the epic prompt — recorded
  explicitly with reasoning so it's reviewable/reversible.

## Dependencies
- None upstream within this epic. Downstream: `T-Zb8Fx3-design-doc-and-e2e-evidence` (design doc
  cites this task's exact mechanism + threshold; late-gate e2e evidence exercises this asset).

## Pseudocode / Algorithm
```text
template.yaml:
  assets:
    - source: instructions/
      target: workflows/routed-runner/instructions/
      keep_existing: true
    - source: agents.recommended.json.tmpl
      target: agents.recommended.json
      keep_existing: true
```

## Schemas / Interface Notes
- Interface / API: none (template manifest + static JSON asset only, no Python code path
  changes — `_materialize_asset`/`_materialize_asset_file` in `templates/__init__.py` already
  handle an arbitrary new `assets:` entry generically).
- Spec / data schema (JSON/YAML): `agents.recommended.json` is a partial, hand-authored
  `agents.json`-shaped document — not validated against `specs/agents.schema.json` at
  instantiate time (it's a seed a human merges by hand, not a live config `ao` loads), stated
  explicitly in the file's own top-level comment-equivalent (a `"_note"` string field, since
  JSON has no comments).
- Triggers / events: N/A.
- Artifacts (inputs/outputs by path):
  `src/agent_orchestrator/templates/builtin/routed-runner/agents.recommended.json.tmpl` (new),
  `<workspace_root>/agents.recommended.json` (materialized per workspace).

## Handoff Boundary
- Upstream: none.
- Downstream: `T-Zb8Fx3-design-doc-and-e2e-evidence` reads this task's exact filenames/values.

## Artifacts
- Docs/comments: `meta/tickets/E-Vt6Lp2-template-cost-hygiene/T-Hn4Rq8-agents-recommended-asset/`
- Large outputs: none.
