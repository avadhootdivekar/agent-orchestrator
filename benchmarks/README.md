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
| **Workspace** | A disposable, path-guarded copy under `${AO_BENCH_WORKSPACE_ROOT:-/var/tmp/ao-bench/<uid>}/ws/` — outside the checkout so no repo `CLAUDE.md`/skills leak into the agent (`T-Ws7Iso`); the subject mutates it, the grader reads it. The committed fixture itself is never touched. |

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

Anything the agent must not see lives in the task's **`grading_dir`**: a per-(subject x task) directory the runner
creates next to — never inside — the agent-visible workspace (HLD §8 "Hidden-asset rule"). A grader reaches it via
`{grading_dir}` / `{repo_dir}` placeholders in its `command`, and a task seeds it from `grader.assets` (a
suite-relative dir that `materialize_workspace` copies into `grading_dir`).

`dev-medium` uses exactly that (migrated from the old agent-readable `fixture/.grading/` on 2026-10-23; every task's
`config_fingerprint` changed with it, so pre-migration results are not run-for-run comparable): each task has
`tasks/<id>/grading/{run.py,tests/}` and

```json
"grader": {"type": "command", "assets": "tasks/<id>/grading",
           "command": "uv run python {grading_dir}/run.py --repo {repo_dir}", "cwd": "."}
```

