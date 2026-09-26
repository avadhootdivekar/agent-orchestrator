# TASK: T-3FlD46-security-review-hardening

## Metadata
- Task ID: `T-3FlD46-security-review-hardening`
- Epic ID: `E-YAAGhk-overseer-runner-template`
- Owner: dev-security
- Created: 2026-09-26
- Last Updated: 2026-09-26
- Status: Draft
- Estimate: 1 day (8 h). Starts as soon as T-HPJcc6 and T-tAKBBB merge.

## Requirements Mapping
- Requirement IDs: NFR-1, NFR-4, NFR-6, NFR-11 (residual), FR-18
- Design: `docs-md/overseer-runner-hld.md` §8.3 (integrity model), §23.3 row 5; ADR-0016 D9

## Description
Do a code-level security review of the implemented tool, hooks, and checkers, against the
design-level findings already recorded (§23.3 row 5). Verify that each mitigation is actually
implemented:

- the pinned `instruction` (OV-R6)
- the hold ledger events (INT-2/INT-4)
- the hash chain (INT-3)
- override coupling to `breaker_overrides`
- `changed_paths` confinement (symlink, `..`, absolute, unknown `repo_id`)
- the size caps
- git subprocess bounds (argv list, no shell, timeout)
- no `eval`/`exec`/`pickle`/`shell=True` anywhere in the tool
- hook argv built only from render-time params

Fix any HIGH/CRITICAL finding in this task. Anything larger is filed as a follow-up ticket and
linked from STATUS.md.

## Acceptance Criteria
1. A findings list (severity, location, status) is recorded in STATUS.md. Each design-level finding
   from §23.3 row 5 is marked verified-implemented, or fixed here.
2. Tests exist for:
   - a symlink escape
   - a `repo_id` spoof
   - `..` and absolute paths
   - a 1.5 MiB breadcrumb (rejected, bounded read)
   - a chain rewrite (INT-3)
   - deleting `hold-request.json` after a hold decision (INT-2)
   - a forged request (INT-4)
   - a forged override without extension (refused)
   - an `instruction` redirection (OV-R6)
   Add any that are missing.
3. `grep` evidence in STATUS.md that the tool contains no `shell=True`, `eval(`, `exec(`, `pickle`,
   or `os.system`.
4. `pip-audit` is not applicable (stdlib only), and STATUS.md states that.
5. No open CRITICAL/HIGH findings at close.

## Risks
- Residual (accepted, NFR-X11): tamper-evident, not tamper-proof.

## Dependencies
- T-ABDjSj, T-C6uQJW, T-HPJcc6, T-tAKBBB.

## Pseudocode / Algorithm
```text
N/A — review checklist per Description.
```

## Schemas / Interface Notes
- N/A

## Handoff Boundary
- Upstream: the checker and tool tasks.
- Downstream: T-gbccdr (the security notes go into the README/HLD).

## Artifacts
- Findings are in STATUS.md. Large outputs, if any, go to `output/E-YAAGhk-overseer-runner-template/security/`.
