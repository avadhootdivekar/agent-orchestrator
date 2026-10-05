# STATUS

- ID: `T-bdQZW4-cache-docs-refresh`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (Dev B), `architect` sign-off

## This update
- Rev 3 ticket: depends on G2 and T-nPMuz4; adds the example workflow, the release-note line and
  the flip-point sentence; deferred features dropped. Estimate unchanged (8 h).

## Evidence
- None yet (not started). Design evidence: HLD Rev 3 and ADR-0019 Rev 3.

## Risks / Blockers
- Depends on G2 PASS and T-nPMuz4.

## Next actions
1. Start after G2 PASS and T-nPMuz4.
2. Work through HLD §25 steps 1–10 with grep-verification, then get architect sign-off.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD §23.5). State stays `Draft`
  (last); this file, `TASK.md`, `HANDOFF.md` (when present) and the epic `STATUS.md` rollup
  agree.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: G1b carry-over for the docs refresh: (1) list sec S-3 (git `filter.<x>.clean` executed by the guard-3 probe; precondition: an in-.git write by a task agent) as a named residual in HLD 7.7 and the authoring guide, recommending --no-cache for untrusted repos; (2) document the agent-writable cache dir and the on-mode banner clause (sec S-5); (3) HLD 8.7.5 allow-list includes cache.cli (rev N-4); (4) residual rows for skip-worktree / assume-unchanged and partial-attempt baselines (sec N-7, N-9) and the orphaned restore temp files that directory hashing ignores (sec S-1). See `T-fXWbqg-cache-review-gates/STATUS.md` (G1b remediation).
