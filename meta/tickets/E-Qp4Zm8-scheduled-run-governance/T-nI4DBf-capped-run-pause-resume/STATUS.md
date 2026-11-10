# STATUS

- ID: `T-nI4DBf-capped-run-pause-resume`
- Updated At: 2026-11-11
- State: Partial (code in working tree, uncommitted, unreviewed; NOT done)
- Owner: TBD (assigned at sprint start)

## This update
- 2026-11-11 — By: developer (unit `w20-08-a2-docs-adr-sync`) · Role: developer · Date: 2026-11-11 · Comment: UNFINISHED. `service/capped_resume.py` (request/drain/sweep) exists in the working tree but is untracked at doc-sync time; carry-over unit w18-01 owns committing it. Not reviewed, not claimed done. HLD §26.2/§26.4.
- Ticket created by the `architect` agent as part of the E-Qp4Zm8 design package. Not started.
- Scope: Pause/abort lifecycle: operator resume request (`resume_capped` drop request), engine drain as single launch authority, TTL expiry sweep.

## Evidence
- None yet. No implementation has begun. Design basis:
  `docs-md/scheduled-run-governance-hld.md` and `docs-md/adr/ADR-0025-scheduled-run-governance-quotas-and-cost-caps.md`.

## Risks / Blockers
- Blocked until `T-PpE5lf` and E-Sc9Rt4 `T-Cl6Jn9` have merged.

## Next actions
1. Read the upstream tasks' **merged code** (including E-Sc9Rt4's), not only the HLD, before starting.
2. Implement against the acceptance criteria in `TASK.md`, in AC order; report the pytest delta, ruff/format, mypy, coverage.
3. Hand `request_capped_resume`/`ResumeRefused` to `T-rwj4nV` and `T-8knqVe`.
