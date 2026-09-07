# REVIEW: T-Tp7Zs2-instructions-and-templates

- Reviewer: reviewer-agent
- Date: 2026-09-07
- Scope: `src/agent_orchestrator/templates/instructions/conflict-friendly-coding.md` (new);
  `src/agent_orchestrator/templates/builtin/routed-runner/{breakdown-contract.md.tmpl, template.yaml,
  workflow.json.tmpl, README.md, instructions/{07,08,09,10,11,31,32,33,34}-*.md}`;
  `tests/test_conflict_instructions.py` (new), `tests/test_builtin_routed_runner_assets.py`
  (extended); ticket docs. Explicitly excludes `T-Wk3Nv6` (`isolation/paths.py`, `worktrees.py`,
  `view.py`, `service/paths.py`, `tests/isolation/*`, `tests/service/*`) and `T-Ov9Bt5`
  (`scheduling/`, `isolation/hotspots.py`, `cli.py` hotspots cmd, its tests) — read `isolation/
  hotspots.py` only, for shape cross-checking; not evaluated on its own merits.
- Verdict: **APPROVE WITH CHANGES** — must fix **C-1** before merge; C-2/C-3 optional polish.

## Verification performed (not just claimed)

| Command | Result |
|---|---|
| `uv run ruff check` (scope files) | All checks passed |
| `uv run ruff format --check` (scope test files) | 2 files already formatted |
| `uv run mypy src` | Exactly 4 errors, all pre-existing in `_version.py` — matches expectation |
| `uv run pytest tests/test_conflict_instructions.py tests/test_builtin_routed_runner_assets.py tests/test_e2e_builtin_routed_runner.py tests/test_e2e_cli_templates.py -q` | **73 passed**, 0 failed |
| `ao new routed-runner ... --validate-only` via `CliRunner`, independent script, agents.json **with** the full base roster **and without** `merge-resolver` | Exit 0, `OK: rendered workflow is valid`, no `WARNING:` lines — confirms challenge #2 (no backward-compat break) |
| `ao new` (full, not `--validate-only`) then `ao validate --workflow ... --agents <roster w/o merge-resolver>` | Exit 0, `OK: all specs valid` — confirms `required_agents` is genuinely unenforced at both `ao new` and `ao validate` |
| Read `templates/__init__.py::instantiate`/`_load_template_info`/`_scan_template_dirs` | Confirmed `required_agents` only flows into `TemplateInfo` (displayed by `cli.py:1591-92`); never checked against a workspace `agents.json` anywhere |
| Read `engine.py:540-578` (`_prepare_dispatch`'s missing-inputs gate) | Confirmed there is no "optional input" concept beyond the `join="any"` producer-liveness relaxation, which does not cover an externally-supplied file with no producing task in the graph |
| Read `models.py:553` (`_is_structural_task`) | Confirmed it is `emit_tasks or router or loop-gate` only — nothing else is ever force-excluded from isolation |

## Blocking

None. Default render is unaffected and behaves byte-identically to a non-isolated workflow (verified independently, not just via the developer's own test). No swallowed errors, no engine/models edits (ticket's own boundary honored), no test regressions.

## Major

### C-1 — README's `defaults.isolation: "worktree"` opt-in recipe silently breaks every route except epic/task, including the terminal push

- **Location**: `templates/builtin/routed-runner/README.md:173-176` ("How to opt in", step 2:
  *"...or `defaults.isolation: "worktree"` to isolate every non-structural task by default —
  `git-branch-off`/`classify`/`task-breakdown` are always forced to `"none"` regardless, since they
  are structural"*). Cross-referenced against `workflow.json.tmpl` (only `git-branch-off` carries an
  explicit `"isolation": "none"` pin) and `instructions/90-final-push.md:25-28` (branch check requires
  *"a non-main **epic branch** (created by the git-branch-off stage)"*, and still says *"commit them
  ... Push: `git -C <repo> push`"*, `90-final-push.md:36-41`).
- **What's wrong**: `_is_structural_task` (`models.py:553`, verified by reading it) force-excludes
  only `emit_tasks`/router/loop-gate tasks — `classify` (router) and `task-breakdown` (`emit_tasks`)
  qualify; `git-branch-off` does **not** (it stays `"none"` purely because of its explicit YAML pin,
  a template-authoring choice, not an engine guarantee — see C-2). Nothing else in this template
  force-excludes or pins: the five route-terminal push tasks (`bug-push`/`epic-push`/`task-push`/
  `doc-push`/`testing-push`, all backed by `90-final-push.md`) and several mid-route tasks
  (`bug-fix`, `doc-write`, `test-write`, and siblings) are **not** structural per that definition and
  carry **no** `"isolation"` field in `workflow.json.tmpl`, so they inherit
  `defaults.isolation: "worktree"` exactly as the README instructs an operator to set it. Those
  instruction files were correctly left untouched by this ticket's own scope (`TASK.md`'s "files you
  own" list is the 6+2 R-22 files only, all in the epic/task-route fan-out pipeline) — but the README
  now documents a mechanism that reaches far beyond that scope without saying so.
- **Why it matters**: following the README's own sanctioned recipe verbatim reintroduces the exact
  R-22 failure mode this ticket exists to eliminate — for five more tasks it never touched. Isolating
  `90-final-push` either (a) fails its branch-safety check outright (an `ao/<run_id>/<task_id>`
  worktree branch is not "the epic branch"), silently failing the whole route's actual deliverable
  with `push-report.md` never written, or (b) if the check is read loosely, runs `git push` from a
  disposable, disconnected branch — littering the real remote with an orphan `ao/...` branch while the
  route's actual accumulated work never reaches origin. HLD §14's own security table (row
  "Auto-commit sweeping secrets", `docs-md/task-isolation-hld.md:2235`) explicitly assumes
  `90-final-push.md` runs against the **shared, synced checkout** after isolated work fast-forwards
  into it — the design's own mental model requires this task to stay unisolated, but nothing in this
  ticket's deliverable enforces or even flags that. `test_builtin_routed_runner_assets.py`'s
  `PUSH_DIRECTIVE_FILES`/`REVIEW_WORDING_FILES` tuples (lines 395-405) don't include
  `90-final-push.md` or any bug/doc/testing-route file, so nothing guards this today.
- **Concrete fix**: pin the five push-task ids to `"isolation": "none"` in `workflow.json.tmpl`,
  mirroring the existing `git-branch-off` pin (small, in-scope, low-risk — the file is already owned
  by this ticket) — this closes the most dangerous half (the actual push). Then reword README step 2
  to stop recommending a blanket `defaults.isolation: "worktree"` flip; either drop that alternative
  entirely (per-task pins only, on the fan-out ids the ticket actually made isolation-aware) or add an
  explicit caveat that `bug-fix`/`bug-test`/`bug-review`/`doc-plan`/`doc-write`/`doc-review`/
  `test-gap-analysis`/`test-write`/`test-run` are not yet isolation-aware and must not be isolated
  until a follow-up updates them. Add a test asserting the five push-task ids resolve to
  `isolation: "none"` in the rendered template (same shape as
  `test_workflow_json_tmpl_git_branch_off_pinned_isolation_none`).
- **Scope note**: this is also a latent gap in the HLD itself (§11 M9's `--isolation worktree` CLI
  override, owned by `T-Cx4Jf1`, documents the identical "force every non-structural task to
  isolation:worktree" semantics with the same blind spot) and in `_is_structural_task`'s definition —
  worth flagging to the architect as a cross-cutting follow-up beyond just this template's docs. But
  this ticket is the one that ships the first concrete, user-facing instance of the unsafe
  recommendation, and it owns both files needed for the narrow, in-scope fix above.

## Minor

### C-2 — README overstates why `git-branch-off` stays unisolated

- **Location**: `README.md:174-176` — *"`git-branch-off`/`classify`/`task-breakdown` are always
  forced to `"none"` regardless, since they are structural."*
- **What's wrong**: only `classify` (router) and `task-breakdown` (`emit_tasks`) are code-forced via
  `_is_structural_task`/V4. `git-branch-off` is not structural by that definition — it stays `"none"`
  solely because `workflow.json.tmpl:23` pins it explicitly. If an operator hand-edits their own
  instantiated `workflow.json` and drops that pin while also setting
  `defaults.isolation: "worktree"`, `git-branch-off` is not protected the way `classify`/
  `task-breakdown` are.
- **Fix**: reword to distinguish "code-forced regardless of spec" (`classify`, `task-breakdown`) from
  "explicitly pinned by this template" (`git-branch-off`) — one sentence, no behavior change.

### C-3 — `required_agents` ordering drifts between `template.yaml` and `README.md`

- **Location**: `template.yaml:62-73` (insertion order, `merge-resolver` appended last) vs.
  `README.md:31-32` (alphabetized list). Cosmetic only — both lists are set-equal and both tests
  (`test_template_yaml_required_agents_exactly_match_dag`,
  `test_template_yaml_required_agents_includes_merge_resolver`) pass regardless of order. Not worth
  blocking on; noted for whoever next touches either file.

## Disposition of the two documented deviations

**Deviation (a) — AC-6's "commented example `integration` block" moved to `README.md` instead of
being embedded live in `workflow.json.tmpl`.** **Accept.** Independently confirmed both technical
claims: `templates/__init__.py`'s module docstring states plainly "There are no conditionals/loops"
in the `{{ var }}` renderer, and `_validate_rendered_workflow` (`templates/__init__.py:741`) runs full
`WorkflowSpec`/JSON-Schema validation unconditionally on every render — so a literal `//`-style
comment breaks JSON parsing on every `ao new`, and a live non-default `integration` block trips V5's
warning on every default render (opt-in stays opt-in, by AC-6's own text). The recipe is proven
end-to-end by `TestIsolationEnabledRecipeFromReadme` (`test_conflict_instructions.py:283-341`), which
I re-derived independently rather than trusting: applying the README's exact JSON recipe plus
`isolation: "worktree"` on a real task id (`task-impl`, confirmed to exist in the rendered template)
validates clean with a correctly-configured `merge-resolver`, and produces a real, non-vacuous V10
warning when the agent's `disallowed_tools` is empty. One theoretical alternative exists — a
`when:`-gated second `files:` entry targeting the same `workflow.json` path could produce a truly
live, param-selected block — but it would require maintaining a near-duplicate ~467-line
`workflow.json.tmpl`, is not required by any AC, and would be scope creep beyond "opt-in stays opt-in
by design." Not a must-fix; note as a possible future ergonomics improvement only.

**Deviation (b) — `.ao/hotspots.json` declared as a prose-only input in `07-task-breakdown.md`, never
a `workflow.json` `inputs:` entry.** **Accept.** Independently verified against `engine.py:540-578`:
the missing-inputs gate has no generic "optional input" flag; the only relaxation is `join="any"`'s
producer-liveness check, which requires the input to have a producing task in the graph — an
externally-supplied file like `.ao/hotspots.json` (produced by `ao hotspots`, outside this workflow)
has none, so a hard `inputs:` entry would hard-fail `task-breakdown` — and therefore the entire
run — for any workspace that has not first run `ao hotspots`. The declared shape
(`version`/`generated_at`/`window_days`/`repos`) also matches `isolation/hotspots.py`'s `Hotspots`
model exactly (cross-checked directly, read-only). Correct call.

## Walked dimensions with nothing further to flag

SOLID/KISS/DRY: N/A at code level (docs/template content); no meaningful duplication introduced
(push-directive wording, branch-check wording, and completion-checklist blocks are copy-pattern
across instruction files by design, matching the pre-existing per-stage instruction convention — not
new duplication this ticket introduced). Magic literals: none found in the new/changed content beyond
the pre-existing per-file conventions (budget thresholds, timeouts are untouched). Pluggability:
N/A — no executor/scheduler/storage seam touched. Determinism/resume safety: N/A — no
stochastic/time-driven logic; template rendering is pure string substitution, already deterministic.
Errors/logging: N/A — no runtime code paths in scope. Testability: tests are asset/regex-based against
rendered content (not brittle full-string matches — normalized-whitespace substring/phrase checks
throughout) plus two real `CliRunner` end-to-end renders; good seam choices. Concurrency/rollout: N/A
for this ticket; schema fields are additive per upstream `T-Sc7Rm2` (confirmed present in
`specs/workflow.schema.json`).

## Testing notes

- **Mock**: nothing new needs mocking — `FakeExecutor` (already used) is the right seam for AC-2's
  general-instructions test.
- **Integration-test** (recommended addition): assert the five push-task ids
  (`bug-push`/`epic-push`/`task-push`/`doc-push`/`testing-push`) resolve to `isolation: "none"` in the
  rendered `workflow.json.tmpl` once C-1's fix lands — same shape as the existing
  `test_workflow_json_tmpl_git_branch_off_pinned_isolation_none`.
- **Coverage gap**: no test today renders the template with `defaults.isolation: "worktree"` and
  checks which tasks actually resolve to worktree isolation — that's exactly the gap C-1 exploits.
  Once fixed, add a `resolve_task_isolation`-level test over every static task id in the template
  (not just the two currently checked) asserting only the intended fan-out ids ever resolve to
  `"worktree"` under that override.
