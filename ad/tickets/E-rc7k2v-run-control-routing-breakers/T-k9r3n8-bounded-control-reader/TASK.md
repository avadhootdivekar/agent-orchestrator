# TASK: T-k9r3n8-bounded-control-reader

## Metadata
- Task ID: `T-k9r3n8-bounded-control-reader`
- Epic ID: `E-rc7k2v-run-control-routing-breakers`
- Owner: developer
- Created: 2026-07-09
- Last Updated: 2026-07-09
- Status: Done
- Estimate: `~1.5 days`

## Requirements Mapping
- FR-B1, FR-CB3, NFR-1. LLD §3.

## Description
Generalise the loop-gate verdict reader into ONE audited bounded-JSON control reader used by loop gates, routers,
and verdict breakers, so NFR-1 has a single content-read surface. Add `read_control(store, path) -> dict` with a
`MAX_CONTROL_FILE_BYTES` size guard (via `store.size()` BEFORE reading — closes today's unbounded read gap), plus
typed helpers `read_bool_field` and `read_routes`. Introduce `ControlFileError` base; make existing `GateError`
subclass it; keep `read_gate` as a thin alias (`return read_bool_field(...)`) so loop code and existing
`except GateError` handlers are unchanged.

## Acceptance Criteria
1. `read_control` raises `ControlFileError` on: missing file, size > `MAX_CONTROL_FILE_BYTES`, invalid JSON,
   non-object root — each with a message naming the path.
2. `read_bool_field` returns the bool / raises on missing-field or non-bool; `read_routes` returns a list of
   non-empty strings / raises otherwise.
3. `read_gate(store, path, field="continue")` still returns a bool and behaves exactly as before (existing loop
   tests pass unchanged, incl. memory `loop-iterate-event-only-on-continue`).
4. `GateError` is a subclass of `ControlFileError`; engine's existing `except GateError` for loops still catches.
5. A control file of `MAX_CONTROL_FILE_BYTES + 1` bytes is rejected without being fully read (assert via size
   check, not content).
6. `uv run ruff/mypy/pytest` clean.

## Risks
- Backward-compat of `read_gate` signature — keep it identical; only its body delegates.
- `store.size()` returns 0 on missing/invalid path — order the checks so "missing" is reported before "too big".

## Dependencies
- Upstream: T-b7q2m4 (for `MAX_CONTROL_FILE_BYTES`, error types placement).
- Downstream: T-m2h5t7 (router uses `read_routes`), T-q5n7k2 (verdict breaker uses `read_bool_field`).

## Pseudocode / Algorithm
See LLD §3.2.

## Schemas / Interface Notes
- Interface: `read_control`, `read_bool_field`, `read_routes` in `artifacts.py`; `ControlFileError` in `errors.py`.
- Artifacts: reads bounded control JSON only; never payloads (NFR-1).
- Triggers/events: N/A.

## Handoff Boundary
- Upstream: LLD §3, T-b7q2m4 types.
- Downstream: routing + verdict-breaker consumers; do not add router/breaker logic here.

## Artifacts
- Docs/comments: `ad/tickets/E-rc7k2v-run-control-routing-breakers/T-k9r3n8-bounded-control-reader/`
