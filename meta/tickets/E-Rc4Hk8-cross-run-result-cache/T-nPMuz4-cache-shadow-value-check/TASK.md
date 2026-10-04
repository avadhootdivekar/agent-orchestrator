# TASK: T-nPMuz4-cache-shadow-value-check

## Metadata
- Task ID: `T-nPMuz4-cache-shadow-value-check`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `manager` (+ `tester`)
- Created: `2026-10-05`
- Last Updated: `2026-10-05`
- Status: `Draft`
- Estimate: `6 focus hours (0.75 day)` of analysis, plus a multi-day observation window in Sprint 3

## Requirements Mapping
- Requirement IDs: R-14 (strategic value risk), FR-16 (shadow mode exercised on real workflows)
- HLD: §22.5 (G0), §3 A-11, §23.2 OQ-6, §16 (rollout)
- ADR-0019: D26; ALT-8 (fallback)

## Description
**Purpose.** G0 is the business go/no-go for **recommending** result-cache mode `on`. It does not
block shipping. It answers dev-critic's strategic objection with data: given fail-closed keys,
committing tasks and per-epic output paths, how often would real workflows actually hit?

**Steps.**

1. **Install a beta build.** Install the epic branch as the **beta flavour** (`install.sh
   --flavor beta`, which gives `ao-beta`; see E-Bi5Nw8), so that the stable `ao` used by other
   work is untouched.
2. **Choose workflows.** Pick at least 3 of this repository's self-dev workflows. Add
   `ao-runner-finplan` workflows **only if the operator agrees**.
3. **Opt tasks in.** Opt in only tasks that are pure by inspection: `cache: true`. Record which
   tasks and why in the report.
4. **Observe.** Run them under `AO_CACHE=shadow` through the normal course of Sprint 3. Shadow
   never restores; it only hashes and stores, so it adds no model spend.
5. **Collect** from each run's `status.json` and `run.log`:
   - `would_hits`, `misses` by reason, and `ineligible` by reason;
   - store skips by reason (`repo_head_moved`, `repo_worktree_changed`,
     `key_changed_during_run`);
   - `avoidable_cost_usd`;
   - the miss `components` that differ most often.
6. **Write `output/E-Rc4Hk8-cross-run-result-cache/g0-shadow-report.md`.** It contains:
   - the method;
   - the workflows and tasks;
   - a metrics table;
   - the dominant miss components;
   - a recommendation using the HLD §22.5 decision rule.
7. **Record the decision** in the epic `STATUS.md`. The parent confirms the thresholds (OQ-6).

## File scope (exclusive)
- `output/E-Rc4Hk8-cross-run-result-cache/g0-shadow-report.md` (new)
- The epic `STATUS.md`: the G0 outcome line only.
- The opt-in edits to the observed workflow files live in the **observed workspaces**, not in
  this repository's source. Record each one in the report.

## Inputs / Outputs
- **Inputs:** T-o95l1M merged on the epic branch (shadow mode usable from the CLI); real workflow
  runs.
- **Outputs:** the G0 report and recommendation (`on` for named workflows, or "keep off and
  pursue ALT-8 / `include_repo_heads: false`").

## Acceptance Criteria
1. The report covers at least 3 workflows and at least 10 lookups of opted-in tasks in total. If
   fewer are available, say so explicitly.
2. The metrics table lists, per workflow: lookups, would_hits, would-hit rate, misses by reason,
   store skips by reason, and the avoidable $ estimate per week.
3. The top miss components are identified, for example `repo_heads` versus `inputs`.
4. The recommendation follows the §22.5 rule: a would-hit rate of at least 10%, **or** an
   avoidable spend of at least $5 per week per workflow, means recommend `on`. These thresholds
   are placeholders pending OQ-6.
5. The epic `STATUS.md` records the G0 outcome and the parent's decision, or "awaiting parent
   decision".
6. The stable `ao` install is untouched: `ao --version` is unchanged before and after.

## Test requirements
- N/A (analysis task). Report reproducibility: list the exact commands and run ids.

## Risks
- **No representative workload during Sprint 3.** Mitigation: use this repository's self-dev
  workflows (A-11), and say clearly in the report when the sample is too small.
- **Observer effect on finplan.** Mitigation: shadow mode never changes dispatch, and the beta
  flavour keeps stable untouched.

## Dependencies
- T-o95l1M (shadow mode end to end). Ideally T-JCOAsq Part 2's I-23 is green first.

## Pseudocode / Algorithm
```text
for wf in chosen_workflows:
    opt in pure tasks (cache: true) ; run with AO_CACHE=shadow (ao-beta) over the window
    for run in runs(wf): parse status.json result_cache blocks + run.log cache.* events
aggregate -> rates, reasons, avoidable $ -> apply §22.5 rule -> report + STATUS line
```

## Schemas / Interface Notes
- **Data sources:** `status.json` `result_cache` (HLD §13.5) and `cache.*` events (HLD §15).

## Handoff Boundary
- **Upstream:** T-o95l1M.
- **Downstream:** the parent (go/no-go on recommending `on`) and T-bdQZW4 (the docs include the
  G0 outcome).

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-nPMuz4-cache-shadow-value-check/`
- **Large outputs:** `output/E-Rc4Hk8-cross-run-result-cache/g0-shadow-report.md`

## Comments
- By: architect · Role: architect · Date: 2026-10-05 · Comment: New in Rev 2. It answers
  dev-critic STRATEGIC #1 (value unproven) with measurement instead of opinion. The go/no-go is
  escalated to the parent; the architect does not overrule the brief.
