# HLD + LLD — `ao` Benchmarking Framework

- Epic: [`E-9Qk4Zt-agent-benchmark-harness`](../meta/tickets/E-9Qk4Zt-agent-benchmark-harness/EPIC.md)
- Landscape survey (evidence): [`benchmark-landscape-survey.md`](benchmark-landscape-survey.md)
- Decision record: [`adr/ADR-0008-benchmark-harness-approach.md`](adr/ADR-0008-benchmark-harness-approach.md)
- Master HLD: [`hld-agent-orchestrator.md`](hld-agent-orchestrator.md)
- Date: 2026-07-22 · Author: architect agent · Status: **Implemented (as-built — MVP delivered 2026-07-22)**. This doc is
  reconciled to the shipped code (T-Dcs2Rk); every as-built deviation from the original design is called out inline
  where it occurs, and Q1–Q3 (§18) are resolved to their final answers. ADR-0008 status: Accepted.

> **Scope of this doc:** design a declarative, pluggable, resumable, observable, safe-by-default
> benchmarking framework that compares **subjects** (an `ao` workflow vs bare `claude -p` at a
> given model vs, later, other CLIs/APIs) on **suites** of dev tasks, and writes machine-readable
> + human-readable **results** to a git-committed directory. It follows the same design principles
> as the core engine (structured JSON/YAML specs validated by schema, ABC/protocol seams,
> deterministic/resumable, observable, bounded/safe) — but is a **separate module that shells out
> to `ao`/`claude` and never enters the engine import graph** (CLAUDE.md: "third-party integrations
> must never destabilize the core"). Phase-2 (actually running Sonnet vs Opus vs `ao` and reporting
> numbers) is **out of scope for this epic** — this delivers the framework + a runnable MVP suite.

---

## 1. Goals / Non-goals / Constraints

### Goals (MVP)
- **G1** Compare ≥3 subjects — (a) an `ao` multi-agent workflow, (b) bare `claude -p --model opus`, (c) bare `claude -p --model sonnet` (+ haiku for cheap smoke) — on the **same** dev tasks.
- **G2** Metrics per (subject × task): correctness/pass-rate, wall-clock seconds, **cost USD**, tokens (in/out/cache), attempts/turns, subject status.
- **G3** Declarative suites (JSON) validated by schema; tasks reference a fixture repo + instruction file + a pluggable grader — payloads referenced **by path**, never inlined (mirrors NFR-1).
- **G4** Machine-readable results (`run.json`) + human summary (`summary.md`) + cross-subject comparison, written to a git-committed `benchmarks/results/<date>-<suite>-<subject>/`.
- **G5** Entry points: a standalone `ao-bench` CLI + Make recipes.
- **G6** A curated MVP dev suite (4–8 tiny fixture tasks: bugfix, small feature, refactor, test-writing) runnable end-to-end for all three subjects, results committed, `make bench-smoke` green at haiku.
- **G7** Safe/bounded/deterministic: workspaces in a repo-local gitignored dir; per-task timeout; budget/max-turns pass-through; recorded configs/fingerprints so a result dir is reproducible from its spec.

### Non-goals (this epic — see §12)
LLM-judge grader; SWE-bench/terminal-bench Docker imports; inspect-ai bridge; non-dev domain suites (biology/physics/legal); HTTP/API subjects (raw Anthropic/OpenAI, aider CLI); statistical rigor (multi-seed, bootstrap CIs, pass@k); dashboards; **and running the actual Phase-2 comparison** (that consumes this framework later).

### Constraints (from repo learnings — see survey §0)
Subjects are subprocesses (C1); workspaces repo-local gitignored, never `/tmp` (C2); real-LLM is expensive → small suite, haiku smoke (C3); reuse core cost/usage helpers (C4); `uv run` only, Docker deferred (C5); deterministic & reproducible (C6). Workflow spec `id`s must be lowercase.

## 2. Assumption Log

```
ASSUMPTION A1: `claude` CLI is on PATH and authenticated in the bench environment (same as
   the repo's real_llm e2e tier). Risk: bare-claude subject can't run. Mitigation: `ao-bench
   validate` probes `claude --version`; a FakeSubject drives all non-real tests in CI.
ASSUMPTION A2: `uv run ao run …` works as a subprocess from the repo (editable install). Risk:
   a stale global `ao` snapshot silently ignores new flags (see learnings). Mitigation: invoke
   `uv run ao` from repo root, never the global `ao`; record `ao --version` in run.json.
ASSUMPTION A3: fixture test commands emit a parseable pass/fail signal. Risk: brittle summary
   parsing. Mitigation: grade primarily on the grader command's EXIT CODE; parse the pytest
   summary line only to enrich `score` (pass-rate), never as the pass/fail source of truth.
ASSUMPTION A4: For the `ao_workflow` subject, run cost = compute_run_usage_totals over the run's
   state.json in the per-task workspace. Risk: multiple runs in one workspace confuse attribution.
   Mitigation: fresh workspace per (subject,task) → exactly one run_id under
   <ws>/.orchestrator/runs/.
ASSUMPTION A5: A task's LLM solution is inherently stochastic; only the harness scaffolding is
   deterministic (fixed task set, pinned instructions, recorded configs). Risk: users expect
   identical scores across runs. Mitigation: document explicitly; make the result DIRECTORY layout
   + inputs reproducible from spec, not the score.
ASSUMPTION A6: Model ids are pass-through strings to `--model`/`AO_MODEL`. The Claude Code CLI
   accepts both aliases (`opus`/`sonnet`/`haiku`) and full ids (`claude-opus-4-8`,
   `claude-sonnet-5`, `claude-haiku-4-5`). Risk: an id drifts/retires. Mitigation: model id lives
   only in committed subject configs; never hard-code in code; record the resolved value in run.json.
```

## 3. HLD — architecture

```
 benchmarks/                              src/agent_orchestrator/bench/   (NEW module — NOT imported by engine)
  suites/<suite>/suite.json  ──────────▶ ┌───────────────────────────────────────────────────────┐
  suites/<suite>/tasks/<t>/  fixture/    │  spec.py     load+validate suite.json / subject.json    │
                             instruction │  (jsonschema, additionalProperties:false, versioned)    │
  subjects/<subject>.json  ─────────────▶│                                                         │
                                         │  runner.py   for (subject × task):                      │
                                         │    materialize ws → run subject → grade → collect       │
                                         │    metrics → write per-task result (resumable, bounded) │
                                         │      │             │              │                     │
                                         │      ▼             ▼              ▼                     │
                                         │  Subject(ABC)   Grader(ABC)   metrics.py                │
                                         │  ├ AoWorkflow   ├ Pytest      (reuse core helpers ↓)     │
                                         │  ├ ClaudeCli    ├ Command                               │
                                         │  └ Fake(test)   ├ FileAssertion                         │
                                         │                 └ Fake(test)                            │
                                         │  results.py  run.json + summary.md + comparison report │
                                         │  cli.py      `ao-bench validate|run|report|list`        │
                                         └───────┬───────────────────────┬────────────────────────┘
             reuses (import only, read-only)     │                       │  shells out (subprocess)
        agent_orchestrator.executors.claude_cli  │                       ▼
        .parse_usage_and_429/.extract_result_event│         playground/.tmp/bench/<runid>/<subject>/<task>/
        agent_orchestrator.models.compute_run_usage_totals        repo/ (mutable fixture copy)  ← gitignored
                                                                  INSTRUCTION.md, capture/*
                                                                          │ grade
   benchmarks/results/<date>-<suite>-<subject>/  ◀── committed ───────────┘
     run.json  summary.md   (+ …-compare/comparison.{json,md})
```

Import direction is **one-way**: `bench/` imports pure helpers *from* core; **nothing in core imports `bench/`**. The engine's `ao run` runs whether or not `bench/` exists (safety invariant SI-1).

