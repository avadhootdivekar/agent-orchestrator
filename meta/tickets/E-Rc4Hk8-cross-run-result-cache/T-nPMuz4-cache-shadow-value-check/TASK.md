# TASK: T-nPMuz4-cache-shadow-value-check

## Metadata
- Task ID: `T-nPMuz4-cache-shadow-value-check`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Title (Rev 3): **G0 protocol and tooling hand-off** (the folder name is kept for id stability)
- Owner: `tester` (+ `manager` sign-off)
- Created: `2026-10-05`
- Last Updated: `2026-10-05` (implemented)
- Status: `In Review` (implemented; manager sign-off pending, AC-6)
- Estimate: `6 focus hours (0.75 day)` · surfaces (after T-o95l1M)

## Requirements Mapping
- Requirement IDs: R-14 (strategic value risk), FR-16 (shadow mode exercised end to end)
- HLD: §22.5 (G0), §3 A-11 (corrected), §13.6, §16 (rollout), §23.2 OQ-6
- ADR-0019: D26, D34; ALT-8 (fallback)

## Description
**Scope (Rev 3, manager decision).** This task does **not** execute G0. Executing G0 needs
multi-day shadow-mode runs of a real consumer workflow with the operator's consent. It is a
**post-merge follow-up owned by the parent or the operator** (finplan, with consent), and it
**does not block epic closure**. This repository cannot supply that workload: `specs/self-dev/`
holds only agent and reposet files, the built-in templates write per-instance output paths, and
the bench forces the cache off.

This task delivers what the G0 executor needs:

1. **`docs-md/result-cache-g0-protocol.md`, a runnable procedure:**
   - install the epic build as the beta flavour (`install.sh --flavor beta` → `ao-beta`), so the
     stable `ao` is untouched;
   - choose workflows and opt in only tasks that are pure by inspection (`cache: true`), with the
     reason recorded per task;
   - run them under `AO_CACHE=shadow` for the observation window (shadow adds hashing and storage
     but no model spend);
   - collect `ao report-usage --json` → `result_cache` (`lookups`, `would_hits`, `misses`,
     `ineligible`, `miss_reasons`, `store_skip_reasons`, `avoidable_cost_usd`; HLD §13.6) and
     `ao cache stats --json` (`entries`, `bytes.total`, `expired_entries`, `oldest_created_at`,
     `newest_created_at`) at the start and end of the window;
   - extract the dominant miss components from `run.log` `cache.miss` events (a documented
     one-liner);
   - compute the would-hit rate (`would_hits / lookups`) and the avoidable spend per week;
   - apply the decision rule of HLD §22.5 **as a recommendation** (thresholds pending OQ-6).
2. **A report template** in the same document: method, workflows and tasks, a metrics table per
   workflow, dominant miss reasons and components, recommendation, and the parent's decision
   line.
3. **A smoke validation** of the procedure on a fake-executor workflow run twice under
   `AO_CACHE=shadow` (outputs deleted between runs), showing that every collection step works and
   that the would-hit rate on the second run is non-zero. Evidence:
   `output/E-Rc4Hk8-cross-run-result-cache/g0-protocol-smoke.md`. This validates the tooling,
   not the value.
4. **The epic `STATUS.md` G0 line:** "G0 protocol shipped; execution is a post-merge follow-up
   (owner: parent/operator); not run in this epic."

## File scope (exclusive)
- `docs-md/result-cache-g0-protocol.md` (new; T-bdQZW4 links to it and does not edit it)
- `output/E-Rc4Hk8-cross-run-result-cache/g0-protocol-smoke.md` (new)
- The epic `STATUS.md`: the G0 line only.

## Inputs / Outputs
- **Inputs:** T-o95l1M (shadow mode usable from the CLI), T-eyn5UG (the `result_cache` usage
  object). The `ao cache stats --json` fields are specified by HLD §13.4 and checked by T-6tRKml
  (AC-2); the smoke validation does not need T-6tRKml.
- **Outputs:** the protocol, the report template, the smoke evidence and the epic G0 line.

## Acceptance Criteria
1. The protocol document exists, states in its first paragraph that executing G0 is a post-merge
   follow-up owned by the parent or operator and does not block epic closure, and never claims G0
   was run.
2. Every command in the protocol is copy-pasteable and names its expected output fields; the field
   names match HLD §13.4 and §13.6 exactly.
3. The report template has every section listed above, including the decision rule table marked
   "recommendation" and the OQ-6 note.
4. The smoke evidence lists the exact commands, run ids and the `report-usage --json`
   `result_cache` object of the two runs, with `would_hits > 0` and `lookups > 0` on the second
   run.
5. The epic `STATUS.md` carries the G0 line above; the stable `ao` install is untouched
   (`ao --version` unchanged before and after).
6. The `manager` signs off in this task's `STATUS.md`.

## Test requirements
- N/A (documentation and tooling validation). The smoke run is reproducible from the listed
  commands.

## Risks
- **G0 never runs after the merge**, so `on` is never recommended. Mitigation: the parent owns the
  follow-up (OQ-6); the protocol makes it a short, mechanical job.

## Dependencies
- T-o95l1M, T-eyn5UG.

## Pseudocode / Algorithm
```text
write protocol (install beta -> opt in pure tasks -> AO_CACHE=shadow runs -> collect
report-usage/stats/run.log -> compute rate and avoidable $ -> recommendation per §22.5)
smoke: fake workflow, run twice in shadow mode with outputs deleted between runs, collect, record
```

## Schemas / Interface Notes
- **Data sources:** `status.json` `result_cache` (§13.5), `ao report-usage --json` `result_cache`
  (§13.6), `ao cache stats --json` (§13.4), `cache.*` events (§15).

## Handoff Boundary
- **Upstream:** T-o95l1M, T-eyn5UG.
- **Downstream:** T-bdQZW4 (links the protocol); the parent or operator (executes G0 after the
  merge).

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-nPMuz4-cache-shadow-value-check/`
- **Large outputs:** `output/E-Rc4Hk8-cross-run-result-cache/g0-protocol-smoke.md`

## Comments
- By: architect · Role: architect · Date: 2026-10-05 · Comment: New in Rev 2 (G0 value check).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 (manager A5): re-scoped to
  "G0 protocol and tooling hand-off". The Rev 2 premise (run G0 on this repo's self-dev
  workflows inside S3) was false. Executing G0 is a post-merge follow-up owned by the parent or
  operator and does not block epic closure. Owner: tester, with manager sign-off.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Implemented (commit `edc3c18`); State -> In Review, awaiting the manager sign-off (AC-6). G0 itself is not run (post-merge, parent/operator). Evidence: `output/E-Rc4Hk8-cross-run-result-cache/g0-protocol-smoke.md`.