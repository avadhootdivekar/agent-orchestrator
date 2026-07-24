# Benchmark & Eval-Harness Landscape Survey (2025–2026)

- Epic: [`E-9Qk4Zt-agent-benchmark-harness`](../meta/tickets/E-9Qk4Zt-agent-benchmark-harness/EPIC.md)
- Design doc: [`benchmarking-framework-hld.md`](benchmarking-framework-hld.md)
- Decision record: [`adr/ADR-0008-benchmark-harness-approach.md`](adr/ADR-0008-benchmark-harness-approach.md)
- Date: 2026-07-22
- Author: architect agent
- Status: survey complete — feeds the build-vs-adopt decision (ADR-0008)

> This is the "landscape / competitor analysis" section of the benchmarking epic. It surveys
> the current (2025–2026) code / agent benchmarks and eval harnesses, judges each against
> **our constraints** (below), and concludes with a Build/Buy/Hybrid recommendation and a
> positioning statement that ties directly to the design in `benchmarking-framework-hld.md`.

## 0. Our constraints (the feasibility yardstick)

Every candidate below is judged against the environment this framework must run in:

| # | Constraint | Consequence for a benchmark |
|---|------------|-----------------------------|
| C1 | **Subjects run through subprocesses** (`ao run` and `claude -p`), not a Python API. | The harness must treat the system-under-test as a black box CLI; benchmarks whose harness assumes "call `model.generate()`" need an adapter. |
| C2 | **Sandbox = repo dir only.** Spawned `claude` cannot touch system `/tmp`. | Task workspaces must live in a repo-local gitignored dir (`playground/.tmp/bench/`). Any harness that hard-codes `/tmp` or a global cache dir is a problem. |
| C3 | **Real-LLM runs are expensive.** Haiku for smoke, Sonnet/Opus for the real comparison. | Big suites (SWE-bench full = 2 294 tasks) are financially infeasible to run repeatedly; MVP needs a *small curated* suite. |
| C4 | **Cost/usage plumbing already exists.** `parse_usage_and_429` + `extract_result_event` + `compute_run_usage_totals` parse `total_cost_usd`/tokens from Claude CLI result events. | Prefer a harness that lets us reuse these so `ao` and bare-`claude` cost numbers are apples-to-apples; a foreign harness computes cost its own way. |
| C5 | **`uv run` only, no `pip`.** Docker IS installed but heavy. | Pure-Python, few-deps harnesses are cheap to adopt; 120 GB-Docker harnesses are non-MVP. |
| C6 | **Determinism / reproducibility.** Fixed task set, pinned prompts, recorded configs; result dir reproducible from spec. | Favor harnesses (or our own design) where the task set + grader are declarative and version-controlled. |

## 1. Comparison matrix

Legend for **Feasibility HERE**: 🟢 MVP-friendly · 🟡 adoptable later / partial · 🔴 non-MVP (heavy) / poor fit.

