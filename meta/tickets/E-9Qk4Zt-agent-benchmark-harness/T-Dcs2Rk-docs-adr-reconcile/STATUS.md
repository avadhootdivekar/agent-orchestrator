# STATUS

- ID: `T-Dcs2Rk-docs-adr-reconcile`
- Updated At: 2026-07-22
- State: Done
- Owner: developer agent

## This update
- By: Claude · Role: developer · Date: 2026-07-22 · Comment: Post-implementation docs-refresh complete. Reconciled
  `docs-md/benchmarking-framework-hld.md` and `docs-md/adr/ADR-0008-benchmark-harness-approach.md` to the as-built
  code (verified against `src/agent_orchestrator/bench/*.py`, `benchmarks/**`, `.github/workflows/ci.yml`,
  `Makefile`, and the committed `benchmarks/results/2026-07-22-dev-core-*` artifacts — not copied from task
  STATUS.md narratives without independent checking). Added `benchmarks/README.md`. Added the feature pointer to
  `docs-md/hld-agent-orchestrator.md`. Appended learnings to `meta/learnings.md` + `meta/learning-compact.md`.

## Scope note — deviation from `TASK.md`'s original deliverable list
The launching orchestrator's explicit task message (this session) scoped deliverables more narrowly than
`TASK.md`'s Description/AC list in two places; followed the launching message as the more current/authoritative
source (the same "explicit assigning message wins" pattern every other task in this epic recorded):
1. **Root `README.md`'s "Benchmarking" section was NOT added.** `TASK.md`/AC3 calls for it; the launching message's
   "Deliverables (files you own)" list does not mention `README.md` at all. Not touched — flagged here for the
   orchestrator to route to another task or a follow-up if still wanted. (`hld-agent-orchestrator.md`'s feature-docs
   pointer, the other half of AC3, WAS added.)
2. **Epic `EPIC.md`/`STATUS.md` were NOT updated to Done.** `TASK.md`/AC5 calls for this; the launching message
   explicitly says "Do NOT touch EPIC.md / epic STATUS.md (orchestrator closes the epic)." Not touched, per that
   explicit instruction — the epic's own STATUS.md already correctly shows "8/9 tasks delivered; remaining:
   T-Dcs2Rk" and will presumably be closed by the orchestrating agent now that this task is Done.

Both are stated as deviations, not silently dropped.

## Deviations reconciled into the docs (verified against code, not assumed)
1. **Runner owns `run.json`; `results.py` owns `summary.md`/comparison.** Verified: `runner.py` defines
   `BenchRunRecord`/`BenchTaskRecord`/`BenchRunSubjectInfo`/`BenchRunEnv` and `_persist_record` (write-temp + atomic
   rename); `results.py` has no `run.json`-writing code, only `load_run`/`write_summary_md`/`build_comparison`/
   `write_comparison`, all reading `BenchRunRecord.model_validate`.
2. **Pydantic models live in `bench/spec.py`; `Assertion.golden` rewritten to absolute at load time.** Verified in
   `spec.py`: no `models.py` file exists; `_check_assertions` does `assertion.golden = str(golden_path.resolve())`.
