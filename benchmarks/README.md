# `ao-bench` — the `ao` benchmarking framework

Compares **subjects** — an `ao` multi-agent workflow, bare `claude -p` at a given model, or a deterministic fake —
against **suites** of dev tasks, grades each (subject × task) run, and writes machine-readable + human-readable
**results** to this git-committed directory. It reuses `ao`'s own cost/usage plumbing so `ao` and bare-`claude`
numbers are apples-to-apples.

Design: [`../docs-md/benchmarking-framework-hld.md`](../docs-md/benchmarking-framework-hld.md) (HLD/LLD, as-built) ·
[`../docs-md/adr/ADR-0008-benchmark-harness-approach.md`](../docs-md/adr/ADR-0008-benchmark-harness-approach.md)
(build-vs-adopt decision, Accepted) · [`../docs-md/benchmark-landscape-survey.md`](../docs-md/benchmark-landscape-survey.md)
(competitor survey). Epic: [`../meta/tickets/E-9Qk4Zt-agent-benchmark-harness/EPIC.md`](../meta/tickets/E-9Qk4Zt-agent-benchmark-harness/EPIC.md).

`bench/` (`src/agent_orchestrator/bench/`) is a **separate module outside the engine import graph** — it imports
pure helpers *from* core (read-only) but nothing in core imports it, and `ao run` works identically whether or not
`bench/` exists. It ships as a standalone `ao-bench` console script, never an `ao` subcommand.

---

## Quick start

```bash
uv sync --extra dev                       # once, so the ao-bench console script resolves
uv run ao-bench validate --suite benchmarks/suites/dev-core/suite.json
uv run ao-bench validate --subject benchmarks/subjects/claude-haiku.json
make bench-smoke                          # fake-pass + claude-haiku + ao-epic-haiku + a comparison report
```

Or drive it directly:

```bash
uv run ao-bench run --suite benchmarks/suites/dev-core/suite.json \
                     --subject benchmarks/subjects/claude-haiku.json
uv run ao-bench report --results-root benchmarks/results --suite dev-core
```

## Concepts

| Concept | Meaning |
|---|---|
| **Suite** (`suite.json`) | A versioned, schema-validated set of dev **tasks**. `id`/`domain`/`description`/`defaults.timeout_seconds` + `tasks: [...]`. |
| **Task** | One benchmark item: `id`, `category` (`bugfix`\|`feature`\|`refactor`\|`test`\|`other`), `instruction` (path to a `.md`), `fixture` (path to a tiny repo dir), `grader`, optional `timeout_seconds`/`tags`. Payloads are always referenced **by path**, never inlined. |
| **Subject** (`subject.json`) | The system under test: `claude_cli` (bare `claude -p --model …`), `ao_workflow` (an `ao` DAG), or `fake` (deterministic, test-only). |
| **Grader** | Pluggable pass/fail + score over the mutated workspace: `pytest`, `command` (generic exit-code — the hook for non-dev domains), `file_assertion`, `fake`. |
| **Run** | One (suite × subject) execution → `benchmarks/results/<date>-<suite>-<subject>/{run.json,summary.md}`, committed. |
| **Comparison** | Cross-subject roll-up over several runs of the same suite → `benchmarks/results/<date>-<suite>-compare/{comparison.json,comparison.md}`, committed. |
| **Workspace** | A disposable, path-guarded copy under `playground/.tmp/bench/` (gitignored) — the subject mutates it, the grader reads it. The committed fixture itself is never touched. |

---

## Authoring a suite + task