> **As-built note (module split — the diagram above simplifies this):** `runner.py`, not `results.py`, owns and
> atomically persists `run.json` (write-temp + atomic rename after every task, mirroring `runstate.RunStateStore.save`)
> — the resume/crash-recovery contract requires the writer and the orchestration loop to be the same module.
> `results.py` builds on top of the already-persisted `run.json` shape (`load_run` → `BenchRunRecord.model_validate`)
> and owns `write_summary_md` + the cross-subject `build_comparison`/`write_comparison` writers. There is also a
> tenth module not shown above, `workspace.py` (`RunContext` model + `materialize_workspace`), which both subjects
> and the runner depend on for path-guarded workspace setup — see §9's file list.

### 3.1 Core domain concepts

| Concept | Meaning |
|---------|---------|
| **Suite** | A `suite.json` (versioned, schema-validated): `id`, `domain`, list of tasks. A benchmark = one suite. |
| **Task** | `id`, `instruction` (path to a `.md`), `fixture` (path to a tiny repo dir), `category` (bugfix/feature/refactor/test), `grader` (structured object), `timeout_seconds`, `tags`. Payloads by path. |
| **Subject** | The system under test, behind a `Subject` adapter: `AoWorkflowSubject` (runs an `ao` DAG), `ClaudeCliSubject` (bare `claude -p --model …`), `FakeSubject` (deterministic, test-only). Config = a `subject.json`. |
| **Grader** | Pluggable verdict over the mutated workspace: `PytestGrader` (test-pass-rate), `CommandGrader` (exit-code of any command — enables non-dev domains), `FileAssertionGrader` (files exist/contain), `FakeGrader` (test-only). LLM-judge = non-MVP. |
| **Runner** | Orchestrates `for subject × task`: materialize workspace → run subject → grade → collect metrics → persist per-task result. Deterministic launch order, resumable, bounded. |
| **Metrics** | `solved`, `score∈[0,1]`, `wall_clock_seconds`, `cost_usd`, tokens, `attempts`/`turns`, `subject_status`. |
| **Result set** | Per (suite × subject): `run.json` + `summary.md`. Cross-subject: `comparison.{json,md}`. Committed under `benchmarks/results/`. |
| **Workspace** | Per (bench-run × subject × task) disposable dir under `playground/.tmp/bench/` (gitignored). The subject mutates it; the grader reads it. |

### 3.2 Integration points & plugin strategy
- **Reuses (import-only) from core:** `executors.claude_cli.parse_usage_and_429`, `.extract_result_event` (bare-claude cost/tokens); `models.compute_run_usage_totals` + `runstate` load (ao-workflow cost/tokens). No engine mutation.
- **Shells out to:** `uv run ao run …` (ao subject), `claude -p …` (claude subject). Both black-box.
- **Extension seams (registries):** `SUBJECT_REGISTRY` (`type` → Subject class), `GRADER_REGISTRY` (`type` → Grader class). Adding a subject/grader = register a class + add its `type` to the schema enum. Non-dev domains slot in via a new suite with `domain: "physics"` + a `CommandGrader` — no code change. External-suite import (SWE-bench/inspect) = a new Subject/Suite-importer behind the same seam (non-MVP).

## 4. LLD

Each module below has: **Purpose · Inputs · Outputs · Dependencies · Pseudocode · Interface · Schemas · Subtasks · Edge cases.** Aim: junior/agent-executable without guessing.

### 4.1 Module `bench/spec.py` — suite & subject loading + validation

- **Purpose:** load and schema-validate `suite.json` / `subject.json` into pydantic models; resolve/verify referenced paths.
- **Inputs:** paths to suite/subject JSON. **Outputs:** `BenchSuite`, `SubjectSpec` models (or a structured `SpecValidationError`).
- **Dependencies:** `jsonschema`, `pydantic`, core `errors.SpecValidationError`, the two new schemas (`benchmarks/schemas/*.json`).

```
FUNCTION load_suite(path) -> BenchSuite:
  data = read_json_or_yaml(path)
  jsonschema.validate(data, BENCH_SUITE_SCHEMA)         # additionalProperties:false, versioned
  suite = BenchSuite(**data)
  ASSERT lowercase(suite.id) AND unique([t.id for t in suite.tasks])   # ids lowercase+unique
  FOR t IN suite.tasks:
    resolve t.instruction, t.fixture RELATIVE TO path.parent
    IF not exists(instruction) OR not isdir(fixture): RAISE SpecValidationError(task=t.id, ...)
    validate t.grader AGAINST GRADER_REGISTRY[t.grader.type].config_schema  # unknown type => error
  RETURN suite

FUNCTION load_subject(path) -> SubjectSpec:
  data = read_json_or_yaml(path); jsonschema.validate(data, SUBJECT_SCHEMA)
  IF data.type NOT IN SUBJECT_REGISTRY: RAISE SpecValidationError("unknown subject type ...")
  RETURN SubjectSpec(**data)
```

