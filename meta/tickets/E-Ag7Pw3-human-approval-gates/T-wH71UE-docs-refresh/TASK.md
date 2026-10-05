# TASK: T-wH71UE-docs-refresh

## Metadata
- Task ID: `T-wH71UE-docs-refresh`
- Epic ID: `E-Ag7Pw3-human-approval-gates`
- Owner: architect
- Created: 2026-10-04
- Last Updated: 2026-10-05
- Status: Draft
- Estimate: 1 day

## Requirements Mapping
- Requirement IDs: NFR-7 (honest documentation of the as-built posture), all FRs (documentation)
- Design: HLD §25

## Description
Post-implementation reconciliation of the docs with the **implemented** code (deviations recorded, never
silently rewritten):

1. `docs-md/human-approval-gates-hld.md`: status → "Implemented"; new **§27 Deviations from design**
   (DV-n with file:line citations of the shipped code).
2. `docs-md/adr/ADR-0020-human-approval-gates-signed-decisions.md`: status → "Accepted (implemented)";
   addendum for any changed decision.
3. `docs-md/hld-agent-orchestrator.md`: approval gates in the capability list; the narrow NFR-1 hashing
   exception and the single `lstat` of gate evidence; link the HLD.
4. `meta/ROADMAP.md`: "Recently delivered" row; §3.1 rows R-AG-1 (separate-user broker / WebAuthn user
   presence — the RR-1 fix), F-15 (approval policy outside the workspace — the RR-5 fix) and the follow-ups
   the parent session wants tracked (F-1…F-14).
5. `.claude/skills/workflow-authoring/SKILL.md`: short "Approval gates" section (HLD §17 rules 1–9: gate
   before the emitter, review the downstream instructions and inputs, keep gates out of optional routes,
   the gate-scoped edit rules for resumed runs, the isolation barrier, review defaults, the
   control-strength ladder, the reject → resume loop).
6. README / `specs/` example: the HLD §13 gated workflow, validated by `ao validate` in a test.
7. Grep-verify every CLI flag, exit code, refusal reason, route, status code, env var and event name in the
   docs against the code; record the check in STATUS.

## Acceptance Criteria
1. Every item 1–7 done; STATUS lists each changed file and the grep evidence for item 7.
2. No statement in the docs claims a stronger guarantee than the as-built security review (T-mfdlOc)
   confirmed; RR-1 and RR-5 are stated in the HLD, the ADR, the skill and the ROADMAP rows.
3. The example workflow passes `ao validate` in a test (new test file), with exactly the warnings HLD §13
   predicts.
4. Epic EPIC.md/STATUS.md rollups updated; a `reviewer`-agent review of the docs diff recorded.

## Risks
- Docs drifting from code (item 7 is the guard).

## Dependencies
- `T-mfdlOc`.

## Pseudocode / Algorithm
```text
n/a (documentation)
```

## Schemas / Interface Notes
- n/a.

## Verification
```
WT=/usr/avadhoot/mounted/agent-orchestrator/.claude/worktrees/agent-a513b4d78ac3b20bb
PY=/usr/avadhoot/mounted/agent-orchestrator/.venv/bin/python
cd $WT && $PY -m pytest -o pythonpath=src -p no:cacheprovider -q tests/approvals
```

## Handoff Boundary
- Upstream: `T-mfdlOc`.
- Downstream: manager (epic close).

## Artifacts
- Docs as listed.
