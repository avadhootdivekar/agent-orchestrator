# TASK: T-7592ux-playground-scaffold-gating

## Metadata
- Task ID: `T-7592ux-playground-scaffold-gating`
- Epic ID: `E-dvehbb-e2e-playground-tests`
- Owner: developer
- Created: 2026-07-01
- Last Updated: 2026-07-01
- Status: Done
- Estimate: `< 2 days`
- MVP: **yes**

## Requirements Mapping
- FR-1, FR-8, NFR-1, NFR-3, NFR-4, NFR-5, NFR-6.

## Description
Lay the foundation shared by every playground example and every test tier:
1. Create the top-level `playground/` directory with a `README.md` describing the
   corpus, the four tiers, and how to run them (`uv run pytest -q` for the always-green
   tiers; `AO_E2E_REAL_LLM=1 uv run pytest -m real_llm` for the gated tier).
2. Register test markers in `pyproject.toml` `[tool.pytest.ini_options]`:
   `real_llm`, `e2e`, `perf` (with one-line descriptions) so `--strict-markers`-clean.
3. Add `tests/playground/conftest.py` implementing the gate: a
   `pytest_collection_modifyitems` hook that attaches a `skip` marker to every
   `real_llm` item unless `os.environ.get("AO_E2E_REAL_LLM") == "1"`.
4. Add shared harness primitives in `tests/playground/_playground.py` (or the
   conftest): `copy_example(example_dir, tmp_path)` (copies a `playground/<ex>/`
   into `tmp_path`), `run_cli(args, tmp_path)` (wraps `CliRunner().invoke(app, ...,
   env={"AO_WORKSPACE_ROOT": str(tmp_path)})`), and `agents_for(tier)` returning the
   fake vs claude agents-file name.

No example content is authored here (that is T-1vuzyi); this task delivers only the
directory, gating, and reusable harness so downstream tasks plug in.

## Inputs / Outputs
- Inputs: existing `tests/conftest.py` patterns, `tests/test_e2e_cli.py`
  (`_copy_examples_with_fake_agents`, `CliRunner` usage), `pyproject.toml`.
- Outputs: `playground/README.md`; `pyproject.toml` marker block; `tests/playground/__init__.py`,
  `tests/playground/conftest.py`, `tests/playground/_playground.py`.

## Acceptance Criteria
1. Given a fresh checkout, When `uv run pytest -q` runs, Then collection is
   `--strict-markers`-clean (no "unknown mark" warnings) and no `real_llm` test runs.
2. Given `AO_E2E_REAL_LLM` unset, When a dummy test marked `@pytest.mark.real_llm`
   is collected, Then it reports `skipped` with reason mentioning `AO_E2E_REAL_LLM`.
3. Given `AO_E2E_REAL_LLM=1`, When the same dummy test is collected, Then it is NOT
   auto-skipped by the gate (it may still fail/pass on its own merits).
4. `playground/README.md` documents the corpus layout, the four tiers, and the exact
   commands to run each tier with `uv run`.
5. `copy_example(...)` copies an example dir into `tmp_path` and `run_cli(...)` invokes
   the real `ao` CLI app via `CliRunner` with `AO_WORKSPACE_ROOT` set (proven by a
   trivial harness self-test that runs `ao --help` or `ao validate` on a stub).
6. No file under `src/` is modified (NFR-1). `ruff`/`mypy` clean on new test files.

## Risks
- Marker gate could accidentally skip non-real tests → scope the hook to items that
  carry the `real_llm` marker only; unit-test the gate with a dummy marked/unmarked pair.
- `--strict-markers` may already be off; still register markers to avoid future churn.

## Dependencies
- None (root of the epic).

## Pseudocode / Algorithm
```text
# tests/playground/conftest.py
def pytest_collection_modifyitems(config, items):
    if os.environ.get("AO_E2E_REAL_LLM") == "1":
        return
    skip = pytest.mark.skip(reason="real_llm tier disabled; set AO_E2E_REAL_LLM=1")
    for item in items:
        if "real_llm" in item.keywords:
            item.add_marker(skip)

# tests/playground/_playground.py
def copy_example(name, tmp_path):
    src = REPO_ROOT / "playground" / name
    shutil.copytree(src, tmp_path, dirs_exist_ok=True)   # workflow.json, instructions/, reposet.json, agents.*.json, fixtures/
    return tmp_path
def run_cli(args, tmp_path):
    return CliRunner().invoke(app, args, env={"AO_WORKSPACE_ROOT": str(tmp_path)})
def agents_for(tier):  # "fake" | "real"
    return "agents.fake.json" if tier == "fake" else "agents.claude.json"
```

## Schemas / Interface Notes
- Interface / API: `pyproject.toml` markers; pytest hook; harness helpers (test-only).
- Spec / data schema: none authored here.
- Triggers / events: N/A.
- Artifacts: `playground/` root, `tests/playground/` harness.

## Handoff Boundary
- Upstream: none.
- Downstream: T-1vuzyi (authors the first example into `playground/`), T-ee8hzo/T-r21p4y
  (consume `copy_example`/`run_cli`/`agents_for` and the gate).

## Artifacts
- Docs/comments: `ad/tickets/E-dvehbb-e2e-playground-tests/T-7592ux-playground-scaffold-gating/`
- Large outputs: none.
