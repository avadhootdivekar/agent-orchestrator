# STATUS

- ID: `T-EJKD6f-docs-adr-reconcile`
- Updated At: 2026-07-15
- State: Done
- Owner: developer agent

## This update
- By: Claude · Role: developer · Date: 2026-07-15
- Comment: Reconciled all documentation to the as-built implementation of
  `E-IasNXu-parallel-execution` (T-JXiI9j/T-j8YLGd/T-VSfAUN/T-TNleFt, all Done). Every deviation
  listed in this ticket's assignment was individually verified against the merged
  `src/agent_orchestrator/engine.py`/`cli.py`/`project_config.py` (not taken from sibling STATUS.md
  summaries on faith) before being written up. Docs-only: no production code, test, or spec file
  was edited — see Evidence.

## Design doc reconciliation (`docs-md/parallel-execution-hld.md`)
- Status header flipped: "Design (pre-implementation)" → "Implemented (as-built reconciled
  2026-07-15)".
- §4 edge-case list corrected: it previously said "`0`/negative → explicit error", directly
  contradicting its own pseudocode a few lines above (which already showed the `or`-chain treating
  `0` as unset). Fixed to state plainly: `0` falls through to the default (no error, same treatment
  as `--quota-max-wait 0`/`--max-attempts 0`); only negative hits the `< 1` guard.
- §6 pseudocode corrected: the `RESHAPED` handling previously showed `run()` calling `build_dag()` a
  second time — the merged code does not do this; `_settle_completed_task` returns the
  already-rebuilt `(graph, order)` via `SettleResult`, and `run()` just takes them. Also added the
  `ctx: _RunContext` parameter (absent from the original pseudocode) and the real
  `in_flight_nonempty` kwarg to `_prepare_and_maybe_dispatch`.
- New §14 "As-built deviations from design" added, covering all 6 items from this ticket's
  assignment (verified against code, not summarized from STATUS.md): `--max-parallel 0` semantics;
  `_RunContext` dataclass threading; `DispatchSignal`/`SettleSignal` as `Literal`s wrapped in
  `DispatchPrep`/`SettleResult`; uniform `REQUEUE` → `"pending"` normalization at the `run()` call
  site; the `emit_tasks` (keeps `_recompute_order`) vs. loop-gate (`topological_order()` directly)
  RESHAPED-site asymmetry; `_estimate` re-derivation from `charged_estimate` in settle. A 7th item
  was added: a flagged (not fixed) in-code docstring staleness — see "Blocked/flagged finding" below.

## ADR-0007 finalize
- Status line: "Accepted" → "Accepted — Implemented" with a one-line evidence pointer (5/5 tasks,
  857/3, 93% coverage).
- New "Implementation notes (2026-07-15, T-EJKD6f)" section added: confirms D1/D3/D4/D6/D7 all held
  exactly as decided; records that D7's one open item at design time (the budget-wait BLOCKED path)
  is now fully closed by T-VSfAUN; records R4 (breaker nondeterminism at N>1) as accepted-by-design,
  not a defect.
- Cross-references verified: ADR-0003 §3 and ADR-0006 (both cited in D5) still hold — confirmed by
  reading the as-built `_resolve_run_settings`/`ProjectConfig`/CLI option wiring; no contradiction.

## HLD index pointer (`docs-md/hld-agent-orchestrator.md`)
- L7 feature-docs list already correctly linked `parallel-execution-hld.md` + ADR-0007 with accurate
  wording — verified, no change needed there.
- L73 was stale and DID need a fix: "MVP runs sequentially in topo order; the model is
  parallel-ready (independent nodes) for a later task" — the "later task" (this epic) has now
  landed. Updated to state the default (serial, `max_parallel=1`) and point to
  `parallel-execution-hld.md`/ADR-0007 for the opt-in parallel path.

## Stale-claim sweep (whole docs tree, learning #35)
Ran `git grep -niE "serial|sequential|one task at a time|no parallel|single-threaded" -- docs-md/
README.md AGENTS.md CLAUDE.md .claude/` — 19 hits. Full disposition below (unchanged from the final
report). Every hit that asserted the engine is strictly/permanently serial without qualification was
updated in place (either with an inline qualifier or an appended "Update/RESOLVED/SUPERSEDED" note
preserving the original historical text, matching how the rest of this repo treats point-in-time
design-doc assumptions). Re-ran the same grep after all edits to confirm every real hit now carries
a qualifier and no new unqualified claim was introduced by this ticket's own new prose.

