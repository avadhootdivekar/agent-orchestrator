# EPIC: E-Vt6Lp2-template-cost-hygiene

## Metadata
- Epic ID: `E-Vt6Lp2-template-cost-hygiene`
- Title: Consolidate cost/context-hygiene wisdom into the `routed-runner` template + `workflow-authoring` skill
- Owner: dev-epic
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: Done

## Summary
- Goal: This repo ships exactly one shared workflow template (`routed-runner`) and one
  workflow-authoring skill (Epic C, `E-DOiDqE`). The user's explicit mandate: "we are adding only
  one workflow too for software so let's consolidate and make that workflow as useful as
  possible" — cost/context-hygiene wisdom must live in ONE place (this template + this skill) so
  every workspace that instantiates `routed-runner` benefits, instead of each `ao-runner-*`
  workspace rediscovering the same lessons independently. Concretely: (1) give the template a
  mechanism to propagate recommended agent `command_template` hygiene — starting with
  `--autocompact` — to every workspace that runs `ao new` with it, and (2) extend the
  `workflow-authoring` skill with a practical "Cost & context hygiene" checklist section.
- Scope In: `routed-runner` template (`template.yaml`, a new templated asset, `README.md`),
  `.claude/skills/workflow-authoring/SKILL.md`, tests extending the existing
  `tests/test_templates.py` / `tests/test_e2e_builtin_routed_runner.py` /
  `tests/test_builtin_routed_runner_assets.py` / `tests/test_e2e_cli_templates.py` suites, a new
  design doc under `docs-md/`.
