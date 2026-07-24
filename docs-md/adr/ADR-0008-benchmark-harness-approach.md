# ADR-0008 — Benchmark harness: build a thin custom harness vs adopt an external one

- Status: **Accepted — implemented as designed** (epic `E-9Qk4Zt` delivered 2026-07-22, MVP complete; see
  "Implementation notes" below for as-built deviations, all recorded/low-risk, none contradicting this decision)
- Date: 2026-07-22
- Deciders: Avadhoot Divekar (user), Claude (architect role)
- Related: Epic [`E-9Qk4Zt-agent-benchmark-harness`](../../meta/tickets/E-9Qk4Zt-agent-benchmark-harness/EPIC.md) · design doc [`benchmarking-framework-hld.md`](../benchmarking-framework-hld.md) · evidence [`benchmark-landscape-survey.md`](../benchmark-landscape-survey.md) · ADR-0001 (Claude-native + thin spec — same "thin, own-the-spine" instinct) · ADR-0003 (settings precedence — bench knobs ride the invocation chain) · ADR-0005 (headless tool policy — bench subjects inherit `--permission-mode` semantics)

## Context

The ask (prompt.md "Current Ask"): survey agent/dev benchmarks; integrate a benchmarking capability to compare **`ao`** vs **plain Claude Sonnet** vs **plain Claude Opus**; add a script/`make` recipe that publishes results to a git-committed directory; keep the framework configurable so non-dev domains can be added later without overbuilding.

The [landscape survey](../benchmark-landscape-survey.md) evaluated SWE-bench (Full/Lite/Verified/Multimodal/Pro), Aider polyglot, HumanEval/MBPP/EvalPlus, LiveCodeBench, terminal-bench/Harbor, mini-swe-agent, **inspect-ai** (UK AISI), lm-eval-harness, GAIA, AgentBench, MLE-bench, OpenAI Evals — against six hard constraints: subjects are **subprocesses** not model providers (C1); workspaces must be **repo-local gitignored**, never `/tmp` (C2); real-LLM is **expensive** → small suite (C3); **reuse `ao`'s own cost/usage plumbing** (C4); **`uv run` only, Docker deferred** (C5); **deterministic/reproducible** (C6).

