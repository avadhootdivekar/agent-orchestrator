# STATUS

- ID: `T-PLsJdO-worked-example`
- Updated At: `2026-09-21`
- State: Done
- Owner: dev-epic

## This update
Worked example produced: "add `ao run --dry-run`" (a real, self-referential, moderately
complex task for this repo — `--dry-run` does not exist on `ao run` today, checked against
`src/agent_orchestrator/cli.py`), decomposed exactly per the skill's own guidance into 5 tasks:
- `design` (architect) → `output/dryrun/design.md`.
- `implement-cli` (developer, depends on `design`) → CLI/engine code only, `touches` scoped to
  `src/agent_orchestrator/{cli,engine}.py`, `pre_hook: check_disk_space`.
- `implement-docs` (developer, depends on `design`, runs in parallel with `implement-cli`) →
  docs only, `touches` scoped to `docs-md/cli-reference.md`, `pre_hook: check_disk_space`.
- `test` (tester, depends on both implement tasks — `join: "all"` default) → `post_hook: grade`
  (`on_failure: "ignore"`, advisory).
- `review` (reviewer, depends on `test`).

**Isolation/parallelism choice, made explicit per the skill's guidance rather than left
implicit**: `isolation` left unset (`inherit` → workflow default `none`) because
`implement-cli`/`implement-docs`'s `outputs` and `touches` are disjoint by construction — the
skill's own rule for when `isolation: none` is safe under `max_parallel > 1` (a companion
`ao run --max-parallel 2 ...` is the run-level setting that would actually parallelize these
two; not a spec field, so not encoded in the JSON itself — noted here instead, matching the
skill's own point that `max_parallel` lives outside the spec).

Files: `specs/examples/workflow-dry-run-flag.json`,
`specs/examples/instructions/dryrun-{design,implement-cli,implement-docs,test,review}.md`.
Reused existing generic hook scripts (`specs/examples/hooks/check_disk_space.py`,
`.../grade.py`) rather than inventing new ones.

## Evidence
Real `ao validate` run, twice (module invocation and the installed console-script entrypoint),
both exit 0:
```
$ .venv/bin/python -m agent_orchestrator.cli validate --workflow specs/examples/workflow-dry-run-flag.json --reposets specs/examples/reposet.json --agents specs/examples/agents.json
OK: all specs valid
EXIT:0

$ .venv/bin/ao validate --workflow specs/examples/workflow-dry-run-flag.json --reposets specs/examples/reposet.json --agents specs/examples/agents.json
OK: all specs valid
EXIT:0
```

## Follow-up (2026-09-21, later same day)
Re-running the full suite after this task landed surfaced a real, genuine test gap:
`tests/test_isolation_models.py::TestSpecsExamplesRoundTripUnchanged::test_loads_with_documented_defaults`
asserted every `specs/examples/*.json` file uses only documented-default `isolation`/`touches`
values — this example's deliberate `touches` usage (the whole point of demonstrating the skill's
isolation guidance) wasn't anticipated by that blanket assertion. Fixed with a named exclusion
set (`_EXAMPLES_WITH_NON_DEFAULT_ISOLATION_FIELDS`) rather than weakening the test for every other
example; `test_round_trips_through_json_unchanged` still covers this file unconditionally.
Confirmed this file postdates the NFR-2 gate's protected pre-epic file set, so no
`_EPIC_MODIFIED_PRE_EPIC_TESTS` exception entry was needed. Independently re-ran `ao validate`
against the spec again in this session's own turn — same exit 0 result.

## Late gate (2026-09-21): real `ao run` execution, not just `ao validate`
Went beyond this task's own AC (schema validation only) to satisfy the epic's pre-close
checklist: ran the spec through the actual engine with a `fake`-executor `agents.json`
(no network/LLM calls) — `ao run.end` status `succeeded`, all 5 tasks succeeded, both
`pre_hook check_disk_space` calls passed, the `test` task's `post_hook grade` passed. Also ran
`ao report-outcomes --grade grade` against the resulting run — all 5 tasks graded `passed`,
score `1.00`, demonstrating the `post_hook` vs. `--grade` distinction the skill teaches on a
real run. Full logs: `output/E-DOiDqE-workflow-authoring-skill/late-gate/` (see its README for
exact commands and what was/wasn't kept).

## Risks / Blockers
- None. Task complete, including late-gate execution evidence.

## Next actions
1. None — task complete.
