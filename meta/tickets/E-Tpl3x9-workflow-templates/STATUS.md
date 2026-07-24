# STATUS: E-Tpl3x9-workflow-templates

- Status: `In Progress`
- Last Updated: `2026-07-24`

## Rollup
| Task | Status |
|------|--------|
| T-Tc0r3a-core-templates | Done |
| T-Tu1api-ui-endpoints | To Do |
| T-Tf2end-frontend-template-form | Done |
| T-Tb3rtr-builtin-routed-runner | Done |
| T-Tw4fpl-finplan-wiring | To Do |
| T-Te5rev-e2e-review | To Do |

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
