# TASK: T-Cli8Nf-bench-cli-make-entrypoints

## Metadata
- Task ID: `T-Cli8Nf-bench-cli-make-entrypoints`
- Epic ID: `E-9Qk4Zt-agent-benchmark-harness`
- Owner: developer agent
- Created: 2026-07-22 · Last Updated: 2026-07-22
- Status: Draft · Estimate: 1.5 days (≤3)

## Requirements Mapping
- FR-7 (entry points), NFR-1 (core untouched / SI-1).

## Description
Wire the standalone `ao-bench` console script and the `make bench-*` recipes (design §7, ADR-0008 D4/D5). Register `ao-bench = "agent_orchestrator.bench.cli:app"` in `pyproject.toml` `[project.scripts]` — a **separate** entry point from `ao`, so the core `ao` command surface and its tests are byte-untouched and `bench` imports never load on `ao run`.

Commands (Typer app in `bench/cli.py`; `validate` skeleton already exists from T-Sc4Hm2):
- `ao-bench validate --suite S [--subject J]` (probe `claude --version` for `claude_cli` subjects).
- `ao-bench run --suite S --subject J [--force] [--budget-total N] [--max-turns N] [--timeout SEC]` → `runner.run_suite`; exit 0 on success, non-zero if any task errored.
- `ao-bench report --suite <id> --results-dir benchmarks/results [--subjects a,b]` → `results.write_comparison` (auto-discover latest per (suite,subject) unless dirs given — Q3).
- `ao-bench list --suites | --subjects | --results`.

Make recipes (append to existing `Makefile`, no edits to existing targets): `bench-validate`, `bench-smoke` (all 3 subjects at haiku, cheap), `bench-run` (`SUITE`/`SUBJECT` vars), `bench-report`.

## Acceptance Criteria
1. `ao-bench --help` lists `validate/run/report/list`; each subcommand `--help` documents its flags. (CliRunner)
2. `ao-bench run --suite <fake suite> --subject <fake subject>` → exit 0, writes `benchmarks/results/<date>-…/run.json`+`summary.md`; a suite where the fake subject fails a task → exit non-zero. (CliRunner)
3. `ao-bench report --suite <id> --results-dir <tmp>` over two fake result dirs → writes `comparison.{json,md}`, exit 0. (CliRunner)
4. `make bench-validate` runs `ao-bench validate` on the committed dev-core suite (green once T-Fx6Dp0 lands; before that, against a fake suite fixture).
5. **SI-1 regression gate:** the existing engine/CLI test suite (`ao run/validate/resume`) passes **unedited**; `python -c "import agent_orchestrator.cli"` does not import `agent_orchestrator.bench` (grep/import-graph check). `pyproject` gains only the `ao-bench` script line + no new hard runtime dep beyond what core already pins.
6. `mypy`/`ruff` clean on `bench/`.

## Risks
- Accidentally importing `bench` from core (breaks SI-1) → enforce with an import-graph assertion test.

## Dependencies
- T-Rpt3Wq (report writer), T-Run5Tz (runner), T-Sc4Hm2 (validate).

## Pseudocode / Algorithm
```text
app = typer.Typer(name="ao-bench")
@app.command() validate/run/report/list  # each lazy-imports spec/runner/results inside the body (mirrors core cli.py)
run(): suite=load_suite; subject=load_subject; res=run_suite(...); exit(0 if all_ok else 1)
```

## Schemas / Interface Notes
- Interface (CLI): design §7. `pyproject.toml [project.scripts] ao-bench = "agent_orchestrator.bench.cli:app"`.
- Triggers/events: N/A. Artifacts: writes committed `benchmarks/results/**` via runner/results.

## Handoff Boundary
- Upstream: T-Run5Tz/T-Rpt3Wq/T-Sc4Hm2.
- Downstream: T-Fx6Dp0 exercises `make bench-smoke` end-to-end; T-Tst4Ln adds CliRunner e2e + the SI-1 import-graph test.

## Artifacts
- Docs/comments: this folder. Large outputs: none.
