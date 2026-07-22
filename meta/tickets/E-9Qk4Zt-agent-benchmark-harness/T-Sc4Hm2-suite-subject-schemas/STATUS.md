# STATUS

- ID: `T-Sc4Hm2-suite-subject-schemas`
- Updated At: 2026-07-22
- State: Done
- Owner: developer agent

## This update
- By: Claude · Role: developer · Date: 2026-07-22
- Comment: Implemented the foundation `bench/` module: both versioned
  `additionalProperties:false` JSON schemas, the pydantic models + `load_suite`/
  `load_subject` loader/validator, the empty `SUBJECT_REGISTRY`/`GRADER_REGISTRY` +
  register helpers, the bench error hierarchy, and a minimal `ao-bench validate` Typer
  app (no `[project.scripts]` entry — that is `T-Cli8Nf`'s job; tests drive it via
  `CliRunner`). Core (`src/agent_orchestrator/` outside `bench/`, `specs/`,
  `pyproject.toml`) is byte-unchanged — grep-verified, see Evidence.
- **Deviation from `TASK.md`'s deliverable list (explicitly scoped by the assigning
  message, not a guess):** `TASK.md` lists a separate `models.py` (`BenchSuite`,
  `BenchTask`, `SubjectSpec`, `GraderConfig`, `Assertion`) alongside `spec.py`
  (`load_suite`, `load_subject`). The assigning orchestrator's explicit "files you own"
  scope instead directed `spec.py` to contain "pydantic models + loader + validation"
  as one module, and listed no `models.py`. Followed the explicit scope: all five
  pydantic models live in `bench/spec.py` (plus `BenchSuiteDefaults`, an undocumented
  but necessary nested model for `suite.defaults`). No `models.py` file exists. This
  does not affect any acceptance criterion (none reference a `models.py` import path)
  and is a pure file-layout choice — `T-Sbj9Ka`/`T-Grd7Vx` can still `from .spec import
  BenchSuite, BenchTask, SubjectSpec, GraderConfig, Assertion` unchanged.
