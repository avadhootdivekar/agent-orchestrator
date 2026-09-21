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

The seed file is a real, parseable, partial `agents.json`-shaped JSON document (not prose),
mirroring `specs/examples/agents.json`'s exact top-level shape (`{"version": "1.0", "agents":
{...}}`, plus a small metadata block of underscore-prefixed keys — `_note`,
`_autocompact_default`, `_seeded_from_template_version` — documenting that this is a merge
reference, not a live config; per early-gate reviewer finding, these extra top-level keys mean
the file is deliberately NOT schema-valid to `cp` wholesale over a real `agents.json` — a
workspace must consciously merge fields, which is disclosed and intentional, not silently
broken). Each of 9 of the 11 `required_agents` gets `"extra_args": ["--autocompact",
"<threshold>"]` (NOT `command_template` — early-gate reviewer correctness finding:
`command_template` fully overrides the base argv `["claude","-p","{prompt}"]`, so recommending a
full override array would force a workspace to discard its own tuning on merge; `extra_args` is
the field the engine unconditionally appends after `command_template`, exactly the additive
mechanism this calls for): `architect`, `architect-opus`, `developer`, `full-tester`, `manager`,
`market-surveyor`, `reviewer`, `reviewer-opus`, `tester`. Excluded: `git-operator` (short-lived,
few-turn git plumbing), `merge-resolver` (short-lived AND, per early-gate architect finding, a
security-sensitive T2 dispatch over unreviewed content — README's existing S-2 note — where
compaction's fidelity risk mid-conflict-resolution is a worse trade than the marginal benefit).

Also add a new `README.md` section documenting the recommended `command_template` snippet, why
these 9 roles and not the other 2, and how a workspace adopts it (copy fields from
`agents.recommended.json` into its own `agents.json`; the file is never wired into `ao run`
itself).

## Acceptance Criteria
1. `template.yaml`'s `assets:` list has 2 entries (was 1); the new entry's `source` is
   `agents.recommended.json.tmpl`, `target` is `agents.recommended.json`, `keep_existing: true`.
2. `agents.recommended.json.tmpl` is valid JSON, top-level shape `{"version": "1.0", "agents":
   {...}}` (plus disclosed `_`-prefixed metadata keys) matching `specs/examples/agents.json`'s
   convention, with `extra_args: ["--autocompact", "<threshold>"]` for exactly the 9 named roles
   (not the other 2), each entry `AgentSpec`-shaped (`executor` + `extra_args` at minimum, both
   valid per `specs/agents.schema.json`'s per-agent `additionalProperties: false` shape).
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

agents.recommended.json.tmpl (shape):
{
  "_note": "...merge reference, not live config, written once (keep_existing)...",
  "_autocompact_default": "200000",
  "_seeded_from_template_version": "1.0",
  "version": "1.0",
  "agents": {
    "architect": {"executor": "claude_cli", "extra_args": ["--autocompact", "200000"]},
    ... (9 roles total)
  }
}
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
