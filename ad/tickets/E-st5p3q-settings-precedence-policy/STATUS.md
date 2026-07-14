# STATUS

- ID: `E-st5p3q-settings-precedence-policy`
- Updated At: 2026-07-09
- State: Draft (Proposed / backlog — not yet scheduled)
- Owner: architect

## This update
Epic drafted from the accepted 2026-07-09 design session (ADR-0003). High-level scope, MVP/non-MVP split, requirements traceability to ADR-0003 sections, and a candidate task list only. No task tickets, no LLD — separate phase.

By: architect · Role: architect · Date: 2026-07-09 · Comment: Created from the user-ACCEPTED ADR-0003 (2026-07-09). Decisions treated as locked (single specificity chain; fill-in-not-override; `--force-model`/`--force-effort`; per-task model/effort in scope; chain governs model/effort/max_turns/max_attempts/timeout_seconds only). Motivating live defect cited: `cli.py` `_resolve_run_settings` clobbers every AgentSpec (`spec.model = eff_model`, lines ~412-425 run / ~568-581 resume). Provides per-task model/effort to epic `E-gd8m4x`.

## Evidence
- Design input: `docs-md/adr/ADR-0003-settings-precedence-policy.md` (Accepted).
- Live defect confirmed in `src/agent_orchestrator/cli.py` (`_resolve_run_settings`, run + resume paths) — read-only; no code changed in this ticketing pass.
- ADR-0003 `Related:` line updated to cross-link this epic.

## Risks / Blockers
- None blocking drafting. Behavior-change risk (fill-in vs clobber) is intended and mitigated by `--force-model` + `ao config explain` + release note.

## Next actions
1. LLD + task-breakdown phase (resolver → clobber-defect fix → force flags → workflow-defaults + per-task model/effort → empty-env rejection → freeze/resume → `config explain` → `validate --strict` → tests/docs).
2. Coordinate the new `RunState` field migration with epic `E-rc7k2v`.
3. Sequence per-task model/effort (FR-S5) so epic `E-gd8m4x` can consume it.