| Benchmark / Harness | What it measures | License | Runtime requirements | Cost profile | Feasibility HERE |
|---|---|---|---|---|---|
| **SWE-bench** (Full 2294 / Lite 300 / Verified 500 / Multimodal 517) | Resolve real GitHub issues; graded by the repo's own test suite (FAIL→PASS + PASS→PASS). | MIT (harness + Full/Lite/Verified data). | **Docker per-instance images**; x86_64, ≥120 GB disk, ≥16 GB RAM, 8 CPU. Multimodal now cloud-graded via `sb-cli`. | Very high: hundreds of tasks × frontier model × multi-turn agent. | 🔴 MVP / 🟡 as an *imported suite* later. Docker is installed but the disk/time/$ cost is out of MVP scope. The **task shape** (fixture repo + test-based grader) is exactly what we model. |
| **SWE-bench Verified** (human-filtered 500) | Same, but human-validated solvable subset — the de-facto "can it code" leaderboard. | MIT. | Same Docker harness (subset of images). | High. | 🟡 Best external import target: our `PytestGrader` + fixture-repo model maps 1:1; import a *handful* of instances post-MVP. |
| **SWE-bench Multimodal** (517, JS front-end) | Issue resolution with image/screenshot context. | MIT (private test split; `sb-cli` cloud eval). | Cloud submission, private grading. | High + cloud dependency. | 🔴 Private grading + cloud submission conflicts with C1/C6. |
| **SWE-bench Pro** (Scale) | Long-horizon SWE on larger/harder + private repos; GPL/copyleft public subset to fight contamination. | Public subset copyleft (GPL); private held-out. | Docker, long-horizon agent. | Very high. | 🔴 Non-MVP; interesting as a "hard tier" reference only. |
| **Aider polyglot benchmark** (225 hard Exercism exercises × 6 langs) | Whole-file/diff code-edit correctness across C++, Go, Java, JS, Python, Rust; 2 attempts (2nd sees test output). Tracks pass-rate **and $ cost**. | Apache-2.0 (exercises repo). | Docker recommended; per-exercise unit tests; needs the target language toolchains. | Moderate–high (225 × 2 attempts). | 🟡 Strong conceptual match (test-graded, **cost-aware** — same axis we want). Non-MVP to import wholesale (needs 6 language toolchains); a *few* Python exercises make good fixtures. |
| **HumanEval / MBPP** (164 / ~1 000 functions) | Function-level code generation; `pass@k` via unit tests. | MIT. | Trivial: run generated code against asserts (sandbox recommended). | Very low. | 🟢 Cheap, but **saturated & contaminated** (in training data) — weak signal for frontier agents. Useful only as a trivial smoke tier. |
| **EvalPlus** (HumanEval+ / MBPP+) | HumanEval/MBPP with ~80×/35× more tests to catch overfit solutions. | Apache-2.0. | Same as HumanEval + more test exec. | Low. | 🟢 Better than raw HumanEval; still function-level, not agentic. Good smoke-tier import. |
| **LiveCodeBench** | Contamination-free competitive-programming (LeetCode/AtCoder/CodeForces), time-segmented by model cutoff; also self-repair, test-output prediction. | MIT. | Dataset download; run code against tests; time-window filtering per model. | Moderate. | 🟡 Good for *pure model* coding signal, but it's single-shot generation, not repo-editing agents — measures a different axis than "does `ao`'s workflow finish a dev task." |
| **terminal-bench / Terminal-Bench 2.0** (Stanford + Laude; ~89 hard tasks) | Agents in a real terminal/sandbox; programmatic verification scripts. Harbor harness supports Claude Code, Codex CLI, OpenHands, Mini-SWE-Agent. | Apache-2.0 (tasks + harness). | **Docker container per task** + verification scripts. | Moderate–high. | 🟡 Closest to our "agent does real work, graded by a script" model, and **already wraps CLI agents like Claude Code**. Docker-per-task = non-MVP; the `CommandGrader` concept is borrowed directly. |
| **mini-swe-agent** | A ~100-line bash-only agent scoring >74 % on SWE-bench Verified; not a benchmark but a *reference minimal agent/harness*. | MIT. | Docker/podman/local; litellm for models. | N/A (it's a runner). | 🟢 Design reference: proves a tiny subprocess-driving harness is enough. Validates our thin-harness choice. |
| **inspect-ai** (UK AISI) + **inspect_evals** (200+ evals) | General eval framework: `dataset → Task → Solver → Scorer`; built-in Docker sandbox, model-graded scoring w/ bootstrap CIs, log viewer. Ships GAIA/SWE-bench/Cybench/GDM-CTF. | MIT (framework) / mixed per-eval. | `pip install inspect-ai`; provider matrix; Docker for sandboxed evals; a judge model for model-graded scorers. | Framework free; evals cost = whatever you run. | 🟡 The strongest *adopt* candidate. But it's provider/SDK-oriented (C1 friction: our subjects are CLIs, not `inspect` model providers), pulls a large dep + provider matrix (C5), and computes cost its own way (C4). Best as a **post-MVP import path** ("run our fixture as an inspect Task"), not the MVP spine. |
| **lm-evaluation-harness** (EleutherAI) | De-facto harness for *static QA / multiple-choice / logprob* LLM benchmarks (MMLU, etc.). | MIT. | `pip`; loads HF models or API endpoints. | Varies. | 🔴 Wrong shape: built for logprob/QA tasks, not agentic file-editing subprocesses. |
| **GAIA** (466 real-assistant Qs) | General assistant multi-step reasoning + tool use; exact-match answers. | Non-commercial-ish; gated HF dataset. | Dataset download; tool-enabled agent; exact-match grader. | Moderate. | 🟡 Non-dev general-agent signal for later "domains" (not software). Gated dataset + non-dev focus = non-MVP. |
| **AgentBench** | LLM-as-agent across 8 environments (OS shell, DB, KG, web, games…). | Apache-2.0. | Per-environment Docker/services. | High setup. | 🔴 Heavy multi-environment setup; non-MVP. |
| **MLE-bench** (OpenAI; 75 Kaggle comps) | ML-engineering agents: train models, prep data, run experiments; graded vs Kaggle medals. | MIT (code; datasets external). | Large Kaggle dataset downloads; GPU-ish; Docker. | Very high. | 🔴 Non-MVP; a candidate "ML domain" suite far in the future. |
| **OpenAI Evals** | Registry of model evals (mostly completion/judge-based). | MIT. | `pip`; API-oriented. | Varies. | 🔴 API/provider-oriented; poor fit for CLI subjects. |

## 2. Gap analysis — where the existing options fall short *for us*

1. **None benchmark an *orchestrated multi-agent workflow* as the unit under test.** Every harness above benchmarks a *model* (or a single agent). Our headline question is different: *does an `ao` multi-agent workflow beat a single `claude -p` call at the same dev task, and at what cost?* That is a **subject-comparison** question — the subject is `ao` (a DAG of agents) vs bare `claude`. No off-the-shelf harness has a first-class "subject = my whole orchestration CLI" abstraction.
2. **Cost is a second-class metric almost everywhere.** Only Aider's leaderboard treats $ cost as a headline axis. SWE-bench/terminal-bench report solve-rate, not cost-per-solve. Our differentiator is *cost & time per solved task, apples-to-apples across subjects* — and we already parse the exact `total_cost_usd` the Claude CLI emits (C4), which a foreign harness would recompute (or not compute) for `ao`'s multi-task run.
3. **Heavy infra is the norm.** SWE-bench (120 GB Docker), terminal-bench (Docker-per-task), MLE-bench (Kaggle datasets), AgentBench (8 services) are all far past MVP weight (C3, C5). The cheap ones (HumanEval/MBPP) are saturated/contaminated and measure function-gen, not agentic dev.
4. **Provider/SDK assumptions clash with CLI subjects (C1).** inspect-ai, lm-eval-harness, OpenAI Evals are built around "give me a model provider/endpoint." Our subjects are *processes* (`ao run …`, `claude -p …`). Bridging them means writing a custom Solver/provider anyway — i.e. most of the adopt cost with less control.
5. **Common complaints in the ecosystem** (from the survey sources): SWE-bench harness is disk/time-heavy and flaky across machines; HumanEval/MBPP are contaminated and saturated; leaderboards rarely report cost or wall-clock; agent benchmarks are "time-consuming and complex to set up" (the exact problem inspect_evals advertises solving). These reinforce: adopt a heavyweight harness now → we inherit its setup burden for little MVP payoff.

## 3. What existing tools do well (worth copying, not adopting wholesale)

- **SWE-bench**: the *fixture-repo + repo's-own-test-suite-as-grader* pattern → our `PytestGrader` + fixture layout copies it exactly.
- **Aider polyglot**: **cost-per-run as a headline metric** and the *2-attempt with test-feedback* protocol → our metrics schema treats `cost_usd`/`wall_clock`/`attempts` as first-class.
- **terminal-bench / Harbor**: *programmatic verification scripts in a sandbox* and *wrapping CLI agents (Claude Code) as subjects* → our `CommandGrader` and `ClaudeCliSubject`/`AoWorkflowSubject` adapters.
- **inspect-ai**: the clean `dataset → Task → Solver → Scorer` seam and pluggable scorers → our `Suite → Subject → Grader` seam is the same shape, deliberately, so an inspect import is a small adapter later.
- **mini-swe-agent**: proof that a ~100-line subprocess-driving harness is enough to be useful → validates a thin custom harness over a framework.

## 4. Recommendation — **Hybrid: thin custom harness now, external-suite import later**

**Build a thin, declarative, `ao`-native benchmark harness for the MVP; design the `Subject`/`Grader`/`Suite` seams so external suites (SWE-bench Verified via Docker, an inspect-ai bridge, a few Aider/EvalPlus exercises) can be *imported* post-MVP without reworking the harness.** Full rationale and the rejected alternatives are in [ADR-0008](adr/ADR-0008-benchmark-harness-approach.md).

Why build the thin core rather than adopt:
- The **headline unit under test is `ao` itself** (a workflow CLI), which no harness models first-class (gap #1) — we'd write a custom Subject/Solver adapter for *any* framework anyway.
- We must **reuse `ao`'s own cost/usage parsing** (C4) for apples-to-apples $ numbers — a foreign harness fights this.
- MVP must run **cheap and repo-sandboxed** (C2, C3, C5) — a thin harness with tiny committed fixtures under `playground/.tmp/bench/` is the only thing that fits; every heavyweight harness is explicitly deferred.
- "Third-party integrations must never destabilize the core" (CLAUDE.md) — a thin harness that lives *outside* the engine import graph and shells out to `ao`/`claude` cannot destabilize `ao run`.

Why keep the import seam (the "hybrid" half):
- SWE-bench Verified / Aider / EvalPlus / inspect-ai are the credible external signals. Our `Grader` (test-pass-rate) and `Suite` (fixture-repo + instruction + grader) abstractions are deliberately SWE-bench-shaped, so importing a subset later is an *adapter*, not a rewrite.

## 5. Positioning statement (drives the design, prevents feature creep)

```
We will:
- MATCH  inspect-ai / SWE-bench in: reproducible, spec-declared task suites with
         pluggable, test-based graders and machine-readable results.
- BEAT   every existing harness in: benchmarking OUR system-under-test — an `ao`
         multi-agent workflow — head-to-head against a bare `claude -p` call, on the
         SAME task, with cost/time/tokens reported apples-to-apples by reusing `ao`'s
         own `total_cost_usd`/usage plumbing.
- AVOID  the complexity of SWE-bench's 120 GB Docker harness, MLE-bench's Kaggle
         downloads, and inspect-ai's full provider matrix for the MVP: tiny committed
         fixtures, subprocess subjects, no dataset download, no judge LLM. Those land
         later as OPT-IN imported suites behind the same Subject/Grader seam.
```

Design consequences that flow from this positioning (see the HLD):
- **Subject = adapter over a CLI process** (not a model provider) — because the differentiator is comparing whole systems, not models.
- **Cost/tokens reuse core helpers** — because apples-to-apples cost is the differentiator, not a foreign harness's estimate.
- **MVP graders are test-based/file-based only; LLM-judge is non-MVP** — avoids a judge-model dependency and keeps the smoke tier free/cheap.
- **A `domain` field on suites/tasks + a generic `CommandGrader`** — the *only* concession to future non-dev domains (biology/physics/legal); deliberately minimal, not overbuilt.

## 6. Sources

- SWE-bench (Full/Lite/Verified/Multimodal), harness & requirements: https://github.com/swe-bench/SWE-bench · https://www.swebench.com/SWE-bench/reference/harness/ · https://www.swebench.com/
- SWE-bench Pro (Scale): https://github.com/scaleapi/SWE-bench_Pro-os · https://scaleapi.github.io/SWE-bench_Pro-os/
- Aider polyglot benchmark: https://aider.chat/docs/leaderboards/ · https://github.com/Aider-AI/polyglot-benchmark
- HumanEval / MBPP / EvalPlus / LiveCodeBench: https://benchmarkingagents.com/humaneval/ · https://arxiv.org/abs/2403.07974 (LiveCodeBench)
- terminal-bench / Terminal-Bench 2.0 / Harbor: https://github.com/laude-institute/terminal-bench · https://artificialanalysis.ai/evaluations/terminalbench-hard
- mini-swe-agent: https://github.com/swe-agent/mini-swe-agent
- inspect-ai / inspect_evals (UK AISI): https://github.com/UKGovernmentBEIS/inspect_ai · https://ukgovernmentbeis.github.io/inspect_evals/ · https://www.aisi.gov.uk/blog/inspect-evals
- lm-evaluation-harness (EleutherAI): referenced via survey (EleutherAI/lm-evaluation-harness)
- GAIA / AgentBench / MLE-bench: https://arxiv.org/abs/2308.03688 (AgentBench) · https://arxiv.org/abs/2410.07095 (MLE-bench) · https://github.com/openai/mle-bench · https://benchmarkingagents.com/benchmarks-list/
