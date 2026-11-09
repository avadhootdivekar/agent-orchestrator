# TASK: T-pMpo4O-schema-driven-launch-forms

## Metadata
- Task ID: `T-pMpo4O-schema-driven-launch-forms`
- Epic ID: `E-aYG5vn-dashboard-run-limits-and-effort-naming`
- Owner: `unassigned`
- Created: `2026-11-09`
- Last Updated: `2026-11-09`
- Status: `Done`
- Estimate: `< 3 days`
- Phase: UI
- Agent: developer (UI)

## Requirements Mapping
- Requirement IDs: E-aYG5vn FR-5 (UI labels), FR-7; E-Sn7tL2 FR-22 (settings form)

## Description
Replace hand-written option fields in `NewRun.tsx` (and the resume options dialog) with a form generated from
`GET /api/schema/run-options`, grouped: **Model & reasoning** (Model, Thinking effort low…max, Autocompact),
**Turns & retries** (Turn budget with turn counts, Max turns, Max attempts), **Concurrency & budget** (Max parallel,
Max parallel ceiling, Token budget), **Quota** (max wait, poll interval), **Recovery** (self-heal), **Sentinel**
(from SentinelSpec once T-Sn2Set lands). `TemplateLaunch.tsx` gets the same options section and sends `options`
(fixes `options: {}`). Labels exactly per FR-5. Unknown/old backend (no schema endpoint) → fall back to current fields.

## Acceptance Criteria
1. UI tests: every registry option with `ui` metadata renders with correct control type and validation.
2. Template launch forwards options (API test + UI test).
3. Old-backend fallback test.
4. No hand-written per-option field remains in NewRun/TemplateLaunch.

## Risks
- Form length — groups collapsed by default except Model & reasoning.

## Dependencies
- T-f0d7gm
- T-Sn2Set (sentinel group only; may land later)

## Pseudocode / Algorithm
```text
N/A (React form generator over schema)
```

## Schemas / Interface Notes
- consumes `/api/schema/run-options`

## Handoff Boundary
- Upstream: T-zP28k9, T-f0d7gm
- Downstream: T-Sn5Ui1 reuses the generator; T-mQs7CQ

## Artifacts
- Docs/comments: `meta/tickets/E-aYG5vn-dashboard-run-limits-and-effort-naming/T-pMpo4O-schema-driven-launch-forms/`
- Large outputs: `N/A`

## Standard gates (every task)
- `pytest -q`, `ruff check . && ruff format --check .`, `mypy .` green; UI changes also `npm test` + `npm run build` in `ui/`.
- New behaviour off / absent settings ⇒ byte-identical behaviour (golden tests stay green).
- Work only in `./agent-orchestrator` on the epic branch; test against temp workspaces; never `install.sh` / restart `ao.service`.
- Update this TASK.md + STATUS.md and the epic STATUS.md rollup together.

## Implementation notes (2026-11-09)
- `ui/src/components/RunOptionsForm.tsx` renders every `ui` option of `GET /api/schema/run-options` (control from
  `type`/`choices`, label/group from `ui`, `choice_labels` -> "high (60 turns)", `providers` -> "applies to: ...").
  Used by `NewRun`, `TemplateLaunch` (now sends `options`) and the `RunDetail` Resume panel (optional overrides).
- Registry groups renamed to the FR-5 groups (Model & reasoning / Turns & retries / Concurrency & budget / Quota /
  Recovery / Sentinel); sentinel leaves that have a CLI flag are now dashboard-forwardable (allow-list derived).
- Degradation: schema 404/failure -> `FALLBACK_SCHEMA` (legacy 5 fields, same renderer); unknown type -> text input;
  unknown group appended; `ui: null` hidden; legacy `effort` still in the schema and accepted.
- Tests: `ui/src/test/run-options-form.test.tsx` (9), `tests/test_run_options.py` (groups/allow-list/snapshot).
