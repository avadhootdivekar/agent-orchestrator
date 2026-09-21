# Epic context — AO workflow-authoring skill (Epic C)

- Epic: [`E-DOiDqE-workflow-authoring-skill`](../../meta/tickets/E-DOiDqE-workflow-authoring-skill/EPIC.md)
- Status: **Done** — C0/C1/C2/C3 complete, early-gate review incorporated, late-gate real engine
  execution passed (§8b), final regression pass clean (same 1 pre-existing unrelated failure as
  baseline, +1 net passed, 0 new failures).
- Author: `dev-epic` agent, 2026-09-21
- Thread: Epic C of the cost/perf/hooks/skills thread on `ad/cost-perf-hooks-skills` — follows
  Epic A ([`task-lifecycle-hooks-hld.md`](../task-lifecycle-hooks-hld.md)) and Epic B
  ([`cost-caching-optimization-hld.md`](../cost-caching-optimization-hld.md)).

## 1. Goal & acceptance criteria

Author a Claude Code skill under `.claude/skills/` that teaches an agent how to decompose a
complex task into a well-formed `ao` DAG / workflow spec, grounded in the **current** spec
schema and CLI (not stale HLD claims — HLDs can drift during implementation) and in real usage
evidence from the sibling `ao-runner-*` consumer repos. Explicitly **not** an import of generic
personal-productivity Claude skills from sibling repos (locked out during scoping).

