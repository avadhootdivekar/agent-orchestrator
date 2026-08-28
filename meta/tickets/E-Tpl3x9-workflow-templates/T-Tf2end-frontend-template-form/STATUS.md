# STATUS

- ID: `T-Tf2end-frontend-template-form`
- Updated At: `2026-07-24`
- State: `Done`
- Owner: `claude`

## This update
- By: `claude` · Role: `developer` · Date: `2026-07-24`
- Comment: Implemented the "From template" New-run mode against HLD §2.6/§2.7 exactly as
  documented (API not yet merged — coded to the documented contract). Added:
  `types.ts` (`TemplateParam`, `TemplateInfo`, `CreateInstanceRequest`,
  `CreateInstanceResponse`), `api.ts` (`templates()`, `createInstance()`),
  `components/TemplateLaunch.tsx` (template picker, per-param controls with
  enum→select/else text-input + required enforcement, prompt textarea prefilled from
  `prompt_skeleton`, optional slug field, Create / Create & run, empty-state), a mode
  toggle in `components/NewRun.tsx` (existing "From workflow" flow untouched), and
  vitest coverage (`test/template-launch.test.tsx` + one toggle test in
  `test/components.test.tsx`).

## Evidence
- `npx tsc -b --noEmit` — clean, no errors.
- `npx vitest run` — 3 test files, 42/42 passed, no regressions.
- `npm run build` — succeeds; regenerated `src/agent_orchestrator/ui/static/` (expected
  build-output side effect).

## Risks / Blockers
- `POST /api/templates/{name}/instances` and `GET /api/templates` (T-Tu1api-ui-endpoints)
  were still in progress at implementation time — this task codes to the HLD §2.6 contract
  exactly (request/response field names and shapes) but has not been exercised against the
  real endpoint yet. Needs a quick smoke check once that task lands.
- `options: {..}` in the POST body is typed as `RunOptions` (mirroring `/runs` POST) since
  the HLD does not spell out its shape beyond "same as run options" by convention; flagged
  as an assumption, not silently guessed past.

## Next actions
1. None outstanding for this task; re-verify against the live API once
   T-Tu1api-ui-endpoints merges.
