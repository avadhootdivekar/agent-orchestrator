---
name: benchmarking
description: Run, extend and read `ao-bench` benchmarks — compare `ao` workflows vs bare `claude -p` models on speed, cost and correctness with parity, generated (Tier 1) and large-codebase (Tier 2) suites, repeats and paired statistics. Use when asked to benchmark models/subjects, add a benchmark task/family, calibrate a suite, or interpret comparison.md.
---

# Benchmarking with `ao-bench`

Design: `docs-md/benchmarking-framework-hld.md` (harness) and `docs-md/benchmark-discriminating-suites-hld.md`
(discriminating suites, epic `E-Bn7Dsc`). Probe contract: `docs-md/bench-probe-dsl.md`. Everything runs from a
repo checkout with `uv run ao-bench …` — never the global `ao`.

## Rules that are not optional
1. **Parity.** Every subject in a comparison runs under the same parity bundle (`benchmarks/parity/default` or the
   suite's `parity` field). Pass `--parity` only to override for *all* subjects. A comparison across different
   bundle fingerprints is refused; `--allow-mixed` stamps the report as non-parity — only for debugging.
2. **Real money.** `claude_cli` / `ao_workflow` subjects spend. Start with `benchmarks/subjects/claude-haiku.json`
   and `--cost-budget-usd`; tier caps in `benchmarks/tiers.json` are enforced regardless.
3. **Hidden assets stay hidden.** Probes, reference implementations and held-out tests live in the grader-owned
   `grading_dir`, never under the agent-visible workspace. Do not put `.grading/` inside fixtures.
4. **Fake tier for CI.** Tests use `FakeSubject` (`scripted_effect`: `noop|copy-solution|copy-reference|fail|
   timeout|error`); Docker/network tests are gated by `AO_E2E_DOCKER=1` / `AO_E2E_NETWORK=1`.

## Suites
| Suite | Tier | What | Grader |
|---|---|---|---|
| `dev-core` | small | 6 tiny fixtures, saturated — smoke only | pytest/command |
| `dev-medium` | medium | 6 multi-file fixtures, held-out tests in `grading_dir` | command |
| `gen-services-mini` / `gen-services` | medium | generated behavioural-spec tasks (crud-relational http, job-queue jsonl, …) in py/ts/go | `blackbox` (Docker sandbox) |
| `repo-mutant-mini` | large | bug injection / feature excision on pinned public repos | `pytest_targeted` |
| `swe-verified-mini` | large | 10 pinned SWE-bench Verified instances | swebench (Docker) |

## Common commands
```bash
uv run ao-bench validate --suite benchmarks/suites/gen-services-mini/suite.json
uv run ao-bench images --build --suite benchmarks/suites/gen-services-mini/suite.json   # sandbox images
uv run ao-bench run --suite <suite.json> --subject benchmarks/subjects/claude-haiku.json --cost-budget-usd 3
uv run ao-bench campaign --suite <suite.json> --subject A.json --subject B.json --repeats 3   # staged repeats
uv run ao-bench report --results-root benchmarks/results --suite <suite-id>
uv run ao-bench calibrate --suite <suite.json> --subject haiku.json --subject opus.json --repeats 2 --write
uv run ao-bench gen --out benchmarks/suites/gen-services --family crud-relational --family job-queue \
    --count 2 --languages python,typescript,go --seed 20261023
uv run ao-bench import-prs --repo pallets/click --since 2026-06-01 --out benchmarks/suites/repo-live
```

## Reading `comparison.md`
- Use **attempted-based rates** and `score_mean` (infra errors — CLI missing, spawn/quota failures, sandbox
  failures — are excluded and listed in the Infra table; "suspect" = unsolved, < $0.15, < 180 s).
- **Pairwise** rows give the paired bootstrap 95 % CI and Wilcoxon p on per-task score differences; a CI that
  straddles 0 means "not distinguishable at this n" — add repeats, not conclusions.
- **Per family / per language** show where a subject wins; a single family dominating is a bias flag.
- A campaign banner `unreliable` (> 10 % infra for a subject) means fix the harness before reading numbers.

## Adding things
- **New generated family**: `src/agent_orchestrator/bench/gen/<family>.py` (subclass `Family`, register), a stdlib
  Python reference that reads `INSTANCE_JSON`, probes per the DSL (core/trap/perf), SPEC.md prose that never
  mentions probes/reference/grading. Calibrate before adding to a live suite (20–80 % band).
- **New Tier-2 task**: a `recipe.json` (`inject|excise|enhance`) + hidden tests under `tasks/<id>/hidden/`; verify
  with the mutation un-fixed (unsolved) and fixed (solved) before committing; record `verified`.
- **New subject**: `benchmarks/subjects/*.json`; keep the wrapper prompt thin (parity lint budget) — instructions
  belong in the parity bundle, not the prompt.