`run.py --repo <repo>` (default: cwd) re-runs the *visible* `tests/` plus the held-out `tests/` next to it (or, for a
test-writing task, mutation-tests the agent's own test file against several independently seeded single-bug
variants). `solved` is gated by the full harness, not the visible suite alone — verified per task at authoring time
(`run.py` must FAIL on the unmodified fixture and PASS once the task's `.bench-solution/` reference fix is applied;
`tests/bench/test_dev_medium_suite.py` asserts this for all 6 tasks, plus that nothing grader-owned is reachable from
the agent's workspace).

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

## Tier-2 brownfield suites (`repo_mutation`, `pr_mined`, `pytest_targeted`)

HLD `docs-md/benchmark-discriminating-suites-hld.md` §4.2. The agent gets a real repo at a pinned commit plus an
instruction; hidden tests and mutation metadata stay in the task's `grading_dir`. Hermetic tests build a local git
repo (`tests/bench/test_mutation_provider.py`, `test_pr_mined.py`); the real-clone tier is opt-in:
`AO_E2E_NETWORK=1 uv run pytest -m network tests/bench`.

**Workspace shape (both providers).** The repo is checked out through the shared cache
(`playground/.tmp/swebench-repo-cache/`), mutated, then **re-snapshotted as one parentless commit**: `git diff` shows
exactly the agent's own work, and `git stash` / `git checkout .` / `git log --all` / `git fetch origin` can neither
undo an injected bug nor reach a merge commit. Hidden tests are never in `repo_dir`; the `pytest_targeted` grader
restores `grading_dir/hidden/**` at grade time. The provider also parks the pristine/gold files in
`grading_dir/reference/`, so `FakeSubject(scripted_effect="copy-reference")` proves a task is solvable at zero cost.

**`repo_mutation`** — `source: {"type": "repo_mutation", "repo": "<clone URL or owner/name>", "base_commit": "<40-hex>",
"recipe": "tasks/<id>/recipe.json"}`. Recipe keys (unknown keys are rejected):

| key | meaning |
|---|---|
| `mode` | `inject` (bug), `excise` (feature + tests removed), `enhance` (new behaviour, hidden tests) |
| `edits` | `[{"file","find","replace","count"=1}]` — literal find/replace; the `find` text must occur exactly `count` times |
| `remove` | repo paths deleted from the checkout (not restored) |
| `hidden` | `{repo path: suite-relative source}` — copied to `grading_dir/hidden/<path>` |
| `hidden_from_repo` | repo paths taken from the checkout into `grading_dir/hidden/` and deleted from `repo_dir` (e.g. the existing tests that catch the injected bug) |
| `fail_to_pass` / `pass_to_pass` / `test_targets` | node ids, and whole files/dirs that must pass |
| `install`, `test_command` | optional; grader defaults |
| `verified` | `{"date","note"}` — how the recipe was proven (`null` if not) |

**`pytest_targeted` grader** (suite.json `grader` block; omitted keys come from the provider's `recipe.resolved.json`):
`fail_to_pass`, `pass_to_pass`, `test_targets`, `pass_to_pass_sample` (30), `install`, `test_command`
(`{python} -m pytest -q -p no:cacheprovider {targets}`), `per_test_timeout_seconds` (60, needs `pytest-timeout`),
`total_timeout_seconds` (900), `install_timeout_seconds` (900), `baseline_commit`. It restores hidden tests, runs
`install` once, runs ONE bounded pytest invocation, and scores `0.7 * f2p_rate + 0.3 * p2p_rate` (an empty group's weight
renormalises away); `solved` = every f2p id / every test in `test_targets` / every sampled p2p id passed. A failing
`install` is a `GraderError` (infrastructure), failing tests are an ordinary unsolved grade. Reported, never scored:
`collateral` (`files_touched`/`lines_added`/`lines_deleted` of the agent's diff vs the snapshot), `agent_tests_added`.

**`pr_mined`** — `source: {"type": "pr_mined", "instance_id": "owner__name-<pr>"}`; metadata in the suite's
`instances.json`, written by

```bash
uv run ao-bench import-prs --repo owner/name --since 2026-06-01 --out benchmarks/suites/<suite> --limit 20 \
    [--min-test-files 1] [--install "uv venv .venv && uv pip install -e . pytest pytest-timeout"]
```

(needs the `gh` CLI, authenticated). It selects merged PRs that change >= `--min-test-files` pytest modules AND a
non-test `.py` file, pins `base_commit` to the merge commit's first parent, strips links/@mentions/`#123` from the
text and generates `tasks/<id>/instruction.md` — **review those for solution hints before committing**. Without
verified node ids an instance is graded by whole test files (`test_targets`), whose score is dominated by tests that
already passed; a calibration step that fills `fail_to_pass`/`pass_to_pass` on the instance upgrades it to precise
scoring (the provider honours them when present).

**`repo-mutant-mini`** (`benchmarks/suites/repo-mutant-mini/`) — 3 inject + 1 excise recipes on pinned
`pallets/click`, `python-attrs/attrs`, `Textualize/rich`, each under 5 s of targeted pytest and network-verified
(unsolved with the mutation, solved with the pristine files; see each recipe's `verified`).

## Discriminating suites (E-Bn7Dsc): parity, generated tasks, repeats

Design: [`../docs-md/benchmark-discriminating-suites-hld.md`](../docs-md/benchmark-discriminating-suites-hld.md);
probe contract: [`../docs-md/bench-probe-dsl.md`](../docs-md/bench-probe-dsl.md); generator internals:
[`../docs-md/bench-generators.md`](../docs-md/bench-generators.md). Motivation in one line: the committed
2026-07-22 results saturate (every subject 6/6 on `dev-core` and `dev-medium`, 9/10 on `swe-verified-mini` at
n=10/r=1), so they rank subjects by price, not capability.

### Parity bundle (hard rule)
Every subject in a comparison runs under ONE bundle — `benchmarks/parity/default/` (`parity.json`, a shared
`CLAUDE.md`, a `claude/` tree of skills/settings, env knobs, `max_turns`, `permission_mode`). A suite names it
with `"parity": "../../parity/default"`; `--parity <dir>` overrides it for *all* subjects of a run/campaign. The
bundle's content fingerprint is recorded in `run.json` (`parity_fingerprint`) and in the run config fingerprint;
`build_comparison` / `ao-bench report` refuse to compare runs whose fingerprints differ (`--allow-mixed` stamps
the report `parity_mixed`). Mechanics: `CLAUDE.md` lands at `<ws>/CLAUDE.md` (parent of `repo/`, read by both
`claude -p` and every `ao` agent, never in the repo diff), `claude/` lands at `<repo>/.claude/` and is added to
`.git/info/exclude` for git checkouts. Keep subject `prompt_template`s thin — instructions belong in the bundle.

### Environment isolation (`T-Ws7Iso`, hard rule for ranking)
Parity fixes what the harness ships; isolation makes sure nothing else reaches the agent (HLD §2c). Preflight:
```bash
uv run ao-bench env --probe --model claude-haiku-5-5   # JSON; exit 2 if the bench root would leak instructions
```
- **Root.** Workspaces: `${AO_BENCH_WORKSPACE_ROOT:-/var/tmp/ao-bench/<uid>}/ws`; grading assets: the sibling
  `<base>/_ao_bench_grading`. `run`/`campaign`/`calibrate` refuse an agent subject when that root or any ancestor
  holds `CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md` or `.claude/`; `--allow-contaminated-env` runs anyway and stamps
  `env_contaminated`.
- **Fingerprint.** Each task records the union of its transcripts' `system/init` events (skills, agents, slash
  commands, MCP servers, plugins); each run records a baseline from a throwaway `claude -p` in an empty
  parity-applied workspace (killed at its init event) and the user-level config (`~/.claude/settings.json` keys
  such as `model`/`effortLevel`/`enabledPlugins`, user `CLAUDE.md` hash, `CLAUDE_*`/`ANTHROPIC_*` env).
  `report` refuses foreign entries, contaminated runs and differing digests between agent subjects
  (`--allow-mixed` stamps the report **NON-ISOLATED**); a campaign stops its repeats instead.
- **Audit.** A tool call whose input names `_ao_bench_grading` marks the task `suspect` (`grading_access`); it is
  excluded from every rate, roll-up, pairwise test and calibration pool.
- **Default bundle env.** `ENABLE_CLAUDEAI_MCP_SERVERS=false` (no claude.ai connectors such as Google Drive) and
  `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`. `CLAUDE_CONFIG_DIR` is deliberately *not* relocated (it would fork the
  operator's OAuth credentials); user-level settings are recorded instead, so e.g. `effortLevel` applies to every
  subject and is visible in `summary.md`.
- **Held-out seeds.** Ranking campaigns use a `--seed` absent from every committed suite and record it in the
  results entry below; keep at least one generated family unseen while tuning `ao` templates/instructions.

### Hidden assets: `grading_dir`
Probes, reference implementations and held-out tests live in a grader-owned dir
(`<base>/_ao_bench_grading/<run>/<subject>/<task>/`), under a sibling root of the agent-visible workspace root
`<base>/ws` — never inside it.
A task's `grader.assets` (suite-relative dir) is copied there; `command`/`pytest` graders may reference it as
`{grading_dir}` (and the repo as `{repo_dir}`). `dev-medium` was migrated to this layout on 2026-10-23.

### Tier 1 — generated behavioural-spec tasks (`generated` provider + `blackbox` grader)
```bash
uv run ao-bench gen --out benchmarks/suites/gen-services --family crud-relational --family job-queue \
    --count 2 --languages python,typescript,go --seed 20261023 --parity ../../parity/default
uv run ao-bench images --build --suite benchmarks/suites/gen-services/suite.json   # per-language sandbox images
uv run ao-bench run --suite benchmarks/suites/gen-services-mini/suite.json --subject benchmarks/subjects/claude-haiku.json
```
- A task is `(family, seed, difficulty{entities,traps,concurrency,scale}, language)`; the suite stores only
  those parameters — SPEC.md + skeleton are regenerated into `repo/`, and `instance.json` + `probes.json` +
  the stdlib Python `reference/` into `grading_dir`, at materialize time. Nothing hidden is ever committed.
- The agent must make `./run.sh` serve the spec (`http`: bind `$HOST:$PORT`, `/healthz`; `jsonl`: stdin→stdout;
  `cli`: argv) using only the suite `stack` allow-list (default: stdlib + `sqlite3`, `montydb` for Python;
  `node:sqlite`; `modernc.org/sqlite`). No external services.
- Grading is behaviour-only, inside Docker (`python:3.12-slim` / `node:22-alpine` / `golang:1.26-alpine`, deps
  pre-installed into a derived image, cpu/mem/pid limits, no network except the published port; `process`
  fallback is tagged in `grader_detail.sandbox`). Score = 0.5·core + 0.35·trap + 0.15·perf; `solved` = all core
  probes pass and score ≥ 0.8. Perf fits a scaling exponent α per op class over N ∈ sizes and compares p95 to
  the hidden reference (`ref_ratio`, enforced only for same-language artefacts).
- Families today: `crud-relational` (http) and `job-queue` (jsonl); six more are specified in the HLD §4.1.

### Repeats, paired statistics, calibration
```bash
uv run ao-bench campaign --suite S --subject A --subject B --repeats 3            # staged: repeat only undecided tasks
uv run ao-bench run --suite S --subject A --repeat 2                              # one explicit repeat (dir <id>-r2)
uv run ao-bench report --results-root benchmarks/results --suite <suite-id>       # groups -r<n> dirs per subject
uv run ao-bench calibrate --suite S --subject haiku --subject opus --repeats 2 --band 0.2,0.8 --write
```
`comparison.json` schema 2.0 adds `parity_*`, `repeats_per_subject`, `subject_stats` (attempted-based rates —
`subject_status="error"` runs are infra, excluded), `infra` (counts + "suspect" unsolved runs under $0.15 / 180 s),
`per_family`, `per_language`, `pairwise` (paired bootstrap 95 % CI + Wilcoxon p on per-task score diffs) and
`unreliable_subjects` (> 10 % infra). Staged campaigns re-run only tasks where subjects disagree, a subject's own
repeats vary, or a subject has no attempted sample; `campaign.json` records the `stage_log`.

### Opt-in test tiers
`AO_E2E_DOCKER=1 uv run pytest -m docker tests/bench` (sandbox + blackbox in a real container),
`AO_E2E_NETWORK=1 uv run pytest -m network tests/bench` (Tier-2 recipes against real clones). Default
`pytest tests/bench` is hermetic.

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
pointer only. Workspaces themselves live under `${AO_BENCH_WORKSPACE_ROOT:-/var/tmp/ao-bench/<uid>}/ws/` (runs
before 2026-10-24: `playground/.tmp/bench/`, gitignored) and are **not auto-deleted** — preserved for audit; clean
up manually.

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

### `medium` — `gen-services-mini` haiku campaign (2026-10-23, E-Bn7Dsc first real run)

`benchmarks/results/2026-10-23-gen-services-mini-{claude-haiku,ao-epic-haiku,campaign}/` — the first real-LLM run
of the Tier-1 path under the default parity bundle (fingerprint `06aaf5ad…`, shared by both runs), graded in Docker.

| Subject | Solved | Mean score | Cost | Wall (2 tasks) | Notes |
|---|---|---|---|---|---|
| claude-haiku | 2/2 | 1.000 | $0.229 | 453 s | crud 69 turns / 316 s; job-queue 46 turns / 136 s |
| ao-epic-haiku | 2/2 | 1.000 | $0.245 | 632 s | implement→verify DAG; +7 % cost, +40 % wall |

Every probe group was 100 % for both (crud 21 core / 4 trap / 5 perf, job-queue 11 / 4 / 6), so the pairwise CI is
[0, 0]: **at difficulty `{entities:2, traps:2, concurrency:1, scale:1}` these two instances are saturated even for
haiku.** That is the expected first-pass outcome and exactly what `ao-bench calibrate` is for — the next step is to
regenerate at a higher difficulty vector (more entities/traps, concurrency ≥ 2, scale ≥ 2, and the TS/Go variants)
and calibrate against haiku + opus until pooled pass rate sits in the 20–80 % band (HLD §7). What this run DOES
establish: the full Tier-1 pipeline (parity → generated spec → agent → Docker blackbox grade → repeat-aware
comparison with infra/pairwise/family tables) works end to end for about $0.25 per subject. One more calibration note from the
perf data: at `scale:1` (sizes 100 / 1000) the fitted α was ≈ −0.4 for every op class — latency *fell* with N,
i.e. JIT/warm-up noise dominates at these sizes — so the perf group only carries signal from `scale ≥ 2`
(sizes up to 10⁴–10⁵); treat `scale:1` perf as a smoke check.

**Environment caveat (found 2026-10-24, `T-Ws7Iso`):** these workspaces lived under `<repo>/playground/.tmp/bench`, so
both agents also had the two ancestor `CLAUDE.md` files and every repo/control-plane skill and agent loaded (see the
`init` event in each transcript). Parity held (identical leak for both subjects) and nothing read `_grading/`, so the
pipeline proof stands, but treat these numbers as *non-clean*; do not cite them as a ranking. Fixed on 2026-10-24
(`T-Ws7Iso`): a re-run of one `dev-core` task per subject from `/var/tmp/ao-bench/<uid>/ws` showed both subjects'
init fingerprints equal to the run's baseline, with no repo or control-plane skill/agent, no MCP server and
auto-memory off ($0.013 total, scratch results not committed).

### `medium`/`large` — calibration + 4-subject ranking (2026-10-24, `T-Cb8Rk2`)

**Question.** Can the generated suites be tuned to tell `claude-haiku`, `claude-sonnet`, `claude-opus` and an `ao`
overseer on Sonnet only (`ao-overseer-sonnet`) apart, within $20 per subject?
**Answer: not on quality.** Every subject solved all 10 ranking tasks. The suites are at their ceiling for current
models even at the maximum valid difficulty. The subjects separate clearly on **cost and time**.

**Setup.** The protocol was pre-registered in the ticket before any spend. The default parity bundle applies to
everyone: `max_turns` 30, `bypassPermissions`, `CLAUDE_CODE_EFFORT_LEVEL=high`, claude.ai connectors off,
auto-memory off. All four subjects share the init fingerprint `62181309c7b1`, which equals each run's baseline. There
were no foreign entries and no grading-access suspects. Tasks ran three at a time; subjects ran one after another
from a clean tree. The ranking used two suites, ten tasks in all:
- `gen-rank-v1`: 6 generated Python tasks (3 crud-relational http, 3 job-queue jsonl). Fresh seeds 9511-9513 at
  the maximum valid vector `entities=3, traps=3, concurrency=3, scale=2`.
- `repo-mutant-mini`: 4 Tier-2 tasks (bugs injected into or a feature excised from click, attrs, rich).
`ao-overseer-sonnet` is the builtin `overseer-runner` with `run_budget_usd=2`, `wave_size=3`, `max_waves=2` and
`final_push=false`.

**Calibration (anchors haiku + opus only, seeds 7301-7322).**

| Round | Vector | Reference self-check | haiku | opus | Spend |
| --- | --- | --- | --- | --- | --- |
| 1 | 3,3,2,2 | failed 4/4 (two harness bugs, fixed) | 4/4 at 1.0 | 4/4 at 1.0 | $2.64 |
| 2 | 3,3,3,2 | 4/4 at 1.0 | 4/4 at 1.0 | 4/4 at 1.0 | $3.29 |
| scale 3 | 3,3,3,3 | crud perf not gradeable (reference over budget at 100k; label collisions) | — | — | $0 |
| Tier 2 | `repo-mutant-mini` | 4/4, do-nothing 0/4 | 4/4 | 4/4 | $0.84 |

**Ranking results** (`2026-10-24-gen-rank-v1-compare/`, `2026-10-24-repo-mutant-mini-compare/`):

| Subject | Solved | Mean score | Spend ($) | $/task | $/solved | Wall/task (s) | Output tokens/task (gen) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| claude-haiku | 10/10 | 0.985 | 0.71 | 0.071 | 0.071 | 205 | 58.7k |
| claude-sonnet | 10/10 | 0.985 | 1.66 | 0.166 | 0.166 | 56 | 10.3k |
| claude-opus | 10/10 | 0.970 | 4.55 | 0.455 | 0.455 | 147 | 15.9k |
| ao-overseer-sonnet | 10/10 | 0.985 | 17.93 | 1.793 | 1.793 | 560 | 74.6k |

Core and trap probes passed 100% for every subject. Every pairwise CI is centred on zero.

**Insights**
- **The score gaps are not capability.** All 4 subjects lost perf points on `crud-relational-py-s9512`. A generator
  bug made the reference itself fail there, because the perf load's random label collided with the unique-label
  trap; this is fixed after the campaign. Opus also lost perf points on `s9513`, and that was a grading flake: the
  artefact refused connections under parallel grading, then scored 1.0 twice when re-graded alone. With both
  removed, every subject scores 1.0 on every task.
- **Cost frontier.** Haiku dominates on cost: 2.3x cheaper than sonnet, 6.4x cheaper than opus and 25x cheaper than
  the overseer, at equal quality. Sonnet dominates on time, at 56 s per task against 147 s for opus and 205 s for
  haiku. Haiku needs 4 to 6 times more turns and output tokens than sonnet, and hit `max_turns` on one task while
  still solving it.
- **Orchestration overhead.** The overseer costs 10.8x and takes 10x the wall time of bare Sonnet, for the same
  outcome. On the generated suite, implementation units take only about 22% of its spend. Verify units take 27%,
  checkpoints 18%, intake 17%, final-verify 8%, branch-off 5% and closeout 4%. At this difficulty the extra
  stages buy nothing measurable.
- **ao finding.** The `run-budget-backstop` breaker stopped 5 of 6 overseer runs on the generated suite at $2,
  before the close-out tail. The graceful budget staging (explore through closeout) never engaged. The template's
  cost heuristics, `default_unit_cost_usd` 8 and `default_ckpt_cost_usd` 5, are sized for large runs. The work was
  already done, so it was graded solved.

**Validity and the tuning-versus-fitting check**
- Every tuned knob was global, chosen on anchor data only, and never moved after Sonnet or the overseer ran.
  Calibration seeds are disjoint from ranking seeds. Grader weights and threshold were frozen.
- Six harness or grader validity fixes were made. Each was triggered by the reference failing its own probes or
  by an infrastructure error, never by a subject's score:
  - jsonl requests may now be any JSON value;
  - HTTP grading connects to the container IP with `SO_LINGER 0`, after about 30k connections exhausted the
    ephemeral ports through `docker-proxy`;
  - a grader exception keeps the subject's real cost;
  - `init_git` now works on repos that are already git;
  - the crud perf load label embeds the load index;
  - the reference self-check now requires a score of 1.0.
- Some levers were deliberately not used, because each would have manufactured a ranking. A lower `max_turns`
  would favour `ao`, since its limit applies per agent. Up-weighting perf would amplify timing noise. Dropping
  instances, or tightening timeouts after seeing results, would also have fitted the instrument.
- Declared asymmetries and reuse:
  - only the overseer's repo is a git repo, which its branch-off stage requires;
  - its per-task budget was lowered from 3 to 2 before ranking, to fit the cap;
  - haiku's and opus's `repo-mutant-mini` ranking runs are their calibration runs, with nothing selected by them.
- Limitations: n=10, Python only, r=1, and two families at their ceiling.

**What it would take to get quality signal:**
- the six unbuilt Tier-1 families, or multi-service tasks;
- TS/Go, once the agent host has Node 22 or later and Go, or agents run inside the sandbox image;
- recent `pr_mined` instances from after the model cutoff, imported with `ao-bench import-prs`;
- a larger brownfield suite.

**Spend:** $32.75 in total, including $0.80 estimated for two unrecorded invalid runs, against the $250 ceiling.
Ranking spend per subject was haiku $0.71, sonnet $1.66, opus $4.55 and overseer $17.93, each within its $20 cap.

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
*framework* feature beyond `grader.assets` + `grading_dir` (dev-medium's held-out harness uses those; see
[Held-out grading](#held-out-grading-for-discriminating-medium-suites)) · the full ~500-instance SWE-bench Verified run (`xlarge`, defined but disabled
— disk-infeasible here) · a cross-process lockfile for concurrent `ao-bench run`/`campaign` invocations against the
same result dir (see [Running](#running)) · `keep_images`/`GraderConfig` becoming suite-driven (today it's an env
var + test-only constructor arg — see ADR-0009's Outcome section) · widening `RunContext`/`runner.py` to carry
`task`/`task.source` through to `Grader.grade` (today the swebench grader derives instance id from `repo_dir`'s
parent dirname instead — same ADR-0009 section).

**Shipped this epic (`E-Bt4Xk9`, previously listed here as non-MVP):** `SweBenchGrader` (Docker eval) + the
SWE-bench Verified importer + `swebench` `WorkspaceProvider` (the `large` tier) · USD budget enforcement ·
`--max-parallel` · `ao-bench campaign` · the `medium`/`large` tiers themselves · the actual Phase-2
Sonnet-vs-Opus-vs-`ao` capability comparison (see [Results interpretation](#results-interpretation)).