- **Interface / data schemas:** see §5 (spec schema) and §6 (models).
- **Subtasks:** JSON/YAML read · jsonschema validate · pydantic parse · path resolution+existence · grader-type/subject-type registry check · lowercase/unique id check.
- **Edge cases:** empty/malformed JSON → structured error naming the file; unknown `grader.type`/`subject.type` → error listing known types; duplicate task id → error naming the id; missing fixture dir / instruction file → error naming the path; uppercase id → error (mirrors core lowercase rule); `version` absent → error (required, versioned spec).
- **As-built note:** the pydantic models (`BenchSuite`, `BenchTask`, `BenchSuiteDefaults`, `GraderConfig`,
  `SubjectSpec`, `Assertion`) live in `bench/spec.py` itself, alongside the loaders — there is no separate
  `models.py`. `Assertion.golden` (the `equals_file` grader's reference file, authored relative to `suite.json`) is
  checked to exist AND **rewritten to an absolute path in place** by `load_suite`/`_check_assertions` at load time —
  necessary because the grade-time `RunContext` carries no suite base directory, so a still-relative `golden` would
  be unresolvable by `FileAssertionGrader` later (§4.3).

### 4.2 Module `bench/subjects.py` — Subject adapters

- **Purpose:** run one task with one subject inside a materialized workspace; return a `SubjectResult` carrying status + raw cost/usage (paths only cross into subprocesses — NFR-1 hygiene preserved).
- **Inputs:** `SubjectSpec`, `BenchTask`, `RunContext` (resolved workspace paths, timeout, budget/turns knobs). **Outputs:** `SubjectResult`.
- **Dependencies (import-only):** core `executors.claude_cli.parse_usage_and_429`/`extract_result_event`; core `runstate`/`models.compute_run_usage_totals`; stdlib `subprocess`.

```
PROTOCOL Subject:
  run(task: BenchTask, ctx: RunContext) -> SubjectResult
  # SubjectResult: {status: "succeeded"|"failed"|"timed_out"|"error",
  #                 wall_clock_seconds, cost_usd?, input_tokens?, output_tokens?,
  #                 cache_creation_input_tokens?, cache_read_input_tokens?,
  #                 attempts?, turns?, capture_dir, raw_error?}

CLASS ClaudeCliSubject(Subject):   # bare `claude -p`, the baseline
  run(task, ctx):
    prompt = render(spec.prompt_template, instruction=ctx.instruction_path, repo=ctx.repo_dir)
    argv = ["claude","-p",prompt, "--model", spec.model,
            "--permission-mode", spec.permission_mode,          # bypassPermissions default (agent runs tests)
            "--output-format","stream-json","--verbose", *spec.extra_args]
    IF spec.max_turns: argv += ["--max-turns", str(spec.max_turns)]
    t0 = monotonic()
    rc, transcript_path = popen_capture(argv, cwd=ctx.repo_dir, stdin=DEVNULL,
                                        stdout=ctx.capture_dir/"transcript.jsonl", timeout=task.timeout)
    wall = monotonic() - t0
    usage = parse_usage_and_429(read(transcript_path), stderr, rc, now)   # REUSE core (C4)
    turns = count_assistant_events(transcript_path)                       # best-effort
    RETURN SubjectResult(status_from(rc, timed_out), wall, usage.cost_usd, usage.*tokens,
                         attempts=1, turns=turns, capture_dir=ctx.capture_dir)

CLASS AoWorkflowSubject(Subject):   # an ao DAG is the system under test
  run(task, ctx):
    render ctx.reposet_json  with workspace_root=ctx.workspace, repos[0].path=ctx.repo_dir
    # INSTRUCTION.md is already present at ctx.repo_dir/INSTRUCTION.md — written once,
    # shared by every subject, by workspace.materialize_workspace BEFORE Subject.run is
    # called (not by AoWorkflowSubject itself; as-built deviation, see note below).
    env = {AO_MODEL: spec.model?, AO_MAX_TURNS: spec.max_turns?,
           AO_MAX_PARALLEL: spec.max_parallel?, AO_WORKSPACE_ROOT: ctx.workspace}
    argv = ["uv","run","ao","run","--workflow",ctx.workflow_json,
            "--reposets",ctx.rendered_reposet,"--agents",spec.agents_json]
    IF ctx.budget_total or spec.budget_total: argv += ["--budget-total", str(ctx.budget_total or spec.budget_total)]
    t0 = monotonic(); rc = run(argv, cwd=REPO_ROOT, stdin=DEVNULL, env=env, timeout=task.timeout,
                               stdout=ctx.capture_dir/"ao.stdout.txt")
    wall = monotonic() - t0
    run_id = latest_run_dir(ctx.workspace/".orchestrator"/"runs")        # exactly one (A4)
    totals = compute_run_usage_totals(load_state(run_id))                # REUSE core (C4)
    RETURN SubjectResult(status_from(rc, timed_out), wall, totals.cost_usd, totals.*tokens,
                         attempts=sum_task_attempts(state), turns=None, capture_dir=ctx.capture_dir)

CLASS FakeSubject(Subject):        # deterministic, network-free — the ONLY subject CI uses
  run(task, ctx):                  # applies a scripted patch/side-effect so graders have real input
    apply spec.scripted_effect to ctx.repo_dir     # e.g. copy a "solution" file, or no-op to force fail
    RETURN SubjectResult("succeeded", wall=0.01, cost_usd=spec.fake_cost, tokens=spec.fake_tokens, ...)
```

- **Subtasks:** prompt/reposet rendering · subprocess spawn+capture (OS-level redirect, `stdin=DEVNULL`) · timeout kill · status mapping · cost/usage extraction (reuse) · turns/attempts best-effort · `FakeSubject` scripted effect.
- **Edge cases:** subject non-zero exit → `status="failed"` (task still graded — a failed subject can still leave a partial fix); timeout → `status="timed_out"`, keep partial capture; `claude`/`ao` not on PATH → `status="error"` with a clear message (checked up-front in `ao-bench validate`); quota exhaustion (`claude_quota_exhausted` from `parse_usage_and_429`) → surface distinctly, do NOT count as a solve, mark `subject_status="error"` with reason; missing `state.json` for ao subject → cost=None but still grade; permission-mode mismatch (write-only agent that runs tests) → documented: default `bypassPermissions` for claude subject (learnings §21); **an `ao_workflow` subject's workflow task CANNOT reference a path under a generic mirrored `instructions/` directory** — `AO_WORKSPACE_ROOT` is that bench task's own ephemeral workspace, which has no such directory, so every workflow task's `instruction` field must be `repo/INSTRUCTION.md` instead (the file `materialize_workspace` already wrote there) — a design gap found and worked around during `T-Fx6Dp0`, see deviation 3 above.

**As-built deviations from this module's pseudocode (T-Sbj9Ka, verified against `bench/subjects.py` + `bench/workspace.py`):**
1. **No `AO_BUDGET_TOTAL` env var.** Core `cli.py`'s `run`/`resume` read budget only via the `--budget-total` CLI
   flag (unlike `AO_MAX_TURNS`/`AO_MODEL`/`AO_MAX_PARALLEL`, which core *does* wire as env vars). `AoWorkflowSubject`
   passes `--budget-total <n>` as an argv flag instead (`ctx.budget_total` wins over `spec.budget_total`, mirroring
   core's own CLI > spec > unset precedence).
2. **`INSTRUCTION.md` is written by `workspace.materialize_workspace`, not by `AoWorkflowSubject.run`.** It writes
   `task.instruction` to `ctx.repo_dir/INSTRUCTION.md` once, before *any* subject runs — `ClaudeCliSubject` benefits
   too (its `{instruction}` prompt placeholder always resolves to a real on-disk file inside the workspace).
3. **The `ao_workflow` subject's `workflow.json`/`reposet.json`/`agents.json` do NOT get a generic `instructions/`
   directory mirrored into the workspace.** `AO_WORKSPACE_ROOT` (set by `AoWorkflowSubject`) is the bench task's own
   ephemeral per-run workspace, and every workflow-relative artifact path — including a task's `instruction` field —
   resolves against *that* workspace, not the committed `benchmarks/subjects/<id>/` directory. So the committed
   `ao-epic` workflow's tasks reference `instruction: "repo/INSTRUCTION.md"` (the same file `materialize_workspace`
   already wrote, per deviation 2) rather than a path under a mirrored `instructions/` dir. The committed
   `benchmarks/subjects/ao-epic/instructions/{implement,verify}.md` files exist purely as human-readable role
   documentation, mirrored verbatim into `agents.json`'s `prompt_template` strings (the text the engine actually
   renders) — see §9 for the full asset list and §18 Q1 for the workflow-shape resolution this fed into.
