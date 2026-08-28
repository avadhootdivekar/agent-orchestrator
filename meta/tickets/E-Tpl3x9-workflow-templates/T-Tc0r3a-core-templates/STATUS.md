# STATUS

- ID: `T-Tc0r3a-core-templates`
- Updated At: `2026-07-24`
- State: `Done`
- Owner: `claude`

## This update
- Implemented `src/agent_orchestrator/templates/__init__.py` (manifest schema via pydantic,
  `{{ var }}` regex renderer, `discover_templates`/`load_template`/`instantiate` per HLD
  §2.4), added `ProjectConfig.templates` (§2.1 anchoring/shadowing), and `ao templates`/
  `ao new` CLI commands (§2.5). Added `tests/test_templates.py` (unit) and
  `tests/test_e2e_cli_templates.py` (CliRunner e2e, own fixture template — isolated from
  the concurrently-developed `routed-runner` built-in via a monkeypatched `_BUILTIN_ROOT`).
  Cross-validated the module directly against the real, concurrently-shipped
  `builtin/routed-runner` template (discovery, instantiate, idempotent re-run, `when`-gate
  delete, and `ao new routed-runner ... --validate-only` all pass against it).

## Evidence
- `uv run pytest -q` (full suite, incl. other concurrently-landed tasks' tests):
  1611 passed, 7 skipped (pre-existing opt-in real_llm/swebench markers), 0 failed — of
  which 67 are new in this task (52 in `test_templates.py` + 15 in
  `test_e2e_cli_templates.py`). 0 failures anywhere in the run is the regression signal;
  no test files other than this task's two new ones were modified.
- `uv run ruff check` / `ruff format --check` on all touched+new files: clean.
- `uv run mypy src/agent_orchestrator`: 0 errors in all touched/new files (48 files
  checked); the only errors reported are 4 pre-existing, unrelated ones in `_version.py`
  (last touched in commit `6b65ccf`, not part of this task).
- Manual smoke verification that `typer.main.get_command(app).main(args=[...],
  standalone_mode=False)` (used by `ao new --run`) suppresses `run`'s internal
  `typer.Exit` from becoming a real `SystemExit` and returns the correct int exit code on
  both success and failure — pinned down as `TestInvokeRunInProcess` in
  `tests/test_e2e_cli_templates.py`.

## Deviations / judgment calls (flagged per task instructions)
1. **`when:` accepts a bare param name, not only the HLD-documented `"params.<name>"`
   form.** The concurrently-developed built-in `routed-runner` template (T-Tb3rtr) uses
   `when: type` (bare), while HLD §2.2's example shows `when: params.type`. Implemented
   `_strip_when_prefix` to accept both — a strict superset, so nothing that satisfies the
   HLD's documented form breaks, and the real shipped template Just Works.
2. **`TemplateInfo` does not cache the parsed manifest.** `instantiate()` re-parses
   `template.path` internally (same private loader `load_template` uses) rather than
   `TemplateInfo` carrying dirs/files/assets/id_pattern. This keeps the public dataclass
   exactly what HLD §2.4 documents and safely `dataclasses.asdict`-serializable for the
   future dashboard API (§2.6), at the cost of a small (few-KB YAML) double-parse across a
   `load_template` → `instantiate` pair.
3. **`id_pattern`/`instance_dir` single-brace tokens (`{rand6}`, `{slug}`, `{id}`) are a
   deliberately separate mechanism from `{{ var }}` content rendering** — not explicit in
   HLD prose but necessary since `instance_dir`'s own value IS the `instance_dir` variable
   (no `{{ instance_dir }}` self-reference is possible).
4. **`rand6` generation uses the stdlib `random` module directly** (not an injected RNG),
   per the task instructions' explicit ask ("generate rand6 from `random` module —
   seedable for tests"). This is scaffold-time id generation, not orchestration-run logic
   on the engine's run path, so CLAUDE.md's injectable-RNG rule (scoped to "the run path"
   in the pre-handoff checklist) doesn't apply; `random.seed(N)` before a call is the test
   seam, exercised in `TestIdSlugResolution`.
5. **Workflow-file sanity check locates the rendered workflow by conventional basename**
   (`workflow.json`/`.yaml`/`.yml`) among `files[]` entries, per the task instructions'
   literal 3-item checklist (parses as JSON/YAML; has `id`/`tasks`; declares non-empty
   `prompt_path`) — does NOT additionally verify some task's `inputs` lists that
   `prompt_path` (HLD §2.3's broader prose), which was out of the explicit checklist handed
   to this task.

## Risks / Blockers
- None. `ui/`, `pyproject.toml`, and `templates/builtin/` were not touched (other
  concurrent tasks' scope).

## Next actions
- None for this task. Downstream: T-Tu1api-ui-endpoints (dashboard service/API) and
  T-Tw4fpl-finplan-wiring can now build on this module's public API.
