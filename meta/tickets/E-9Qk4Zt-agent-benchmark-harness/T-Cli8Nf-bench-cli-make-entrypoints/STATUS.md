# STATUS

- ID: `T-Cli8Nf-bench-cli-make-entrypoints`
- Updated At: 2026-07-22
- State: Done
- Owner: developer agent

## This update
- By: Claude · Role: developer · Date: 2026-07-22
- Comment: Extended `bench/cli.py`'s Typer app with `run`/`report`/`list` alongside the
  existing `validate`, registered the standalone `ao-bench` console script
  (`pyproject.toml [project.scripts]`, the ONLY pyproject change), and appended
  `bench-validate`/`bench-smoke`/`bench-run`/`bench-report` to the `Makefile` (no
  existing target edited).
  - `run --suite S --subject J [--out-dir] [--force] [--task] [--budget-total]
    [--max-turns] [--timeout]` → `runner.run_suite` + `results.write_summary_md`;
    prints the result dir + an aggregate line. Exit 0 clean run; exit 2 if any task's
    SUBJECT status (not its grader verdict — an unsolved-but-cleanly-run task is a
    normal benchmark outcome) is failed/timed_out/error; exit 1 on a usage/spec error.
  - `report --run-dir <path>... | --results-root <root> --suite <id> [--out-dir]
    [--allow-mixed]` → `results.build_comparison` + `write_comparison`; auto-discovery
    (`--results-root`+`--suite`) picks the LATEST result dir per subject id under that
    root (dir names sort lexicographically the same as their embedded ISO date, so a
    single forward sorted pass keeping the last match per subject is "latest wins", no
    date parsing needed). Prints the comparison paths + the same "winner" lines
    `comparison.md` carries (`results.render_winners`, made non-`_`-prefixed for this
    reuse). Exit 0 on success, 1 on a refused/usage error.
  - `list --suite <path>` → a task table (id, category, grader type, timeout, tags).
- **Command-shape deviation from TASK.md (explicitly superseded by the assigning
  message's own literal CLI spec, which differs from TASK.md in three places — followed
  the assigning message as the more current/authoritative source):**
  1. `report` uses `--run-dir` (repeatable) / `--results-root`+`--suite`, not TASK.md's
     `--suite <id> --results-dir ... [--subjects a,b]`.
  2. `list` is `--suite <path>` → a single suite's task table, not TASK.md's
     `--suites | --subjects | --results` discovery-listing mode.
  3. `bench-smoke`'s Makefile guard uses a file-existence check + a friendly skip
     message against a placeholder `fake-pass` subject (assigning message, explicit),
     not TASK.md's "all 3 subjects at haiku" (those committed subject configs don't
     exist yet — they land with `T-Fx6Dp0`).