4. **`RunContext` gained an additive `subject_base_dir` field** (not in §6's original listing) so `AoWorkflowSubject`
   can resolve `spec.workflow`/`.reposets`/`.agents` (documented as "relative to this subject.json") against the
   subject.json's own directory; the runner sets it to `Path(subject_path).resolve().parent`.
5. **`SubjectResult` gained additive `argv`/`resolved_model`/`resolved_permission_mode` fields** (observability for
   `config_fingerprint` and audit), populated by all three subjects (`FakeSubject`: `argv=[]`).
6. **Exactly-one-run-dir (A4/R2) is enforced by `subjects._latest_run_dir`,** which raises `SubjectError` (not a
   silent pick) if zero or more than one `run_id` directory exists under `<ws>/.orchestrator/runs/` — the runner
   catches this and records the task as `subject_status="error"`.

### 4.3 Module `bench/graders.py` — Grader adapters

- **Purpose:** produce a `GradeResult{solved: bool, score: float∈[0,1], detail: dict, raw_tail: str}` over the mutated workspace.
- **Inputs:** `GraderConfig`, `RunContext` (repo dir). **Outputs:** `GradeResult`.

```
PROTOCOL Grader:
  grade(cfg: GraderConfig, ctx: RunContext) -> GradeResult

CLASS PytestGrader(Grader):        # test-pass-rate — the MVP default
  grade(cfg, ctx):
    rc, out = run(cfg.command or "uv run pytest -q --tb=no",
                  cwd=ctx.repo_dir/(cfg.cwd or "."), timeout=cfg.timeout_seconds)
    passed, failed, total = parse_pytest_summary(out)          # enrich only; NEVER the pass source
    solved = (rc == 0) AND (total > 0) AND (failed == 0)       # exit code is source of truth (A3)
    score  = passed/total IF total>0 ELSE (1.0 IF rc==0 ELSE 0.0)
    IF cfg.pass_threshold: solved = score >= cfg.pass_threshold
    RETURN GradeResult(solved, score, {passed,failed,total,returncode:rc}, tail(out,2000))

CLASS CommandGrader(Grader):       # generic: exit 0 == pass — enables non-dev domains
  grade(cfg, ctx):
    rc, out = run(cfg.command, cwd=ctx.repo_dir/(cfg.cwd or "."), timeout=cfg.timeout_seconds)
    RETURN GradeResult(rc==0, 1.0 if rc==0 else 0.0, {returncode:rc}, tail(out,2000))

CLASS FileAssertionGrader(Grader): # files exist / contain substrings / match golden
  grade(cfg, ctx):
    checks = [check(a, ctx.repo_dir) for a IN cfg.assertions]  # exists|contains|equals_file
    solved = all(checks); score = sum(checks)/len(checks)
    RETURN GradeResult(solved, score, {per_assertion: checks}, "")
```

- **Subtasks:** command spawn+timeout · pytest-summary regex (enrich) · assertion evaluation · score normalization to [0,1].
- **Edge cases:** grader command missing/timeout → `solved=False`, `score=0.0`, detail records the failure (never raises); `total==0` (no tests collected) → not solved (an empty test run is not a pass); grader itself errors (import error in fixture) → captured in `raw_tail`, `solved=False`; `equals_file` golden missing → structured error at load time (§4.1), not at grade time.

### 4.4 Module `bench/metrics.py` — normalization

- **Purpose:** merge a `SubjectResult` + `GradeResult` into a persisted `TaskMetric`; compute per-subject aggregates.
- **Pseudocode:**
```
FUNCTION build_task_metric(subject_id, task, sr: SubjectResult, gr: GradeResult, fingerprint) -> TaskMetric:
  RETURN TaskMetric(subject_id, task.id, task.domain, task.category,
                    solved=gr.solved, score=gr.score,
                    wall_clock_seconds=sr.wall_clock_seconds, cost_usd=sr.cost_usd,
                    input_tokens=sr.input_tokens, output_tokens=sr.output_tokens,
                    cache_creation_input_tokens=sr.cache_creation_input_tokens,
                    cache_read_input_tokens=sr.cache_read_input_tokens,
                    attempts=sr.attempts, turns=sr.turns, subject_status=sr.status,
                    grader_type=task.grader.type, workspace=sr.capture_dir,   # POINTER, not content
                    config_fingerprint=fingerprint, started_at, ended_at)

FUNCTION aggregate(metrics: [TaskMetric]) -> Aggregate:
  solved = count(m.solved); total = len(metrics)
  RETURN Aggregate(solved, total, solve_rate=solved/total,
                   total_cost_usd=sum(m.cost_usd or 0), total_wall_clock=sum(m.wall_clock_seconds),
                   mean_score=mean(m.score),
                   cost_per_solved = (sum_cost/solved) if solved else None)   # the headline efficiency metric
```
- **Edge cases:** `cost_usd is None` (actuals unavailable) → excluded from cost sums, flagged `cost_available:false` in aggregate; `solved==0` → `cost_per_solved=None` (no divide-by-zero).

### 4.5 Module `bench/runner.py` — orchestration (deterministic, resumable, bounded)

- **Purpose:** run a suite against one subject: `for task in suite (sorted)` → materialize → run → grade → persist per-task metric; skip already-done tasks (resume); bound each task; never mutate committed fixtures.
- **As-built signature (supersedes the pseudocode below, which used already-loaded objects):**
  `run_suite(suite_path, subject_path, *, out_dir=None, force=False, task_filter=None, budget_total=None,
  max_turns=None, default_timeout=None, clock=None) -> BenchRunRecord`. `run_suite` itself calls `load_suite`/
  `load_subject` as its first step (paths in, not pre-loaded models — matches how the CLI invokes it:
  `--suite <path> --subject <path>`). It returns the persisted `BenchRunRecord` directly (no separate `RunResult`
  wrapper); `compute_bench_run_id`/`resolve_result_dir` are exposed as public functions so callers (the CLI, or
  `results.py`) can independently derive the result directory. **`run_suite` does NOT call `write_summary_md`
  itself** — `summary.md` is written by the CLI's `run` command, calling `results.write_summary_md` on the returned
  record, after `run_suite` completes (keeping `results.py` the sole owner of human-readable output). An injectable
  `clock` param satisfies the determinism rule (CLAUDE.md) — defaults to `datetime.now(UTC)`.
- **Original pseudocode (illustrative — see the as-built signature/behavior above and deviations below):**
```
FUNCTION run_suite(suite, subject_spec, opts) -> RunResult:
  bench_run_id = f"{utc_date()}-{suite.id}-{subject_spec.id}"        # stable, reproducible dir name
  result_dir   = benchmarks/results/ + bench_run_id                  # COMMITTED
  ws_root      = playground/.tmp/bench/ + bench_run_id               # GITIGNORED (C2)
  metrics = load_existing(result_dir/"run.json")                     # resume: {} on first run
  FOR task IN sorted(suite.tasks, key=id):                           # deterministic order (C6)
    IF task.id IN metrics AND not opts.force: CONTINUE               # idempotent skip (A5)
    ws = ws_root/subject_spec.id/task.id
    IF exists(ws): rmtree(ws)                                        # fresh per (subject,task) (A4)
    copytree(task.fixture, ws/"repo")                               # mutable copy; fixture untouched
    fingerprint = sha256(canonical(task) + canonical(subject_spec) + ao_version())
    ctx = RunContext(ws, repo_dir=ws/"repo", instruction=task.instruction, capture_dir=ws/"capture",
                     timeout=task.timeout_seconds or opts.default_timeout,
                     budget_total=opts.budget_total, max_turns=opts.max_turns)
    TRY:
      sr = SUBJECT_REGISTRY[subject_spec.type](subject_spec).run(task, ctx)
      gr = GRADER_REGISTRY[task.grader.type]().grade(task.grader, ctx)
    EXCEPT BenchError as e:
      sr = SubjectResult("error", 0, raw_error=str(e)); gr = GradeResult(False, 0.0, {}, str(e))
    metrics[task.id] = build_task_metric(subject_spec.id, task, sr, gr, fingerprint)
    write_json(result_dir/"run.json", assemble_run_json(...metrics...))   # persist after EACH task (resumable)
    log("bench.task.end", task=task.id, solved=gr.solved, cost=sr.cost_usd, wall=sr.wall_clock_seconds)
  write_summary_md(result_dir/"summary.md", metrics, aggregate(metrics.values()))
  RETURN RunResult(bench_run_id, result_dir, metrics, aggregate(...))
```
- **Interface (CLI):** see §7.
- **Subtasks:** bench-run-id derivation · workspace materialize (copytree, path-guarded under `playground/.tmp/bench`) · deterministic task iteration · resume skip · fingerprint · subject dispatch · grader dispatch · per-task persist · structured logging (`bench.run.start/task.start/task.end/run.end`).
- **Edge cases:** partially-written `run.json` from an interrupted run → resume reads valid task entries, re-runs the rest (persist-after-each-task guarantees the file is always valid JSON per completed task); `--force` re-runs all; a fixture path that escapes `playground/.tmp/bench` (symlink/`..`) → rejected (path guard, mirrors engine `working_dir` guard); one task crashing → recorded as `subject_status="error"`, run continues (no all-or-nothing); disk cleanup is manual (`ao prune`-style; workspaces preserved for audit per learnings §18).

**As-built deviations (T-Run5Tz, verified against `bench/runner.py`):**
1. **`run.json` persistence is owned by `runner.py`, not `results.py`.** The resume/crash-resumable contract is
   meaningless unless the same module that decides "which tasks to run" also durably records completed ones — write-
   temp + atomic rename (mirrors `runstate.RunStateStore.save`) after every task that actually runs.
   `BenchRunRecord`/`BenchTaskRecord`/`BenchRunSubjectInfo`/`BenchRunEnv` (the `run.json` shape) are defined in
   `runner.py`; `results.py` builds `summary.md`/`comparison.*` **on top of** this shape by reading it back
   (`BenchRunRecord.model_validate`), not by assembling it itself (there is no separate `assemble_run_json`
   function — see §4.6).
2. **`BenchTaskRecord` (a `TaskMetric` subclass, local to `runner.py`) adds `raw_error`/`grader_detail`/
   `grader_raw_tail`** beyond `metrics.TaskMetric`'s fields, so the persisted per-task record carries full
   diagnostic detail without a `metrics.py` edit.
3. **Two `config_fingerprint`s exist:** a per-task one (`BenchTaskRecord.config_fingerprint`, `sha256` of
   task+subject+`ao --version`, as originally specified) AND a run-level one
   (`BenchRunRecord.config_fingerprint`, hashing suite id/version + subject spec + the behavior-affecting overrides
   `budget_total`/`max_turns`/`default_timeout` + `ao`/`claude` versions — `force`/`task_filter` are excluded as
   control-flow, not config that produced the metric values).
4. **`_effective_timeout` has three tiers, not two:** a task's own `timeout_seconds` → the run-level
   `--timeout`/`default_timeout` override → `suite.defaults.timeout_seconds` (§5.1's schema field, now actually
   consumed) → the builtin `DEFAULT_TASK_TIMEOUT_SECONDS = 1800` last resort.
5. **A fully-skipped resume (every considered task already recorded) writes nothing to disk** — `run_suite` returns
   the existing record without reopening `run.json` for writing, so the file's mtime is provably unchanged (AC2's
   literal "run.json unchanged").
