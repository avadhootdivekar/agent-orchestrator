# TASK: T-EJKD6f-docs-adr-reconcile

## Metadata
- Task ID: `T-EJKD6f-docs-adr-reconcile`
- Epic ID: `E-IasNXu-parallel-execution`
- Owner: architect/developer agent
- Created: 2026-07-15
- Last Updated: 2026-07-15
- Status: Done
- Estimate: 1.0 day

## Requirements Mapping
- Post-implementation reconciliation (Phase 6); documentation coverage for FR-1..FR-8, NFR-1..NFR-4

## Description
After `T-JXiI9j`/`T-j8YLGd`/`T-VSfAUN`/`T-TNleFt` land, reconcile all documentation with the **as-built**
implementation and finalize the decision record. This is the mandatory docs-refresh ticket: it is
completed only after docs are checked against the merged code, including any deviations from this
design. Follow learning #35 — grep the WHOLE doc for now-stale claims, don't just append a note.

### Sub-deliverables
1. **Design doc reconciliation** (`docs-md/parallel-execution-hld.md`): update pseudocode/method names,
   the thread-boundary table, and the edge-case list to match the merged code. Flip the header Status
   from "Design (pre-implementation)" to "Implemented (as-built)" and record any deviations (e.g. if a
   `Graph.predecessors()` helper was added, or the BLOCKED/drain rule shifted).
2. **ADR-0007 finalize**: confirm Status "Accepted" still reflects reality; add a short
   "Implementation notes" addendum if the built design deviated (e.g. barrier predicate details,
   drain policy nuances). Verify all cross-references (ADR-0003 §3, ADR-0006) still hold.
3. **HLD index pointer** (`docs-md/hld-agent-orchestrator.md`): confirm the feature-docs list (L7)
   links `parallel-execution-hld.md` + ADR-0007 and any wording is accurate post-merge.
4. **README** (repo root or `docs-md/`): document `--max-parallel` / `AO_MAX_PARALLEL` /
   `max_parallel:` and the default-serial behavior in the runtime-settings section, alongside the
   existing quota/model/effort docs. If a "Key commands" table exists, add the `--max-parallel` example.
5. **`ao init` template**: verify `T-JXiI9j`'s commented `max_parallel:` line is present and accurate.
6. **Learnings**: add a compact entry to `meta/learning-compact.md` (and long-form
   `meta/learnings.md`) capturing the durable facts — e.g. "parallel execution is serialized-core +
   worker-dispatch; `_run_with_retries` is the only worker-side call; `max_parallel=1` is the
   byte-identical gate; `FakeExecutor` is not thread-safe, use the gated executor for `N>1`;
   `max_parallel` is invocation-scoped, no schema change."
7. **Epic/status sync**: flip `EPIC.md` + epic `STATUS.md` to Done (or the correct end state) with an
   evidence pointer to the passing test matrix; check the box for each task in the EPIC Task List and
   keep counts/wording consistent across all touched docs (README rule #9).

## Acceptance Criteria
1. `docs-md/parallel-execution-hld.md` matches the merged code: a reviewer can follow the pseudocode
   to the actual method names/signatures with no contradictions; header Status = "Implemented
   (as-built)"; deviations (if any) are called out explicitly, not silently dropped.
2. ADR-0007 Status is correct and its cross-references resolve; any deviation from the original
   decision is captured in an "Implementation notes" section (or the ADR is confirmed unchanged).
3. `grep -ri "serial\|max_parallel\|parallel" docs-md/ README*` surfaces no stale claim that the
   engine is "strictly serial" without the "default / N=1" qualifier; the docs consistently describe
   opt-in parallelism.
4. README documents `--max-parallel`, `AO_MAX_PARALLEL`, `max_parallel:`, precedence, and default=1.
5. `meta/learning-compact.md` has a new, clearly-separated entry with `By`/`Role`/`Date`.
6. `EPIC.md` + `STATUS.md` (epic) reflect the final state with evidence; all task checkboxes updated;
   counts/wording consistent across EPIC/STATUS.
7. No code behavior change in this task (docs only) — `uv run pytest` still green; `ruff`/`mypy src`
   clean.

## Risks
- Stale-claim drift (learning #35): appending a note while leaving a contradicting sentence elsewhere.
  Mitigate with the whole-doc grep in AC-3.
- Deviation blindness (learning #36): the design doc must be checked against the real artifact, not
  against this ticket's intent. Read the merged `engine.py` before flipping the Status line.

## Dependencies
- `T-JXiI9j`, `T-j8YLGd`, `T-VSfAUN`, `T-TNleFt` all merged.

## Pseudocode / Algorithm
```text
1. Read merged engine.py + cli.py + project_config.py.
2. Diff design-doc pseudocode/method-names vs code; update doc; record deviations.
3. grep docs-md/ + README for "strictly serial" / "one task at a time" — qualify or remove.
4. Add README runtime-settings row + ao-init check.
5. Append learning-compact entry (By/Role/Date).
6. Flip EPIC.md + STATUS.md to Done; tick task boxes; verify counts/wording parity.
```

## Schemas / Interface Notes
- Interface / API: none (docs only). Data schema / triggers / artifacts: none.

## Handoff Boundary
- Upstream: all implementation + test tasks merged.
- Downstream: none (epic close-out). Marks the epic Done once docs verified against code.

## Artifacts
- Docs/comments: `docs-md/parallel-execution-hld.md`, `docs-md/adr/ADR-0007-parallel-task-execution.md`,
  `docs-md/hld-agent-orchestrator.md`, README, `meta/learning-compact.md`, `meta/learnings.md`.
- Large outputs: none.