- Scope Out: engine/core code changes (`--autocompact` is an opaque CLI flag passed through
  `AgentSpec.command_template`/`extra_args` — confirmed via `executors/claude_cli.py` that argv is
  passed through unmodified, no engine parsing needed); a NEW second template (explicitly
  rejected — this is a consolidation epic, not an expansion); any write/edit to any sibling
  `ao-runner-*` workspace (read-only reference only, per hard boundary); a full `ao validate`
  lint/warning for missing `--autocompact` (evaluated, deliberately deferred to Non-MVP — see
  Requirements below and the design doc's "Alternatives considered").

## Requirements

### MVP (must-ship)
- FR-D1-1 (Functional): The `routed-runner` template ships a new `assets:` entry
  (`agents.recommended.json.tmpl` → workspace-root `agents.recommended.json`, `keep_existing:
  true`) that seeds a recommended `extra_args: ["--autocompact", "<threshold>"]` (NOT
  `command_template` — early-gate reviewer finding: `command_template` fully overrides the argv
  positional list `["claude","-p","{prompt}"]`, `extra_args` is the unconditionally-appended
  additive field per `executors/claude_cli.py:547-548`, exactly designed for a recommended
  add-on flag that must not clobber a workspace's own `command_template` customization) for the
  subset of the template's 11 `required_agents` that do long, multi-turn agentic work.
  Verification: extend `tests/test_builtin_routed_runner_assets.py` (manifest shape) +
  `tests/test_templates.py`-style instantiate assertions + a real `ao new` e2e test in
  `tests/test_e2e_builtin_routed_runner.py` proving the file renders AND that a second `ao new`
  respects `keep_existing` (never clobbers a workspace's own edits to the seed).
- FR-D1-2 (Functional): `routed-runner/README.md` documents the recommended `command_template`
  snippet, which roles it applies to (and why), and the mechanism (`agents.recommended.json`) a
  workspace uses to adopt it. Verification: a test asserting the README section exists with the
  expected content markers (mirroring `test_readme_documents_parallel_isolation_section`'s
  pattern).
- FR-D2-1 (Functional): `.claude/skills/workflow-authoring/SKILL.md` gains a new "Cost & context
  hygiene" section (checklist-style, matching the skill's existing "Quick decision guide" tone)
  covering: cache-hit-rate-is-a-ratio-not-a-cost-measure, `--autocompact` (pointing at FR-D1-1's
  mechanism), front-loading static reference content over agent-driven exploration, and
  package/context-boundary hygiene (scoped `CLAUDE.md`s). Verification: manual content-presence
  check (no existing automated test harness covers `SKILL.md` prose — same as Epic C's
  `T-buzXEz-author-skill`, confirmed via that task's own TASK.md), reported as evidence in
  STATUS.md with exact section text quoted.
- NFR-D1-1 (Non-functional, reliability): the new asset must not change the byte-identical,
  no-warnings rendering of the rest of the template with default params — no regression in any
  existing `tests/test_builtin_routed_runner_assets.py` / `tests/test_templates.py` /
  `tests/test_e2e_*` assertion.
- NFR-D1-2 (Non-functional, spec ergonomics): `agents.recommended.json` is valid, parseable JSON
  shaped like a real (partial) `agents.json` (`AgentSpec`-compatible fields) so a workspace can
  literally copy fields out of it — not prose describing the shape.
- NFR-D1-3 (Non-functional, operability): the `--autocompact` default value is chosen and
  justified from the real observed growth-curve numbers (232 turns, ~10K→~280K token growth,
  42.5M summed `cache_read_input_tokens`), not picked arbitrarily — recorded in the design doc,
  disclosed honestly in real dollars (confirmed via the `claude-api` skill: Sonnet 5 cache-read
  ≈$0.20/MTok, so 42.5M tokens ≈$8.50 on that one trajectory — a modest, not oversold, figure)
  and explicit about why this is framed as a context-hygiene/headroom lever that DOES fire on
  realistic complex-task trajectories (per the epic's own anchor-to-280K instruction), not a
  near-1M "safety net" that would barely ever trigger and reproduce Claude Code's own inadequate
  default.

### Non-MVP (deferred, with a stated later-validation method)
- NFR-D1-4: a real `ao validate` warning (new `V`-numbered rule or a parallel mechanism) when a
  workspace's `agents.json` entries referenced by `routed-runner`'s `required_agents` lack
  `--autocompact` in their `command_template`. Deferred because `spec.py`'s existing V1-V13 rules
  are a cohesive, isolation/integration-specific cross-validation module
  (`_cross_validate_isolation` / `validate_isolation_and_integration`) — bolting an unrelated
  "agents.json command_template lint" onto that module is exactly the kind of cross-cutting
  change CLAUDE.md's parallel-epics policy says to avoid unless the epic absolutely requires it,
  and this epic's MVP (the recommended asset + README) already delivers the propagation mechanism
  the epic exists for. Later validation method: a follow-up epic/ticket adds a small, standalone
  `agents.json` linter (CLI subcommand or `ao validate` extension point) that is NOT wedged inside
  the isolation-specific V-rule module; tracked as a new ticket if/when prioritized (see design
  doc "Alternatives considered").

### Stretch (nice-to-have, not required to ship)
- `ao new` CLI output message calling out `agents.recommended.json` was (re)generated / skipped,
  beyond the existing generic `created:`/`skipped:` list `ao new` already prints (the generic
  listing already surfaces this — a stretch item would be a more prominent, dedicated callout).

## Task List
- [x] `T-Hn4Rq8-agents-recommended-asset` — D1: `agents.recommended.json.tmpl` asset,
  `template.yaml` wiring, README section, extended tests. Done.
- [x] `T-Kd2Wp5-skill-cost-hygiene-section` — D2: "Cost & context hygiene" section in
  `workflow-authoring/SKILL.md`. Done.
- [x] `T-Zb8Fx3-design-doc-and-e2e-evidence` — D3: design doc under `docs-md/` (mechanism choice +
  `--autocompact` default justification), real `ao new` scratch-workspace evidence, full suite
  regression run. Done — also found and closed a pre-existing, undeclared NFR-2 gate gap
  unrelated to this epic's own changes (see `docs-md/template-cost-hygiene-hld.md` §7).

## Risks and Dependencies
- Depends on Epics A/B/C already landed on this branch (`git log b0cb467..HEAD`) — this epic reads
  `docs-md/cost-caching-optimization-hld.md` (Epic B) and
  `.claude/skills/workflow-authoring/SKILL.md` (Epic C) as inputs, extends both without
  duplicating their content.
- Risk: `--autocompact` default is an informed estimate from partial real data (we have the
  aggregate 232-turn/280K-token/42.5M-cache-read numbers, not the full per-turn transcript) — the
  design doc discloses this as an estimate, not a measured optimum, and recommends the value stays
  workspace-overridable (it lives in a copy-paste seed file, never enforced).
- Risk: engine trust in `--autocompact` itself (does Claude Code's compaction summary preserve
  enough fidelity for a long agentic task to keep succeeding) is OUT of this epic's scope to
  validate — this epic propagates the flag's *availability* as a documented lever; a workspace
  adopting it should watch its own task success rate, per the skill's own framing.

## Early-gate review — outcome
- By: dev-epic · Role: manager · Date: 2026-09-21
- Comment: Ran `architect` + `reviewer` early-gate passes in parallel on the concrete D1
  mechanism (asset-based `agents.recommended.json` seed). Both returned GO-WITH-CHANGES.
  Reconciled as follows (full reasoning also in `docs-md/template-cost-hygiene-hld.md`):
  - **Adopted (both/either agreed or one found a real bug):** `extra_args` instead of
    `command_template` for `--autocompact` (reviewer's finding — `command_template` fully
    overrides argv, `extra_args` is the additive field the engine already appends
    unconditionally; a real correctness bug in the original proposal, not a style choice).
    Seed's top-level shape mirrors `specs/examples/agents.json` (`version`+`agents`).
    `manager` stays included; `merge-resolver`'s exclusion rationale sharpened to cite its
    security/unreviewed-content posture (S-2) alongside "short-lived." README gets a one-line
    drift-check grep recipe as the cheap Non-MVP substitute for a real `ao validate` warning.
  - **Diverged, with reasoning (reviewers disagreed with each other; I made the call):**
    Kept `keep_existing: true` (per the epic prompt's own explicit instruction, and the
    reviewer's "accept for MVP" assessment) rather than the architect's `keep_existing: false`
    — mitigated the architect's real staleness concern with an in-file version marker + a
    documented "delete and re-run `ao new`" refresh path instead. Kept workspace-root
    placement (`agents.recommended.json`) over the architect's suggested
    `workflows/routed-runner/` nesting — the reviewer's discoverability argument (sits beside
    the real, path-configurable `agents.json`) is concrete and grounded in actual `cli.py`
    agent-resolution behavior (no auto-discovery either way, so no functional collision risk).
    Kept the `--autocompact` default framed as a context-hygiene lever that fires on realistic
    complex-task trajectories (200,000) rather than the architect's suggested near-280K
    "safety net" framing — a safety-net threshold would barely ever trigger on the exact
    trajectory the epic asked to anchor against, reproducing the inadequate status quo instead
    of fixing it; the dollar magnitude is disclosed honestly (~$8.50 on the one real example)
    so the lever isn't oversold as a large cost saver.

## Closure summary
- By: dev-epic · Role: manager · Date: 2026-09-21
- Comment: All 3 tasks Done with evidence (see each `STATUS.md`). Final full-suite run:
  **3977 passed, 1 skipped, 7 deselected, 0 failed** (`.venv/bin/python -m pytest -q -m "not
  real_llm and not swebench"`). `ruff check`/`ruff format --check` clean and `mypy` error count
  unchanged from pre-epic baseline (confirmed via clean content-swap comparisons, not
  assumption) on every one of this epic's 3 touched Python files. Epic's entire Python footprint
  confirmed via `git diff --stat 7114e37..HEAD -- '*.py'`: exactly `tests/
  test_builtin_routed_runner_assets.py`, `tests/test_e2e_builtin_routed_runner.py`, `tests/
  test_nfr2_regression_gate.py` — no engine/core/spec/schema code touched, honoring the
  change-scope boundary. Did not read, write, or run any git command in any `ao-runner-*`
  sibling workspace.

## Links
- Design doc: `docs-md/template-cost-hygiene-hld.md`
- Prior art: `docs-md/cost-caching-optimization-hld.md` (Epic B), `docs-md/workflow-templates-hld.md`
  (Epic/`E-Tpl3x9`), `.claude/skills/workflow-authoring/SKILL.md` (Epic C)
- Output artifacts (if any): N/A — no `output/` artifacts, everything lands as source/docs.