6. **A per-run structured-log file** (`bench.run.start`/`task.start`/`task.end`/`run.end`, §8) is written under the
   GITIGNORED workspace tree (`playground/.tmp/bench/<bench_run_id>/bench.log`) via the reused core
   `attach_run_handler`/`get_run_logger`/`detach_run_handler`, never inside the committed `benchmarks/results/` dir.

### 4.6 Module `bench/results.py` — result & comparison writers

- **Purpose:** read back the `run.json` `runner.py` already persisted (§4.5) and write `summary.md` (human) per
  subject; build + write cross-subject `comparison.{json,md}`. `results.py` does **not** assemble `run.json` itself —
  there is no `assemble_run_json` function; that shape (`BenchRunRecord`) is defined and persisted by `runner.py`.
- **As-built API (supersedes the illustrative pseudocode below):**
```
load_run(run_dir) -> BenchRunRecord                          # accepts a run dir OR a run.json path directly
write_summary_md(record | run_dir, run_dir=...) -> Path       # per-task table + aggregate footer
build_comparison(run_dirs, *, allow_mixed=False, clock=None) -> ComparisonRecord
write_comparison(comparison, out_dir) -> tuple[Path, Path]    # (comparison.json path, comparison.md path)
```
- **Original illustrative pseudocode (kept for the shape of the idea; see the as-built API + deviations for the real behavior):**
```
FUNCTION write_comparison(result_dirs: [dir]) -> None:                 # `ao-bench report`
  runs = [load run.json from d for d IN result_dirs where same suite]
  matrix = {task_id: {subject_id: {solved, score, cost_usd, wall}}}    # per-task, all subjects
  per_subject = {subject_id: aggregate}
  write_json(compare_dir/"comparison.json", {suite, subjects, matrix, per_subject})
  write_md(compare_dir/"comparison.md", side_by_side_table(per_subject) + per_task_table(matrix))
```
- **`summary.md` shape (committed):** a markdown table `task | category | solved | score | cost($) | wall(s) | tokens(in/out) | status` + an aggregate footer (`solved/total`, `solve_rate`, `total_cost`, `cost_per_solved`).
- **`comparison.md` shape:** `subject | solved/total | solve_rate | total_cost | cost_per_solved | total_wall` side-by-side, a per-task solved/cost matrix, and a "winner" line per axis (solve rate, total cost, cost per solved, wall-clock) — ties break on subject id ascending for a deterministic winner (`render_winners`).
- **Edge cases:** subjects run against different suites, or different `schema_version`s → `report` refuses
  (`ResultsError`); two runs for the SAME subject id with a different `config_fingerprint` → refused unless
  `--allow-mixed`/`allow_mixed=True` (then last-wins); a subject's `run.json` missing → listed as "not run" in the
  comparison (`ComparisonRecord.not_run`), never a crash — its subject id is recovered from the run-dir name itself
  (`<date>-<suite_id>-<subject_id>`) once at least one sibling dir DID load, so the suite id prefix is known; if
  **every** given dir is missing, `build_comparison` raises `ResultsError` (no information to determine the suite
  from at all); a single run dir degrades gracefully to a one-subject "comparison"; transcripts are **not** committed
  (huge, may contain noise) — `run.json` stores the workspace path as a pointer only (§4.4).
- **`ResultsError`** (new, in `bench/errors.py`, co-located with `SubjectError`/`GraderError`) is the typed error
  `load_run`/`build_comparison` raise for all of the above refusals.

## 5. Spec schemas (JSON, versioned, `additionalProperties:false`)

### 5.1 `benchmarks/schemas/benchmark-suite.schema.json`
```jsonc
{
  "version": "1.0",                       // required — versioned spec
  "id": "dev-core",                       // lowercase kebab, unique
  "domain": "software",                   // free string label; enables non-dev suites later
  "description": "…",
  "defaults": { "timeout_seconds": 1800 },
  "tasks": [{
    "id": "bugfix-off-by-one",            // lowercase, unique within suite
    "category": "bugfix",                 // bugfix|feature|refactor|test|other
    "instruction": "tasks/bugfix-off-by-one/instruction.md",   // PATH, relative to suite.json
    "fixture":     "tasks/bugfix-off-by-one/fixture",          // PATH to a tiny repo dir
    "grader": { "type": "pytest", "command": "uv run pytest -q", "cwd": ".", "pass_threshold": 1.0 },
    "timeout_seconds": 900,
    "tags": ["python", "easy"]
  }]
}
```

### 5.2 `benchmarks/schemas/subject.schema.json` (discriminated by `type`)
```jsonc
// claude_cli subject (the baseline)
{ "version":"1.0", "id":"claude-opus", "type":"claude_cli",
  "model":"claude-opus-4-8",              // pass-through to --model (alias "opus" also valid)
  "permission_mode":"bypassPermissions",  // acceptEdits for write-only; bypass for tasks whose agent runs tests
  "max_turns": 40, "prompt_template":"Solve the task described in {instruction}. Repo: {repo}.",
  "extra_args": [] }

// ao_workflow subject (system under test)
{ "version":"1.0", "id":"ao-epic", "type":"ao_workflow",
  "workflow":"subjects/ao-epic/workflow.json",   // template DAG (its first task reads INSTRUCTION.md)
  "reposets":"subjects/ao-epic/reposet.json",    // template; workspace_root+repo path rewritten per task
  "agents":  "subjects/ao-epic/agents.json",
  "model":"claude-sonnet-5", "max_parallel":1, "budget_total":null, "max_turns":null }

// fake subject (test-only)
{ "version":"1.0", "id":"fake-pass", "type":"fake",
  "scripted_effect":"copy-solution", "fake_cost":0.01, "fake_tokens":{"in":10,"out":5} }
```

## 6. Interface / API contracts (models)

