# STATUS

- ID: `T-Wp4Nz5-workspace-provider-seam`
- Updated At: 2026-07-22
- State: Done
- Owner: developer agent

## This update
- By: developer agent / Role: developer / Date: 2026-07-22
- Comment: Implemented the `WorkspaceProvider` ABC + `WORKSPACE_PROVIDER_REGISTRY`,
  moved the existing fixture-copy logic verbatim into a `FixtureProvider` (registered as
  the `"fixture"` default), added the optional task-level `source` field (+ `Source`
  pydantic model, `KNOWN_WORKSPACE_PROVIDER_TYPES` closed list) to `bench/spec.py`, and
  relaxed `benchmark-suite.schema.json` so `fixture` is optional when `source` is
  present. `materialize_workspace`'s public signature/return (`RunContext`) is
  unchanged, so `runner.py` needed zero edits (confirmed read-only, untouched).

## Evidence (AC-by-AC)
1. **Fixture behavior byte-identical**: the entire pre-existing `tests/bench/test_workspace.py`
   suite (17 tests) passes unedited against the refactored code — `FixtureProvider.prepare`
   is the original copytree/guard logic moved verbatim, called from `materialize_workspace`
   in the same order (fixture-check-and-copy, then instruction-check-and-copy, then
   `capture/`) as before the refactor.
2. `WORKSPACE_PROVIDER_REGISTRY["fixture"] is FixtureProvider` at import time
   (`test_workspace_provider_registry_contains_fixture_at_import_time`,
   `test_workspace_provider_registry_populated_on_import`); `register_workspace_provider`
   raises `ValueError` on duplicate (`test_register_workspace_provider_duplicate_raises`),
   mirroring `register_subject`/`register_grader`.
3. A task with `source: {type: "unknown"}` → `load_suite` raises `SpecValidationError`
   naming the task id and listing `KNOWN_WORKSPACE_PROVIDER_TYPES`
   (`test_load_suite_unknown_source_type_rejected`); the JSON schema itself also rejects a
   `source` object missing `type` before the Python-level check even runs
   (`test_load_suite_source_missing_type_rejected_by_schema`).
4. A task with `source` present and no `fixture` loads OK
   (`test_load_suite_source_present_no_fixture_ok`); a task with neither raises
   `SpecValidationError` naming "must declare either 'fixture' or 'source'"
   (`test_load_suite_neither_fixture_nor_source_rejected`).
5. `WorkspaceProvider.prepare(task, repo_dir, *, suite_base_dir, subject_base_dir)` is the
   ABC contract (matches the literal signature in TASK.md's "Schemas / Interface Notes");
   `FixtureProvider.prepare` reproduces the copytree + guard behavior
   (AC1 evidence above); the sandbox guard (`_assert_under_bench_root`) fires before any
   provider is even resolved, proven for a non-fixture provider too
   (`test_materialize_workspace_sandbox_guard_applies_before_provider_dispatch` — a
   registered dummy provider's `.calls` list stays empty after the escape is rejected).
   Dispatch to a declared non-default provider is proven end-to-end
   (`test_materialize_workspace_dispatches_to_declared_source_provider`); an unregistered
   `source.type` reaching `materialize_workspace` directly (bypassing `load_suite`) raises
   a typed `SubjectError`, not a `KeyError`
   (`test_materialize_workspace_unregistered_provider_type_raises`).
6. SI-1 unaffected: `grep -rn "import.*bench" src/agent_orchestrator --include="*.py" | grep -v "^src/agent_orchestrator/bench/"` → no hits; `import agent_orchestrator; import agent_orchestrator.cli` succeeds standalone.

## Test run (actual numbers)
- `uv run pytest tests/bench -q --ignore=tests/bench/test_ao_epic_plus_subject.py --ignore=tests/bench/test_runner.py --ignore=tests/bench/test_cli_bench.py --ignore=tests/bench/test_budget.py` → **235 passed, 1 skipped** (was 216 passed, 1 skipped before this task's new tests — +19 net new, zero regressions; two other in-flight tasks landed tests concurrently in this same run, all green).
- `uv run ao-bench validate --suite benchmarks/suites/dev-core/suite.json` → `OK`; `git diff --stat benchmarks/suites/dev-core` → empty (byte-unchanged, per epic invariant).
- `ruff check` / `ruff format --check` / `mypy` clean on all 6 touched files (`workspace.py`, `spec.py`, `registries.py`, `benchmark-suite.schema.json`, `test_workspace.py`, `test_spec.py`, `test_registries.py`); `errors.py` untouched (no new error type was needed — `SubjectError`/`SpecValidationError` already covered every case).

## Risks / Blockers
- None outstanding for this task. Forward risk for T-Sw5Hd9: a real checkout provider
  will need its own timeout/cleanup discipline inside `prepare()` — the ABC contract
  doesn't impose one (see Next actions below).
- Pre-existing, out-of-scope: widening `BenchTask.fixture` to `str | None` makes
  `tests/bench/test_dev_core_suite.py:334` (`base / task.fixture`) mypy-flag as
  `str | None` under `mypy src tests/bench` (not CI's `mypy src`-only gate, so this does
  not fail CI). That file is not in this task's ownership list; flagged here rather than
  edited.

## Next actions (forward notes for T-Sw5Hd9 / T-Sg6Jf2)
1. Provider ABC signature to implement: `prepare(self, task: BenchTask, repo_dir: Path, *, suite_base_dir: Path, subject_base_dir: Path | None = None) -> None`. `repo_dir` does NOT exist yet when called — the provider creates it (fixture provider uses `shutil.copytree`; a `swebench` provider would `git clone`/`checkout` into it directly).
2. Registration: add a `SwebenchProvider(WorkspaceProvider)` class in a new module (or `workspace.py` if small), call `register_workspace_provider("swebench", SwebenchProvider)` at that module's import time, and add `"swebench"` to `bench/spec.py`'s `KNOWN_WORKSPACE_PROVIDER_TYPES` frozenset — both steps are required together (the repo learning noted in this task's docstrings: registering a class without extending the closed list still gets rejected by `load_suite`).
3. A provider reads task-specific config off `task.source` (a `Source` pydantic model with `type: str` plus open `extra="allow"` fields — e.g. `task.source.instance_id`/`.dataset`/`.revision` for swebench are NOT modeled in `bench/spec.py`; access them via `task.source.model_extra` or add typed fields to a `SwebenchProvider`-local subclass/parser). `suite_base_dir` is available for any suite-relative paths the provider itself might need (e.g. a pinned instances.json); `subject_base_dir` is passed through but unused by every provider today.
4. Checkout caching hook point: inside `prepare()`, before the `git` checkout — e.g. clone/fetch once into a shared cache dir under `BENCH_WORKSPACE_ROOT`'s sibling (NOT inside the per-run `ws/repo` itself, which is torn down and recreated every run) and `git worktree add`/local-clone from the cache into `repo_dir`. This keeps `materialize_workspace`'s per-run guarantees (fresh `ws`, guarded sandbox) while avoiding a network fetch per task run.
