# STATUS

- ID: `T-Tu1api-ui-endpoints`
- Updated At: `2026-07-24`
- State: `Done`
- Owner: `claude`

## This update
- By: claude · Role: developer · Date: 2026-07-24 · Comment: Implemented `DashboardService.list_templates`/`create_instance`
  (`src/agent_orchestrator/ui/service.py`) and `GET /api/templates` /
  `POST /api/templates/{name}/instances` (`src/agent_orchestrator/ui/app.py`), thin-adapter
  split preserved (service is framework-free; app.py maps exceptions to status codes only).
  Added `tests/ui/test_templates_api.py` (23 tests: service-level + FastAPI `TestClient`)
  and a `write_template` fixture helper in `tests/ui/conftest.py`.

## Evidence
- `uv run pytest -q tests/ui/test_templates_api.py` → 23 passed.
- `uv run pytest -q tests/ui/` → 239 passed (no regressions in the existing dashboard suite).
- `uv run pytest -q` (full suite) → **1634 passed, 7 skipped, 0 failed**.
  `uv run pytest -q --ignore=tests/ui/test_templates_api.py` (i.e. every test that existed
  before this task) → **1611 passed, 7 skipped, 0 failed**, confirming the full-suite
  delta is exactly the 23 new tests added here, with zero regressions.
- `uv run ruff check .` → all checks passed.
- `uv run ruff format --check .` → 214 files already formatted (ran `ruff format` once on
  the two new/touched test files to apply the project's line-wrapping; re-checked clean
  after).
- `uv run mypy src/agent_orchestrator` → clean except the 4 pre-existing `_version.py`
  errors (present before this task; same as T-Tc0r3a and prior UI work reported).

## Contract fidelity (HLD §2.6)
- `GET /api/templates` → `TemplateInfo[]` via `dataclasses.asdict`; field names verified
  against `ui/src/types.ts`'s `TemplateInfo`/`TemplateParam` in a `TestClient`-level test.
- `POST /api/templates/{name}/instances` body/response fields match
  `ui/src/types.ts`'s `CreateInstanceRequest`/`CreateInstanceResponse` exactly
  (`instance_dir`, `workflow_path`, `workflow`, `launch`), verified end-to-end against the
  already-shipped `TemplateLaunch.tsx` request shape.
- Missing `slug_or_id` → slug derived from the first non-empty prompt line (kebab-cased);
  error if neither is available.
- `start: true` → launches via the existing `ProcessSupervisor.launch_run` path, with
  `reposets`/`agents` sourced from config exactly as `start_run` does; the prompt is
  deliberately **not** re-passed to the launcher since `instantiate()` already wrote it
  into the instance's `prompt.md` — verified by a test asserting the stub supervisor's
  `launch_run` call carries no `prompt` kwarg.
- Status mapping: 404 unknown template, 400 bad params/slug/validation failure, 409
  prompt conflict — each covered by both a service-level test (`DashboardError` message)
  and an HTTP-level test (status code).

## Judgment calls flagged
1. **404-vs-400 message collision, resolved with named prefixes.** A naive
   `"unknown template" in str(exc)` check would wrongly 404 a legitimate 400: `templates.
   load_template` raises `"unknown template 'x': ..."` (should be 404) but `templates.
   _render` separately raises `"unknown template variable '...' in ..."` for a rendering
   failure inside `instantiate()` (should be 400), and the latter message contains
   `"unknown template"` as a substring of the former's wording. Fixed by having
   `create_instance` wrap the `load_template()` failure with a deliberately distinct,
   locally-defined prefix (`TEMPLATE_NOT_FOUND_PREFIX = "template not found"`, not reused
   from either underlying message), exported from `service.py` and imported by name into
   `app.py` for the status-code check — no fragile substring guessing.
2. **`start: true` validation depth.** The task instructions say to launch "exactly like
   `start_run` does." `start_run` does not run a full `ao validate`-equivalent spec check
   before launching (that happens inside the `ao run` subprocess itself); it only checks
   the workflow file exists. `create_instance` matches that depth — `instantiate()`'s own
   structural check (`_validate_rendered_workflow`: parses JSON/YAML, requires `id`/
   `tasks`/non-empty `prompt_path`) is the only pre-launch validation, run unconditionally
   regardless of `start`. Not the full `ao new --validate-only` path (which additionally
   loads agents/reposets and does spec validation) — flagging this in case a stricter
   pre-launch validation was intended; the task's explicit "exactly like start_run does"
   wording is what this implementation follows.
3. **Blank/whitespace-only `prompt` is treated as absent**, mirroring `start_run`'s
   existing `if prompt and prompt.strip()` pattern — it neither derives a slug from it nor
   passes it as `prompt_text` to `instantiate()` (which then renders the template's own
   auto-generated skeleton into `prompt.md` on first scaffold, same as `ao new` with no
   `--prompt-file`).

## Risks / Blockers
- None. No regressions; scope stayed inside the three files named in the task brief plus
  the shared `tests/ui/conftest.py` fixture helper (smallest correct change to support the
  new tests without duplicating template-fixture-writing logic already present in
  `tests/test_templates.py`, which was deliberately NOT imported cross-suite to avoid
  coupling the two test files to one private fixture shape).

## Next actions
- None for this task. Epic-level next steps (`T-Tw4fpl-finplan-wiring`,
  `T-Te5rev-e2e-review`) are unblocked on the API side.
