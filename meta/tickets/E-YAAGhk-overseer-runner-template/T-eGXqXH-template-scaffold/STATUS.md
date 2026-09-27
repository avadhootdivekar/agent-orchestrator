# STATUS

- ID: `T-eGXqXH-template-scaffold`
- Updated At: 2026-09-26
- State: Done
- Owner: developer

## This update
- Created the template skeleton under `src/agent_orchestrator/templates/builtin/overseer-runner/`:
  `template.yaml` (16 params exactly per HLD §13.1, `repo_set` required, enums on `final_push`/
  `overseer_effort`), `workflow.json.tmpl` (2 static tasks `git-branch-off`/`intake`, 6 hooks,
  11 circuit breakers, all matching HLD §13.2 exactly), `overseer-config.json.tmpl` (schema
  `ao.overseer.config/v1`, every field `load_config` reads plus the full 10-entry `kind_map` per
  HLD §13.4), `prompt.md.tmpl` (Asks / What usable means to me / Constraints / Budget notes / Out
  of scope, per HLD §17). `template.yaml` adds a `files` entry for the already-existing
  `tools/overseer_tool.py` (T-ABDjSj/T-C6uQJW) without touching its content.
- New `tests/test_builtin_overseer_runner_assets.py`, 28 tests covering AC1-9.
- Since `instructions/` doesn't exist yet (T-5ZzAZp), a full `ao new overseer-runner` /
  `instantiate()` can't be exercised end to end at this point in the epic — verified this by
  actually attempting it and confirming it fails at the `assets` step. Render-level tests instead
  call the real, private `_render` directly per `.tmpl` file, then feed the result through the
  real `load_workflow` + `cross_validate` + `build_dag` + `validate_run_control` pipeline (the same
  one `ao validate` runs) — the closest faithful proxy available at this point.

By: developer · Role: developer · Date: 2026-09-26 · Comment: All 9 acceptance criteria verified
with real command output. `pytest tests/test_builtin_overseer_runner_assets.py -q` → 28 passed.
`routed-runner`'s own suites re-run unmodified: 122 passed. `ruff`/`ruff format --check` clean.
`mypy src` clean (no new `.py` source files added by this task — only YAML/JSON-template/markdown
files plus one test file). Judgment calls: (1) no `overseer-contract.md.tmpl` created (per explicit
dev-epic scope override of the ticket's own "a stub is OK" wording) — see the reviewer's Warning
#1 below for how this was resolved; (2) verified AC8's packaging claim empirically by running
`uv build --wheel` twice (before/after, with the new files untracked) and inspecting the wheel's
`zipfile.namelist()` directly, not just reading `hatch_build.py`'s comments; (3) AC6's "no
half-written instance on failure" was investigated directly against `instantiate()`/`cli.new_cmd`
and found to be a pre-existing, generic engine gap (no rollback path exists for ANY template) —
see the reviewer's Warning #2.

By: dev-epic · Role: manager · Date: 2026-09-26 · Comment: Independently re-verified: read all 4
new template files in full against HLD §13.1/13.2/13.4 line-by-line (all matched exactly), re-ran
`pytest tests/test_builtin_overseer_runner_assets.py -q` (28 passed), and — once the sibling
T-C6uQJW task also landed on the shared `overseer_tool.py` — ran the combined full suite:
**4190 passed, 8 skipped, 0 failed**.

By: reviewer · Role: reviewer · Date: 2026-09-26 · Comment: **Verdict: approve with nits, no
blocking issues.** Independently reproduced every one of dev-epic's unverified claims: confirmed
the YAML param-spec field names against the real `_ParamSpec`/`_FileEntry`/`_AssetEntry` pydantic
models (`extra="forbid"`, so a wrong field name would fail loudly — verified `load_template()` is
genuinely exercised, not a bare `yaml.safe_load`); read `instantiate()`/`cli.new_cmd` directly and
confirmed there truly is no rollback-on-failure path anywhere in the engine, for any template (not
a watered-down excuse); ran `uv build --wheel` independently and inspected the resulting wheel's
contents, confirming all 5 files present; traced `_render`/`_json_escape_value` by hand and
confirmed an unquoted `{{ params.final_push }}` really does render as a genuine JSON boolean
token, and that the corresponding test asserts `isinstance(value, bool)` precisely rather than a
string comparison; spot-checked test quality on 3 named tests (all genuine, non-tautological); and
re-ran `routed-runner`'s own suites unmodified (47 passed).
**Two Warnings (non-blocking, both resolved/tracked below):**
1. `template.yaml`'s `files:` list omits `overseer-contract.md.tmpl` (called for by HLD §13.1 and
   this ticket's own AC2, even as a stub), while `workflow.json.tmpl`'s `intake` task still lists
   `overseer-contract.md` as an input — a real, if currently harmless (rendering already fails
   earlier at the missing `instructions/` asset), spec-fidelity gap.
   **Resolution**: `T-ltBLUY` (the very next task, whose whole job is authoring
   `overseer-contract.md.tmpl`) is explicitly briefed to also add the `template.yaml` `files:`
   entry that references it, so the file and its registration land together and this gap closes
   in the next task rather than lingering unowned.
2. AC6 ("no half-written instance is left") is not actually true today anywhere in the engine —
   confirmed pre-existing and generic, not introduced by this task. **Resolution**: recorded as a
   known limitation in the epic's execution log
   (`docs-md/ai-epics/E-YAAGhk-overseer-runner-template.md`) as a candidate follow-up ticket for
   `templates.instantiate()` (stage writes atomically or track-and-rollback on failure), explicitly
   out of this narrowly-scoped epic's change boundary (core `templates/__init__.py` is shared
   across every builtin template, not something this epic touches without much broader
   justification).

## Evidence
- `.venv/bin/pytest -q tests/test_builtin_overseer_runner_assets.py` → `28 passed` (dev-epic and
  reviewer both independently re-ran this and got the same result).
- Full repo suite (after this task + sibling T-C6uQJW both landed): `.venv/bin/pytest -q` →
  `4190 passed, 8 skipped, 0 failed`.
- `.venv/bin/ruff check` + `.venv/bin/ruff format --check` on the 5 new files → clean.
- `uv build --wheel` (run independently by both the developer and the reviewer) → the built
  wheel's `namelist()` contains `template.yaml`, `workflow.json.tmpl`, `overseer-config.json.tmpl`,
  `prompt.md.tmpl`, `tools/overseer_tool.py`.
- `routed-runner` regression check: `tests/test_builtin_routed_runner_assets.py` +
  `tests/test_e2e_builtin_routed_runner.py` → `47 passed` (reviewer's independent re-run); the
  developer's own broader re-run (including `test_templates.py`/`test_e2e_cli_templates.py`) →
  `122 passed`.
- Code: `template.yaml`, `workflow.json.tmpl`, `overseer-config.json.tmpl`, `prompt.md.tmpl`.
- Tests: `tests/test_builtin_overseer_runner_assets.py`.

## Risks / Blockers
- None outstanding for this task. Both reviewer Warnings are tracked (see above) rather than
  silently absorbed: #1 handed to `T-ltBLUY` explicitly, #2 recorded as a known engine-wide gap in
  the epic execution log, out of this epic's narrow-change-scope.

## Next actions
1. None outstanding — done, reviewed (approve with nits, both warnings tracked/handed off).
2. Unblocks `T-ltBLUY` (contract + README, now also explicitly scoped to add the
   `overseer-contract.md.tmpl` `files:` entry) to proceed next.