1. Pick (or create) a suite dir: `benchmarks/suites/<suite-id>/suite.json` + `benchmarks/suites/<suite-id>/tasks/`.
2. For a new task, create `tasks/<task-id>/`:
   - `instruction.md` — what the subject must do (the file itself, not its contents, is what specs reference).
   - `fixture/` — a tiny, self-contained repo dir (stdlib-only where possible — no extra install step for a
     subject's agent to fumble). It becomes `repo/` inside the disposable workspace; a copy, not a symlink.
   - (optional) `fixture/.bench-solution/` — a reference-fix overlay used ONLY by `FakeSubject`'s
     `"copy-solution"` scripted effect (copied onto the repo, then deleted — see `fake-pass.json` below). Not part
     of any committed schema; a bench-local test convention.
   - (optional) a `check.py`/`check_tests.py` script alongside `fixture/` for a `command` grader (see
     `refactor-extract-validation`/`test-write-clamp` in `dev-core` for real examples).
3. Add a task entry to `suite.json`:
   ```json
   {
     "id": "bugfix-off-by-one",
     "category": "bugfix",
     "instruction": "tasks/bugfix-off-by-one/instruction.md",
     "fixture": "tasks/bugfix-off-by-one/fixture",
     "grader": { "type": "pytest", "command": "uv run pytest -q --tb=no", "cwd": "." },
     "tags": ["python", "easy"]
   }
   ```
   `id` must be lowercase-kebab and unique within the suite; `instruction`/`fixture` are paths relative to
   `suite.json`. `version`/`id`/`domain`/`tasks` are required at the suite level; every object in the schema is
   `additionalProperties:false` — an unknown key is a validation error, not a silent no-op.
4. Grader shapes (`grader.type`):
   - `pytest` — `solved` iff exit code 0 **and** at least one test collected **and** zero failures (exit code is
     the source of truth; the pytest summary line only *enriches* `score`, per-a `pass_threshold` if set).
   - `command` — generic: `solved = (exit code == 0)`. The hook for non-dev domains (any shell-scriptable check).
   - `file_assertion` — `assertions: [{type: exists|contains|equals_file, path, substring?, golden?}]`; score =
     fraction passed. `equals_file`'s `golden` (a path relative to `suite.json`) is checked to exist AND rewritten
     to an absolute path **at suite-load time** — it will never silently fail to resolve at grade time.
   - `fake` — test-only; not for real suites.
5. Validate: `uv run ao-bench validate --suite benchmarks/suites/<suite-id>/suite.json`. A malformed spec, a
   duplicate/uppercase id, a missing `instruction`/`fixture` path, or an unknown `grader.type` all fail with a
   structured error naming the offending task — never a silent pass.
6. Prove it works with the free tier first: `uv run ao-bench run --suite <suite.json> --subject
   benchmarks/subjects/fake-pass.json` — `FakeSubject`'s `"copy-solution"` effect (via your task's
   `.bench-solution/`) should make the grader pass; drop the overlay and re-run to confirm it fails without a real
   fix (proves the grader actually discriminates before you spend real money on it).

## Authoring a subject

Subject configs live in `benchmarks/subjects/*.json`. All fields are optional except `version`/`id`/`type`; each
type uses the subset relevant to it (the schema is a discriminated union, `additionalProperties:false`).

**`claude_cli`** (bare CLI, the baseline):
```json
{
  "version": "1.0", "id": "claude-haiku", "type": "claude_cli",
  "model": "claude-haiku-4-5-20251001",
  "permission_mode": "bypassPermissions",
  "max_turns": 30,
  "prompt_template": "Solve the task described in {instruction}. Repo: {repo}. Make the necessary code changes directly in the repo, then run the test suite described in the instruction ... to confirm your fix before finishing.",
  "extra_args": []
}
```
`permission_mode: bypassPermissions` is the committed default for every subject in this repo — a subject whose
grader runs tests needs Bash, and `acceptEdits` silently denies it (learnings §21).

**`ao_workflow`** (the system under test — an `ao` DAG):
```json
{
  "version": "1.0", "id": "ao-epic-haiku", "type": "ao_workflow",
  "workflow": "ao-epic/workflow.json", "reposets": "ao-epic/reposet.json", "agents": "ao-epic/agents.json",
  "model": "claude-haiku-4-5-20251001", "max_turns": 30
}
```
`workflow`/`reposets`/`agents` are paths **relative to this subject.json**, e.g. `benchmarks/subjects/ao-epic/`.
Important as-built constraint: a workflow task's `instruction` field must be `repo/INSTRUCTION.md` — the bench
task's own instruction file, which `bench/workspace.py` copies into the disposable workspace before the subject
runs (`AO_WORKSPACE_ROOT` for an `ao_workflow` subject IS that disposable per-run workspace; there is no generic
`instructions/` directory mirrored into it). See `subjects/ao-epic/workflow.json` + `subjects/ao-epic/agents.json`
for the shipped 2-task `implement`→`verify` reference; the `subjects/ao-epic/instructions/*.md` files are
human-readable role docs mirrored verbatim into `agents.json`'s `prompt_template` strings (the text the engine
actually renders) — keep both in sync if you edit either.

**`fake`** (deterministic, test-only — the only subject CI ever runs):
```json
{ "version": "1.0", "id": "fake-pass", "type": "fake", "scripted_effect": "copy-solution", "fake_cost": 0.0, "fake_tokens": {"in": 0, "out": 0} }
```

### Model-id pinning policy
Every committed subject config pins a **full model id**, never a bare alias (`opus`/`sonnet`/`haiku`) — resolved
per ADR-0008/HLD §18 Q2. Current pinned ids: `claude-opus-4-8`, `claude-sonnet-5`, `claude-haiku-4-5-20251001`.
Record the id in the subject config only — never hard-code a model id in `bench/` code; `run.json`'s
`SubjectResult.resolved_model` records the value actually used per run for audit. If a pinned id ever retires,
update the committed subject `.json` file(s), not the framework.

