# STATUS

- ID: `E-Vt6Lp2-template-cost-hygiene`
- Updated At: 2026-09-21
- State: In Progress
- Owner: dev-epic

## This update
- Epic scaffolded: EPIC.md + STATUS.md + 3 task folders. Requirements categorized MVP/Non-MVP/
  Stretch with traceability. Investigated `templates/__init__.py` rendering mechanics
  (`assets:` entries are workspace-root-relative and materialized once per workspace with
  `keep_existing` semantics — exactly the seed/merge mechanism D1 needs), `models.py`'s
  `AgentSpec.command_template`/`extra_args` (confirmed `--autocompact` needs zero engine code
  changes, it is opaque argv), and `spec.py`'s V1-V13 rules (confirmed they are a cohesive
  isolation/integration-specific module, informing the decision to defer the `ao validate`
  warning to Non-MVP).

## Evidence
- `git log b0cb467..HEAD` confirms Epics A/B/C already landed on this branch.
- No `--autocompact` usage anywhere in `src/agent_orchestrator/` or any `agents*.json` in this
  repo (`grep -rn autocompact`), confirming the epic's premise.

## Risks / Blockers
- None yet — see EPIC.md Risks for disclosed estimation risk on the `--autocompact` default.

## Next actions
1. Early-gate architect + reviewer pass on the concrete D1 mechanism proposal (asset-based seed
   file vs. validator-warning-only) before implementing.
2. Implement D1 (template asset + README + tests), D2 (skill section), D3 (design doc + e2e
   evidence + full suite run).
3. Late-gate: real `ao new` instantiation into a scratch workspace, full `pytest` regression run,
   ruff/mypy on touched files.