**Files changed by the sweep** (beyond this epic's own HLD/ADR, already listed above):
- `docs-md/hld-agent-orchestrator.md` (L73, see above).
- `docs-md/multi-endpoint-circuit-breaker-hld.md` (L22 fact-table row; L98 evaluation-point note) —
  both now note that breaker evaluation stayed centralized on the main thread post-ADR-0007 (task
  *dispatch* went parallel, evaluation did not move "per worker" as that doc had speculated).
- `docs-md/lld-run-control-routing-breakers.md` (§6.1 evaluation-point note; `run_wall_clock_seconds`
  bullet; Assumption-log entry) — same theme, plus a note that the `run_wall_clock_seconds`
  task-boundary-granularity limitation is not fixed and not worsened in kind, but its exposure grows
  under `N>1` (more tasks can be in flight and overrunning before any settle triggers a check).
- `docs-md/logging-dynamic-workflows-hld.md` (the "STANDING ASSUMPTION — single-threaded engine"
  block) — the most directly falsified claim found; marked RESOLVED with an explanation of why the
  originally-flagged risk ("done/cursor state would need per-shard coordination") never
  materialized: dispatch went parallel while state mutation stayed fully serialized by design
  (ADR-0007 D3), and reshaping tasks additionally became serial barriers (D4) — a stronger guarantee
  than the original mitigation asked for.

**Hits reviewed and left unchanged** (false positives / genuinely unrelated, confirmed by reading
surrounding context, not by pattern alone):
- `.claude/agents/dev-security.md:22`, `lld-run-control-routing-breakers.md:41,~780` —
  "deserialization" substring match, unrelated to execution concurrency.
- `.claude/agents/manager.md` (×4), `.claude/skills/repo-intel/SKILL.md` (×2) — describe the
  Claude-Code *manager subagent's* own delegation style or the `ri` CLI/ADR-numbering convention,
  not the orchestration engine's task-dispatch model.
- `docs-md/guide-dynamic-task-injection.md:8` — "bounded, sequential re-execution" describes loop
  *iteration* semantics (iteration N+1 genuinely cannot start before iteration N's gate settles,
  loop-gate tasks are barriers under ADR-0007 too) — still accurate, not a claim about inter-task
  dispatch parallelism.
- `docs-md/logging-dynamic-workflows-hld.md:175` — "concurrent or sequential *runs*" refers to
  multiple separate `ao run` invocations not cross-contaminating log files, not tasks within one run.
- `docs-md/lld-run-control-routing-breakers.md:~1019` — "No parallel bookkeeping was added" means no
  duplicate/analogous counter structure, not a concurrency claim.
- `docs-md/token-budgeting-hld.md:263` — "serialised" = JSON serialization of `RunState`, unrelated.
- `docs-md/hld-agent-orchestrator.md:7` — already-correct pointer, not a stale claim.

## README + `ao init`
- Added `AO_MAX_PARALLEL` / `--max-parallel` row to the Environment variables table (positioned with
  the other runtime-execution-setting rows, before the quota rows — matches `ao init`'s own grouping).
- Added the commented `max_parallel: 1` line to the "Per-project config file" sample YAML block,
  verbatim-matching `ao init`'s real template line.
- Added a new "Parallel execution" H2 section (registered in the Contents TOC) between "Claude
  usage-quota exhaustion" and "Configuring multiple repos", matching that section's own
  How-it-works/Configuration structure: default `max_parallel=1`, precedence chain, `0` vs. negative
  behavior, link to ADR-0007.
- `ao init` `_INIT_TEMPLATE` (`project_config.py:267`): verified already correct
  (`# max_parallel: 1          # AO_MAX_PARALLEL — max independent ready tasks run at once (1 =
  serial)`), column-aligned with its neighbors — no change needed, per T-JXiI9j's own evidence.

## Learnings
- `meta/learnings.md`: 4 new long-form entries (`LRN-20260715-verbatim-extraction-for-byte-identical-
  regression-gate`, `-serialized-core-worker-dispatch-concurrency-pattern`,
  `-blocked-drain-not-sleep-while-sibling-holds-capacity`, `-int-flag-zero-falls-through-to-default-
  not-error`), each with Learning/Context/By/Role/Date, following the file's existing format exactly.
- `meta/learning-compact.md`: 4 matching crisp bullets appended. Deviation from this ticket's literal
  AC-5 wording, flagged rather than silently done differently: the file's 51 pre-existing entries are
  ALL single-line, attribution-free bullets (the long-form `By`/`Role`/`Date` structure lives only in
  `learnings.md`, which is where it's fully satisfied for all 4 entries here). To still honor the
  letter of "a new, clearly-separated entry with By/Role/Date" without breaking the compact file's
  established one-line convention, each new compact bullet carries a trailing
  `(By: agent, developer, 2026-07-15)` parenthetical rather than a multi-line block.

## Blocked/flagged finding — NOT fixed (out of this ticket's scope)
Two in-code docstrings are stale and were found while verifying deviation #2 against the real
`__init__` signature — flagged per this ticket's own instruction ("if you find a genuine code/doc
mismatch that needs a code fix, STOP and report it rather than editing code"):
1. `Orchestrator.__init__`'s `max_parallel` parameter docstring (`engine.py:204-211`) still reads
   "`run()` does **not** consume this value yet... every run is fully serial regardless of what is
   passed here."
2. `ProjectConfig.max_parallel`'s field docstring (`project_config.py:118-122`) still reads
   "Plumbed by T-JXiI9j; not yet consumed by the engine -- `Orchestrator.run()` stays fully serial
   until T-j8YLGd's wave/barrier scheduler lands."

Both were accurate at T-JXiI9j's handoff (plumbing-only) and are now false: T-j8YLGd's
`ThreadPoolExecutor(max_workers=self._max_parallel)` and the wave/barrier scheduler landed after
`__init__` was written, and — per T-j8YLGd's own STATUS.md extraction methodology — `__init__` was
deliberately verified byte-for-byte UNTOUCHED during that extraction, so the docstring was never
revisited. This is a genuine, verified (not assumed) code/doc mismatch. It needs a small, code-only,
behavior-free follow-up (two docstring edits) that is out of scope for this docs-reconciliation
ticket's own charter (no production-code edits). Documented in `parallel-execution-hld.md` §14 item
7 and reported here for the epic owner to action as a trivial fast-follow.

## Evidence
- Files changed (docs + tickets + learnings only):
  `docs-md/parallel-execution-hld.md`, `docs-md/adr/ADR-0007-parallel-task-execution.md`,
  `docs-md/hld-agent-orchestrator.md`, `docs-md/multi-endpoint-circuit-breaker-hld.md`,
  `docs-md/lld-run-control-routing-breakers.md`, `docs-md/logging-dynamic-workflows-hld.md`,
  `README.md`, `meta/learnings.md`, `meta/learning-compact.md`,
  `meta/tickets/E-IasNXu-parallel-execution/{EPIC.md,STATUS.md,T-EJKD6f-docs-adr-reconcile/{TASK.md,STATUS.md}}`.
- `uv run pytest -q` — **857 passed, 3 skipped** (identical to the T-TNleFt handoff baseline — zero
  regressions, as expected for a docs-only change).
- `git status --short -- src tests specs` — **non-empty**, but 100% pre-existing uncommitted work
  from `T-JXiI9j`/`T-j8YLGd`/`T-VSfAUN`/`T-TNleFt` (`engine.py`, `cli.py`, `models.py`,
  `project_config.py`, `test_cli.py`, `test_engine.py`, `test_project_config.py`, plus 3 new
  untracked test files) — this ticket's own session never called an edit/write tool on any file
  under `src/`, `tests/`, or `specs/` (confirmed by session tool-call history: only `Read` was used
  on those paths). `git diff --stat -- src tests specs` = 7 files changed, matching the sibling
  tickets' own reported file lists exactly.
- `uv run ruff check .` — 2 pre-existing errors, both in untouched `tests/test_e2e_cli.py`
  (`E501` line 209, `F841` line 270) — identical to every sibling ticket's baseline.
- `uv run ruff format --check .` — 72 files already formatted (clean).
- `uv run mypy src` — 4 pre-existing errors, all in untouched `src/agent_orchestrator/_version.py`
  lines 24-27 — identical to every sibling ticket's baseline.
- Grep sweep: see "Stale-claim sweep" above for the full hit list and disposition.

## Risks / Blockers
- None. This was the epic's final, docs-only task.
- Forward item (not a blocker for this ticket): the two flagged stale docstrings above should get a
  trivial follow-up code-only PR.

## Next actions
1. None for this ticket — Done.
2. Epic `E-IasNXu-parallel-execution` rolled up to Done in `EPIC.md`/epic `STATUS.md` (this update).
3. Recommend a tiny fast-follow PR for the two flagged docstrings (engine.py:204-211,
   project_config.py:118-122) — code-only, no behavior change, out of this ticket's scope.
