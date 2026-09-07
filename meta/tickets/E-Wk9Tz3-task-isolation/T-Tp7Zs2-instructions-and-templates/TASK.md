# TASK: T-Tp7Zs2-instructions-and-templates

## Metadata
- Task ID: `T-Tp7Zs2-instructions-and-templates`
- Epic ID: `E-Wk9Tz3-task-isolation`
- Owner: unassigned (developer)
- Created: 2026-09-06
- Last Updated: 2026-09-06
- Status: Draft
- Estimate: 1.5 days

## Requirements Mapping
- Requirement IDs: FR-16, NFR-6 (documentation half) · Design: HLD §11 M10

## Description
Make most collisions **mechanical before they happen**, and let the breakdown agent express `touches`
and per-task `isolation`. This is the "conflict-friendly coding rules" half of the epic — it is what
makes the union resolver actually correct.

Files you own:
- `src/agent_orchestrator/templates/instructions/conflict-friendly-coding.md` (new asset)
- `src/agent_orchestrator/templates/builtin/routed-runner/breakdown-contract.md.tmpl` (edit)
- `src/agent_orchestrator/templates/builtin/routed-runner/instructions/07-task-breakdown.md` (edit)
- `src/agent_orchestrator/templates/builtin/routed-runner/instructions/08-implement-task.md` (edit)
- `src/agent_orchestrator/templates/builtin/routed-runner/workflow.json.tmpl` (edit)
- `src/agent_orchestrator/templates/builtin/routed-runner/template.yaml` (edit — `required_agents`)
- `src/agent_orchestrator/templates/builtin/routed-runner/README.md` (edit)
- `tests/test_builtin_routed_runner_assets.py` (extend), `tests/test_conflict_instructions.py` (new)

Do NOT touch: `merge-resolve.md` (owned by `T-Lr6Ka3`), any `isolation/` module, `engine.py`,
`models.py`, and **nothing whatsoever in `../ao-runner-finplan`**.

## Acceptance Criteria
1. `conflict-friendly-coding.md` ships in the package and contains the seven rules from HLD §11 M10(a)
   as numbered, checkable statements: new files over hub edits; append-only, one-entry-per-line,
   trailing-newline registrations; no drive-by reformatting/import re-sorting; focused diffs; additive
   data/model changes; worktree hygiene (commit freely, never `checkout` another branch, never
   `stash`, never `push`, never rewrite ao-created history); and "report a `touches` mismatch in your
   output".
2. It is usable through the **existing** `general_instructions` mechanism with no new plumbing —
   verified by an integration test that adds it via `--general-instruction` and asserts the rendered
   prompt names its path (paths only, never contents — NFR-1).
3. `breakdown-contract.md.tmpl`'s Hard Rule 5 field allowlist is widened from
   `id, agent, instruction, depends_on, inputs, outputs, timeout_seconds, skip_if_outputs_exist,
   effort, model, max_turns` to additionally include **`touches`** and **`isolation`**, with the
   guidance text: *"`touches` is a best-effort glob list; being incomplete is fine and never blocks
   scheduling. Because tasks are isolated, prefer NOT to add a cross-task `depends_on` purely to avoid
   file collisions — add it only for a genuine build-on-top-of dependency."* A test asserts both new
   field names and that guidance sentence are present in the rendered contract.
4. `07-task-breakdown.md` declares `.ao/hotspots.json` as an input and instructs the agent to name
   `touches` per task and avoid concentrating tasks on hotspot files. The existing "Minimize
   collision" bullet is **rewritten**, not merely appended to, so it no longer pushes the agent
   towards serializing `depends_on` chains.
5. `08-implement-task.md` gains a short "you are in an isolated worktree" section consistent with rule
   6 of the instruction pack, and its existing branch check is made worktree-aware (an
   `ao/<run>/<task>` branch is valid, not a reason to stop).
6. `workflow.json.tmpl` gains `defaults.isolation: "none"` (opt-in stays opt-in), a **commented**
   example `integration` block showing `verify_command`, `resolver_agent` and `resolvers.union`, and
   `git-branch-off` pinned to `"isolation": "none"`. `template.yaml` adds `merge-resolver` to
   `required_agents`.
7. The rendered template still passes `ao validate` end to end (extend the existing routed-runner e2e
   test), and `ao new routed-runner <id> --validate-only` succeeds.
8. `README.md` gains a short "Parallel isolation" section: what `isolation: worktree` does, where
   worktrees live, how to opt in, how to set `verify_command`, the cold-rebuild caveat and the shared
   build-cache recipe (NFR-6).
9. `uv run pytest -q` green with recorded counts; `ruff` clean (markdown is not linted, but the test
   file is); no behaviour change for a workflow that does not opt in.

## Risks
- The breakdown contract is generated **per run**, so an existing run directory keeps the old
  contract. Document that in the README; do not attempt to migrate existing runs.
- The consumer (`../ao-runner-finplan`) maintains its **own** copy of this contract and its Hard Rule
  5 forbids extra fields. Their adoption is a separate ticket **in that repo**; this ticket changes
  only the builtin template. State this in your handoff so nobody edits the sibling repo.
- Over-long instruction files eat agent context. Keep `conflict-friendly-coding.md` under ~80 lines.

## Dependencies
- Upstream: `T-Sc7Rm2` (the fields must exist and validate).
- Downstream: `T-Ee3Mn8` (asset assertions), `T-Dr5Yq6` (docs).

## Pseudocode / Algorithm
```text
No algorithm — content work. HLD §11 M10 lists the exact rule set and every template edit.
```

## Schemas / Interface Notes
- Interface / API: none.
- Spec / data schema: the routed-runner manifest field allowlist (documentation-level contract).
- Triggers / events: none.
- Artifacts: `templates/instructions/conflict-friendly-coding.md` (packaged asset).

## Handoff Boundary
- Upstream: `T-Sc7Rm2`'s merged fields.
- Downstream: `T-Dr5Yq6` links the instruction pack from `docs-md/`.

## Artifacts
- Docs/comments: `meta/tickets/E-Wk9Tz3-task-isolation/T-Tp7Zs2-instructions-and-templates/`
- Large outputs: none