```
BenchTask:   id:str · category:str · instruction:str(path) · fixture:str(path)
             grader:GraderConfig · timeout_seconds:int|None · tags:[str]
             # AS-BUILT: BenchTask has NO `domain` field. `domain` lives only on BenchSuite;
             # build_task_metric(..., domain: str, ...) takes it as an explicit param from the
             # suite (the caller), rather than reading a (nonexistent) task.domain.
BenchSuite:  version:str · id:str · domain:str · description:str · defaults:{timeout_seconds:int} · tasks:[BenchTask]
GraderConfig: type:Literal["pytest","command","file_assertion","fake"] · command?:str · cwd?:str
             · timeout_seconds?:int · pass_threshold?:float · assertions?:[Assertion]
SubjectSpec: version:str · id:str · type:Literal["claude_cli","ao_workflow","fake"] · model?:str
             · permission_mode?:str · max_turns?:int · prompt_template?:str · extra_args?:[str]
             · workflow?:str · reposets?:str · agents?:str · max_parallel?:int · budget_total?:int
             · scripted_effect?:str · fake_cost?:float · fake_tokens?:{in:int,out:int}
RunContext:  workspace:str · repo_dir:str · instruction_path:str · capture_dir:str · timeout_seconds:int
             · budget_total:int|None · max_turns:int|None · workflow_json?:str · rendered_reposet?:str
             · subject_base_dir?:str   # AS-BUILT additive: dir containing the running subject's own
                                       # subject.json, so AoWorkflowSubject can resolve workflow/reposets/agents
SubjectResult: status:Literal["succeeded","failed","timed_out","error"] · wall_clock_seconds:float
             · cost_usd?:float · input_tokens?:int · output_tokens?:int
             · cache_creation_input_tokens?:int · cache_read_input_tokens?:int
             · attempts?:int · turns?:int · capture_dir:str · raw_error?:str
             · argv?:[str] · resolved_model?:str · resolved_permission_mode?:str   # AS-BUILT additive (observability)
GradeResult: solved:bool · score:float · detail:dict · raw_tail:str
TaskMetric:  subject_id · task_id · domain · category · solved · score · wall_clock_seconds
             · cost_usd? · input_tokens? · output_tokens? · cache_* · attempts? · turns?
             · subject_status · grader_type · workspace(pointer) · config_fingerprint · started_at · ended_at
             # AS-BUILT: the type PERSISTED into run.json is `BenchTaskRecord(TaskMetric)` (runner.py), which
             # adds `raw_error` / `grader_detail` / `grader_raw_tail` on top of the fields above.

PROTOCOL Subject:  run(task:BenchTask, ctx:RunContext) -> SubjectResult   # errors: SubjectError
PROTOCOL Grader:   grade(cfg:GraderConfig, ctx:RunContext) -> GradeResult # errors: GraderError
REGISTRIES: SUBJECT_REGISTRY:{str->type[Subject]} · GRADER_REGISTRY:{str->type[Grader]}
ERRORS: BenchError(base) · SpecValidationError(reuse core) · SubjectError · GraderError · ResultsError
        # ResultsError (AS-BUILT additive, bench/errors.py): raised by results.py's load_run/build_comparison
        # for a malformed/missing run.json or a refused (unlike-suite / mixed-fingerprint) comparison.
```
Idempotency & versioning: result dir name = `<date>-<suite>-<subject>` is stable → re-run skips completed tasks unless `--force`; `schema_version` on suite/subject/run.json enables forward migration; a `config_fingerprint` (sha256 of task+subject+`ao --version`) records exactly what produced each metric.

## 7. Entry points — CLI & Make

**Decision (ADR-0008 D4): a standalone `ao-bench` console script**, not an `ao bench` subcommand. Rationale: keeps the core `ao` command surface and its test suite untouched (SI-1, "third-party integrations must never destabilize core"); `bench` deps/imports never load on a normal `ao run`. Registered as a separate `[project.scripts]` entry point.

**As-built CLI (T-Sc4Hm2/T-Cli8Nf, `bench/cli.py` — supersedes the illustrative sketch originally here):**
```
ao-bench validate --suite <suite.json>                     # schema + semantic validate a suite
ao-bench validate --subject <subject.json>                 # also probes `claude --version` (non-fatal WARNING only)
ao-bench run --suite <suite.json> --subject <subject.json> [--out-dir D] [--force] [--task ID ...]
             [--budget-total N] [--max-turns N] [--timeout S]
             # writes run.json + summary.md. Exit 0 clean run; exit 2 if any task's SUBJECT status (not its
             # grader verdict — an unsolved-but-cleanly-run task is a normal outcome) is failed/timed_out/error;
             # exit 1 on a usage/spec-loading error.
ao-bench report (--run-dir D [--run-dir D ...] | --results-root R --suite SUITE_ID) [--out-dir D] [--allow-mixed]
             # --run-dir: explicit result dirs to compare.
             # --results-root+--suite: auto-discover the LATEST result dir per subject id under that root
             #   (dir names sort lexicographically = their embedded ISO date, so "last in sorted order" = latest).
             # writes comparison.{json,md}; exit 0 on success, 1 on a refused/usage error.
ao-bench list --suite <suite.json>                          # a task table: id/category/grader type/timeout/tags
```
`list` and `report` differ from the design's original one-line sketches (`list --suites|--subjects|--results`;
`report --suite dev-core --results-dir ...`) — the shapes above are what shipped; both are simpler and map directly
onto how `T-Fx6Dp0`'s committed suite/subjects and `Makefile` recipes actually invoke them.

`Make` recipes (as-built, appended to the existing `Makefile` — no existing target edited):
```
bench-validate:  uv run ao-bench validate --suite $(BENCH_SUITE_PATH)
bench-smoke:     uv run ao-bench run --suite $(BENCH_SUITE_PATH) --subject $(BENCH_FAKE_SUBJECT_PATH)
                 -uv run ao-bench run --suite $(BENCH_SUITE_PATH) --subject $(BENCH_HAIKU_SUBJECT_PATH) \
                     --max-turns $(BENCH_SMOKE_MAX_TURNS)
                 -uv run ao-bench run --suite $(BENCH_SUITE_PATH) --subject $(BENCH_AO_EPIC_HAIKU_SUBJECT_PATH) \
                     --max-turns $(BENCH_SMOKE_MAX_TURNS)
                 uv run ao-bench report --results-root $(RESULTS) --suite $(BENCH_SMOKE_SUITE_ID)
                 # fake-pass (free, proves plumbing) + claude-haiku + ao-epic-haiku (both real, cheap; the `-`
                 # prefix means one flaky real-subject line never blocks the rest) + a report over all three.
bench-run:       uv run ao-bench run --suite $(SUITE) --subject $(SUBJECT)          # real (sonnet/opus/…)
bench-report:    uv run ao-bench report --results-root $(RESULTS) --suite $(SUITE)
```
See `benchmarks/README.md` for the full knob reference (`BENCH_SUITE_PATH`, `BENCH_*_SUBJECT_PATH`, `RESULTS`,
`SUITE`, `SUBJECT`, `BENCH_SMOKE_MAX_TURNS`).

## 8. Trigger / event schema (observability)

The runner emits structured log events (same logging infra as the engine, reused import-only). No cron/webhook trigger in MVP — benchmark runs are manual/`make`-driven (a scheduled nightly bench is a natural non-MVP cron trigger).
```
bench.run.start   {bench_run_id, suite, subject, task_count, ao_version, claude_version}
bench.task.start  {task_id, category, workspace}
bench.task.end    {task_id, solved, score, cost_usd, wall_clock_seconds, subject_status}
bench.run.end     {bench_run_id, solved, total, solve_rate, total_cost_usd}
```

## 9. Where things live (as-built layout)
```
benchmarks/
  schemas/  benchmark-suite.schema.json  subject.schema.json          # committed
  suites/dev-core/  suite.json  tasks/<task-id>/{instruction.md, fixture/…}   # committed fixtures
                    # fixture/ carries a committed .bench-solution/ reference overlay (FakeSubject's
                    # "copy-solution" scripted_effect); refactor/test-writing tasks also carry their own
                    # check.py/check_tests.py that a `command` grader shells out to.
  subjects/  claude-haiku.json  claude-sonnet.json  claude-opus.json
             ao-epic-haiku.json  ao-epic-sonnet.json  fake-pass.json  # committed subject configs
             ao-epic/  workflow.json  reposet.json  agents.json      # the ao_workflow subject's own
                       instructions/{implement,verify}.md            # templates (§4.2 deviation 3)
  results/<date>-<suite>-<subject>/  run.json  summary.md             # COMMITTED (git-preserved)
          <date>-<suite>-compare/    comparison.json  comparison.md   # COMMITTED — see the naming
                                                                       # limitation note below
src/agent_orchestrator/bench/  __init__.py spec.py subjects.py workspace.py graders.py metrics.py
                               runner.py results.py cli.py errors.py registries.py      # code (10 modules)
tests/bench/                   unit + CliRunner e2e (FakeSubject/FakeGrader — no real LLM) + opt-in real_llm
playground/.tmp/bench/         # GITIGNORED workspaces (already covered by playground/.tmp/)
```