- **Filled an underspecified gap:** the design doc's §6 interface table lists
  `assertions?:[Assertion]` without spelling out `Assertion`'s own fields. Defined it
  as `type: Literal["exists","contains","equals_file"] · path:str · substring:str|None
  · golden:str|None`, matching the `FileAssertionGrader` pseudocode's `exists|contains|
  equals_file` comment (§4.3) and the §4.3 edge case "`equals_file` golden missing →
  structured error at load time (§4.1), not at grade time" — implemented as a
  load-time check in `load_suite` (`_check_assertions`), covered by 4 dedicated tests.
  `file_assertion`/`FileAssertionGrader` itself is not implemented this task (`T-Grd7Vx`
  owns it); this is schema/model shape only, forward-compatible.
- **AC1 exit-0 output is `"suite '...': N task(s)"` / `"subject '...': type='...'"` lines
  followed by a final `"OK"` line** (not a bare `"OK"`) — chosen for parity with the
  core `ao validate` command's terse informative style; AC1 only requires `"OK"` to
  appear in stdout on exit 0, which it does (tests assert `"OK" in result.output`).

## Evidence
- Files added:
  - `benchmarks/schemas/benchmark-suite.schema.json`, `benchmarks/schemas/subject.schema.json`
  - `src/agent_orchestrator/bench/{__init__.py, errors.py, registries.py, spec.py, cli.py}`
  - `tests/bench/{__init__.py, conftest.py, test_spec.py, test_cli_validate.py, test_registries.py}`
- `uv run pytest tests/bench -q` → **48 passed**.
- `uv run pytest -q -m "not real_llm"` → **905 passed, 3 deselected** (baseline before this
  task, on the same branch pre-change, was 857 passed/3 deselected — net +48, **zero
  regressions**; every pre-existing test file is untouched).
- `uv run ruff check .` → clean on every file this task touched
  (`src/agent_orchestrator/bench/*`, `tests/bench/*`, `benchmarks/schemas/*`); the only
  remaining repo-wide hits are the 2 pre-existing `tests/test_e2e_cli.py` errors
  (confirmed untouched by this task, unrelated file).
- `uv run ruff format --check .` → all files clean (one bench test file was
  auto-reformatted by `ruff format` during this task, no logic change).
- `uv run mypy src/agent_orchestrator/bench` → **Success: no issues found in 5 source
  files**.
- `uv run mypy src` → clean except the 4 pre-existing `src/agent_orchestrator/_version.py`
  errors, confirmed present identically on the pre-task tree via `git stash` (unrelated,
  untouched file, not part of this task's scope).
- NFR-1 grep verification: `git status --porcelain` shows only new files under
  `benchmarks/`, `src/agent_orchestrator/bench/`, `tests/bench/` (plus one pre-existing
  unrelated untracked doc from a prior task); `git diff --stat` against tracked files is
  empty (no core file modified); `grep -rF "bench" src/agent_orchestrator --include=*.py
  -l` matches only files under `src/agent_orchestrator/bench/` itself — nothing in core
  imports `bench` (SI-1 held).

## Acceptance criteria verification
1. **AC1** (valid suite, unique lowercase ids, existing paths → exit 0 "OK"):
   `test_validate_suite_ok`, `test_load_suite_valid`, `test_load_suite_multiple_tasks_unique_ids`.
2. **AC2** (duplicate id / uppercase id / missing fixture / unknown grader.type / missing
   version → exit non-zero, distinct named error per case): `test_validate_suite_duplicate_task_id_fails`,
   `test_validate_suite_unknown_grader_type_fails`, `test_validate_suite_missing_fixture_fails`,
   `test_validate_suite_missing_version_fails`, `test_validate_suite_uppercase_id_fails` (CLI) +
   the matching `test_load_suite_*` unit tests (also covering missing instruction file, unknown
   top-level/task-level/grader-level keys, malformed JSON/YAML, missing file, non-object
   top-level, and the `file_assertion` assertion edge cases).
3. **AC3** (subject with unknown `type` → exit non-zero, lists known subject types):
   `test_validate_subject_unknown_type_lists_known_types` (asserts `claude_cli`,
   `ao_workflow`, `fake` all appear in the CLI error output) + `test_load_subject_unknown_type_rejected`.
4. **AC4** (`additionalProperties:false` at every object level + required `version`; unknown
   key → validation error): both schema files have `additionalProperties: false` on every
   object (`$defs.task`, `$defs.grader`, `$defs.assertion`, `$defs.fake_tokens` inline object
   in `subject.schema.json`, plus both root objects and `suite.defaults`); `version` is in both
   roots' `required` list. `test_load_suite_unknown_top_level_key_rejected`,
   `test_load_suite_unknown_task_level_key_rejected`, `test_load_suite_unknown_grader_level_key_rejected`,
   `test_load_subject_unknown_key_rejected`, `test_load_suite_missing_version_rejected`,
   `test_load_subject_missing_version_rejected`.
5. **AC5** (`mypy`/`ruff` clean; no file outside `bench/`/`benchmarks/`/`pyproject.toml`
   touched): see Evidence above — `pyproject.toml` was in fact NOT touched (no
   `[project.scripts]` entry added per the assigning message's explicit instruction,
   since `typer` was already a declared dependency and no new dependency was needed).

## `claude --version` probe (ASSUMPTION A1)
Implemented as a **non-fatal WARNING**, not a validate failure: `ao-bench validate --subject`
for a `claude_cli`-typed subject shells out to `claude --version` (5s bounded timeout) and
prints its output, but a missing/broken binary only prints `WARNING: ...` to stderr — it never
flips the exit code. Rationale (recorded, not silently assumed): spec-level validation
correctness is orthogonal to whether the *local* environment happens to have `claude` on PATH
(e.g. CI, which per the design doc §13 only ever exercises `FakeSubject`); a hard-fail here
would make `ao-bench validate` flaky in exactly the environments most likely to run it.
Verified by `test_validate_claude_cli_subject_probes_claude_version`,
`test_validate_claude_cli_subject_probe_missing_binary_warns_not_fails` (mocked
`subprocess.run`, never a real process spawn), and `test_validate_fake_subject_does_not_probe_claude`.

## Risks / Blockers
- None blocking handoff. Forward note for `T-Sbj9Ka`/`T-Grd7Vx`: `KNOWN_SUBJECT_TYPES`/
  `KNOWN_GRADER_TYPES` in `bench/spec.py` are the closed lists `load_suite`/`load_subject`
  validate against (registries are intentionally empty this task); those tasks should extend
  these two frozensets alongside registering their concrete classes into
  `SUBJECT_REGISTRY`/`GRADER_REGISTRY`, per `spec.py`'s module docstring and
  `registries.py`'s module docstring.
- `file_assertion`'s `Assertion` model shape (see "Filled an underspecified gap" above) was
  authored here ahead of `T-Grd7Vx`'s `FileAssertionGrader`; if that task's grading semantics
  need a different assertion shape, `spec.py`'s `Assertion`/`_check_assertions` and
  `benchmark-suite.schema.json`'s `$defs.assertion` are the two places to revisit together.

## Next actions
1. Handed off to `T-Sbj9Ka-subject-adapters` (Subject ABC + registry, consumes
   `SubjectSpec`/`load_subject`) and `T-Grd7Vx-grader-registry` (Grader ABC + registry,
   consumes `GraderConfig`/`Assertion`/`load_suite`) — both unblocked, no further work
   needed on this task.