3. **`AoWorkflowSubject` does not mirror a generic `instructions/` dir.** Verified in `workspace.py`
   (`materialize_workspace` writes `repo/INSTRUCTION.md`, no `instructions/` copy) and
   `benchmarks/subjects/ao-epic/workflow.json` (both tasks' `instruction: "repo/INSTRUCTION.md"`); the committed
   `instructions/{implement,verify}.md` files are role docs mirrored into `agents.json`'s `prompt_template` strings
   (confirmed by reading both — content matches).
4. **CLI shapes.** Verified in `cli.py`: `report --run-dir <d>... | --results-root <root> --suite <id>`;
   `list --suite <path>`; exit codes 0/1/2 with `EXIT_RUN_HAD_FAILURES` (2) driven by `subject_status`, never
   `solved` (confirmed by reading the `run` command body and `_HARNESS_FAILURE_STATUSES`).
5. **`ResultsError` in `errors.py`; `RunContext.subject_base_dir`; `SubjectResult` argv/resolved_model/
   resolved_permission_mode.** Verified by reading `errors.py`, `workspace.py`'s `RunContext` model.
6. **Comparison-dir naming limitation.** Verified: `compute_compare_id` = `f"{date}-{suite_id}-compare"` (no
   subject-set component). Confirmed the limitation manifested for real: `benchmarks/results/2026-07-22-dev-core-
   compare/comparison.json`'s `subjects` list is `[claude-haiku, ao-epic-haiku, claude-sonnet, claude-opus,
   ao-epic-sonnet]` — a superset of `make bench-smoke`'s original 3-subject run, proving same-day regeneration
   overwrote the dir as more real subjects landed. Documented in HLD §9/§11, ADR-0008 Implementation notes, and
   `benchmarks/README.md`.
7. **Coverage 98%.** Independently re-ran (not copied from T-Tst4Ln's STATUS.md):
   `uv run pytest tests/bench -q -m "not real_llm" --cov=agent_orchestrator.bench --cov-report=term-missing` →
   `221 passed, 1 deselected`, `TOTAL 1120 18 98%` — exact match to the claimed figures.
8. **Makefile `bench-smoke` = fake-pass + claude-haiku + ao-epic-haiku + report.** Verified by reading `Makefile`
   lines 63–83 directly.
9. **ADR-0008 status Proposed → Accepted.** Done, with an "Implementation notes" section citing the as-built
   deviations and the real cost numbers, not just a status-line flip.

## Real dev-core numbers used in the docs — verified against committed artifacts, not narrated
Parsed `aggregate` blocks directly out of each committed `run.json` (not copied from any STATUS.md prose):
`fake-pass` 6/6 $0.0000; `claude-haiku` 6/6 $0.3732/$0.0622 per solved/166.37s; `ao-epic-haiku` 6/6 $0.6617/
$0.1103/343.05s; `claude-sonnet` 6/6 $1.2148/$0.2025/122.28s; `claude-opus` 6/6 $1.5483/$0.2580/129.97s;
`ao-epic-sonnet` 6/6 $2.8744/$0.4791/387.31s. These five real subjects go beyond what `T-Fx6Dp0-mvp-dev-suite-
fixtures/STATUS.md` reported (it only ran haiku); the sonnet/opus/ao-epic-sonnet runs and the fuller committed
`comparison.md` are evidence of Phase-2 work already in flight outside this epic (per `EPIC.md`'s own note), used
here only as a verified data source, not re-attributed to this epic's tasks.

## Grep sweeps used (whole-doc, not single-site — per the repo's own docs-refresh learning)
- HLD: `grep -n "not yet\|Design (not\|--results-dir\|AO_BUDGET_TOTAL\|--suites | --subjects\|models\.py\|RunResult(\|assemble_run_json\|write_comparison(result_dirs\|domain:str(inherited)"`
- HLD (broader): `grep -n "Design (not yet implemented)\|OPEN_QUESTION\|Non-goals\|instructions/\|AO_BUDGET_TOTAL\|--results-dir\|--suites | --subjects | --results\|models.py\|RunResult\|write_summary_md(result_dir\|assemble_run_json\|write_comparison(result_dirs"`
- HLD + ADR (residual sweep post-edit): `grep -n "not yet\|TBD\|future work\|to be determined\|empty registr\|registries are empty"`
- ADR: `grep -n "Proposed\|awaiting\|not yet\|TBD"`
- Survey: `grep -n "not yet\|Design (not\|Status:\|Proposed\|awaiting"` — no hits requiring a change (checked, not
  edited; the survey's claims are about external benchmarks, not our own implementation state).
- Final residual sweep after all edits: `grep -n "results-dir\b"` / `grep -n "RunResult"` / `grep -n "bench-unit\|
  bench-e2e"` on the HLD — all remaining hits confirmed to sit inside explicitly-labeled "illustrative/superseded"
  blocks, not asserted as current fact.

