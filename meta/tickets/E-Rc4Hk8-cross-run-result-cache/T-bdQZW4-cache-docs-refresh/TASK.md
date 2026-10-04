# TASK: T-bdQZW4-cache-docs-refresh

## Metadata
- Task ID: `T-bdQZW4-cache-docs-refresh`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev B), with `architect` sign-off
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 2)
- Status: `Draft`
- Estimate: `8 focus hours (1 day)` · Sprint 3 (last task of the epic)

## Requirements Mapping
- Requirement IDs: the mandatory post-implementation reconciliation (architect Phase 6)
- HLD: §25

## Description
Reconcile `docs-md/` and the other docs with the **implemented** behaviour, including every
deviation from the design. Mark this task Done only after each statement has been checked against
the code. Steps, following HLD §25:

1. **HLD.** Add "§0 Implementation outcome and deviations", following the `run-graph-canvas-hld.md`
   precedent. It covers:
   - what shipped;
   - the resolved OQs, including the G0 decision from T-nPMuz4;
   - each deviation, with its reason;
   - follow-ups.

   Re-verify GV-1 and its component digests against the code.
2. **ADR-0019.** Mark it Accepted, or add an addendum.
3. **`docs-md/hld-agent-orchestrator.md`.** Add the cache component (§2), the NFR-1 carve-out
   (§4) and the lookup step (§5).
4. **`.claude/skills/workflow-authoring/SKILL.md`.** Add a "Result cache" section, using the HLD
   §17 outline: double opt-in, when to opt in, guards, `skip_if_outputs_exist`, `refresh` / `rm`,
   and pinned models.
5. **`README.md`.** Add "Result cache (opt-in)": flags, env, config, modes and `ao cache`
   commands. State that it is not prompt caching.
6. **`meta/ROADMAP.md`.** Add a "Just landed" entry, list the non-MVP items in §3.6, and note
   R-15 under §3.4.
7. **`docs-md/usage-analytics.md` and `docs-md/benchmarking-framework-hld.md`.** Document the
   `result_cache_*` totals, `settle_reason: cached`, and that the bench always runs with
   `--no-cache`.
8. **Cross-links.** In `docs-md/cost-caching-optimization-hld.md`, add the terminology box and a
   pointer. In `docs-md/token-budgeting-hld.md`, state that a hit charges no budget and that stale
   charges are reversed.
9. **Pointer comments.** Next to `models.EFFORT_MAX_TURNS` and in `executors/claude_cli.py`, add:
   a behaviour-relevant change that is invisible in argv or the fingerprint must bump
   `KEY_SCHEMA_VERSION`.
10. **Learnings.** Add entries to `meta/learnings.md` and `meta/learning-compact.md` only if they
    are genuinely new.

## File scope (exclusive)
- `docs-md/**`, `README.md`, `meta/ROADMAP.md`, `.claude/skills/workflow-authoring/SKILL.md`
- Pointer comments only in `src/agent_orchestrator/models.py` and
  `src/agent_orchestrator/executors/claude_cli.py`. No behaviour change.

## Inputs / Outputs
- **Inputs:** the merged implementation, the gate reports (G1a, G1b, G2) and the G0 report.
- **Outputs:** reconciled docs.

## Acceptance Criteria
1. Every statement about flags, env values, config keys, reasons, events, exit codes and JSON
   schema ids in the updated docs matches the code. Spot-check each one with `grep` against
   `cache/constants.py` and `cache/cli.py`, and list the checks in `STATUS.md`.
2. The HLD has a "§0 Implementation outcome and deviations" section, and each deviation has a
   reason.
3. ADR-0019 is Accepted, or has an addendum describing deviations.
4. The authoring skill has the Result cache section, and it states the double opt-in and the
   "deleting outputs replays" warning (EC-20).
5. The pointer comments exist. The full suite still passes; comment-only changes cannot fail the
   static audits, because they avoid the `open(` / `.read(` patterns.
6. The architect signs off in this task's `STATUS.md`.

## Test requirements
- The full suite is re-run after the pointer comments: `pytest -q`, `ruff`, `mypy`.

## Risks
- **Docs describe the design rather than the implementation.** Mitigation: AC-1 spot-checks
  against code.

## Dependencies
- G2 PASS (T-fXWbqg); the G0 report (T-nPMuz4) when available.

## Pseudocode / Algorithm
```text
for doc in §25 list: read code -> update doc -> grep-verify each changed claim -> note in STATUS
```

## Schemas / Interface Notes
- N/A (docs only).

## Handoff Boundary
- **Upstream:** T-fXWbqg (G2), T-nPMuz4 (G0).
- **Downstream:** none. This closes the epic.

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-bdQZW4-cache-docs-refresh/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Mandatory post-implementation
  docs reconciliation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 adds:
  - the G0 outcome;
  - the pointer comments next to `EFFORT_MAX_TURNS` and in `claude_cli.py` (critic #4);
  - the token-budgeting cross-link (stale-charge reversal);
  - authoring-guide items for double opt-in, `refresh` / `rm` and EC-20.
