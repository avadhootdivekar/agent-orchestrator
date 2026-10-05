# TASK: T-bdQZW4-cache-docs-refresh

## Metadata
- Task ID: `T-bdQZW4-cache-docs-refresh`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev B), with `architect` sign-off
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (as-built docs reconciliation delivered)
- Status: `Done` (all ten steps done and grep-verified; AC-7 architect sign-off APPROVE-WITH-NOTES, 2026-10-05)
- Estimate: `8 focus hours (1 day)` · last task of the epic

## Requirements Mapping
- Requirement IDs: the mandatory post-implementation reconciliation (architect Phase 6)
- HLD: §25

## Description
Reconcile the docs with the **implemented** behaviour, including every deviation from the design.
Mark this task Done only after each statement has been checked against the code. Steps (HLD §25):

1. **HLD:** add "§0 Implementation outcome and deviations" (what shipped; resolved OQs; G0 status
   "protocol shipped, execution post-merge"; each deviation with its reason; follow-ups);
   re-verify GV-1 and its component digests.
2. **ADR-0019:** mark it Accepted, or add an addendum.
3. **`docs-md/hld-agent-orchestrator.md`:** the cache component (§2), the NFR-1 carve-out (§4),
   the lookup step (§5).
4. **`.claude/skills/workflow-authoring/SKILL.md`:** a "Result cache" section (HLD §17 outline):
   double opt-in and the `DEFAULT_TASK_CACHE_POLICY` flip point, when to opt in, guards,
   `skip_if_outputs_exist`, re-rolling with `ao cache rm`, pinned models, the residuals, and the
   `cache: true` **example workflow** from HLD §17.
5. **`README.md`:** "Result cache (opt-in)" (flags, env, config, shadow mode, `ao cache`
   commands, "not prompt caching"), the **release-note line** from HLD §16 (there is no changelog
   file), and a link to `docs-md/result-cache-g0-protocol.md`.
6. **`meta/ROADMAP.md`:** "Just landed"; the non-MVP list (HLD §2.3, including the Rev 3
   deferrals) in §3.6; R-15 under §3.4; the G0 follow-up.
7. **`docs-md/usage-analytics.md`** and **`docs-md/benchmarking-framework-hld.md`:** the
   `result_cache` usage object, how current hits are excluded at both usage sites,
   `settle_reason: cached`, and that the bench always runs with `--no-cache`.
8. **Cross-links:** `docs-md/cost-caching-optimization-hld.md` (terminology box and pointer);
   `docs-md/token-budgeting-hld.md` (a hit charges no budget; a stale previous-cycle charge is
   reversed through the shared helper).
9. **Pointer comments** next to `models.EFFORT_MAX_TURNS` and in `executors/claude_cli.py`: a
   behaviour-relevant change invisible in argv or the fingerprint must bump `KEY_SCHEMA_VERSION`.
10. **Learnings** in `meta/learnings.md` / `meta/learning-compact.md`, only if genuinely new.

## File scope (exclusive)
- `docs-md/**` **except** `docs-md/result-cache-g0-protocol.md` (owned by T-nPMuz4),
  `README.md`, `meta/ROADMAP.md`, `.claude/skills/workflow-authoring/SKILL.md`
- Pointer comments only in `src/agent_orchestrator/models.py` and
  `src/agent_orchestrator/executors/claude_cli.py` (no behaviour change)

## Inputs / Outputs
- **Inputs:** the merged implementation, the gate reports (G1a, G1b, G2) and the G0 protocol.
- **Outputs:** reconciled docs.

## Acceptance Criteria
1. Every statement about flags, env values, config keys, reasons, events, exit codes and JSON
   schema ids in the updated docs matches the code; each is spot-checked with `grep` against
   `cache/constants.py` and `cache/cli.py`, and the checks are listed in `STATUS.md`.
2. The HLD has "§0 Implementation outcome and deviations", each deviation with a reason, and the
   G0 status worded as "protocol shipped; execution post-merge".
3. ADR-0019 is Accepted, or has an addendum describing the deviations.
4. The authoring skill has the Result cache section with the double opt-in, the flip point, the
   "deleting outputs replays" warning (EC-20) and the example workflow.
5. `README.md` has the section and the release-note line; nothing claims `refresh`,
   `rm --run/--task` or `verify --repair` exist.
6. The pointer comments exist; the full suite still passes (comment-only changes avoid the
   `open(` / `.read(` patterns).
7. The architect signs off in this task's `STATUS.md`.

## Test requirements
- The full suite is re-run after the pointer comments: `pytest -q`, `ruff`, `mypy`.

## Risks
- **Docs describe the design rather than the implementation.** Mitigation: AC-1.

## Dependencies
- G2 PASS (T-fXWbqg) **and** T-nPMuz4 (the G0 protocol).

## Pseudocode / Algorithm
```text
for doc in the §25 list: read code -> update doc -> grep-verify each changed claim -> note in STATUS
```

## Schemas / Interface Notes
- N/A (docs only).

## Handoff Boundary
- **Upstream:** T-fXWbqg (G2), T-nPMuz4.
- **Downstream:** none; this closes the epic. (G0 execution is a post-merge follow-up.)

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-bdQZW4-cache-docs-refresh/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Mandatory post-implementation
  docs reconciliation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2: G0 outcome, pointer
  comments, budgeting cross-link, authoring-guide items.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 (early-gate A1e, D): now
  depends on T-nPMuz4; adds the example workflow, the release-note line and the flip-point
  sentence; drops `refresh`/`rm --run`/`--repair`; the G0 protocol doc is owned by T-nPMuz4.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Implemented (commits `643ad11` HLD + ADR, `eaead55` other docs and learnings, `5748316` pointer comments, then the ticket-docs commit). All ten HLD 25 steps are done and every changed statement was checked against the code (AC-1: the commands are listed in `STATUS.md` Evidence). AC-2 to AC-6 PASS; **AC-7 (architect sign-off) is pending**, so the state is `In Review`, not `Done`; `STATUS.md`, `HANDOFF.md` and the epic `EPIC.md` / `STATUS.md` rollup agree. The HLD new section 0 corrects the Rev 3 design text where the code or a gate remediation superseded it (DV-1..DV-24).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: AC-7 sign-off: APPROVE-WITH-NOTES after an independent 37-claim re-check against the code; seven small doc corrections and the manager-authorized G0 protocol Step 9 applied (details in `STATUS.md` "Architect sign-off"). State -> `Done`; `STATUS.md`, `HANDOFF.md` and the epic `EPIC.md` / `STATUS.md` agree.