**Multi-agent subjects (`ao_workflow`) must stay uniform-model.** A known core defect (`meta/learnings.md`
"model-override-clobbers-agents") means a global `--model`/`AO_MODEL` override silently clobbers every agent's own
`model` field. The shipped `ao-epic/agents.json` sidesteps this by design: neither `developer` nor `tester` sets
its own `model` — the subject's `model` flows uniformly through `AO_MODEL` to every agent, so there is nothing to
clobber. If you add a per-agent `model` override to a bench workflow template, that agent's model will be silently
overwritten by the subject's `model` field; don't rely on per-agent pinning inside a bench `ao_workflow` subject
until that core defect is fixed.

---

## Running

```bash
ao-bench validate --suite <suite.json>                # schema + semantic validate a suite
ao-bench validate --subject <subject.json>             # + probes `claude --version` (WARNING only, never fails)
ao-bench run --suite <suite.json> --subject <subject.json> \
             [--out-dir D] [--force] [--task ID ...] [--budget-total N] [--max-turns N] [--timeout S]
ao-bench report (--run-dir D [--run-dir D ...] | --results-root R --suite SUITE_ID) [--out-dir D] [--allow-mixed]
ao-bench list --suite <suite.json>                      # a task table: id/category/grader type/timeout/tags
```

- `run` is **resumable**: a task already recorded in `run.json` is skipped unless `--force`; `run.json` is
  persisted after every task (write-temp + atomic rename), so an interrupted run leaves a valid, resumable file.
- Exit codes for `run`: **0** clean run; **2** if any task's SUBJECT status (not its grader verdict — an
  unsolved-but-cleanly-run task is a normal benchmark outcome, never a failure) was
  `failed`/`timed_out`/`error`; **1** on a usage/spec-loading error.
- `report --results-root R --suite ID` auto-discovers the **latest** result dir per subject id under `R` (dir
  names sort lexicographically the same as their embedded ISO date); `--run-dir` takes explicit dirs instead
  (repeatable). Exit codes: **0** success, **1** refused/usage error.
- **One `ao-bench run` per `(suite, subject)` at a time.** The runner has no cross-process lock: two concurrent
  runs against the same `run.json` each snapshot completed tasks at start and last-write-wins on persist, so a
  concurrent run can silently drop the other's completed-task records (valid JSON, lost update). Don't launch
  `make bench-smoke` twice in parallel; a lockfile is a tracked follow-up.

### `make` targets
```
make bench-validate   # validate the committed dev-core suite
make bench-smoke      # fake-pass (free) + claude-haiku + ao-epic-haiku (both real, cheap) + a comparison report
                       # — the `-` prefix on the two real-subject lines means one flaky run never blocks the rest
make bench-run SUITE=<suite.json> SUBJECT=<subject.json>     # any suite/subject, e.g. sonnet/opus
make bench-report RESULTS=<results-root> SUITE=<suite-id>
```
Override knobs on the command line, e.g. `make bench-run SUITE=benchmarks/suites/dev-core/suite.json
SUBJECT=benchmarks/subjects/claude-opus.json`. See the `Makefile`'s `BENCH_*`/`RESULTS` variables for defaults.

---

## Results layout + committed-results policy

```
benchmarks/results/
  <date>-<suite>-<subject>/
    run.json      # machine-readable: schema_version, subject config (resolved), env (ao/claude version, git sha),
                   # per-task metrics (solved/score/cost/tokens/wall-clock/subject_status/raw_error/grader detail),
                   # aggregate (solve_rate, total_cost_usd, cost_per_solved, ...)
    summary.md     # human-readable: one row per task + an aggregate footer
  <date>-<suite>-compare/
    comparison.json   # union-of-task-ids matrix across subjects + per-subject aggregate table
    comparison.md      # side-by-side subject table + per-task matrix + a "winner" line per axis
```
**Everything under `benchmarks/results/` is committed** (git-preserved) — a result is evidence, not scratch output.
Transcripts/captures are **not** committed (large, may contain noise); `run.json` records the workspace path as a
pointer only. Workspaces themselves live under `playground/.tmp/bench/` (gitignored) and are **not auto-deleted**
— preserved for audit; clean up manually.

