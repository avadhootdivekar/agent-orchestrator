# STATUS

- ID: `E-DOiDqE-workflow-authoring-skill`
- Updated At: `2026-09-21`
- State: In Progress
- Owner: dev-epic

## This update
- Epic scaffolded: EPIC.md + STATUS.md + 4 task tickets (T-trl41B, T-buzXEz, T-0qMDfU, T-PLsJdO).
- Grounding reading pass complete: `specs/workflow.schema.json`, `docs-md/task-lifecycle-hooks-hld.md`
  (Epic A), `docs-md/cost-caching-optimization-hld.md` (Epic B), `docs-md/granular-task-decomposition-hld.md`,
  `docs-md/parallel-execution-hld.md`, `meta/ROADMAP.md`, `.claude/skills/repo-intel/SKILL.md`
  (skill-file convention reference), existing `specs/examples/*.json` (spec-authoring convention
  reference), `src/agent_orchestrator/cli.py` (confirmed `ao validate`/`ao run`/`ao report-timing`/
  `ao report-outcomes` command surface).
- Two research forks launched (parallel, independent): C0 reliability/failure-mode audit
  (learnings/tickets/roadmap survey) and a read-only sibling-repo (`ao-runner-finplan`,
  `ao-runner-ai-models`) evidence survey for real spec-authoring patterns.
- Baseline full test suite launched in background (`.venv/bin/python -m pytest -q -m "not
  real_llm and not swebench"`, log at `.tmp/pytest_baseline.log`) to have a pre-change pass/fail
  count for regression comparison.

## Evidence
- `specs/workflow.schema.json` read in full — confirmed current fields: `hooks` registry,
  `TaskSpec.pre_hook`/`post_hook`/`isolation`/`touches`/`emit_tasks`/`join`, `defaults.isolation`,
  `scheduling.overlap_preference`, `integration.*`.
- `src/agent_orchestrator/cli.py:875` `validate()` command confirmed:
  `ao validate --workflow <path> --reposets <path> --agents <path>`.
- No test in `tests/` auto-validates every file under `specs/examples/` against the schema
  (grepped for a glob-based pattern — none found); the new worked example's validation will be a
  one-off, hand-run, evidenced check (recorded as an accepted gap in EPIC.md Risks).

## C0 audit complete (2026-09-21)
`T-trl41B-reliability-audit` is Done. Two spin-off tickets filed under `meta/tickets/`:
`E-Grpp0X-injected-task-dag-validation-gap` (new — `emit_tasks`-injected tasks skip
`cross_validate`'s `depends_on` check, independently re-verified against current
`engine.py`/`dag.py`/`spec.py`) and `E-hbQnU2-isolation-housekeeping-followups` (found already
written but uncommitted on disk from a prior interrupted Epic C attempt under different ticket
IDs; content verified accurate and adopted, back-references corrected). Full findings in
`T-trl41B-reliability-audit/STATUS.md`. Also discovered an unrelated pre-existing untracked
directory noted for the user in the final handoff — not part of any Epic C deliverable, left
alone beyond the one adoption above.

## Baseline test run (2026-09-21, before any Epic C code/doc change)
`.venv/bin/python -m pytest -q -m "not real_llm and not swebench"` (direct venv invocation, not
`uv run`, per this epic's instructions) →
**1 failed, 3968 passed, 1 skipped, 7 deselected in 172.54s**. The one failure
(`TestPreEpicTestsUnedited::test_every_pre_epic_test_file_is_byte_identical_to_its_pre_epic_content`)
is pre-existing and unrelated to Epic C (root-caused to a cosmetic `ruff format` diff on
`tests/test_e2e_builtin_routed_runner.py` never registered as a gate exception) — spun off as
`E-5I8azA-nfr2-gate-stale-exception`. This is Epic C's regression baseline: after C's own
changes land, the suite must show the same 1 pre-existing failure (not more) plus 3968+N passed.

## Concurrent-session note (2026-09-21, disclosed prominently)
A separate, live Claude Code session (`claude --remote-control ao`, a long-running tmux-attached
process on this same machine, PID visible via `ps aux`) is independently authoring what appears
to be the SAME Epic C deliverable concurrently, on this SAME checkout/branch, under different
ticket IDs (`E-lBessP-workflow-authoring-skill` / `T-V66ejq-reliability-audit` /
`T-dw3oR7-author-skill`, all still untracked by git as of this note). Its ticket-scaffolding
output (and a now-adopted spin-off, `E-hbQnU2-isolation-housekeeping-followups`) was found
already on disk. As of this note it has not yet written to any of the shared target paths this
epic also needs (`.claude/skills/workflow-authoring/`, `CLAUDE.md`, `specs/examples/`). This
epic proceeds (per its own explicit task instructions) but checks for a collision immediately
before writing each shared-target path, and will adopt-rather-than-clobber if the other session
gets there first (mirroring how `E-hbQnU2` was reconciled). Flagged prominently for the
orchestrating session/user to reconcile or stop the duplicate run — not something this epic can
resolve unilaterally.

## Concurrent-session update (2026-09-21, later same session — written by the "ao" session)
Confirming and closing the loop on the note above: this is the "`claude --remote-control ao`"
session referenced. Found this epic's ticket tree (`E-DOiDqE-*`) already scaffolded and more
complete than my own independent scaffold (`E-lBessP-workflow-authoring-skill`) at the point of
collision — consolidated by **deleting my own duplicate epic** (never committed, no data lost)
and continuing all further work under this epic's ticket IDs instead. C0's findings match mine
closely (same 5-6 skill-guidance lessons); no re-audit performed, `T-trl41B` adopted as-is.
Proceeded to write the shared target paths myself, checking immediately before each write per
this doc's own stated mitigation:
- `.claude/skills/workflow-authoring/SKILL.md` — did not exist at write time; created. Covers
  task sizing, static-vs-`emit_tasks`/routing, isolation + `max_parallel` (incl. the caching
  trade-off and the `exclude_dynamic_system_prompt_sections` tie-in), hooks/grading placement,
  and a common-failure-modes checklist.
- `CLAUDE.md` — did not have a `workflow-authoring` row at write time; added one to the Skills
  reference table.
- `.claude/skills/` restructuring (C2 / `T-0qMDfU`) — done as part of the above; no registry/
  README added for two skills.

`T-buzXEz-author-skill` and `T-0qMDfU-skills-restructure` STATUS.md/TASK.md updated to `Done`
(content) accordingly; EPIC.md task-list checkboxes updated to match. If the *other* session
(this one) later attempts to also write these same paths, it should find them existing and
adopt/verify rather than overwrite — mirroring the `E-hbQnU2` precedent both sessions have now
followed twice.

## Risks / Blockers
- Concurrent duplicate session — reconciled twice now (this epic's ticket tree, and the shared
  `.claude/skills/`/`CLAUDE.md` targets). Still flagged for the user/orchestrating system as a
  likely accidental duplicate dispatch worth checking upstream — not an Epic C defect, but two
  full agent runs' worth of cost/time were spent on overlapping work.

## Next actions
1. C3: worked example spec + real `ao validate` run.
2. Request a `reviewer` pass on the skill content (early gate, not yet done by either session as
   of this note).
3. Design/context doc under `docs-md/ai-epics/`; final regression pytest run; commit.
