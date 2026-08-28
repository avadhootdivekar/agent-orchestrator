# STATUS: E-Tpl3x9-workflow-templates

- Status: `Done`
- Last Updated: `2026-07-24`

## Rollup
| Task | Status |
|------|--------|
| T-Tc0r3a-core-templates | Done |
| T-Tu1api-ui-endpoints | Done |
| T-Tf2end-frontend-template-form | Done |
| T-Tb3rtr-builtin-routed-runner | Done |
| T-Tw4fpl-finplan-wiring | Done |
| T-Te5rev-e2e-review | Done |

## Comments
- By: avadhoot · Role: user · Date: 2026-07-24 · Comment: Requested generalizing
  new-epic-run.sh into ao (config-time template registration, UI-launchable) and wiring
  ao-runner-finplan so epic/bug runs start from the dashboard.
- By: claude · Role: architect · Date: 2026-07-24 · Comment: HLD written
  (docs-md/workflow-templates-hld.md); contracts pinned for parallel implementation.
- By: claude · Role: developer · Date: 2026-07-24 · Comment: T-Tf2end-frontend-template-form
  done — "From template" New-run mode shipped in `ui/`, coded to HLD §2.6/§2.7 exactly
  ahead of the API landing; see task STATUS.md for evidence and the flagged assumption on
  `options` typing.
- By: claude · Role: developer · Date: 2026-07-24 · Comment: T-Tb3rtr-builtin-routed-runner
  done — finplan-free `routed-runner` built-in template shipped under
  `src/agent_orchestrator/templates/builtin/routed-runner/` (manifest, workflow/prompt/
  breakdown-contract templates, 31 generalized instructions, README); wheel-packaging
  verified with no pyproject.toml change needed; see task STATUS.md for full evidence.
- By: claude · Role: developer · Date: 2026-07-24 · Comment: T-Tc0r3a-core-templates done
  — `src/agent_orchestrator/templates/__init__.py` (manifest/discovery/rendering/
  instantiate per HLD §2.4), `ProjectConfig.templates` (§2.1), and `ao templates`/`ao new`
  CLI (§2.5) shipped and cross-validated live against the concurrently-shipped
  `builtin/routed-runner` template (discovery, idempotent instantiate, `ao new
  routed-runner ... --validate-only` all pass). 67 new tests, full suite 1611 passed/7
  skipped/0 failed; flagged one compatibility decision (`when:` accepts both the
  HLD-documented `params.<name>` form and the bare `<name>` form the built-in template
  actually uses) — see task STATUS.md for full evidence and all flagged judgment calls.
- By: claude · Role: developer · Date: 2026-07-24 · Comment: T-Tw4fpl-finplan-wiring
  done — sibling `ao-runner-finplan` repo wired per HLD §2.9 (no engine-repo code
  touched): `workflows/epic-runner/template/` (manifest + 3 `.tmpl` files),
  `.ao/config.yaml` `templates:` registration, `new-epic-run.sh` slimmed 729→295 lines to
  delegate scaffolding to `ao new epic-runner` behind a new `require_template_support`
  probe, `README.md` updated. Verified live against the real `.venv/bin/ao`: `ao
  templates` lists `epic-runner [workspace]` + `routed-runner [builtin]` side by side;
  scaffold→validate OK; workflow.json/breakdown-contract.md/prompt.md diffed against a
  replay of the original (pre-edit) script generator — only delta is the new
  `prompt_path` field (contract/prompt files are exact byte matches); idempotent
  re-scaffold keeps an edited prompt.md; `--type bug` writes/clears `forced-type.txt`
  correctly; throwaway run dir deleted after. Flagged two judgment calls (the `.ao/
  config.yaml` `templates:` path needed a `../` prefix the task text omitted, since
  entries anchor to `.ao/`'s own directory same as `agents:`/`reposets:`; and the
  `workflow.json`'s `name` field was kept literally as `"routed-runner: {{ id }}"` to
  match the original script's actual (seemingly leftover) literal text) — see task
  STATUS.md for full evidence and rationale.
- By: claude · Role: developer · Date: 2026-07-24 · Comment: T-Tu1api-ui-endpoints done —
  `DashboardService.list_templates`/`create_instance` + `GET /api/templates` +
  `POST /api/templates/{name}/instances` shipped per HLD §2.6, thin-adapter split
  preserved. 23 new tests (service-level + FastAPI `TestClient`); full suite 1634
  passed/7 skipped/0 failed (confirmed +23 over the 1611/7/0 baseline with the new test
  file excluded — no regressions). Flagged a real message-collision risk between two
  different `TemplateError`s that both contain the substring "unknown template" (one is
  the true 404 case, the other a 400 rendering failure) and fixed it with a dedicated,
  non-colliding `TEMPLATE_NOT_FOUND_PREFIX` constant; see task STATUS.md for full
  evidence and all flagged judgment calls.
- By: claude · Role: manager · Date: 2026-07-24 · Comment: T-Te5rev-e2e-review done, epic
  closed. E2e half: 7 tests incl. scaffold→validate→engine-dispatch of the builtin
  template; empirically established that the FakeExecutor overwrites declared outputs, so
  a routed run cannot COMPLETE under fakes (route-verdict.json is classify's declared
  output; the finplan "pre-seed" technique only works for task_manifest_path files) — the
  run-to-completion gate is instead covered by the core CLI e2e's fixture template
  (`ao new --run` driven to completed with fakes). Review half: 2 BLOCKERs + 1 MAJOR
  found, all fixed same-day with regression tests (B1 template-dir source escape, B2 JSON
  structural injection via params + shallow rendered-workflow validation, M1 dashboard
  ad-hoc-path fallthrough) — see REVIEW.md "Fixes applied". Final: 1651 passed/7 skipped/
  0 failed, ruff clean, mypy clean (pre-existing _version.py errors only).
  Deferred follow-ups (reviewer W1–W3, non-blocking): atomic writes/locking for
  concurrent instantiate on the same id; bare-slug-that-matches-id-pattern ambiguity;
  duplicate config parsing in `ao new --run`.
