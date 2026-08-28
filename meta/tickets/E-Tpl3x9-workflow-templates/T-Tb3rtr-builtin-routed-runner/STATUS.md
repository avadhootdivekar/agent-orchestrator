# STATUS

- ID: `T-Tb3rtr-builtin-routed-runner`
- Updated At: `2026-07-24`
- State: `Done`
- Owner: `claude`

## This update
- Implemented per TASK.md: built-in `routed-runner` template under
  `src/agent_orchestrator/templates/builtin/routed-runner/` — `template.yaml` (HLD
  §2.2 shape), `workflow.json.tmpl` (faithful transcription of the finplan
  `new-epic-run.sh` generator's DAG/routes/breakers, with only the specified deltas:
  `{{ instance_dir }}`-relative paths, `workflows/routed-runner/instructions/...`
  instruction paths, `{{ params.repo_set }}`, bare-numeric budget breaker thresholds,
  `{{ id }}`/name, and an added top-level `prompt_path`), `prompt.md.tmpl`,
  `breakdown-contract.md.tmpl`, all 31 instruction files (FinPlan/stack/multi-tenancy
  specifics generalized, contracts preserved verbatim), and a `README.md` carrying
  over the route table + breaker rationale from the finplan README/DESIGN doc.
- Packaging: verified `uv build --wheel` ships the full 36-file tree with **zero**
  `pyproject.toml` changes (hatchling's default file selection already includes it) —
  no explicit `artifacts` entry added, per the "only if actually needed" instruction.
- Test: `tests/test_builtin_routed_runner_assets.py` (16 tests) — manifest shape,
  file-reference existence, template-token allow-list, JSON-Schema-valid rendered
  workflow.json with the exact 30-task-id set, and a FinPlan-reference sweep. Does
  not import `agent_orchestrator.templates` (owned by concurrent T-Tc0r3a).

## Evidence
- `uv run pytest -q tests/test_builtin_routed_runner_assets.py` → 16 passed.
- `uv run pytest -q` (full suite) → 1596 passed, 7 skipped (pre-existing, unrelated),
  0 regressions.
- `uv run ruff check` / `ruff format --check` / `uv run mypy` on the touched test
  file → clean.
- `uv build --wheel` then `python3 -m zipfile -l` → all 36 files under
  `templates/builtin/routed-runner/` present in the wheel, byte-for-byte matching the
  on-disk set.
- Extra confidence check (not part of the committed suite, since `ao new` doesn't
  exist yet): rendered `workflow.json.tmpl` with dummy values, validated against
  `specs/workflow.schema.json` + `WorkflowSpec` pydantic model, and ran the real
  `ao validate` CLI end-to-end (via `CliRunner`) against a temp workspace with a fake
  reposet/10-agent registry — exit code 0, `OK: all specs valid`.

## Risks / Blockers
- None. Genericization decisions (kept "epic"-branch naming scheme across all routes;
  removed FinPlan/stack/multi-tenancy/billing specifics; instructions describe repo
  paths generically via "the target repository ... see your prompt's Repos line")
  are documented in the final handoff message for T-Te5rev's cross-cutting review.

## Next actions
1. None for this task. Downstream: T-Tc0r3a's `discover_templates`/`instantiate` will
   exercise this tree at runtime; T-Te5rev covers the cross-cutting e2e review.