- **Exit-code semantics (design doc/TASK.md leave this underspecified beyond "non-zero
  if any task errored"):** exit 2 is driven by `SubjectResult.status` (harness-level:
  did the subject itself complete normally?), never by the grader's `solved` verdict —
  an unsolved-but-cleanly-run task is the normal, expected shape of a benchmark result
  and must never make `ao-bench run` "fail". Verified by
  `test_run_subject_task_failure_exits_nonzero` (uses `scripted_effect: "fail"`, which
  sets `subject_status="failed"`) and by contrast `test_run_writes_run_json...` (a
  successful subject run, regardless of the grader's verdict, exits 0).
- **`ao-bench report`'s `--out-dir`/`--results-root` compare-dir resolution mirrors
  `run`'s own `--out-dir`:** both are the RESULTS ROOT, not the specific per-run/per-
  comparison directory — `resolve_result_dir` (reused from `runner.py`, not
  reimplemented) appends the actual `<bench_run_id>`/`<compare_id>` directory name.

## Evidence
- Files added: `tests/bench/test_cli_bench.py`.
- Files touched: `src/agent_orchestrator/bench/cli.py` (extended), `pyproject.toml`
  (single `[project.scripts]` line), `Makefile` (four new targets appended, `.PHONY`
  line extended — no existing target's recipe edited).
- `uv run pytest tests/bench -q` → **168 passed, 1 skipped** (was 152 passed/1 skipped
  after `T-Rpt3Wq` on the same branch — net +16, zero regressions).
- `uv run pytest -q -m "not real_llm"` → **1025 passed, 4 deselected** (epic baseline
  before Sprint 2 was 990 passed/4 deselected; `T-Rpt3Wq` +19, this task +16 — net +35
  overall, zero regressions).
- `uv run pytest tests/test_cli.py tests/test_e2e_cli.py -q` → **81 passed** (the core
  `ao` CLI test suite, byte-unedited — SI-1 regression gate).
- `uv run ruff check .` → clean on every touched file; the only remaining repo-wide
  hits are the same 2 pre-existing, untouched `tests/test_e2e_cli.py` errors this
  epic's earlier tasks have already documented (unrelated file, out of scope).
- `uv run ruff format --check .` → 95 files already formatted (clean).
- `uv run mypy src/agent_orchestrator/bench` → **Success: no issues found in 11 source
  files**. `uv run mypy src` → clean except the same 4 pre-existing
  `src/agent_orchestrator/_version.py` errors (confirmed present before this task's
  changes too, unrelated/untouched file). `mypy tests/bench` STANDALONE (without `src`
  on the invocation) surfaces pre-existing `import-untyped`/"missing py.typed" noise
  across EVERY bench test file (not something this task introduced — confirmed by
  running `mypy .` at the repo root, which also shows pre-existing errors in untouched
  files like `tests/test_cli.py`/`tests/test_engine.py`/`tests/bench/test_subjects.py`/
  `tests/bench/test_runner.py`); this repo's own established gate (every prior task in
  this epic) is `mypy src/agent_orchestrator/bench` + `mypy src`, which stays clean —
  `mypy src/agent_orchestrator/bench/cli.py tests/bench/test_cli_bench.py` (given
  together, the normal way to type-check one module + its own tests) is also clean.
- SI-1 verification: `uv run python -c "import agent_orchestrator.cli"` then checking
  `sys.modules` for any `agent_orchestrator.bench*` key → **empty** (also asserted by
  `test_core_cli_import_does_not_pull_in_bench`, run in a fresh subprocess so no other
  test's import can mask a regression). `grep -rn "agent_orchestrator\.bench" src/
  agent_orchestrator --include="*.py"` outside `bench/` itself → **no matches**.
  `git diff pyproject.toml` → exactly the one `ao-bench = ...` line added.
- `uv run ao-bench --help` (after `uv sync --extra dev` to re-resolve the console
  script) → lists `validate/run/report/list` under "Commands", confirmed. `uv run ao
  --help` unaffected (still resolves, unchanged output).
- `make bench-validate` / `make bench-smoke` → both print a `SKIP ...` friendly message
  and exit 0 (`benchmarks/suites/dev-core/suite.json` doesn't exist yet, `T-Fx6Dp0` not
  landed). `make bench-run SUITE=... SUBJECT=...` / `make bench-report RESULTS=...
  SUITE=...` manually verified end-to-end against a scratch fake suite/subject (written
  run.json/summary.md/comparison.{json,md}, exit 0) — scratch artifacts removed from
  the committed `benchmarks/results/` tree afterward, never left behind.

## Acceptance criteria verification
1. **AC1** (`--help` lists every command; each documents its flags): `test_help_lists_
   all_commands`, `test_run_help_documents_flags`, `test_report_help_documents_flags`,
   `test_list_help_documents_flags` + `tests/bench/test_cli_validate.py` (unchanged,
   still covers `validate --help`).
2. **AC2** (`run` over a fake suite/subject → exit 0, writes run.json+summary.md; a
   failing subject → exit non-zero): `test_run_writes_run_json_and_summary_md_exit_0`,
   `test_run_subject_task_failure_exits_nonzero`.
3. **AC3** (`report` over two fake result dirs → comparison.{json,md}, exit 0):
   `test_report_two_run_dirs_writes_comparison_exit_0`,
   `test_report_results_root_auto_discovers_latest_per_subject`.
4. **AC4** (`make bench-validate` green once `T-Fx6Dp0` lands; guarded skip before
   that): manually verified above (`SKIP ...`, exit 0); will go green with zero code
   changes once `benchmarks/suites/dev-core/suite.json` exists.
5. **AC5** (SI-1: core `ao` test suite unedited/passing; `ao` import graph excludes
   `bench`; `pyproject` gains only the `ao-bench` line, no new hard runtime dep):
   verified above — `typer`/`jsonschema`/`pyyaml`/`pydantic` were already core
   dependencies pre-epic; no new dependency was added.
6. **AC6** (`mypy`/`ruff` clean on `bench/`): verified above.
- Extra coverage beyond the AC list: bad spec path → exit 1, `--task` filter matching
  nothing → exit 0 + nothing written, `--force` re-run, no-`--run-dir`-and-no-
  `--results-root` usage error → exit 1, different-suite refusal surfaces through the
  CLI as exit 1, `list` sorts rows by task id and rejects a bad suite path.

## Risks / Blockers
- None blocking handoff. Forward note for `T-Fx6Dp0`: once `benchmarks/suites/dev-core/
  suite.json` + `benchmarks/subjects/fake-pass.json` land, `bench-validate`/`bench-
  smoke` go green with no code change (the guard simply stops skipping); once real
  haiku subject configs land, `bench-smoke`'s single-fake-subject placeholder can be
  extended to loop over them per the design doc's original multi-subject sketch (left
  as an explicit forward note in the Makefile's own comment).

## Next actions
1. Handed off to `T-Fx6Dp0-mvp-dev-suite-fixtures` (commits the real dev-core suite +
   fake-pass subject that make `bench-validate`/`bench-smoke` exercise for real) and
   `T-Tst4Ln-bench-tests` (broader test-strategy coverage + its own SI-1 import-graph
   test, ADR-0008 §13).
