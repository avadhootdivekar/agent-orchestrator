# TASK: T-Md7Vc3-dev-medium-suite

## Metadata
- Task ID: `T-Md7Vc3-dev-medium-suite`
- Epic ID: `E-Bt4Xk9-complex-benchmark-tiers`
- Owner: developer agent (+ tester for discrimination check)
- Created: 2026-07-22
- Last Updated: 2026-07-22
- Status: Draft
- Estimate: 3.0 days

## Requirements Mapping
- FR-7 (self-authored medium tier — discriminating, longer, multi-file, no Docker)

## Description
Author `benchmarks/suites/dev-medium/` — 4–8 genuinely longer, multi-file tasks (target 30–90 min of agent work each), pytest/command-graded like `dev-core` but **designed for discrimination**: a single trivial `claude -p` turn should NOT ace them, so an orchestrated multi-agent workflow has headroom to show a *higher solve rate*. No Docker. Each task fixture is a small but realistic multi-module Python package (stdlib-only where possible) with an existing test suite, plus a **held-out grading harness** under `fixture/.grading/` that the instruction forbids touching (honor-system, matching `dev-core`'s `check.py` precedent) so agents can't game visible tests. Tier `medium`.

## File ownership (exclusive — all NEW, no code overlap with any other task)
- `benchmarks/suites/dev-medium/suite.json` — NEW (tier `medium`, 4–8 tasks).
- `benchmarks/suites/dev-medium/tasks/<task-id>/{instruction.md, fixture/**, fixture/.grading/**}` — NEW.
- `benchmarks/suites/dev-medium/tasks/<task-id>/fixture/.bench-solution/**` — NEW (reference-fix overlay for `FakeSubject` `copy-solution`, so the free tier proves the grader discriminates).
- (read-only) existing subject configs; `bench/tiers.py` (tier field).

## Task design (author at least these categories; 6 recommended)
1. **Feature across a package** — implement a not-yet-existing feature that threads through 3–4 modules of an existing package with a partial impl + failing/xfail tests to satisfy + a couple of new behaviors the held-out tests check.
2. **Cross-cutting refactor** — rename/extract a concept used across multiple modules while keeping the visible suite green AND satisfying a new requirement the refactor is supposed to enable (held-out tests check the new capability, not just "still green").
3. **Misleading-symptom bug** — a failing test whose obvious/local fix is wrong; the real root cause is in a different module. A one-turn agent that patches the symptom fails the held-out regression tests; a workflow that investigates/verifies is more likely to find the root cause.
4. **Multi-file feature with an integration point** — e.g. a small CLI/parser + a data layer that must agree on a contract; edits in both are required.
5–6. Two more in the above spirit (e.g. a performance/correctness fix under an existing benchmark test, or an API-compat change across modules).

## Inputs / Outputs
- Inputs: an authored fixture package + instruction per task.
- Outputs: a committed, validated `dev-medium` suite; committed `.bench-solution/` overlays + `.grading/` harnesses.

## Acceptance Criteria
1. `ao-bench validate --suite benchmarks/suites/dev-medium/suite.json` passes; 4–8 tasks; `tier=="medium"`; every task path-references its instruction/fixture.
2. **Discrimination (free tier):** for every task, `ao-bench run --subject fake-pass` (with the task's `.bench-solution/` overlay via `copy-solution`) → `solved`; the same run with the overlay removed → `not solved`. (Proves the grader actually discriminates before any spend.)
3. **Held-out integrity:** the grader command runs tests from `fixture/.grading/` (copied/executed by a `command`-grader script the instruction forbids modifying), so making only the *visible* tests pass does NOT mark the task solved. Documented per task.
4. **Not one-turn-saturable (real smoke, gated):** an opt-in haiku smoke on ≥1 task shows a bare `claude -p` turn does NOT trivially solve it (solve rate < 1.0 at low `--max-turns`), i.e. there is headroom — recorded as the authoring gate before the suite is committed as "medium".
5. Each task is estimated (in a task `tags`/notes) at 30–90 min agent-work scale; fixtures are multi-module (≥3 files of real code).
6. No Docker, no network; stdlib-only where feasible (any third-party dep is pinned + minimal and noted).

## Risks
- **R4 saturation:** if both a bare turn and the workflow solve everything, the suite still only measures cost. Mitigation: the misleading-symptom + held-out-tests design; the AC-4 smoke gate; iterate task difficulty until ≥1 task discriminates.
- **Honor-system gaming:** an agent under `bypassPermissions` *could* edit `.grading/`. Mitigation: instruction forbids it (same as `dev-core`'s `check.py`); the grading script re-derives its tests from a pristine copy if feasible; if gaming proves real, the epic's non-MVP `grading_overlay` framework feature is the escalation (not built now).
- Fixtures too large/slow → long wall-clock. Keep each fixture self-contained and fast to test (<60 s grading).

## Pseudocode / Algorithm
```text
# suite.json (excerpt)
{ "version":"1.0","id":"dev-medium","domain":"software","tier":"medium",
  "defaults":{"timeout_seconds":3600},
  "tasks":[
    { "id":"feature-plugin-registry", "category":"feature",
      "instruction":"tasks/feature-plugin-registry/instruction.md",
      "fixture":"tasks/feature-plugin-registry/fixture",
      "grader":{"type":"command","command":"uv run python .grading/run.py","cwd":"."},
      "tags":["python","medium","multi-file","~60min"] },
    { "id":"bug-misleading-cache-invalidation", "category":"bugfix", ... },
    ... ] }
# fixture/.grading/run.py: copies/loads held-out tests, runs pytest on them, exit 0 iff all pass.
```

## Schemas / Interface Notes
- Reuses existing `pytest`/`command` graders (no new grader). Uses the tier field (T-Tr1Km8). `.grading/` + `.bench-solution/` are bench-local conventions (not schema).
- Triggers/events: N/A. Artifacts: committed fixtures/instructions/overlays.

## Handoff Boundary
- Upstream: T-Tr1Km8 (tier field).
- Downstream: T-Cm9Tb4 (`make bench-medium` targets this suite), the PLAN medium run (sonnet/opus/ao-epic-sonnet/ao-epic-plus-sonnet). Parallel-safe with all code tasks (new dir only).

## Artifacts
- Docs/comments: this folder. Large outputs: fixtures live under `benchmarks/suites/dev-medium/` (kept small; committed).