> **Known limitation — comparison directory naming is not subject-set-aware.** `compute_compare_id` derives
> `<date>-<suite_id>-compare` from only the UTC date + suite id (§4.6/`results.py`) — it does not encode which
> subjects went into the comparison. Regenerating `ao-bench report` for the same suite on the same day (e.g. adding
> a newly-run subject to the comparison, or re-running with `--allow-mixed`) **overwrites** the existing
> `<date>-<suite>-compare/comparison.{json,md}` in the working tree with the new subject set — there is no
> versioning or subject-set suffix. This is scoped to the *comparison* artifact only: per-run `run.json`/`summary.md`
> dirs (`<date>-<suite>-<subject>/`) are keyed by subject id too, so they are never clobbered by each other. In
> practice (`benchmarks/results/2026-07-22-dev-core-compare/`), this is exactly what happened: the comparison was
> regenerated as more real-subject runs (sonnet, opus) landed later the same day, and the committed
> `comparison.md` now reflects the fuller 5-subject set, not the original 3-subject `make bench-smoke` set. Accepted
> for MVP (§12 lists statistical/dashboard rigor as non-MVP); a fix would suffix the compare id with a subject-set
> hash or a monotonic counter. See `benchmarks/README.md`.
`pyproject.toml`: add `ao-bench = "agent_orchestrator.bench.cli:app"` to `[project.scripts]`. `bench/` ships in the wheel (packaged under `src/agent_orchestrator`); **schemas/suites/fixtures under `benchmarks/` are NOT packaged** — `ao-bench` resolves them relative to the repo/CWD (bench is a dev/repo tool, not an installed end-user command), so no wheel-packaging trap (learnings §49) for data files.

