# TASK: T-lTW88y-security-and-review-gate

## Metadata
- Task ID: `T-lTW88y-security-and-review-gate`
- Epic ID: `E-Qp4Zm8-scheduled-run-governance`
- Owner: reviewer + dev-security agents
- Created: 2026-11-10
- Last Updated: 2026-11-10
- Status: Draft
- Estimate: 1.5 days (0.5 d early gate + 1 d final gate)

## Requirements Mapping
- Requirement IDs: NFR-1, NFR-2, NFR-6 (see `../EPIC.md`)

## Description
This task has two gates.
1. **Early gate (before any Sprint 1 code merges):** `reviewer` and `dev-security` review the HLD + ADR-0025 + task
   ACs in parallel. They target the three places most likely to hide a blocking defect:
   - the reservation/flock/at-most-once interplay (HLD §8.2, §10.3);
   - the sticky re-armed cost gate vs breaker latching (HLD §9.1–9.2);
   - the fail-closed argv check (HLD §8.4).
   Findings go into the HLD, a new "Early-gate corrections" section in ADR-0025, and the affected tasks' ACs.
2. **Final gate (after `T-88wSLC`):**
   - a `dev-security` audit of the money-spending surfaces: the resume POST, the `ao schedule resume` CLI, the prompt
     path reads, `state.json` tamper resistance and the `run_args` exclusion;
   - a `reviewer` architecture pass on the scoped `engine.py` / `schedule_engine.py` diffs;
   - NFR-6 re-verification.

Files you own: the two gates' write-ups in this task's STATUS. HLD/ADR edits are limited to the corrections sections.
Bug fixes go back to the owning task.

Do NOT touch: `src/`, `tests/`, `ui/` (file findings instead).

## Acceptance Criteria
1. The early gate is recorded with findings classified Critical/Warning/Info. Every Critical is folded into the HLD/ADR
   and task ACs before the affected task starts. Counts match across this STATUS, the epic STATUS and ADR-0025.
2. Security checklist, each item with evidence (test name or code reference):
   - (a) no route or CLI path can pass an absolute cap, workflow path, or argv;
   - (b) `add_usd` is bounded server-side;
   - (c) `prompt_file` resolves through the workspace-root guard (`..`/absolute/symlink-out rejected) and the read is
     ≤ `MAX_PROMPT_BYTES`;
   - (d) the high-water mark lives outside the workspace and cannot decrease;
   - (e) `run_args.cost_cap_usd` is rejected;
   - (f) no event or log contains prompt text (sha prefix only);
   - (g) with auth configured, new routes are `AUTHENTICATED`, and the POST has the Origin/Content-Type gate;
   - (h) governance errors fail closed.
3. Reviewer: the `git diff` of `engine.py` touches only the four sites named in `T-et7Edf`, and the `evaluate()`
   order change is the single block named in `T-PpE5lf`. Any extra hunk is justified or reverted.
4. NFR-6: full `pytest -q` delta vs epic baseline, `ruff check` + `ruff format --check` clean, `mypy src` no regression,
   coverage ≥ 80 % on each new module. All of these are reported with actual numbers.

## Risks
- Skipping the early gate. E-GIytcL's and E-Sc9Rt4's histories show it catches blocking defects that author review
  misses.

## Dependencies
- Early gate: none (design package only). Final gate: `T-88wSLC`.
- Downstream: `T-Hq3vXd`.

## Pseudocode / Algorithm
```text
N/A (review task)
```

## Schemas / Interface Notes
- N/A

## Handoff Boundary
- Upstream: the design package (early gate) and all implementation tasks (final gate).
- Downstream: the docs refresh records any deviations accepted here.

## Artifacts
- Docs/comments: `meta/tickets/E-Qp4Zm8-scheduled-run-governance/T-lTW88y-security-and-review-gate/`
- Large outputs: N/A
