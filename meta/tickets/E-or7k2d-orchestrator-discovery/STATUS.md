# STATUS

- ID: `E-or7k2d-orchestrator-discovery`
- Updated At: `2026-06-16`
- State: `In Progress` (discovery done; blocked on user decision gate)
- Owner: `Avadhoot Divekar`

## This update
- Consolidated requirements (FR-1..7, NFR-1..4); identified NFR-1 (context hygiene) + FR-7 (multi-repo) as the only real differentiators.
- Completed market survey across 3 tiers (general orchestrators, in-process agent frameworks, coding-agent orchestrators) + pricing.
- Concluded: not viable as a standalone business; achievable as an internal capability in < 1 week, < $40/mo.

## Evidence
- Full analysis: `output/E-or7k2d-orchestrator-discovery/market-survey.md`
- Sources cited inline (Kestra 1.0, Claude Code Agent Teams/Dynamic Workflows, pricing pages, framework comparisons).

## Risks / Blockers
- BLOCKED on user decision gate (Option 1–4) before any design/LLD/task breakdown.

## Next actions
1. Get user's direction choice (Claude-native+spec / Kestra+adapter / full build / other).
2. On consent: expand epic → `workflow.json` schema + ADR/HLD/LLD + task breakdown.

## Comments
- By: Claude · Role: architect · Date: 2026-06-16 · Comment: Discovery complete per `prompt.md`. Deliberately did **not** start design/task breakup — awaiting consent at the decision gate. Recommendation: hybrid (Claude-native engine + optional Kestra), custom code limited to a thin spec/adapter.
