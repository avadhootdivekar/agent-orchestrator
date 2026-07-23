# `ao-bench` — the `ao` benchmarking framework

Compares **subjects** — an `ao` multi-agent workflow, bare `claude -p` at a given model, or a deterministic fake —
against **suites** of dev tasks, grades each (subject × task) run, and writes machine-readable + human-readable
**results** to this git-committed directory. It reuses `ao`'s own cost/usage plumbing so `ao` and bare-`claude`
numbers are apples-to-apples.

Suites are organized into **tiers** (`small`/`medium`/`large`/`xlarge`) with harness-enforced USD spend caps and an
opt-in bounded-parallel runner — see [Tiers](#tiers) below. `small` (`dev-core`) is the original MVP suite (kept
byte-unchanged); `medium` (`dev-medium`) and `large` (`swe-verified-mini`, a pinned SWE-bench Verified subset) were
added by epic `E-Bt4Xk9` to turn the harness from a cost/latency meter into a capability meter.

Design: [`../docs-md/benchmarking-framework-hld.md`](../docs-md/benchmarking-framework-hld.md) (HLD/LLD, as-built) ·
[`../docs-md/adr/ADR-0008-benchmark-harness-approach.md`](../docs-md/adr/ADR-0008-benchmark-harness-approach.md)
(MVP build-vs-adopt decision, Accepted) ·
[`../docs-md/adr/ADR-0009-benchmark-tiers-external-suites-budgets-parallelism.md`](../docs-md/adr/ADR-0009-benchmark-tiers-external-suites-budgets-parallelism.md)
(tiers/budgets/parallelism/SWE-bench decision, Accepted) ·
[`../docs-md/benchmark-landscape-survey.md`](../docs-md/benchmark-landscape-survey.md) (competitor survey). Epics:
[`../meta/tickets/E-9Qk4Zt-agent-benchmark-harness/EPIC.md`](../meta/tickets/E-9Qk4Zt-agent-benchmark-harness/EPIC.md)
(MVP) · [`../meta/tickets/E-Bt4Xk9-complex-benchmark-tiers/EPIC.md`](../meta/tickets/E-Bt4Xk9-complex-benchmark-tiers/EPIC.md)
(tiers/budgets/parallelism/SWE-bench).

`bench/` (`src/agent_orchestrator/bench/`) is a **separate module outside the engine import graph** — it imports
pure helpers *from* core (read-only) but nothing in core imports it, and `ao run` works identically whether or not
`bench/` exists. It ships as a standalone `ao-bench` console script, never an `ao` subcommand. The `large` tier's
`swebench`/`datasets` dependencies are an **optional extra**, lazy-imported only inside the swebench provider/grader
— core install and the network-free CI job are unaffected whether or not that extra is installed.

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

For the `medium`/`large` tiers, see [Tiers](#tiers) and [Running a tier campaign](#running-a-tier-campaign) below —
they're spend-gated and should be run via `make bench-medium`/`make bench-large`, not ad hoc.

## Concepts

| Concept | Meaning |
|---|---|
| **Suite** (`suite.json`) | A versioned, schema-validated set of dev **tasks**. `id`/`domain`/`description`/`defaults.timeout_seconds` + `tasks: [...]`. |
| **Task** | One benchmark item: `id`, `category` (`bugfix`\|`feature`\|`refactor`\|`test`\|`other`), `instruction` (path to a `.md`), `fixture` (path to a tiny repo dir), `grader`, optional `timeout_seconds`/`tags`. Payloads are always referenced **by path**, never inlined. |
| **Subject** (`subject.json`) | The system under test: `claude_cli` (bare `claude -p --model …`), `ao_workflow` (an `ao` DAG), or `fake` (deterministic, test-only). |
| **Grader** | Pluggable pass/fail + score over the mutated workspace: `pytest`, `command` (generic exit-code — the hook for non-dev domains), `file_assertion`, `fake`. |
| **Run** | One (suite × subject) execution → `benchmarks/results/<date>-<suite>-<subject>/{run.json,summary.md}`, committed. |
| **Comparison** | Cross-subject roll-up over several runs of the same suite → `benchmarks/results/<date>-<suite>-compare/{comparison.json,comparison.md}`, committed. |
| **Tier** | A suite's `tier` field (`small`\|`medium`\|`large`\|`xlarge`, default `small`) selects its default USD caps + parallelism from the committed `benchmarks/tiers.json`. See [Tiers](#tiers). |
| **Campaign** | `ao-bench campaign` runs a suite against a *list* of subjects under one whole-run USD cap → `benchmarks/results/<date>-<suite>-campaign/{campaign.json,comparison.json,comparison.md}`, committed. |
| **Workspace provider** | How a task's mutable `repo/` is materialized: `fixture` (default — copy a committed dir) or `swebench` (git checkout a repo at a pinned `base_commit`). See [SWE-bench import](#swe-bench-import-the-large-tier). |
| **Workspace** | A disposable, path-guarded copy under `playground/.tmp/bench/` (gitignored) — the subject mutates it, the grader reads it. The committed fixture itself is never touched. |

---

## Tiers

`benchmarks/tiers.json` is the single committed source of truth for per-tier defaults. A suite opts in via an
optional `tier` field (`suite.json`); omitting it defaults to `small` — `dev-core` has no `tier` key and is
byte-unchanged by this epic. Precedence for every knob below is **CLI flag > the suite's tier default (from
`tiers.json`) > a builtin fallback** — verified against `benchmarks/tiers.json` and `bench/tiers.py` at HEAD:

| Tier | `enabled` | Suite | `cost_budget_usd_per_run` (whole campaign) | `cost_budget_usd_per_subject` (one `run`) | `default_max_parallel` | `default_timeout_seconds` | What it measures | How to run |
|---|---|---|---|---|---|---|---|---|
| `small` | true | `dev-core` (6 trivial spot-fix tasks) | $10 | $5 | 1 (serial) | 900 | Cost/latency at saturation — every subject solves 6/6; not a capability signal. | `make bench-smoke`, or `ao-bench run --suite benchmarks/suites/dev-core/suite.json --subject <s>` |
| `medium` | true | `dev-medium` (6 multi-file, 30–90-min-of-agent-work tasks) | $150 | $50 | 4 | 3600 | Cost/latency **and** turn-budget-constrained discrimination on harder self-authored tasks (see [Results interpretation](#results-interpretation)). | `make bench-medium AO_BENCH_CONFIRM=1` |
| `large` | true | `swe-verified-mini` (10 pinned SWE-bench Verified instances) | $800 | $100 | 3 | 5400 | An externally-credible "can the agent resolve a genuine repo issue" capability signal, graded by the official SWE-bench Docker harness. | `make bench-large AO_BENCH_CONFIRM=1` (needs Docker + network — see [SWE-bench import](#swe-bench-import-the-large-tier)) |
| `xlarge` | **false** | none committed | $8000 | $1000 | 4 | 7200 | Defined only — the full ~500-instance SWE-bench Verified run. **Disabled by design**: a full run needs far more than the ~37 GB free disk this environment has (Docker images are ~1 GB+ each, cleaned per-instance on `large` but a 500-instance sweep would still churn far more disk/time than is safe here). Re-import with a bigger `--instances` list on a bigger-disk host is the intended path if this tier is ever needed for real (the importer makes scaling `N` trivial — see below). | `make bench-xlarge FORCE_XLARGE=1 AO_BENCH_CONFIRM=1` demonstrates the `--enable-xlarge` override plumbing only; no `xlarge` suite is committed, so the underlying `ao-bench campaign` call still fails to find one. |

Two distinct budget levels exist per tier, both **enforced by the harness itself** (a hard stop, not a soft
warning) — and both are distinct from the pre-existing **token** budget (`--budget-total`, threaded through to `ao
run`, never renamed/conflated with the USD caps below):
- **Per-subject cap** (`cost_budget_usd_per_subject`, CLI `--cost-budget-usd` on `ao-bench run`/`campaign`) — a
  single (suite × subject) `run` stops scheduling new tasks once its own cumulative recorded cost reaches the cap;
  every task past that point is recorded `subject_status="skipped_budget"` (not a harness failure, not a grader
  verdict — `solved=false`/`score=0`/`cost=0.0`, never scheduled). Re-running with a higher cap re-attempts exactly
  the skipped tasks (resume-aware).
- **Whole-run cap** (`cost_budget_usd_per_run`, CLI `--run-budget-usd` on `ao-bench campaign` only) — the total
  spend across *every* subject in a campaign; once cumulative actual spend from completed subjects reaches this
  cap, every remaining subject is recorded `status="skipped_budget"` without being launched at all.

Both checks are **check-before-schedule**, so overshoot is bounded (documented, not zero): under `--max-parallel N`,
up to `N` tasks can be mid-flight past the check before their cost lands, so a run can overshoot its cap by at most
`N × max_task_cost`. `skipped_budget` never trips a non-zero exit code on either `run` or `campaign`.

`--max-parallel` (bounded thread pool, opt-in task-level parallelism) defaults to the suite's tier
`default_max_parallel` on **both** `ao-bench run` and `ao-bench campaign` — there is no flag needed to get medium's
4-way or large's 3-way concurrency; pass `--max-parallel 1` explicitly to force serial. This is a real, intentional
behavior change vs the small/MVP tier's original always-serial default: **any medium/large-tier suite now runs
concurrently by default.**

A suite whose resolved tier is `enabled: false` in `tiers.json` (currently only `xlarge`) refuses to run on both
`ao-bench run` and `ao-bench campaign` unless you pass `--enable-xlarge` — the flag name is literal/historical (it
gates **any** disabled tier, not only one literally named `xlarge`).

**Don't confuse two same-named-but-different `max_parallel` knobs.** The bench-level `--max-parallel` CLI flag
above runs multiple **bench tasks** concurrently (a thread pool inside `ao-bench run`/`campaign`, ADR-0009 D4). An
`ao_workflow` subject config's own `max_parallel` field (`subject.json`) is unrelated — it is threaded via
`AO_MAX_PARALLEL` into the `ao run` subprocess itself, controlling the **core engine's own DAG wave/barrier
scheduler** (ADR-0007) for that one workflow's internal tasks. A bench campaign can use both simultaneously (e.g.
`--max-parallel 4` for concurrent bench tasks, each running an `ao_workflow` subject whose own `agents.json`-level
DAG is itself internally serial or parallel) — they compose, they don't share a value or a meaning.

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
7. Add a `tier` field to `suite.json` if authoring beyond `small` (default) — see [Tiers](#tiers).

### Held-out grading for discriminating (`medium`+) suites

There is no dedicated `grading_overlay` framework feature (non-MVP — see the "Non-MVP / roadmap" section near the
end of this doc). `dev-medium` instead uses a **per-fixture `.grading/` honor-system convention** (same shape as `dev-core`'s
`check.py`/`check_tests.py` precedent, generalized): each task's `fixture/.grading/{run.py,tests/}` is a held-out
harness the agent's own instruction never points at and is never told to run — `run.py` re-runs the *visible*
`tests/` plus the held-out `.grading/tests/` (or, for a test-writing task, mutation-tests the agent's own test file
against several independently seeded single-bug variants). `solved` is gated by the full grading harness passing,
not by the visible suite alone — verified per-task at authoring time (`.grading/run.py` must FAIL on the unmodified
fixture and PASS once the task's own `.bench-solution/` reference fix is applied; `dev-medium`'s own
`tests/bench/test_dev_medium_suite.py` asserts this for all 6 committed tasks). This is an honor-system convention,
not a schema-enforced one — nothing stops a task's grader `command` from pointing only at the visible `tests/`, so
new held-out-graded tasks must wire their `grader.command` to the `.grading/run.py` entry point explicitly.

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
             [--out-dir D] [--force] [--task ID ...] [--budget-total N] [--max-turns N] [--timeout S] \
             [--cost-budget-usd U] [--max-parallel N] [--enable-xlarge]
ao-bench report (--run-dir D [--run-dir D ...] | --results-root R --suite SUITE_ID) [--out-dir D] [--allow-mixed]
ao-bench list --suite <suite.json>                      # a task table: id/category/grader type/timeout/tags
```

- `run` is **resumable**: a task already recorded in `run.json` is skipped unless `--force`; `run.json` is
  persisted after every task (write-temp + atomic rename), so an interrupted run leaves a valid, resumable file.
  The ONE exception to "already recorded → skip" is a `skipped_budget` record — resume always re-attempts it.
- Exit codes for `run`: **0** clean run (a run that stopped early on its USD budget is still exit 0 —
  `skipped_budget` is a clean, resumable outcome, not a failure); **2** if any task's SUBJECT status (not its
  grader verdict — an unsolved-but-cleanly-run task is a normal benchmark outcome, never a failure) was
  `failed`/`timed_out`/`error`; **1** on a usage/spec-loading error (including a disabled tier without
  `--enable-xlarge`).
- `--cost-budget-usd U` — hard USD cost cap for this run (default: the suite's tier `cost_budget_usd_per_subject`).
  Distinct from `--budget-total` (a **token** budget threaded to `ao run`, never enforced by the harness itself —
  see [Tiers](#tiers) for the naming distinction).
- `--max-parallel N` — bounded task-level parallelism via a thread pool (default: the suite's tier
  `default_max_parallel`). `N<=1` is byte-identical to the original serial path.
- `report --results-root R --suite ID` auto-discovers the **latest** result dir per subject id under `R` (dir
  names sort lexicographically the same as their embedded ISO date); `--run-dir` takes explicit dirs instead
  (repeatable). Exit codes: **0** success, **1** refused/usage error.
- **One `ao-bench run`/`campaign` per `(suite, subject)` at a time — still no cross-process lock.** Concurrency
  *within* one `run_suite` call (`--max-parallel N`) is lock-correct (a single `threading.Lock` guards the budget
  check, the `tasks_dict` write-back, and every `run.json` persist). What is **not** guarded is two *separate*
  `ao-bench` processes racing the same `run.json`: each snapshots completed tasks at its own start and
  last-write-wins on persist, so a second concurrent invocation can silently drop the first's completed-task
  records (valid JSON, lost update). Don't launch `make bench-smoke`/`bench-medium`/`bench-large` twice in
  parallel against the same suite+subject; a cross-process lockfile is a tracked follow-up (unchanged limitation
  from the MVP epic, still open after this epic's intra-process locking landed).

### `make` targets
```
make bench-validate   # validate the committed dev-core suite
make bench-smoke      # fake-pass (free) + claude-haiku + ao-epic-haiku (both real, cheap) + a comparison report
                       # — the `-` prefix on the two real-subject lines means one flaky run never blocks the rest
make bench-run SUITE=<suite.json> SUBJECT=<subject.json>     # any suite/subject, e.g. sonnet/opus
make bench-report RESULTS=<results-root> SUITE=<suite-id>
make bench-medium AO_BENCH_CONFIRM=1                          # dev-medium campaign x 4 subjects, see Tiers
make bench-large  AO_BENCH_CONFIRM=1                          # swe-verified-mini campaign x 3 subjects, see Tiers
make bench-xlarge FORCE_XLARGE=1 AO_BENCH_CONFIRM=1           # demonstrates the disabled-tier refusal/override path only
```
Override knobs on the command line, e.g. `make bench-run SUITE=benchmarks/suites/dev-core/suite.json
SUBJECT=benchmarks/subjects/claude-opus.json`. See the `Makefile`'s `BENCH_*`/`RESULTS` variables for defaults.

---

## Running a tier campaign

`ao-bench campaign` runs one suite against a **list** of subjects under a single whole-run USD cap (the tier's
`cost_budget_usd_per_run`), writing each subject's own `run.json`/`summary.md` plus a campaign-level
`campaign.json` + `comparison.{json,md}`:

```bash
ao-bench campaign --suite <suite.json> --subject <s1.json> [--subject <s2.json> ...] \
                   [--max-parallel N] [--cost-budget-usd U] [--run-budget-usd U] \
                   [--out-dir D] [--force] [--enable-xlarge]
```
- `--cost-budget-usd` is the **per-subject** cap (same meaning as `ao-bench run`'s flag), further bounded by
  whatever whole-run headroom remains: the cap actually applied to a given subject is
  `min(--cost-budget-usd or tier default, remaining whole-run headroom)`.
- `--run-budget-usd` is the **whole-campaign** cap (default: the tier's `cost_budget_usd_per_run`). Once
  cumulative actual spend from completed subjects reaches it, every remaining subject is recorded
  `status="skipped_budget"` without being launched — never a harness failure, exit 0.
- Subjects launch **in the order given** and a campaign is resumable exactly like a single `run`: re-running with
  the identical command skips subjects/tasks already recorded and recomputes cumulative spend from the resumed
  `run.json`s (never carried over in memory across invocations).
- Exit codes: **0** whether every subject completed or some were budget-skipped; **1** on a usage/spec error
  (including a disabled tier without `--enable-xlarge`). Unlike `run`, `campaign` has **no** exit-2 equivalent — a
  per-task harness failure inside one subject's own run is fully visible in `campaign.json`/stdout but does not
  escalate the campaign's own exit code.

**The per-tier `make` recipes are the intended entry point** (`bench-medium`/`bench-large`/`bench-xlarge`, shown
above). Each recipe:
1. Always **prints** the tier's expected-cost envelope and the exact `ao-bench campaign` command it would run.
2. **Refuses to launch (exit 1) unless `AO_BENCH_CONFIRM=1`** is set on the same invocation — a bare `make
   bench-medium` never spends money. This is deliberate friction for an operation that costs real dollars.
3. `bench-xlarge` additionally refuses unconditionally unless `FORCE_XLARGE=1` (and even then, only demonstrates
   the `--enable-xlarge` plumbing — no `xlarge` suite is committed, so the underlying campaign call still fails to
   find one to run).

`make -n bench-medium` / `make -n bench-large` (dry-run) print the exact command without executing anything —
useful to sanity-check the invocation before spending. Both real recipes are resumable: a re-run after an
interruption picks up exactly where it left off, re-spending nothing for subjects already fully recorded.

**`AO_BENCH_SWEBENCH_KEEP_IMAGES=1`** — `make bench-large` sets this automatically for its whole invocation. A
multi-subject campaign against the same `large`-tier suite re-pulls each ~1 GB SWE-bench Docker image once per
subject by default (images are deleted after each instance for disk safety — see
[SWE-bench import](#swe-bench-import-the-large-tier)); setting this env var keeps pulled images resident for the
campaign's duration instead, trading ~10 GB of retained disk (well within the ~37 GB budget the tier is sized for)
for avoiding ~2 extra re-pulls × ~4 min × 10 instances (~80 min saved) across a 3-subject run. Leave it unset
(default `False`) for a single-subject run or when you want each instance to self-clean immediately.

---

## SWE-bench import (the large tier)

`swe-verified-mini` (the committed `large`-tier suite, 10 pinned SWE-bench Verified instances) was produced by
`ao-bench import-swebench`, which regenerates `<out>/{suite.json,instances.json,tasks/*/instruction.md}`
**deterministically** from a pinned `(dataset, revision)` HuggingFace dataset row set + an explicit instance-id
list — re-running with the same inputs reproduces byte-identical output (verified: a real HuggingFace-driven import
and a jsonl-fixture-driven import of the same ids/revision diff to zero bytes).

```bash
uv sync --extra swebench                  # installs `datasets`+`swebench`; ONLY needed for import + the large tier
uv run ao-bench import-swebench \
  --dataset princeton-nlp/SWE-bench_Verified \
  --revision c104f840cc67f8b6eec6f759ebc8b2693d585d4a \
  --instances django__django-11138,django__django-11292,django__django-12304,django__django-14007,django__django-14053,psf__requests-2931,pytest-dev__pytest-10356,pytest-dev__pytest-7571,sphinx-doc__sphinx-10466,sympy__sympy-20590 \
  --out benchmarks/suites/swe-verified-mini
```
(This exact invocation regenerates the already-committed `swe-verified-mini` suite — you do not need to run it
unless re-importing with a different/larger instance set.) `--revision` defaults to the pinned commit sha above and
should never be pointed at a live/unpinned dataset head. `--instances` also accepts a path to a file with one id
per line. Pass `--no-probe-images` for an offline/no-docker regen (skips the `docker manifest inspect` size-only
probe recorded in `instances.json`; never pulls image layers either way).

**Network needs**: the importer needs outbound access to the HuggingFace dataset CDN (one-time, per distinct
revision) and, if `--probe-images` is on (the default), to Docker Hub (`docker manifest inspect`, no pull). Running
the `large` tier itself (`ao-bench run`/`campaign` against a `swebench`-sourced suite) needs a real `git clone` of
each instance's repo (cached at `playground/.tmp/swebench-repo-cache/`, gitignored, shared across runs) plus a real
Docker image pull per instance the first time it's graded.

**Disk budgeting**: this environment has **~37 GB free disk**, which is why `large` is capped at N=10 pinned
instances (not the full ~500) and why `xlarge` (the full suite) is disabled — see [Tiers](#tiers). Each instance's
Docker image is ~0.9–1.1 GiB; the `SweBenchGrader` removes the image (`docker rmi -f`) and prunes the build cache
after every instance **unless** `AO_BENCH_SWEBENCH_KEEP_IMAGES=1` is set, in which case images accumulate for the
run's duration (bounded to ~10 GB for this suite's 10 instances) and must be cleaned up manually afterward (`docker
image prune`/`docker rmi`). The repo-local git cache (`playground/.tmp/swebench-repo-cache/`) is small (tens of MB
per repo, shared across instances of the same repo) and is not part of this budget. `--enable-xlarge` on `run`
does **not** by itself make a full run safe — it only lifts the tier-disabled gate; nothing about disk safety
changes, so don't point it at a large instance list without first re-checking the free-disk math for your host.
Scaling `N` up (e.g. to run more than 10 instances) is otherwise trivial via the importer — the disk/time budget,
not the tooling, is the limiting factor here.

Grading uses the **official `swebench` evaluation harness** (subprocess, `sys.executable`, never an internal
import) against a `git diff HEAD` patch extracted from the mutated `ws/repo` checkout (captures both staged and
unstaged changes against the pinned `base_commit` — a bare `git diff` would silently miss a `git add`ed fix); the
harness's own `resolved` verdict is authoritative (`solved = resolved`). An empty diff short-circuits before any
Docker/subprocess spawn. The Docker-eval step is serialized behind a module-global lock across concurrent
`--max-parallel` workers (disk safety takes priority over eval-time parallelism — agent execution still
parallelizes normally).

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
  <date>-<suite>-campaign/                       # written by `ao-bench campaign`, one per campaign run
    campaign.json   # schema_version, campaign_id, tier, caps (whole_run/per_subject), max_parallel,
                     # total_spent_usd, per-subject {status, launched, run_dir, total_cost_usd, solved/total/solve_rate}
    comparison.json / comparison.md   # same shape as a `report`-generated comparison, over exactly this campaign's subjects
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

## Results interpretation

Three tiers now have real, committed run data. Read all three together, not in isolation — each answers a
different question, and the caveats below are load-bearing, not boilerplate.

### `small` — `dev-core` (2026-07-22, saturated cost/latency signal)

All 6 `dev-core` tasks (2 bugfix / 2 feature / 1 refactor / 1 test-writing) solved **6/6** by every subject run —
this is a deliberately *trivial* smoke suite (every task solvable by a competent junior in under 10 minutes):

| Subject | Solved | Total cost | Cost/solved | Total wall-clock |
|---|---|---|---|---|
| `fake-pass` | 6/6 | $0.0000 | $0.0000 | ~0.001s |
| `claude-haiku` | 6/6 | $0.3732 | $0.0622 | 166.37s |
| `ao-epic-haiku` | 6/6 | $0.6617 | $0.1103 | 343.05s |
| `claude-sonnet` | 6/6 | $1.2148 | $0.2025 | 122.28s |
| `claude-opus` | 6/6 | $1.5483 | $0.2580 | 129.97s |
| `ao-epic-sonnet` | 6/6 | $2.8744 | $0.4791 | 387.31s |

Takeaway: on tasks this easy, the `ao-epic` 2-agent workflow does not raise solve rate over bare `claude -p` (both
6/6) — it costs ~1.8–2.4× and takes ~2.1–3.2× the wall-clock of the bare CLI, because the verify agent's extra turn
isn't repaid when the implement agent already gets it right. Read these as **cost/latency at equal (saturated)
quality**, not capability evidence — this is exactly why the `medium`/`large` tiers below exist. Live artifacts:
`benchmarks/results/2026-07-22-dev-core-*/run.json`, `.../2026-07-22-dev-core-compare/comparison.md`.

### `medium` — `dev-medium` campaign (2026-07-22, PLAN Run 1)

`make bench-medium AO_BENCH_CONFIRM=1` — 6 tasks × 4 subjects, `--max-parallel 4` (medium tier default):

| Subject | Solved | Total cost |
|---|---|---|
| `claude-sonnet` | 6/6 | $1.9346 |
| `claude-opus` | 6/6 | $3.2723 |
| `ao-epic-sonnet` (2-agent) | 6/6 | $4.2324 |
| `ao-epic-plus-sonnet` (4-agent plan→implement→review→fix) | 6/6 | $7.6140 |
| **Campaign total** | — | **$17.0533** of $150 whole-run cap |

Verified against `benchmarks/results/2026-07-22-dev-medium-campaign/campaign.json` and each subject's own
`run.json`.

**Finding: the suite saturates at the dev-core-tuned default config (`max_turns=30`) — all 4 subjects solve 6/6.**
This is a real, useful result, but not the one "does orchestration lift solve rate" question implies at face value:
a companion authoring-time probe (documented in `T-Md7Vc3`'s STATUS.md — **not** a committed subject config change,
a scratchpad-only smoke) showed the **same 6 tasks** solve at only **2/6 (33%)** when `max_turns` is capped to 6 for
a bare-CLI subject. **The suite's proven discrimination axis is turn/investigation budget, not raw task
solvability** — at a generous budget, modern agents (even bare `claude -p`) solve well-specified, single-repo,
30–90-minute tasks reliably; discrimination shows up in `cost_per_solved`/turns/wall-clock at a saturated solve
rate, not in solve rate itself, unless the budget is tightened or the task moves outside a single repo (see the
`large` tier below for that).

### `large` — `swe-verified-mini` campaign (2026-07-22, PLAN Run 2)

`make bench-large AO_BENCH_CONFIRM=1` — 10 pinned SWE-bench Verified instances × 3 subjects, official Docker
grading (`resolved` = solved), `--max-parallel 3` (large tier default), ~45.5 min wall-clock for all 3 subjects:

| Subject | Solved | Total cost |
|---|---|---|
| `claude-sonnet` | 9/10 | $7.5931 |
| `claude-opus` | 9/10 | $9.9118 |
| `ao-epic-sonnet` | 9/10 | $8.1571 |
| **Campaign total** | — | **$25.6620** of $800 whole-run cap |

Verified against `benchmarks/results/2026-07-22-swe-verified-mini-campaign/campaign.json` and each subject's own
`run.json`. Grading cost is $0 (Docker, no LLM).

**Key finding**: both bare models (`claude-sonnet`, `claude-opus`) failed the same instance,
`pytest-dev__pytest-10356` (SWE-bench-labeled difficulty "1-4 hours") — `ao-epic-sonnet` **solved it**. On the
single hardest instance in the set, the 2-agent workflow beat both bare models while costing **~7% more than bare
sonnet** and **~18% less than bare opus** ($8.1571 vs $7.5931 vs $9.9118). `ao-epic-sonnet`'s own single miss
(`django__django-11138`) was a **4.5-second transient harness abort** (an `ao_workflow` implement task recorded
`missing_outputs` after 1 turn, empty patch — not a genuine capability failure); the committed `run.json` records
this as-run failure (result integrity — see below), but a supplementary diagnostic re-run of just that instance
(scratch dir, deliberately **not** committed to `benchmarks/results/`) solved it 1/1 at $1.1492/252s, which is why
we call out `max_attempts` for `ao_workflow` benchmark subjects as a follow-up (see `meta/learnings.md`).

**Caveats — read every one of these before citing a number above:**
- **N=10 is a small sample.** One instance is worth 10 percentage points of solve rate. Treat the 90% vs 90% vs 90%
  solve-rate parity as directional, not statistically significant.
- **Subset selection is biased toward smaller-image/faster-test instances** (see `instances.json`'s difficulty mix:
  6× "15 min–1 hour", 3× "1–4 hours", 1× "<15 min fix"; Django favored for small images + fast targeted test runs
  — see [SWE-bench import](#swe-bench-import-the-large-tier)). Our 90% solve rate is **higher** than published
  ~70–77% sonnet-class numbers on the full SWE-bench Verified leaderboard — this pinned subset is measurably
  **easier** than the full benchmark; do not extrapolate our solve rate to the full 500-instance suite.
- **The `pytest-10356` discrimination is one instance.** Directionally meaningful (it IS the hardest-by-label
  instance in the set, and it IS where the multi-agent workflow's extra verify/review capacity earned its keep),
  but not a statistically powered claim about `ao-epic-sonnet` vs bare models in general.
- **Repo mix is skewed: 5 of 10 instances are `django/django`** (a side effect of the small-image/fast-test
  selection bias), so django familiarity dominates half the suite. On the next `import-swebench` regeneration,
  prefer ≤2–3 instances per repo if repo-diversity of the signal matters more than image efficiency.
- **`medium`-tier numbers measure cost/latency at a saturated solve rate plus turn-budget-constrained
  discrimination** (see above) — not raw capability, which is what the `large` tier's external, harder suite is
  for.

### Total epic spend

Across the whole `E-Bt4Xk9` epic (framework authoring smoke/gold-patch verification + both PLAN campaigns above):
**≈$48.6 total** — `medium` campaign $17.05 + `large` campaign $25.66 + authoring/smoke/gold-patch/diagnostic
spend (dev-medium's haiku/sonnet authoring gate, the swebench grader's real gold-patch verification, the
`django__django-11138` diagnostic re-run above) ≈$5.9 — against an **$800** whole-large-run envelope (and a $100
per-model envelope, both from `benchmarks/tiers.json`'s `large` tier). Scaling levers exist but were deliberately
**not** pulled for this PLAN run: raising `large`-tier N to ~30 would exceed the ~37 GB disk budget under
`keep_images` and add hours of wall-clock for still-small-sample-size gains; the intended path if more signal is
ever needed is re-importing with a larger `--instances` list on a host with more free disk (`ao-bench
import-swebench` makes scaling N a one-command operation — see [SWE-bench import](#swe-bench-import-the-large-tier)).

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

## Non-MVP / roadmap (not shipped — see HLD §12/§20, ADR-0008 + ADR-0009 follow-ons)
`LlmJudgeGrader` · an inspect-ai bridge · Aider/EvalPlus subset importers · non-dev domain suites (framework
supports via `domain`+`CommandGrader`, ships none) · HTTP/API subjects · multi-seed/bootstrap-CI/pass@k statistical
rigor · a results dashboard/`ao-bench serve` · a nightly cron trigger · a `grading_overlay` held-out-test
*framework* feature (medium's per-fixture `.grading/` honor-system convention is used instead, matching
`dev-core`'s `check.py` precedent) · the full ~500-instance SWE-bench Verified run (`xlarge`, defined but disabled
— disk-infeasible here) · a cross-process lockfile for concurrent `ao-bench run`/`campaign` invocations against the
same result dir (see [Running](#running)) · `keep_images`/`GraderConfig` becoming suite-driven (today it's an env
var + test-only constructor arg — see ADR-0009's Outcome section) · widening `RunContext`/`runner.py` to carry
`task`/`task.source` through to `Grader.grade` (today the swebench grader derives instance id from `repo_dir`'s
parent dirname instead — same ADR-0009 section).

**Shipped this epic (`E-Bt4Xk9`, previously listed here as non-MVP):** `SweBenchGrader` (Docker eval) + the
SWE-bench Verified importer + `swebench` `WorkspaceProvider` (the `large` tier) · USD budget enforcement ·
`--max-parallel` · `ao-bench campaign` · the `medium`/`large` tiers themselves · the actual Phase-2
Sonnet-vs-Opus-vs-`ao` capability comparison (see [Results interpretation](#results-interpretation)).