Two structural facts from the survey dominate the decision:
1. **The headline unit under test is `ao` itself** — an orchestrated multi-agent workflow (a DAG-of-agents CLI), compared against a bare `claude -p` call on the *same* task. **No existing harness models "subject = my whole orchestration CLI" first-class**; they benchmark a *model* or a single agent. We would write a custom subject/solver adapter for `ao` no matter which framework we adopt.
2. **Cost/time-per-solved-task, apples-to-apples across subjects, is the differentiator.** Only Aider treats $ as a headline axis. We already parse the exact `total_cost_usd`/usage the Claude CLI emits (`parse_usage_and_429`/`extract_result_event`/`compute_run_usage_totals`). A foreign harness recomputes cost its own way (or not at all for `ao`'s multi-task run), breaking apples-to-apples.

## Decision

Adopt a **hybrid: build a thin, declarative, `ao`-native harness for the MVP, with `Subject`/`Grader`/`Suite` seams shaped so external suites can be *imported* later.** Sub-decisions:

### D1 — Build a thin custom harness now (don't adopt a framework as the MVP spine)
A small module (`src/agent_orchestrator/bench/`) that: loads schema-validated `suite.json`/`subject.json`; for each (subject × task) materializes a repo-local gitignored workspace, shells out to the subject (`uv run ao run …` or `claude -p …`), grades the mutated workspace, and writes committed results. It **reuses core's pure cost/usage helpers** so `ao` and bare-`claude` numbers are directly comparable.

### D2 — Keep an external-import seam (the "hybrid" half)
`Grader` (test-pass-rate) and `Suite` (fixture-repo + instruction + grader) are deliberately **SWE-bench-shaped**, and the `Subject` seam is deliberately **inspect-ai-shaped** (`dataset → Task → Solver → Scorer` ≈ `Suite → Subject → Grader`). Importing a subset of SWE-bench Verified (via a future `DockerSubject`) or bridging to inspect-ai is then an *adapter behind the existing seam*, not a rewrite. This is explicitly **non-MVP** (design §12).

### D3 — MVP graders are test/file-based only; LLM-judge is non-MVP
`PytestGrader` (default), `CommandGrader` (generic exit-code → the one hook for non-dev domains), `FileAssertionGrader`. No judge model in MVP → the smoke tier is free/cheap and CI is network-free (fake subject/grader).

### D4 — Entry point: a standalone `ao-bench` console script, not an `ao bench` subcommand
A separate `[project.scripts]` entry (`ao-bench = "agent_orchestrator.bench.cli:app"`). The core `ao` command surface and its tests stay byte-untouched, and `bench` imports/deps never load on a normal `ao run`. This directly honors CLAUDE.md's "third-party integrations must never destabilize the core." (An `ao bench` sub-typer that lazy-imports was considered — see Alternatives.)

### D5 — `bench/` is a separate module OUTSIDE the engine import graph
`bench/` imports pure, read-only helpers *from* core; **nothing in core imports `bench/`.** `ao run` works whether or not `bench/` exists (safety invariant SI-1, regression-gated by running the existing engine suite unedited). No change to `workflow.schema.json`/`agents.schema.json`/`reposet.schema.json` — bench specs are their own schemas under `benchmarks/schemas/`.

### D6 — Heavyweight harnesses (SWE-bench Docker, terminal-bench, MLE-bench) are non-MVP
Documented and justified as deferred (C3/C5): Docker is installed but 120 GB images / Kaggle datasets / multi-service setups are out of MVP weight. They land later behind the D2 seam as opt-in imports.

## Alternatives considered

- **Adopt inspect-ai as the spine (rejected for MVP).** Strongest framework candidate (clean scorer seam, Docker sandbox, 200+ evals). But it's provider/SDK-oriented (C1: our subjects are CLIs → we'd write a custom Solver/provider anyway), pulls a large dependency + provider matrix (C5), and computes cost its own way (C4 — breaks apples-to-apples for `ao`'s multi-task run). Kept as a **post-MVP import target** (D2), not the MVP spine.
- **Adopt the SWE-bench harness (rejected for MVP).** Exactly the fixture-repo + test-grader shape we want, but 120 GB Docker / ≥16 GB RAM / hundreds of instances is financially and operationally out of MVP scope (C3/C5), and it still has no "subject = `ao` workflow" concept. We **copy its task shape** and keep it as an import target (D2/D6).
- **Adopt terminal-bench/Harbor (rejected for MVP).** Closest to "agent does real work, graded by a script," and it already wraps CLI agents like Claude Code — but Docker-per-task is non-MVP. We borrow its `CommandGrader` idea.
- **Cheap classic benchmarks only — HumanEval/MBPP/EvalPlus (rejected as the headline).** Trivial to run but saturated/contaminated and function-gen, not agentic dev — weak signal for the `ao`-vs-`claude` question. Usable only as a trivial smoke tier import.
- **`ao bench` subcommand instead of a standalone script (rejected).** Discoverable and shares `--version`/logging, and could lazy-import to stay light — but it couples bench into the `ao` entrypoint and its test surface, weakening SI-1. The standalone script is the cleaner "never destabilize core" choice (D4).
- **Express the benchmark run itself as an `ao` workflow (rejected).** Tempting (dogfooding), but it would couple the harness to the very engine it benchmarks, complicate cost attribution, and blur the subject boundary. The benchmark **suite** is a declarative spec; the **harness** is plain code that treats `ao` as a black box.

## Consequences
- **+** MVP fits the constraints exactly: cheap (haiku smoke), repo-sandboxed, reuses core cost plumbing for true apples-to-apples, and cannot destabilize `ao run` (separate module + script).
- **+** The differentiator is realized: a first-class "subject = `ao` workflow vs bare `claude`" comparison with cost/time/tokens — which no surveyed harness offers.
- **+** Future external suites (SWE-bench Verified, inspect-ai, Aider/EvalPlus) and non-dev domains are unblocked behind the same seam without reworking the harness.
- **−** We own a (small) harness rather than inheriting a maintained one; the D2 seam is a design commitment we must honor to make imports cheap later.
- **−** MVP scores are LLM-stochastic (only the scaffolding is deterministic); the framework guarantees a reproducible result-dir + recorded configs, not identical numbers (documented, A5).
- **Follow-ons:** `DockerSubject` + SWE-bench importer; inspect-ai bridge; `LlmJudgeGrader`; non-dev domain suites; multi-seed statistical rigor; nightly cron trigger. All non-MVP (design §12).

## Implementation notes (2026-07-22, as-built)

All 9 epic tasks delivered; every D1–D6 sub-decision above shipped as designed. Full module-by-module deviations
are documented inline in [`benchmarking-framework-hld.md`](../benchmarking-framework-hld.md) §4; the ones that
matter at the ADR level:

- **D1 (thin custom harness):** shipped as 10 modules under `src/agent_orchestrator/bench/` — the pydantic spec
  models live in `spec.py` itself (no separate `models.py`); `runner.py` owns `run.json` persistence
  (write-temp + atomic rename, resumable), `results.py` owns `summary.md` + cross-subject `comparison.{json,md}`
  on top of it. Bench coverage: **98%** (1120 stmts/18 missed), all tests deterministic except an opt-in
  `real_llm` tier never run in CI.
- **D4 (standalone `ao-bench` script, not an `ao bench` subcommand):** shipped exactly as decided — one
  `[project.scripts]` line, core `ao`'s import graph and test suite (81 tests) confirmed byte-unaffected
  (`agent_orchestrator.bench` never appears in `sys.modules` after importing `agent_orchestrator.cli`).
- **D5 (SI-1, `bench/` outside the engine import graph):** held throughout; zero edits to
  `engine.py`/executors/`workflow.schema.json`/`agents.schema.json`/`reposet.schema.json` across all 9 tasks
  (grep-verified per task, re-verified at docs-refresh time).
- **New, ADR-relevant finding not anticipated in the original design:** the cross-subject `comparison.{json,md}`
  directory name (`<date>-<suite>-compare`) is not subject-set-aware, so same-day report regeneration overwrites
  the working-tree comparison artifact (per-run `run.json`/`summary.md` are unaffected — they're subject-id-keyed).
  Documented as a known limitation, not a defect requiring a design change; see HLD §9.
- **Real dev-core numbers (2026-07-22, all 6/6 solved on every subject)** validate the ADR's core differentiator
  claim — cost/time apples-to-apples via reused core usage plumbing: bare `claude -p` cost $0.37 (haiku) /
  $1.21 (sonnet) / $1.55 (opus); the `ao-epic` 2-agent workflow cost $0.66 (haiku) / $2.87 (sonnet) — roughly
  1.8–2.4× the bare-CLI cost for the same solve rate on this trivial suite (see HLD §11.1). This is the expected
  shape for an *easy* suite (no headroom for a verify/retry loop to pay for itself); the framework is now ready for
  the real Phase-2 comparison on harder tasks, which is explicitly out of this epic's scope.

## Positioning (anti-feature-creep, from the survey)
```
MATCH inspect-ai / SWE-bench in reproducible spec-declared suites + pluggable test-based graders.
BEAT  every harness in benchmarking OUR system (an `ao` workflow) vs bare `claude -p`, same task,
      cost/time/tokens apples-to-apples via `ao`'s own usage plumbing.
AVOID SWE-bench's 120 GB Docker, MLE-bench's Kaggle downloads, inspect-ai's provider matrix for MVP:
      tiny committed fixtures, subprocess subjects, no dataset download, no judge LLM — those land
      later as opt-in imports behind the Subject/Grader seam.
```