Acceptance criteria (traced to requirements in `EPIC.md`):
1. `.claude/skills/workflow-authoring/SKILL.md` exists, frontmatter matches the `repo-intel`
   convention, and covers: task sizing, routing/`emit_tasks` vs. static task lists,
   isolation/`max_parallel` (incl. Epic B's caching/parallelism trade-off), hooks/grading
   placement, common failure modes.
2. Every schema/CLI claim in the skill is checked against current
   `specs/workflow.schema.json`/`specs/agents.schema.json`/`src/agent_orchestrator/` source.
3. `.claude/skills/` restructured only as proportionate; `CLAUDE.md`'s skills table updated.
4. A worked-example workflow spec under `specs/examples/`, schema-valid via a real `ao validate`
   run (not eyeballed).
5. C0 reliability/failure-mode audit (time-boxed scoping input) completed first; any real
   unrelated bug/gap spun into its own ticket, not folded into this epic.

## 2. Decomposition (tasks)

| Task | Scope | Status |
|---|---|---|
| `T-trl41B-reliability-audit` (C0) | Survey `meta/learnings.md`, `meta/learning-compact.md`, ticket `REVIEW*.md`/`STATUS.md`, `meta/ROADMAP.md` §4 for DAG-authoring failure patterns | **Done** |
| `T-buzXEz-author-skill` (C1) | Author `.claude/skills/workflow-authoring/SKILL.md` | **Done** |
| `T-0qMDfU-skills-restructure` (C2) | `.claude/skills/` layout + `CLAUDE.md` table update | **Done** |
| `T-PLsJdO-worked-example` (C3) | Worked example spec + real `ao validate` run | **Done** |

## 3. C0 audit — findings (feeds C1 directly)

Full detail: `meta/tickets/E-DOiDqE-workflow-authoring-skill/T-trl41B-reliability-audit/STATUS.md`.

**Skill-guidance findings** (all incorporated into the skill):
1. Task-sizing precedent from `docs-md/granular-task-decomposition-hld.md` (still-backlog design,
   sizing heuristic reused by hand): one focused change, ~single module, ≤5 files, sized to a
   generous turn budget; both over- and under-fragmentation named as failure modes.
2. Routers need a real sink per branch (`LRN-20260710-route-sink-required-per-branch`).
3. Unknown-N fan-out needs a fixed-id aggregator, even at N=0
   (`LRN-20260704-dynamic-fanout-fixed-aggregator`).
4. `emit_tasks: true` tasks must set `skip_if_outputs_exist: false` or silently fail to
   re-inject on resume.
5. Parallel write conflicts (with `isolation: none` + `max_parallel > 1`) are the spec author's
   job — corroborated by a real finplan file-contention incident (project memory).
6. Cache-cost vs. parallelism trade-off (Epic B, `cost-caching-optimization-hld.md` §1.5).
7. Isolation caveat: `filter`/`merge` git-attribute drivers are not suppressed under isolation.

**Real gaps found, spun off (NOT fixed in this epic, per its own scope rules):**
1. **`E-Grpp0X-injected-task-dag-validation-gap`** — `engine.py::_inject` never re-runs
   `spec.py::cross_validate`'s "unknown depends_on" rule against runtime-injected
   (`emit_tasks`) tasks; `dag.py::build_dag` tolerates the unknown dep with a phantom adjacency
   entry rather than raising, deferring the failure downstream. Independently re-verified
   against current `engine.py`/`dag.py`/`spec.py` (not taken on the originating 2026-07-04
   learning's word alone).
2. **`E-hbQnU2-isolation-housekeeping-followups`** — four small, previously-unticketed
   isolation-subsystem cleanups (worktree-porcelain `-z` parsing, `_pid_alive`/boot-id
   triplication, a possible T2-resolver dispatch-window race, missing `GitRepo.diff_patch()`),
   all already-accepted-with-rationale in `docs-md/task-isolation-hld.md` but never ticketed.
   Found already drafted on disk by a concurrently-running second `dev-epic` session working
   this same epic (see §5) and adopted after independent content verification.
3. **`E-5I8azA-nfr2-gate-stale-exception`** — the pre-change baseline full-suite run
   (`.venv/bin/python -m pytest -q -m "not real_llm and not swebench"`) showed
   **1 failed, 3968 passed, 1 skipped, 7 deselected**; the failure is a pre-existing,
   unrelated-to-Epic-C NFR-2 regression-gate trip (a cosmetic `ruff format` diff on
   `tests/test_e2e_builtin_routed_runner.py` never registered as a gate exception).

## 4. Skill content (C1/C2) — summary

`.claude/skills/workflow-authoring/SKILL.md`: quick-decision table, then five sections — task
sizing (artifact-boundary contract + safe retry/resume unit, not a duration), static task list
vs. `emit_tasks`/routing (with the injected-task validation caveat linked to
`E-Grpp0X-injected-task-dag-validation-gap`), isolation & parallelism (incl. the caching/
parallelism trade-off from `cost-caching-optimization-hld.md` §1.5 and the
`exclude_dynamic_system_prompt_sections` tie-in, honestly scoped per the early-gate fix in §8),
hooks & grading (`pre_hook`/`post_hook` vs. post-run `ao report-outcomes --grade`), and
a seven-item common-failure-modes checklist grounded in §3's C0 findings. Every field/CLI claim
was checked against `specs/workflow.schema.json`, `specs/agents.schema.json`, and
`src/agent_orchestrator/cli.py` at authoring time (confirmed, e.g., `max_parallel` is a
CLI/env/config setting with no workflow-spec field — grepped, absent from the schema).

`.claude/skills/` restructuring (C2): the new skill lives in its own `workflow-authoring/`
subdirectory (matching `repo-intel`'s layout); no top-level skills README/index added (two
skills doesn't warrant a registry — an explicit, recorded "not needed" decision, not a silent
skip); `CLAUDE.md`'s "Skills reference" table gained one row.

## 5. Sibling-repo survey (evidence gathering, read-only)

A research fork surveyed `/usr/avadhoot/mounted/ao-runner-finplan`,
`/usr/avadhoot/mounted/ao-runner-ai-models` (read-only; `ao-runner-1` not present), treated as
live/moving snapshots (other unrelated sessions actively run real workflows inside
`ao-runner-finplan` concurrently on this machine). Findings folded into the skill without
quoting sibling-repo content verbatim — summarized as structural/numeric patterns per the
epic's own instruction. *(Fork result incorporated by whichever session's turn observed it
first — see §6 for the concurrency note; both sessions were working from the same underlying
survey instructions.)*

## 6. Operational anomaly — concurrent duplicate session (disclosed)

During this epic's execution, a **second, independently-running `dev-epic` session** was found
to be working this exact same epic prompt concurrently, on the same checkout/branch, in the same
working directory (evidenced by: an untracked ticket tree appearing mid-run referencing ticket
IDs this session never created; `.claude/skills/workflow-authoring/SKILL.md` and `CLAUDE.md`
being written by a process this session did not invoke; file mtimes advancing in real time
between checks). Likely cause: an accidental duplicate dispatch of the same Epic C prompt to two
separate Claude Code sessions on this machine (one of the long-running `claude --remote-control
ao` tmux sessions visible via `ps aux`, and this conversation's own `dev-epic` invocation).

**Resolution applied, live, without an explicit coordination channel between the two sessions**:
both sessions independently converged on consolidating onto ONE ticket tree
(`E-DOiDqE-workflow-authoring-skill`) rather than shipping two competing epics for identical
work — the other session discarded its own duplicate scaffold
(`E-lBessP-workflow-authoring-skill`, never committed) and continued directly under this epic's
IDs, correctly citing this epic's own spun-off tickets (`E-Grpp0X-...`) it found already on disk
rather than re-filing duplicates. Content produced by either session was verified (schema/CLI
re-checked, citations spot-confirmed) before being treated as this epic's deliverable, the same
standard applied to this session's own output.

**Flagged explicitly for the user/orchestrating session**: this is worth checking — was a
duplicate dispatch intentional (e.g., a deliberate hedge) or accidental? No production code was
at risk (this epic's scope is docs/skill/example-spec only), and the two sessions' convergent
behavior avoided a worse outcome (two shipped, conflicting epics), but the underlying dispatch
mechanism allowing two `dev-epic` agents to run the identical prompt unknowingly on the same
checkout is worth a look outside this epic's own scope.

## 7. Evidence log

- Baseline pytest (before any Epic C change): `1 failed, 3968 passed, 1 skipped, 7 deselected`
  in 172.54s, direct `.venv/bin/python -m pytest` invocation (not `uv run`, per this epic's
  instructions — `uv run` has been observed hanging on this shared machine).
- Schema/CLI claims re-verified against `specs/workflow.schema.json` (read in full),
  `specs/agents.schema.json`, `src/agent_orchestrator/cli.py` (`validate`, `run`,
  `report_timing`, `report_outcomes` signatures read directly).
- `E-Grpp0X-injected-task-dag-validation-gap`'s core claim independently re-verified against
  `src/agent_orchestrator/engine.py:4084-4115` (`_inject`), `src/agent_orchestrator/dag.py:
  133-184` (`build_dag`), `src/agent_orchestrator/spec.py:82-136` (`cross_validate`).
- C3 (worked example): `specs/examples/workflow-dry-run-flag.json` (+ 5
  `specs/examples/instructions/dryrun-*.md` files) — "add `ao run --dry-run`" decomposed into
  design → two parallel disjoint-output implement tasks (`isolation` left `none`, justified
  inline in the ticket per the skill's own rule) → test (`post_hook: grade`) → review. Real
  `ao validate` run, twice (module invocation + installed `ao` console entrypoint), both
  `OK: all specs valid`, exit 0. Full command transcript:
  `meta/tickets/E-DOiDqE-workflow-authoring-skill/T-PLsJdO-worked-example/STATUS.md`.

## 8. Early gate (reviewer/architect pass) — complete

Both `reviewer` and `architect` reviewed the finished skill + worked example. Both verdicts:
**approve with changes** (neither found a structural problem requiring rework). Full findings
and disposition: `meta/tickets/E-DOiDqE-workflow-authoring-skill/EPIC.md` "Early gate" section.
Summary of what changed in `SKILL.md` as a direct result:
- Corrected an overclaim about `AgentSpec.exclude_dynamic_system_prompt_sections`: it addresses
  only one of two documented Claude Code cache-scope determinants (working directory/environment
  info), not the separate branch/recent-commits determinant that per-task worktree isolation
  necessarily varies — now states "reduces, not proven to eliminate" per
  `cost-caching-optimization-hld.md` §1.4's own honest accounting, plus the version-incompatibility
  reason it's opt-in.
- Added a note that a structural task's (`emit_tasks`/router/loop-gate) own `isolation` setting
  is always ignored at dispatch (`spec.py` V4 validate-time warning).
- Fixed a dangling "ADR-0015 §1.5" citation (that content lives in
  `cost-caching-optimization-hld.md` §1.5, not the ADR).
- Added a note that `ao report-outcomes --grade` needs the full spec
  (`--workflow`/`--reposets`/`--agents`) to resolve the hook registry.
- Disclosed, in the Worked-example section itself, that the one example is static-only and does
  not exercise `emit_tasks`/routing/`max_parallel > 1` — pointed at the two existing examples
  that do, rather than building a second full worked example (proportionate to scope).

**Real regression caught during this pass** (not by the reviews themselves, but by re-running the
full suite after C3 landed, which is exactly the kind of check this process exists to force):
a genuine new test failure in `tests/test_isolation_models.py` (a pre-existing test's blanket
"every example uses only documented-default isolation fields" assumption didn't anticipate the
new example's deliberate `touches` usage). Fixed with a named exclusion, not a weakened
assertion — see `T-PLsJdO-worked-example/STATUS.md` for detail. Final suite:
**1 failed (same pre-existing, unrelated `E-5I8azA` gate trip), 3969 passed, 1 skipped,
7 deselected** — net +1 passed vs. baseline, 0 new failures.

## 8b. Late gate — real engine execution (complete)

C3's own acceptance criteria only required `ao validate` (schema-level). The `dev-epic` mandate's
pre-close checklist separately requires exercising a representative workflow spec through the
engine with evidence, before the epic is truly closeable — done after §8:

- `ao run` against `specs/examples/workflow-dry-run-flag.json` with a throwaway `agents.json`
  (every agent's `executor` set to `"fake"` — no network/LLM calls) → **`run.end` status
  `succeeded`**, all 5 tasks succeeded in dependency order, both `implement-*` tasks' `pre_hook`
  passed, `test`'s `post_hook` passed.
- `ao report-outcomes --grade grade` against the resulting run → all 5 tasks graded `passed`,
  demonstrating the `post_hook`-vs-`--grade` distinction the skill teaches, on a real run.
- Evidence (`run.jsonl.txt`, `status.json`, `report-outcomes.txt`, the `agents-fake.json` used):
  `output/E-DOiDqE-workflow-authoring-skill/late-gate/` (README explains reproduction; the
  FakeExecutor's stub output files and full `.orchestrator/runs/<run_id>/` directory were
  deleted after capture — regenerable, and no run-state directory has ever been committed to
  this repo).
- A first attempt, run against a fully isolated scratch workspace instead of the repo root,
  correctly failed at `implement-cli`'s `pre_hook` — the hooks' argv uses a relative script path
  (`specs/examples/hooks/check_disk_space.py`), which only resolves when the task's cwd is the
  repo root. Not a defect in the worked example (`specs/examples/workflow-hooks.json`, the
  pre-existing Epic A example, uses the identical pattern); recorded as why the final run uses
  the real repo root with the real `specs/examples/reposet.json`.

## 9. Non-MVP / deferred

- Interactive skill tooling / CLI integration (e.g., an `ao new --from-skill` scaffolding path).
- Syncing/publishing this skill into the sibling `ao-runner-*` repos (this epic's sibling-repo
  access is read-only by design; scope explicitly excludes writing there).
