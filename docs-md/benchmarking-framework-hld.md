# HLD + LLD — `ao` Benchmarking Framework

- Epic: [`E-9Qk4Zt-agent-benchmark-harness`](../meta/tickets/E-9Qk4Zt-agent-benchmark-harness/EPIC.md)
- Landscape survey (evidence): [`benchmark-landscape-survey.md`](benchmark-landscape-survey.md)
- Decision record: [`adr/ADR-0008-benchmark-harness-approach.md`](adr/ADR-0008-benchmark-harness-approach.md)
- Master HLD: [`hld-agent-orchestrator.md`](hld-agent-orchestrator.md)
- Date: 2026-07-22 · Author: architect agent · Status: **Design (not yet implemented)**

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
    write task.instruction    to ctx.repo_dir/INSTRUCTION.md   (subject workflow's first task reads it)
    env = {AO_MODEL: spec.model?, AO_MAX_TURNS: spec.max_turns?, AO_BUDGET_TOTAL: spec.budget_total?,
           AO_MAX_PARALLEL: spec.max_parallel?, AO_WORKSPACE_ROOT: ctx.workspace}
    argv = ["uv","run","ao","run","--workflow",ctx.workflow_json,
            "--reposets",ctx.rendered_reposet,"--agents",spec.agents_json]
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
- **Edge cases:** subject non-zero exit → `status="failed"` (task still graded — a failed subject can still leave a partial fix); timeout → `status="timed_out"`, keep partial capture; `claude`/`ao` not on PATH → `status="error"` with a clear message (checked up-front in `ao-bench validate`); quota exhaustion (`claude_quota_exhausted` from `parse_usage_and_429`) → surface distinctly, do NOT count as a solve, mark `subject_status="error"` with reason; missing `state.json` for ao subject → cost=None but still grade; permission-mode mismatch (write-only agent that runs tests) → documented: default `bypassPermissions` for claude subject (learnings §21).

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
- **Pseudocode:**
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
- **Subtasks:** bench-run-id derivation · workspace materialize (copytree, path-guarded under `playground/.tmp/bench`) · deterministic task iteration · resume skip · fingerprint · subject dispatch · grader dispatch · per-task persist · summary write · structured logging (`bench.run.start/task.start/task.end/run.end`).
- **Edge cases:** partially-written `run.json` from an interrupted run → resume reads valid task entries, re-runs the rest (persist-after-each-task guarantees the file is always valid JSON per completed task); `--force` re-runs all; a fixture path that escapes `playground/.tmp/bench` (symlink/`..`) → rejected (path guard, mirrors engine `working_dir` guard); one task crashing → recorded as `subject_status="error"`, run continues (no all-or-nothing); disk cleanup is manual (`ao prune`-style; workspaces preserved for audit per learnings §18).

### 4.6 Module `bench/results.py` — result & comparison writers

- **Purpose:** write `run.json` (machine) + `summary.md` (human) per subject; write cross-subject `comparison.{json,md}`.
- **Pseudocode:**
```
FUNCTION assemble_run_json(suite, subject_spec, metrics, agg, env) -> dict:
  RETURN {schema_version:"1.0", bench_run_id, suite:suite.id, domain:suite.domain,
          subject:{id,type,model,resolved_config}, env:{ao_version, claude_version, os, git_sha},
          started_at, ended_at, tasks:[metric.dict() for metric in metrics],
          aggregate: agg.dict()}

FUNCTION write_comparison(result_dirs: [dir]) -> None:                 # `ao-bench report`
  runs = [load run.json from d for d IN result_dirs where same suite]
  matrix = {task_id: {subject_id: {solved, score, cost_usd, wall}}}    # per-task, all subjects
  per_subject = {subject_id: aggregate}
  write_json(compare_dir/"comparison.json", {suite, subjects, matrix, per_subject})
  write_md(compare_dir/"comparison.md", side_by_side_table(per_subject) + per_task_table(matrix))
```
- **`summary.md` shape (committed):** a markdown table `task | category | solved | score | cost($) | wall(s) | tokens(in/out) | status` + an aggregate footer (`solved/total`, `solve_rate`, `total_cost`, `cost_per_solved`).
- **`comparison.md` shape:** `subject | solved/total | solve_rate | total_cost | cost_per_solved | total_wall` side-by-side, plus a per-task solved/cost matrix.
- **Edge cases:** subjects run against different suites → `report` errors (refuses to compare unlike suites); a subject's `run.json` missing → listed as "not run" in the comparison, never a crash; transcripts are **not** committed (huge, may contain noise) — `run.json` stores the workspace path as a pointer only (§4.4).

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
             grader:GraderConfig · timeout_seconds:int|None · tags:[str] · domain:str(inherited)
BenchSuite:  version:str · id:str · domain:str · description:str · defaults:{timeout_seconds:int} · tasks:[BenchTask]
GraderConfig: type:Literal["pytest","command","file_assertion","fake"] · command?:str · cwd?:str
             · timeout_seconds?:int · pass_threshold?:float · assertions?:[Assertion]
SubjectSpec: version:str · id:str · type:Literal["claude_cli","ao_workflow","fake"] · model?:str
             · permission_mode?:str · max_turns?:int · prompt_template?:str · extra_args?:[str]
             · workflow?:str · reposets?:str · agents?:str · max_parallel?:int · budget_total?:int
             · scripted_effect?:str · fake_cost?:float · fake_tokens?:{in:int,out:int}
RunContext:  workspace:str · repo_dir:str · instruction_path:str · capture_dir:str · timeout_seconds:int
             · budget_total:int|None · max_turns:int|None · workflow_json?:str · rendered_reposet?:str
SubjectResult: status:Literal["succeeded","failed","timed_out","error"] · wall_clock_seconds:float
             · cost_usd?:float · input_tokens?:int · output_tokens?:int
             · cache_creation_input_tokens?:int · cache_read_input_tokens?:int
             · attempts?:int · turns?:int · capture_dir:str · raw_error?:str
GradeResult: solved:bool · score:float · detail:dict · raw_tail:str
TaskMetric:  subject_id · task_id · domain · category · solved · score · wall_clock_seconds
             · cost_usd? · input_tokens? · output_tokens? · cache_* · attempts? · turns?
             · subject_status · grader_type · workspace(pointer) · config_fingerprint · started_at · ended_at

PROTOCOL Subject:  run(task:BenchTask, ctx:RunContext) -> SubjectResult   # errors: SubjectError
PROTOCOL Grader:   grade(cfg:GraderConfig, ctx:RunContext) -> GradeResult # errors: GraderError
REGISTRIES: SUBJECT_REGISTRY:{str->type[Subject]} · GRADER_REGISTRY:{str->type[Grader]}
ERRORS: BenchError(base) · SpecValidationError(reuse core) · SubjectError · GraderError
```
Idempotency & versioning: result dir name = `<date>-<suite>-<subject>` is stable → re-run skips completed tasks unless `--force`; `schema_version` on suite/subject/run.json enables forward migration; a `config_fingerprint` (sha256 of task+subject+`ao --version`) records exactly what produced each metric.

## 7. Entry points — CLI & Make

**Decision (ADR-0008 D4): a standalone `ao-bench` console script**, not an `ao bench` subcommand. Rationale: keeps the core `ao` command surface and its test suite untouched (SI-1, "third-party integrations must never destabilize core"); `bench` deps/imports never load on a normal `ao run`. Registered as a separate `[project.scripts]` entry point.

```
ao-bench validate --suite benchmarks/suites/dev-core/suite.json
ao-bench validate --subject benchmarks/subjects/claude-opus.json     # also probes `claude --version`
ao-bench run  --suite <suite.json> --subject <subject.json> [--force] [--budget-total N] [--max-turns N] [--timeout S]
ao-bench report --suite dev-core --results-dir benchmarks/results     # writes comparison.{json,md}
ao-bench list --suites | --subjects | --results
```
`Make` recipes (added to the existing `Makefile`):
```
bench-validate:  uv run ao-bench validate --suite benchmarks/suites/dev-core/suite.json
bench-smoke:     for s in claude-haiku ao-epic-haiku ; do \
                   uv run ao-bench run --suite benchmarks/suites/dev-core/suite.json \
                     --subject benchmarks/subjects/$$s.json --max-turns 20 ; done   # cheap, haiku
bench-run:       uv run ao-bench run --suite $(SUITE) --subject $(SUBJECT)          # real (sonnet/opus)
bench-report:    uv run ao-bench report --suite dev-core --results-dir benchmarks/results
```

## 8. Trigger / event schema (observability)

The runner emits structured log events (same logging infra as the engine, reused import-only). No cron/webhook trigger in MVP — benchmark runs are manual/`make`-driven (a scheduled nightly bench is a natural non-MVP cron trigger).
```
bench.run.start   {bench_run_id, suite, subject, task_count, ao_version, claude_version}
bench.task.start  {task_id, category, workspace}
bench.task.end    {task_id, solved, score, cost_usd, wall_clock_seconds, subject_status}
bench.run.end     {bench_run_id, solved, total, solve_rate, total_cost_usd}
```

## 9. Where things live (proposed layout)
```
benchmarks/
  schemas/  benchmark-suite.schema.json  subject.schema.json          # committed
  suites/dev-core/  suite.json  tasks/<task>/{instruction.md, fixture/…}   # committed fixtures
  subjects/  claude-opus.json  claude-sonnet.json  claude-haiku.json
             ao-epic.json  ao-epic-haiku.json  fake-pass.json         # committed
  results/<date>-<suite>-<subject>/  run.json  summary.md             # COMMITTED (git-preserved)
          <date>-<suite>-compare/    comparison.json  comparison.md   # COMMITTED
src/agent_orchestrator/bench/  __init__.py spec.py subjects.py graders.py metrics.py runner.py
                               results.py cli.py errors.py registries.py                # code
tests/bench/                   unit + CliRunner e2e (FakeSubject/FakeGrader — no real LLM)
playground/.tmp/bench/         # GITIGNORED workspaces (already covered by playground/.tmp/)
```
`pyproject.toml`: add `ao-bench = "agent_orchestrator.bench.cli:app"` to `[project.scripts]`. `bench/` ships in the wheel (packaged under `src/agent_orchestrator`); **schemas/suites/fixtures under `benchmarks/` are NOT packaged** — `ao-bench` resolves them relative to the repo/CWD (bench is a dev/repo tool, not an installed end-user command), so no wheel-packaging trap (learnings §49) for data files.

## 10. Deployment / rollout / upgrade
- **Rollout:** additive, opt-in, isolated. New module + new console script + `benchmarks/` tree; zero change to `src/agent_orchestrator/engine.py`, executors, or the workflow/agents/reposet schemas. `ao run/validate/resume` and their tests are byte-unaffected (SI-1, regression-gated in tests).
- **CI:** add a `bench-unit`/`bench-e2e` job using `FakeSubject`/`FakeGrader` (no network, no real LLM). The real-LLM bench run is **never** in CI (cost); it's a manual `make bench-*`.
- **Upgrade/versioning:** `schema_version` on every spec + `run.json`; a fingerprint per metric. New grader/subject types are additive registry+enum entries. Old result dirs stay valid (append-only history).
- **Docker/external suites:** documented as non-MVP; Docker is installed, so a `DockerSubject`/SWE-bench importer is unblocked behind the same seam later.

## 11. Developer / operator experience
- **Author a task in minutes:** drop a tiny `fixture/` repo + `instruction.md`, add a task entry with a `pytest` grader. No code.
- **Diagnosable failures:** per-task `capture/` (transcript, stdout) preserved in the workspace; `run.json` records `subject_status`, `raw_error`, grader `detail`; `summary.md` is human-scannable.
- **Fast local iteration:** `make bench-smoke` at haiku is cheap; `FakeSubject` gives instant, network-free end-to-end runs for harness development.
- **Reproducible:** result dir name + `config_fingerprint` + recorded `ao/claude` versions make "what produced this number" answerable; re-run resumes.
- **Ergonomic knobs:** budget/max-turns/timeout are CLI + per-suite defaults; models live in committed subject configs (swap Opus↔Sonnet↔Haiku by pointing at a different subject file).

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
- **R1** Bare `claude -p` needs the right `--permission-mode` to actually edit + run tests (learnings §21). Mitigation: default subjects to `bypassPermissions` in committed configs; document; smoke-test at haiku surfaces it early.
- **R2** ao-workflow subject cost attribution depends on exactly one run_id per workspace (A4). Mitigation: fresh workspace per task; `latest_run_dir` asserts a single run dir.
- **R3** pytest summary parsing is brittle across versions. Mitigation: exit-code is the source of truth (A3); summary parse only enriches `score`.
- **OPEN_QUESTION Q1:** the `ao-epic` subject's default workflow template — how many agents (design→implement→test) vs a single "solver" task? Proposed: start with a **minimal 2-task** template (implement→test) for cost control; T-Fx6Dp0 owns finalizing it. Escalate if a richer epic template is wanted for the headline comparison.
- **OPEN_QUESTION Q2:** exact model ids to pin in committed subject configs (`claude-opus-4-8`/`claude-sonnet-5`/`claude-haiku-4-5` vs aliases `opus`/`sonnet`/`haiku`). Proposed: pin full current ids, record resolved value in `run.json`; revisit if the CLI rejects a full id.
- **OPEN_QUESTION Q3:** should `report` auto-discover the latest result dir per subject, or take explicit dirs? Proposed: auto-discover latest per `(suite,subject)` under `--results-dir`, with an explicit-dirs override.

## 19. Post-implementation docs-refresh (T-Dcs2Rk — mandatory)
After implementation, reconcile this HLD/LLD + ADR-0008 to as-built (grep the whole doc for contradicting claims, not just the fix site — learnings §35), add the feature pointer to `hld-agent-orchestrator.md`, add a `benchmarks/README.md`, and capture learnings. Mark complete only after confirming docs against implemented code.
