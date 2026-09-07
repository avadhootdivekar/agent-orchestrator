# TASK: T-Tp7Zs2-instructions-and-templates

## Metadata
- Task ID: `T-Tp7Zs2-instructions-and-templates`
- Epic ID: `E-Wk9Tz3-task-isolation`
- Owner: unassigned (developer)
- Created: 2026-09-06
- Last Updated: 2026-09-07
- Status: Done
- Estimate: 2 days

## Requirements Mapping
- Requirement IDs: FR-16, NFR-6 (documentation half) · Design: HLD §11 M10
- Review findings folded in: **R-22** (major — six shipped instruction files tell agents to `git push`,
  which contradicts the isolation model), **S-2** (the shipped `merge-resolver` agent must declare its
  own `disallowed_tools`), **S-3** and **S-5** (documentation duties). Re-estimated 1.5 -> 2 days.

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

### Amendments from the 2026-09-07 review gates

10. **R-22 — remove the `git push` directives from all six per-task instruction files.** Confirmed in
    the builtin template: `08-implement-task.md:18,47`, `11-fix-task.md:17,41`, `31-task-impl.md:12,41`,
    `34-task-fix.md:15,38`, `09-write-tests.md:18,51`, `32-task-test.md:12,37` all instruct the agent to
    commit **and push**. These are exactly the per-task-type instructions the breakdown agent reuses for
    the injected implement/fix/test tasks this epic isolates. Under isolation each runs on an ephemeral,
    ao-owned `ao/<run_id>/<task_id>` branch that per ADR-0013 D4 nobody should ever push: a compliant
    agent would either **fail** on `git push` with no upstream (a spurious task failure unrelated to its
    work) or **succeed** and litter the shared remote with dozens of disposable branches per run.
    Replace "commit **and push**" with "commit only — the engine integrates your work; do not push",
    gated on the same isolation-awareness note AC-5 already plans.
    Test: an assertion over all six files that no push directive remains.
11. **R-22 (cont.) — correct the two review instructions.** `10-review-task.md` and
    `33-task-review.md` tell the reviewer to inspect the **pushed** commits. Under isolation a review
    task's own worktree is created from the now-advanced integration head (FR-9), so the predecessor's
    changes are present in ordinary local history — the review still works, but the wording must not
    send an agent hunting for a remote push that will never exist. Reword to "inspect the commits on
    your current branch".
12. **S-2 — the shipped `merge-resolver` agent declares `disallowed_tools: ["WebFetch", "WebSearch"]`.**
    The engine force-injects the union regardless (`T-Lr6Ka3` AC-12), so this is belt-and-braces — but
    V10 warns precisely when a named resolver agent's own spec looks unrestricted, and the shipped
    template must not be the thing that triggers that warning. Test: the template's agent entry carries
    both tools, and `ao validate` on the rendered template produces **no** V10 warning.
13. **S-3 / S-5 documentation duties.** `conflict-friendly-coding.md` states plainly that **the engine
    auto-commits everything staged in your worktree at task end**, so tasks must not write secret
    material into a worktree at all — the `commit_denylist` is a backstop, not a guarantee. The README
    states that **with `verify_command` unset a `rerere` replay is functionally unreviewed**, and
    recommends setting a real `verify_command` on any repo where isolation is more than a convenience.

## Risks
- The breakdown contract is generated **per run**, so an existing run directory keeps the old
  contract. Document that in the README; do not attempt to migrate existing runs.
- The consumer (`../ao-runner-finplan`) maintains its **own** copy of this contract and its Hard Rule
  5 forbids extra fields. Their adoption is a separate ticket **in that repo**; this ticket changes
  only the builtin template. State this in your handoff so nobody edits the sibling repo.
- Over-long instruction files eat agent context. Keep `conflict-friendly-coding.md` under ~80 lines.
- Eight instruction files change here (six push directives + two review wordings). They are consumed by
  the dynamically-injected fan-out tasks, so a missed file surfaces only during a real run. AC-10's
  assertion over all six is the guard; extend it to the two review files.

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

---
- By: architect · Role: architect · Date: 2026-09-07 · Comment: Phase-2 amendment. R-22 widened this
  ticket from "add an isolation note to one file" to "remove the push directives from six files and
  reword two review files", with a repo-wide assertion so a missed file cannot slip to the e2e pass;
  plus S-2 (the shipped `merge-resolver` agent declares its own disallowed tools so the template never
  triggers V10) and the S-3/S-5 documentation duties. Re-estimated 1.5 -> 2 days.
- By: developer-agent · Role: developer · Date: 2026-09-07 · Comment: Implemented. All 13 base ACs
  plus AC-10..13 amendments delivered; one deliberate deviation from AC-6's literal wording, documented
  in `STATUS.md` and this ticket's handoff report (a live "commented" `integration` block cannot be
  embedded in `workflow.json.tmpl` without either breaking JSON parsing or triggering `spec.py`'s V5
  warning on every default render — the example moved to `README.md` instead; `T-Ov9Bt5`'s
  `merge-resolver` disallowed-tools/V10 recipe is proven end-to-end in
  `tests/test_conflict_instructions.py`). Status -> In Review.