## 10. Deployment / rollout / upgrade
- **Rollout:** additive, opt-in, isolated. New module + new console script + `benchmarks/` tree; zero change to `src/agent_orchestrator/engine.py`, executors, or the workflow/agents/reposet schemas. `ao run/validate/resume` and their tests are byte-unaffected (SI-1, regression-gated in tests).
- **CI (as-built):** `.github/workflows/ci.yml`'s single `test` job gained one added step, "Run bench tests
  (deterministic, no real_llm)": `pytest tests/bench -q -m "not real_llm" --cov=agent_orchestrator.bench
  --cov-fail-under=80` — `FakeSubject`/`FakeGrader` only, network-free, no separate job. Actual coverage as of
  T-Tst4Ln: **98%** (1120 statements, 18 missed — documented hard-to-trigger abstract-method/race-condition lines).
  The real-LLM bench run is **never** in CI (cost); it's a manual `make bench-*`.
- **Upgrade/versioning:** `schema_version` on every spec + `run.json`; a fingerprint per metric. New grader/subject types are additive registry+enum entries. Old result dirs stay valid (append-only history).
- **Docker/external suites:** documented as non-MVP; Docker is installed, so a `DockerSubject`/SWE-bench importer is unblocked behind the same seam later.

## 11. Developer / operator experience
- **Author a task in minutes:** drop a tiny `fixture/` repo + `instruction.md`, add a task entry with a `pytest` grader. No code.
- **Diagnosable failures:** per-task `capture/` (transcript, stdout) preserved in the workspace; `run.json` records `subject_status`, `raw_error`, grader `detail`; `summary.md` is human-scannable.
- **Fast local iteration:** `make bench-smoke` at haiku is cheap; `FakeSubject` gives instant, network-free end-to-end runs for harness development.
- **Reproducible:** result dir name + `config_fingerprint` + recorded `ao/claude` versions make "what produced this number" answerable; re-run resumes.
- **Ergonomic knobs:** budget/max-turns/timeout are CLI + per-suite defaults; models live in committed subject configs (swap Opus↔Sonnet↔Haiku by pointing at a different subject file).
- **Known limitation:** the cross-subject `comparison.{json,md}` directory name is date+suite-keyed only, not
  subject-set-aware — see §9's "Known limitation" box (same-day regenerate overwrites the working-tree comparison;
  per-subject `run.json`/`summary.md` are unaffected).

### 11.1 Real dev-core results (2026-07-22, verified against committed `run.json`s)
All 6 dev-core tasks (2 bugfix / 2 feature / 1 refactor / 1 test-writing) solved 6/6 by every subject run so far:

| Subject | Solved | Total cost | Cost/solved | Total wall-clock |
|---|---|---|---|---|
| `fake-pass` | 6/6 | $0.00 | $0.00 | ~0.001s |
| `claude-haiku` | 6/6 | $0.3732 | $0.0622 | 166.37s |
| `ao-epic-haiku` | 6/6 | $0.6617 | $0.1103 | 343.05s |
| `claude-sonnet` | 6/6 | $1.2148 | $0.2025 | 122.28s |
| `claude-opus` | 6/6 | $1.5483 | $0.2580 | 129.97s |
| `ao-epic-sonnet` | 6/6 | $2.8744 | $0.4791 | 387.31s |

On this trivial-suite signal (tasks a single `claude -p` call can already ace), the `ao-epic` 2-agent
(implement→verify) workflow does **not** raise the solve rate over bare `claude -p` (both are 6/6) but costs
**~1.8× (haiku) to ~2.4× (sonnet)** the bare-CLI run, and takes **~2.1× to ~3.2×** the wall-clock — the extra
verify-agent turn's overhead is not repaid on tasks this easy. This is the expected, documented shape of a "trivial"
smoke suite (§12/ADR-0008 consequences: "MVP scores are LLM-stochastic … the framework guarantees a reproducible
result-dir + recorded configs, not identical numbers") — a harder suite is where an orchestrated multi-agent
workflow's verify/retry loop is expected to pay for itself via a HIGHER solve rate, not just show up as overhead.
See `benchmarks/README.md` for the full table and `benchmarks/results/2026-07-22-dev-core-compare/comparison.md`
for the live artifact.

## 12. Non-MVP (explicitly deferred — anti-feature-creep)
`LlmJudgeGrader` (needs a judge model) · SWE-bench Verified / terminal-bench **DockerSubject + importer** · **inspect-ai bridge** (run a fixture as an inspect Task) · Aider/EvalPlus subset importers · non-dev **domain suites** (biology/physics/legal/marketing — the framework *supports* them via `domain` + `CommandGrader`, but ships none) · HTTP/API subjects (raw Anthropic/OpenAI SDK, aider CLI) · statistical rigor (multi-seed, bootstrap CIs, pass@k) · a results dashboard/`ao-bench serve` · a nightly cron trigger.

## 13. Test strategy
- **Unit (target ≥80% of `bench/`):** schema validate (good/malformed/unknown-type/duplicate-id/uppercase-id/missing-fixture) · `PytestGrader.parse_pytest_summary` + exit-code-source-of-truth · `CommandGrader`/`FileAssertionGrader` · metrics aggregation incl. `cost_usd is None` and `solved==0` · runner resume (skip completed) · path-guard rejection · fingerprint stability.
- **Integration:** full runner over a 2-task fake suite with `FakeSubject`+`PytestGrader` on a real tiny fixture (fake subject writes a real solution file → real pytest passes; and a "no-op" fake → real pytest fails) → asserts `run.json`/`summary.md` shape, resume, and comparison across two fake subjects. No network.
- **E2E via the CLI (outer boundary, per CLAUDE.md):** `CliRunner`-style invocation of `ao-bench validate|run|report|list` with fake subjects/graders; assert exit codes, committed result files, comparison output.
- **Real-LLM tier (opt-in, `AO_E2E_REAL_LLM=1`, marked `real_llm`, never in CI):** one dev-core task × the haiku subject × claude_cli and ao_workflow → asserts cost/tokens populated (reuse-of-core-helpers proof) and a result dir written. Workspaces under `playground/.tmp/bench/` (fixture pattern), preserved on teardown.
- **Determinism:** fake subjects + fixed fixtures make every non-real test deterministic; real-LLM asserts *shape/plumbing*, not scores.

## 14. Acceptance criteria matrix (traceability)

| Req | Acceptance (Given/When/Then or pass/fail) | Verified by | Task |
|-----|-------------------------------------------|-------------|------|
| G3 | Malformed/unknown-type/duplicate-id/missing-fixture suite → `ao-bench validate` exits non-zero with a structured error naming the offender; a valid suite → "OK". | unit + CliRunner | T-Sc4Hm2 |
| G1/Subjects | `FakeSubject`, `ClaudeCliSubject`, `AoWorkflowSubject` each return a `SubjectResult` with status + (for real ones) cost/tokens from the reused core helpers; workspace materialized under `playground/.tmp/bench/`, fixture untouched. | unit + real_llm | T-Sbj9Ka |
| G2/Graders | `PytestGrader` marks solved iff exit 0 ∧ tests>0 ∧ 0 failed; score=pass-rate; `CommandGrader` solved iff exit 0; unknown grader type rejected at load. | unit | T-Grd7Vx |
| G7/Runner | Runner runs each task once, skips completed on re-run, `--force` re-runs, one task's crash doesn't abort the run, `run.json` valid after every task; path-escape rejected. | unit + integration | T-Run5Tz |
| G4 | `run.json` (schema-valid) + `summary.md` (table+aggregate) written to `benchmarks/results/<date>-<suite>-<subject>/`; `ao-bench report` writes `comparison.{json,md}`; unlike-suite compare refused. | integration + CliRunner | T-Rpt3Wq |
| G5 | `ao-bench` console script exposes validate/run/report/list; `make bench-validate|bench-smoke|bench-report` work; core `ao` unaffected (existing engine tests pass unedited). | CliRunner + regression gate | T-Cli8Nf |
| G6 | 4–8 committed fixture tasks (bugfix/feature/refactor/test); `make bench-smoke` runs all 3 subjects at haiku end-to-end; results committed. | real_llm smoke + committed artifacts | T-Fx6Dp0 |
| all | Unit+integration+CliRunner suites pass; ≥80% coverage of `bench/`; ruff/mypy clean on `bench/`. | test task | T-Tst4Ln |
| docs | HLD/LLD/ADR/README reconciled to as-built; learnings captured. | docs-refresh | T-Dcs2Rk |

**Verified as-built (2026-07-22):** all rows above confirmed against the shipped code/tests, not just the epic's
self-reported STATUS.md narratives — `pytest tests/bench -q -m "not real_llm" --cov=agent_orchestrator.bench` →
**221 passed, 1 deselected, 98% coverage** (1120 stmts/18 missed); `pytest -q -m "not real_llm"` (whole repo) →
**1078 passed, 4 deselected**, zero regressions; `make bench-smoke` + the 6 committed subject `run.json`s (§11.1)
confirm G6/G1/G2 end to end for real (not just `FakeSubject`).

## 15. Design Artifacts Checklist
**After HLD:** [x] logical architecture diagram (§3) · [x] component breakdown (§3.1) · [x] integration points (§3.2) · [x] plugin/extension strategy (§3.2 registries).
**After LLD:** [x] all interfaces/contracts (§6) · [x] all schemas (§5) · [x] pseudocode for every module (§4) · [x] edge cases per module (§4) · [x] ADRs (ADR-0008).
**Before sprint planning:** [x] tasks atomic (§ epic) · [x] tasks testable (§14 acceptance matrix) · [x] tasks unambiguous.

## 16. Execution Readiness Gate
- Can a junior implement without guessing? **Yes** — every module has pseudocode, interfaces, schemas, edge cases; subjects reuse named core helpers.
- Can an AI agent execute without ambiguity? **Yes** — registries, schemas, and result layout are fully specified; `FakeSubject`/`FakeGrader` give a network-free build/test loop.
- Interfaces/schemas fully defined? **Yes** (§5, §6).
- Failure scenarios handled? **Yes** — per-module edge cases + runner "one task crash doesn't abort" + timeout/quota/error statuses.
- **Verdict: PASS → ready for dev.**

## 17. Handoffs & ownership
- **architect** (this doc, ADR-0008, epic/tasks) → **developer** (T-Sc4Hm2…T-Cli8Nf: schemas, subjects, graders, runner, results, CLI) → **developer/tester** (T-Fx6Dp0 fixtures + smoke) → **tester** (T-Tst4Ln suites/CI) → **developer** (T-Dcs2Rk docs-refresh). Each task's HANDOFF boundary is in its `TASK.md`.

## 18. Risks / open questions
- **R1** Bare `claude -p` needs the right `--permission-mode` to actually edit + run tests (learnings §21). Mitigation: default subjects to `bypassPermissions` in committed configs; document; smoke-test at haiku surfaces it early. **Closed:** all 6 committed subject configs (`claude-*`) and the `ao-epic` `agents.json` (`developer`/`tester`) use `bypassPermissions`; the real haiku/sonnet/opus runs (§11.1) confirm it works end to end.
- **R2** ao-workflow subject cost attribution depends on exactly one run_id per workspace (A4). Mitigation: fresh workspace per task; `latest_run_dir` asserts a single run dir. **Closed:** `subjects._latest_run_dir` raises `SubjectError` on zero/multiple run dirs (not a silent pick); real `ao-epic-haiku`/`ao-epic-sonnet` runs confirm real cost/token attribution.
- **R3** pytest summary parsing is brittle across versions. Mitigation: exit-code is the source of truth (A3); summary parse only enriches `score`. **Closed:** implemented exactly as designed in `PytestGrader`; no issues surfaced.

**Resolved (final answers, verified against the shipped code — no longer open):**
- **Q1 (`ao-epic` workflow shape):** resolved to the proposed **minimal 2-task pipeline** — `implement` (agent
  `developer`) → `verify` (agent `tester`), both `claude_cli`-executor tasks reading `repo/INSTRUCTION.md` (§4.2
  deviation 3). Committed at `benchmarks/subjects/ao-epic/{workflow,reposet,agents}.json`. Not escalated for a
  richer template — kept minimal for cost control as proposed; a richer epic template remains available as a future
  subject variant (add a new `ao-epic-*` config, no framework change needed).
- **Q2 (model ids):** resolved to **full pinned ids in every committed subject config**, exactly as proposed — no
  bare aliases anywhere. Actual pinned values: `claude-opus-4-8` (`claude-opus.json`), `claude-sonnet-5`
  (`claude-sonnet.json`, `ao-epic-sonnet.json`), `claude-haiku-4-5-20251001` (`claude-haiku.json`,
  `ao-epic-haiku.json`) — note the haiku id carries a date suffix (`-20251001`) not shown in this doc's original
  `claude-haiku-4-5` example; the CLI accepted it without issue, so the "revisit if the CLI rejects a full id"
  contingency never triggered. `SubjectResult.resolved_model` records the value actually used per run.
- **Q3 (`report` auto-discovery):** resolved to the proposed **auto-discover latest per `(suite,subject)`**, with an
  explicit-dirs override — shipped as `ao-bench report --results-root R --suite ID` (auto-discover) vs
  `--run-dir D [--run-dir D...]` (explicit; repeatable), per §7's as-built CLI shapes (note: the flag is
  `--results-root`, not the original sketch's `--results-dir`).

## 19. Post-implementation docs-refresh (T-Dcs2Rk — mandatory)
After implementation, reconcile this HLD/LLD + ADR-0008 to as-built (grep the whole doc for contradicting claims, not just the fix site — learnings §35), add the feature pointer to `hld-agent-orchestrator.md`, add a `benchmarks/README.md`, and capture learnings. Mark complete only after confirming docs against implemented code.

**Done (2026-07-22, T-Dcs2Rk):** this doc reconciled §3–§9, §11, §14, §18 to as-built (module ownership split,
`AoWorkflowSubject`/`instructions/` design gap, CLI/Make shapes, comparison-naming limitation, Q1–Q3 resolved);
ADR-0008 moved Proposed → Accepted with an as-built implementation-notes section; `benchmarks/README.md` added;
`hld-agent-orchestrator.md`'s feature-docs list updated; learnings appended to `meta/learnings.md` +
`meta/learning-compact.md`. Every claim above was checked against the actual code/tests/committed results, not
copied from a task STATUS.md narrative — see the completion note in
`meta/tickets/E-9Qk4Zt-agent-benchmark-harness/T-Dcs2Rk-docs-adr-reconcile/STATUS.md` for the verification commands.