## Stale claims found + fixed (count per doc)
- `docs-md/benchmarking-framework-hld.md`: **16 corrected** (status line; §4.2 `AO_BUDGET_TOTAL` + INSTRUCTION.md
  writer attribution; §4.5 `run_suite` signature/return type + inline `write_summary_md` call; §4.6
  `assemble_run_json`/`write_comparison` shape; §6 `BenchTask.domain`; §7 `report`/`list` CLI shapes + Make
  recipes; §9 module list (missing `workspace.py`) + subject file list (`ao-epic.json` doesn't exist); §10 CI job
  shape; §18 Q1/Q2/Q3 left open) **+ 8 additive as-built clarifications/gap-fills** (module-split note, `spec.py`
  models/`Assertion.golden` note, `RunContext.subject_base_dir`/`SubjectResult` additive fields, `TaskMetric`→
  `BenchTaskRecord` note, `ResultsError` in the errors list, comparison-naming limitation box, §11.1 real numbers,
  §14 verification evidence).
- `docs-md/adr/ADR-0008-benchmark-harness-approach.md`: **1 corrected** (status line Proposed→Accepted)
  **+ 1 addition** ("Implementation notes" section). No other claim in the ADR was contradicted by the shipped
  code — D1–D6 and the Alternatives/Consequences sections all matched as-built; verified by re-reading the whole
  doc, not assumed.
- `docs-md/benchmark-landscape-survey.md`: **0 changes.** Checked (see grep sweep above); its claims are about
  external benchmarks (SWE-bench, inspect-ai, etc.), not this repo's implementation state, so nothing in it went
  stale from shipping the epic.

## Evidence
- Read (verification, not assumption): `TASK.md`; `docs-md/benchmarking-framework-hld.md`;
  `docs-md/adr/ADR-0008-benchmark-harness-approach.md`; `docs-md/benchmark-landscape-survey.md`; epic
  `EPIC.md`/`STATUS.md`; all 8 other task `STATUS.md` files; `meta/learnings.md`/`meta/learning-compact.md`.
- Code read directly (not narrated): `src/agent_orchestrator/bench/{spec,errors,runner,results,cli,subjects,
  workspace}.py`; `benchmarks/schemas/*.json`; `benchmarks/subjects/{fake-pass,claude-haiku,claude-sonnet,
  claude-opus,ao-epic-haiku,ao-epic-sonnet}.json` + `benchmarks/subjects/ao-epic/{workflow,agents}.json` +
  `instructions/implement.md`; `benchmarks/suites/dev-core/suite.json` + one task fixture tree; `.github/
  workflows/ci.yml`; `Makefile`.
- Commands run (this task, independently):
  - `uv run pytest tests/bench -q -m "not real_llm" --cov=agent_orchestrator.bench --cov-report=term-missing` →
    **221 passed, 1 deselected**, `TOTAL 1120 18 98%` (matches T-Tst4Ln's claimed figures exactly).
  - `uv run pytest -q -m "not real_llm"` (whole repo) → **1078 passed, 4 deselected**, zero regressions.
  - `python3 -c "..."` parsing each committed `benchmarks/results/2026-07-22-dev-core-*/run.json`'s `aggregate`
    block directly (see "Real dev-core numbers" above) and `benchmarks/results/2026-07-22-dev-core-compare/
    comparison.json`'s `subjects` list (5-subject superset, confirming the naming-limitation finding).
- Markdown-only change — no `ruff`/`mypy` gate applies (no `.py` files touched); confirmed via `git status`-style
  review that only `.md` files under `docs-md/`, `benchmarks/README.md`, `meta/learnings*.md`, and this ticket's
  own docs were written.

## Acceptance criteria verification (against `TASK.md`, adjusted for the launching-message scope note above)
1. Design doc + ADR-0008 status/claims match shipped code; every deviation noted; Q1/Q2/Q3 resolved — **done**, see
   HLD §18 "Resolved" block + ADR-0008 "Implementation notes".
2. `benchmarks/README.md` lets a new developer author a task and run a bench without reading the source — **done**
   (authoring walkthrough, full CLI/Make reference, results layout, model-id policy, cost table).
3. `hld-agent-orchestrator.md` feature list references the benchmarking framework — **done**. Root `README.md` —
   **not done**, see scope note above.
4. `meta/learnings.md` + `meta/learning-compact.md` updated with separable, attributed entries — **done**, 5 new
   long-form entries + 5 matching compact one-liners (see below), deduplicated against the existing ~62 entries
   (checked for overlap with the existing model-override-clobber and parallel-execution entries — genuinely
   distinct, not duplicates).
5. Epic `EPIC.md`/`STATUS.md` marked Done — **not done**, see scope note above (explicit instruction not to touch
   it this session). All 9 task `STATUS.md` files independently confirmed to already reflect Done/Complete state
   (read all 9 as part of this task).
6. Docs verified against implemented code, citing specific modules/tests checked — **done**, see Evidence above.

## Learnings added
- `meta/learnings.md` (long-form, By/Role/Date, deduplicated against the existing ~62 entries):
  1. `LRN-20260722-date-suite-keyed-artifact-name-overwrites-on-regen`
  2. `LRN-20260722-uniform-model-workaround-for-global-model-clobber-defect`
  3. `LRN-20260722-directory-scan-attribution-needs-exactly-one-match-enforced`
  4. `LRN-20260722-parallel-dev-agents-strict-ownership-plus-arbitrated-shared-files`
  5. `LRN-20260722-trivial-benchmark-suite-shows-orchestration-cost-not-solve-rate-gain`
- `meta/learning-compact.md`: 5 matching one-liners appended, same order.

## Risks / Blockers
- None blocking. Two explicit scope gaps carried forward for the orchestrator (root `README.md` Benchmarking
  section; epic `EPIC.md`/`STATUS.md` → Done) — see "Scope note" above, not silently dropped.
- Nothing found in code/results that contradicted the docs and could NOT be fixed within this task's scope — every
  deviation the task instructions flagged was verified and reconciled.
- **Process finding (not a docs defect): a concurrent, uncommitted code edit landed on this branch mid-task,
  contradicting the "reviewer agent is concurrently READING the branch; it writes nothing" assumption in this
  task's own launch instructions.** Partway through this session, `src/agent_orchestrator/bench/{graders,results,
  runner,spec}.py` and `tests/bench/{test_cli_validate,test_results,test_runner,test_spec}.py` acquired real,
  growing, uncommitted diffs (visible via `git status`/`git diff`, `git stash list` empty — not caused by this
  task, which touched only markdown + one new `benchmarks/README.md`) — apparent reviewer-finding fixes (comments
  reference "W1"/"W2"/"W4"/"W5": a missing-schema-file guard, checkout-independent fingerprint hashing for
  `equals_file` goldens, a stale-`config_fingerprint`-on-resume guard, and a winner-tie disclosure). At the moment
  of this task's final verification pass, that in-progress edit had **one failing test**
  (`test_render_winners_discloses_ties`) in the working tree — evidence it was still mid-edit, not a completed,
  reviewable change. Per this task's docs-only/no-code-changes scope, these files were left untouched and their
  edits were NOT incorporated into the HLD/ADR/README (they are not among the 9 known deviations this task was
  scoped to reconcile, and re-documenting an unstable, currently-broken, uncommitted target mid-edit would itself
  be a stale-docs risk). **This task's "Verified as-built" numbers (98% coverage, 1120 stmts/18 missed, 221
  passed; whole-repo 1078 passed/4 deselected) were captured EARLY in this session and independently confirmed to
  match the last committed HEAD (`1c60a07`) exactly** (`git show HEAD:.../runner.py` has zero `W2`/`W4`
  occurrences) — i.e. they describe the shipped, committed epic, not the concurrent in-flight edit. Recommend the
  orchestrator: (a) let that concurrent fix pass finish and land its own commit, then (b) route a short follow-up
  docs delta (not a full T-Dcs2Rk re-run) once it's committed and green.

## Next actions
1. None for this task. Orchestrator: close the epic (`EPIC.md`/`STATUS.md` → Done) now that all 9 tasks are Done;
   optionally route the root `README.md` "Benchmarking" section to a follow-up if still wanted.
