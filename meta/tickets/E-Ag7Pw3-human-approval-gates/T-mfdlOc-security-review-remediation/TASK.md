# TASK: T-mfdlOc-security-review-remediation

## Metadata
- Task ID: `T-mfdlOc-security-review-remediation`
- Epic ID: `E-Ag7Pw3-human-approval-gates`
- Owner: dev-security (review) + developer (fixes)
- Created: 2026-10-04
- Last Updated: 2026-10-05
- Status: Draft
- Estimate: 2 days (1 day review, 1 day remediation buffer)

## Requirements Mapping
- Requirement IDs: NFR-4, NFR-7; FR-4, FR-5, FR-6, FR-9, FR-13 (security-relevant behaviour)
- Design: HLD §7 (threat model, TM-1..TM-34, RR-1..RR-15), §9.3–§9.11, §9.14; ADR-0020; HLD §0.5 (Gate 1
  findings that the as-built code must honour)

## Description
As-built security review of the implemented code (not the design) and remediation:

1. For every TM row of HLD §7.4, trace the mitigation in the code (file:line) and the test that proves it;
   flag any mitigation that exists only in a docstring.
2. Review specifically: key custody (perms, ownership, symlink, workspace refusal, `pwd` home, `link()`-only
   bootstrap, no leakage incl. tracebacks), canonicalization/MAC (domain separation, constant-time compare,
   the bounded strict parser), record verification order (no trust in fields before V4), **resume
   integrity** (no integrity decision derived from `state.json`/run-dir/spec content; `is_resume` from
   engine knowledge; evidence rules; gate-scoped policy checks; gate and gate-ancestor status
   normalisation; loop-clone re-derivation and the static base spec), path handling (review paths,
   decision file names, `decision_file` from `state.json`, `.refused` moves, `O_NOFOLLOW` everywhere, the
   file-browser denial of `approvals/`), the dashboard (principal reader, anonymous refusal, CSRF, XFO,
   bounded reads, error bodies leaking paths, refusal mapping), CLI (marker before any key access, TTY,
   identity, control-character stripping), marker coverage of all 10 spawn points (set last), the
   config-env denylist (6 keys), audit bounds, DoS bounds (per-poll re-hash budget).
3. Classify findings (CRITICAL/HIGH/MEDIUM/LOW) with the assumed attacker capability (same-uid agent /
   other local user / remote page). Fix every CRITICAL/HIGH (developer), with a regression test each.
4. Re-confirm the residual risks of HLD §7.6 (RR-1..RR-15) and the "closed at Gate 1" table are still
   accurate; update the HLD §23 risk table and §7.6 if not.

## Acceptance Criteria
1. A findings table in STATUS.md: id, severity, attacker capability, location, description, fix commit or
   deferral reason.
2. No open CRITICAL or HIGH finding; every fix has a regression test that fails before the fix.
3. Every TM row of HLD §7.4 (TM-1..TM-34) has a code trace (file:line) and a test reference recorded in
   STATUS.
4. Full suite still green vs baseline after fixes (numbers recorded).

## Risks
- Findings may require design changes (route back to the architect; update ADR-0020 addendum).

## Dependencies
- `T-pdLR96`.

## Pseudocode / Algorithm
```text
n/a (review)
```

## Schemas / Interface Notes
- n/a.

## Verification
```
WT=/usr/avadhoot/mounted/agent-orchestrator/.claude/worktrees/agent-a513b4d78ac3b20bb
PY=/usr/avadhoot/mounted/agent-orchestrator/.venv/bin/python
cd $WT && $PY -m pytest -o pythonpath=src -p no:cacheprovider -q
cd $WT && $PY -m ruff check . && $PY -m mypy src/agent_orchestrator
```

## Handoff Boundary
- Upstream: `T-pdLR96` evidence.
- Downstream: `T-wH71UE` (docs record the verified posture).

## Artifacts
- Findings in STATUS.md; any long report under `output/E-Ag7Pw3-human-approval-gates/`.