### Known limitation: comparison-dir naming is not subject-set-aware
`comparison`'s directory name is `<date>-<suite>-compare` — derived from only the UTC date + suite id, with no
encoding of *which* subjects went into it. **Regenerating `ao-bench report` for the same suite on the same day
overwrites the existing `comparison.{json,md}` in place**, even if the subject set changed (e.g. you added a
newly-run subject, or used `--allow-mixed`). This happened for real in this repo: `2026-07-22-dev-core-compare/`
was regenerated as more subjects finished running the same day, and the committed `comparison.md` reflects the
final (5-subject) set, not the original 3-subject `make bench-smoke` set. Per-run `run.json`/`summary.md` dirs are
subject-id-keyed and are **never** affected by this — only the cross-subject comparison artifact is. If you need
to preserve an intermediate comparison, copy the dir aside before re-running `report`, or wait for a future
subject-set-hash fix (not scheduled — MVP-accepted limitation, see HLD §9/§11).

---

## Cost expectations (real dev-core numbers, 2026-07-22, verified against committed `run.json`s)

All 6 `dev-core` tasks (2 bugfix / 2 feature / 1 refactor / 1 test-writing) solved **6/6** by every subject run so
far — this is a deliberately *trivial* smoke suite (every task solvable by a competent junior in under 10 minutes):

| Subject | Solved | Total cost | Cost/solved | Total wall-clock |
|---|---|---|---|---|
| `fake-pass` | 6/6 | $0.0000 | $0.0000 | ~0.001s |
| `claude-haiku` | 6/6 | $0.3732 | $0.0622 | 166.37s |
| `ao-epic-haiku` | 6/6 | $0.6617 | $0.1103 | 343.05s |
| `claude-sonnet` | 6/6 | $1.2148 | $0.2025 | 122.28s |
| `claude-opus` | 6/6 | $1.5483 | $0.2580 | 129.97s |
| `ao-epic-sonnet` | 6/6 | $2.8744 | $0.4791 | 387.31s |

Takeaway: on tasks this easy, the `ao-epic` 2-agent (`implement`→`verify`) workflow does not raise the solve rate
over a single bare `claude -p` call (both 6/6) — it costs **~1.8× (haiku) to ~2.4× (sonnet)** the bare-CLI run and
takes **~2.1× to ~3.2×** the wall-clock, because the verify agent's extra turn isn't repaid when the implement
agent already gets it right. This is the expected shape of a trivial suite, not a framework bug — a harder suite
(where a single `claude -p` call more often needs a second look) is where an orchestrated verify/retry loop is
expected to earn back its overhead via a *higher* solve rate, not just show up as cost. These six rows ARE the
user-requested Phase-2 comparison (run 2026-07-22 on `dev-core`; the epic's own tickets cover only the framework,
with the run executed on top of it): because every subject saturates the suite at 6/6, read them as
**cost/latency measurements at equal (saturated) quality**, not as evidence about relative capability. A harder,
discriminating suite (e.g. a SWE-bench Verified subset behind the ADR-0008 D2 import seam) is the designed
follow-up for capability claims.

Live artifacts: `benchmarks/results/2026-07-22-dev-core-*/run.json` (per subject) and
`benchmarks/results/2026-07-22-dev-core-compare/comparison.md` (cross-subject).

---

## Adding a grader or subject type

Both are registry seams (`SUBJECT_REGISTRY`/`GRADER_REGISTRY` in `bench/registries.py`) — no schema edit needed to
add a new implementation, but two places must move together:
1. Implement the class (`Subject`/`Grader` ABC) in `bench/subjects.py`/`bench/graders.py` and register it (import-
   time `register_subject`/`register_grader` call).
2. Add its `type` string to the matching closed list in `bench/spec.py`
   (`KNOWN_SUBJECT_TYPES`/`KNOWN_GRADER_TYPES`) — the JSON schemas leave `type` as an open string precisely so
   these Python-side sets, not a schema edit, are what `ao-bench validate` checks against.

Non-dev domains (biology/physics/legal/...) need **no code change at all**: add a new suite with a different
`domain` string and `command`-type graders (shell out to whatever verification script that domain needs).

## Non-MVP / roadmap (not shipped this epic — see HLD §12, ADR-0008 follow-ons)
`LlmJudgeGrader` · `DockerSubject` + SWE-bench Verified importer · an inspect-ai bridge · Aider/EvalPlus subset
importers · non-dev domain suites (framework supports via `domain`+`CommandGrader`, ships none) · HTTP/API
subjects · multi-seed/bootstrap-CI/pass@k statistical rigor · a results dashboard/`ao-bench serve` · a nightly cron
trigger · the actual Phase-2 Sonnet-vs-Opus-vs-`ao` headline comparison on a harder suite (consumes this framework,
tracked as a separate follow-up).
