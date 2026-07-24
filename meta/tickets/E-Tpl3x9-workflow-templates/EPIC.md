# EPIC: E-Tpl3x9-workflow-templates

## Metadata
- Epic ID: `E-Tpl3x9-workflow-templates`
- Title: `Workflow templates — config-registered scaffolding via CLI + dashboard`
- Owner: `avadhoot`
- Created: `2026-07-24`
- Last Updated: `2026-07-24`
- Status: `In Progress`

## Summary
- Goal: Generalize ao-runner-finplan's `new-epic-run.sh` into a first-class ao feature:
  workflow *templates* registered at config time (`.ao/config.yaml templates:` +
  built-ins in the wheel), instantiated repeatedly via `ao new` and from the `ao ui`
  dashboard ("From template" launcher with prompt + params), producing self-contained
  run instances the existing run/resume/status machinery handles unchanged.
- Scope In: templates module (manifest, discovery, rendering, instantiate);
  `ao new`/`ao templates` CLI; dashboard service + API endpoints + frontend form;
  built-in finplan-free `routed-runner` template; ao-runner-finplan registration +
  script delegation.
- Scope Out: authoring templates in the UI; template versioning/migrations; non-string
  params; template-file conditionals/loops; dashboard auth.

## Requirements
- FR-1: `templates:` list in ProjectConfig (dir-of-template or template dir), resolved
  relative to config; workspace templates shadow built-ins; old `ao` ignores the key.
- FR-2: `template.yaml` manifest per HLD §2.2 (params/dirs/files/assets/`when`/
  `keep_existing`); `{{var}}` rendering; unknown var → hard error.
- FR-3: `ao new <template> <slug|id> [--param k=v] [--prompt-file] [--validate-only]
  [--run]`; `ao templates` listing. Idempotent re-scaffold; prompt never clobbered.
- FR-4: Rendered workflow declares `prompt_path` (validated) so UI/CLI prompt injection
  works with zero engine changes.
- FR-5: `GET /api/templates`; `POST /api/templates/{name}/instances` (scaffold +
  optional `start`) per HLD §2.6, thin-adapter split as in E-Ui7Kq2.
- FR-6: Frontend "New run → From template" mode: picker, param controls, prompt
  textarea (skeleton-prefilled), Create / Create & run → RunDetail.
- FR-7: Built-in `routed-runner` template: full routed multi-type DAG, all FinPlan
  context removed; instructions materialize as workspace assets (`keep_existing`).
- FR-8: ao-runner-finplan: template dir + `templates:` registration + `prompt_path` in
  generated workflow; `new-epic-run.sh` delegates scaffolding to `ao new` (with
  template-support probe), keeping resume/extend passthrough.
- NFR-1: No new runtime deps for rendering (no Jinja2); manifest validated with clear
  errors; all paths workspace-contained.
- NFR-2: Tests per HLD §4 — unit + CLI e2e (CliRunner, fake executors) + API
  integration + vitest; no regressions in existing suites.

## Task List
- [x] `T-Tc0r3a-core-templates` — templates module + ProjectConfig + `ao new`/`ao templates` + tests
- [x] `T-Tu1api-ui-endpoints` — DashboardService + FastAPI template endpoints + tests
- [x] `T-Tf2end-frontend-template-form` — New-run "From template" mode + vitest
- [x] `T-Tb3rtr-builtin-routed-runner` — finplan-free built-in template + validate e2e
- [x] `T-Tw4fpl-finplan-wiring` — ao-runner-finplan template/config/script delegation
- [ ] `T-Te5rev-e2e-review` — cross-cutting e2e (scaffold→run with fakes), review pass, docs

## Risks and Dependencies
- Builds on `ad/ui-dashboard` (E-Ui7Kq2) — not yet merged to main; PR ordering matters.
- Global `ao` snapshot staleness: finplan UI/scripts need `install.sh --yes` re-run
  after merge (probe in script fails loudly, per E-it9xz2 policy).
- Generalizing 31 instruction files without semantic drift (route behavior must match
  finplan originals minus project specifics).

## Links
- Design doc: `docs-md/workflow-templates-hld.md`
- Origin workflow: `ao-runner-finplan/workflows/epic-runner/` (script + README + design)
