# STATUS

- ID: `T-ABDjSj-tool-state-ledger-budget`
- Updated At: 2026-09-26
- State: Done
- Owner: developer

## This update
- Implemented module M1 in a brand-new
  `src/agent_orchestrator/templates/builtin/overseer-runner/tools/overseer_tool.py` (stdlib only,
  Python ≥3.11, zero repo-internal imports, no `{{ ident }}` sequences): bounded JSON/JSONL IO +
  atomic writes, hook-context reader, `load_config` (CFG-0/1/2/3), `load_state` (ST-1/2, pinned to
  `RunState`/`TaskRunState`), hash-chained ledger (`append_chained`/`verify_ledger_chain`/idempotent
  `ingest_ledger`), a minimal-scope path-confinement helper (`classify_path_entry` + a lightweight
  `update_path_history`), the budget stage machine (`derive_budget` pure function + `compute_budget`
  I/O orchestrator, HLD §8.2), cadence math, the hold gate (HOLD/INT-2/INT-4), charter-lock verify
  (INT-1), the `unit-gate` CLI subcommand (BUDGET/FANOUT, $0/zero-files), `request-closeout` (FR-19),
  `intake-prep`, `ckpt-prep`, and the M1/M2-stub/M3-reservation section layout the three follow-on
  tasks (T-C6uQJW, T-HPJcc6, T-tAKBBB) build on additively.
- Tests: `tests/test_overseer_tool_budget.py` (65), `tests/test_overseer_tool_ledger.py` (33),
  `tests/test_overseer_tool_gates.py` (26) — 124 total, all passing. Coverage on the tool's own
  source path: **99%** (9/752 lines missed — real-clock fallback, the `__main__` guard, and a few
  defensive "already absent" branches; well over the ≥90% bar).
- Full repo suite: **4107 passed, 8 skipped, 0 failed** (no regressions — no existing file was
  touched). `ruff check`/`ruff format --check` clean on all 4 touched files. `mypy` clean (must be
  invoked together with `src/` for `agent_orchestrator.*` imports in the test files to resolve as
  first-party — a pre-existing repo characteristic, confirmed identical on `tests/test_dag.py`/
  `tests/test_artifacts.py` run in isolation; `mypy src tests/test_overseer_tool_*.py` is clean of
  any new error).
- One real latent bug found and fixed while writing tests: `compute_budget`'s call to
  `effective_budget` didn't thread `dry_run` through, so a `ckpt-prep --dry-run` with a
  newly-honorable budget override would have written a real ledger line despite `--dry-run`. Fixed
  by adding `dry_run` params to `effective_budget`/`compute_budget`, threaded from `ckpt_prep`.
- Judgment calls (design underspecified/ambiguous on these points; all commented in the source):
  1. **`OV-` rule-id prefix**: default `Violation` formatting is `OV-<id>: <detail>` (HLD §13.3's
     namespacing note), except `HOLD`/`BUDGET`/`FANOUT`, whose exact literal leading text is
     mandated verbatim by the task brief (a later e2e test greps for it) and passed as `message=`.
  2. **`prep-result.json` location**: only `intake-prep` (`outputs/`) and `ckpt-prep`
     (`outputs/checkpoints/ck-KK/`) write it; `unit-gate` writes zero files even on failure (AC5's
     explicit "zero files" requirement plus its $0/fast design intent), `request-closeout`'s stderr
     is already the complete answer.
  3. **`request-closeout`'s `run_id` resolution**: it's an operator CLI, never a hook, so there is
     no `$AO_HOOK_CONTEXT_PATH`. Resolves by reading the instance's own `workflow.json.id` and
     picking the most-recently-started run under `<runs_root>` whose `state.json.workflow_id`
     matches (neither the HLD nor the ticket specify this).
  4. **Path history (HLD §8.3's git-derived half)**: per the ticket's own AC6 narrowing ("it's
     enough that your path-confinement helper... classifies/rejects... not required to wire them
     into a signal yet"), `update_path_history` hashes breadcrumb `changed_paths` only (via
     `classify_path_entry`) and records `repo_heads: {}` with a `TODO(T-C6uQJW)` — no `git diff`/
     `git status` subprocess. This is a smaller scope than the ticket's Description bullet list
     literally suggests, but matches the ACs and keeps the git-subprocess risk fully with T-C6uQJW.
  5. **`BC-2`**: a new rule id (not in the HLD's normative list) for "missing breadcrumb on a
     settled, non-`failed` unit" — the M1 pseudocode's unconditional synthesis is narrowed by this
     task's own build instructions to `failed`-only; anything else fails closed (NFR-4) rather than
     fabricating data.

By: developer · Role: developer · Date: 2026-09-26 · Comment: All 11 acceptance criteria verified
with real command output (see Evidence). Scope boundary respected — created exactly
`tools/overseer_tool.py` and the 3 named test files; no `template.yaml`/`*.tmpl`/`instructions/*`,
no `intake-check`/`ckpt-check`/M2 detectors, no edits to `engine.py`/`breakers.py`/`runstate.py` or
any other existing file. Ready for the `reviewer` pass this ticket's own AC11 calls for.

By: dev-epic · Role: manager · Date: 2026-09-26 · Comment: Independently re-verified before
requesting review — read ~400 of 1694 lines myself (constants/exceptions/pure budget-math
functions, and the full budget/hold/charter-lock/unit-gate/request-closeout/dispatch section),
re-ran all 124 tests, the full suite (4107 passed/8 skipped/0 failed, matching exactly), ruff, and
mypy on the tool file. All matched the developer's report.

By: reviewer · Role: reviewer · Date: 2026-09-26 · Comment: **Verdict: approve with nits, no
blocking issues.** Closely read the ~200-1040 line range dev-epic had not yet verified
(`load_config`, `load_state` cross-checked field-by-field against real `models.RunState`/
`TaskRunState`, `read_hook_context` cross-checked against `engine.py`'s actual hook-context
fields, the bounded-IO/atomic-write helpers, and the hash chain — traced `append_chained`/
`verify_ledger_chain` by hand over a 3-line example and confirmed a genuine forward-linked
tamper-evidence property, not a simulated one). Independently proved render-safety by running the
file's text through the real `templates/__init__.py::_render` with an empty variables dict and
confirming byte-identical output (stronger than a grep). Reproduced the 99%/9-missed-line coverage
claim exactly and inspected every missed line (all genuinely low-value/defensive). Found the
budget-override dedup key's one inherent ambiguity (two identical `(amount, reason)` overrides are
indistinguishable from "already honored") but traced it to the HLD's own frozen schema having no
nonce field — not a code defect, flagged for whoever owns the schema, not actioned here. **One
substantive, non-blocking Warning**: three call sites (`hold_gate`, `verify_charter_lock`,
`resolve_run_id_for_instance`) read a JSON file without catching `OversizeInputError`/
`JSONDecodeError` the way `load_config` does, so a malformed file bypasses the rule-id path
entirely and surfaces as a bare internal-error exit 1 instead of `INT-2`/`INT-1`/`ST-1` — exactly
on the tamper/corruption paths where a diagnosable failure matters most (NFR-9). dev-epic fixed all
three (wrapped consistently with `load_config`'s own pattern, `from exc` chaining preserved) and
added one regression test per site
(`test_hold_gate_malformed_request_json_is_int2_not_an_uncaught_exception`,
`test_verify_charter_lock_malformed_json_is_int1_not_an_uncaught_exception`,
`test_resolve_run_id_for_instance_malformed_workflow_json_is_st1`). Re-verified after the fix: 127
passed (124+3), coverage still 99% (9/761 lines missed, same categories), full suite 4110
passed/8 skipped/0 failed, ruff/mypy clean.

## Evidence
- `.venv/bin/pytest -q tests/test_overseer_tool_budget.py tests/test_overseer_tool_ledger.py tests/test_overseer_tool_gates.py`
  → `127 passed` (post-fix; 124 original + 3 regression tests for the reviewer's Warning).
- `.venv/bin/pytest -q tests/test_overseer_tool_budget.py tests/test_overseer_tool_ledger.py tests/test_overseer_tool_gates.py --cov=src/agent_orchestrator/templates/builtin/overseer-runner/tools --cov-report=term-missing`
  → `761 stmts, 9 miss, 99%` (missing: 493, 538, 795, 1059, 1071, 1079, 1351, 1598, 1714 — same
  defensive/real-clock categories as before, line numbers shifted by the fix).
- `.venv/bin/pytest -q` (full repo, no path arg) → `4110 passed, 8 skipped, 1 warning` (post-fix;
  zero regressions).
- `.venv/bin/ruff check` + `.venv/bin/ruff format --check` on all 4 touched files → clean (post-fix).
- `.venv/bin/mypy src/agent_orchestrator/templates/builtin/overseer-runner/tools/overseer_tool.py` → clean (post-fix). Also confirmed together with the test files + `models.py` per the developer's original invocation.
- `reviewer`'s independent re-derivations: hash chain traced by hand (3-line example), render-safety
  proven via the real `_render` function (not just a grep), `state.json` field contract checked
  against `models.py` line-by-line, coverage numbers reproduced exactly.
- Code: `src/agent_orchestrator/templates/builtin/overseer-runner/tools/overseer_tool.py`.
- Tests: `tests/test_overseer_tool_budget.py`, `tests/test_overseer_tool_ledger.py`, `tests/test_overseer_tool_gates.py`.

## Risks / Blockers
- None outstanding for this task. The reviewer's one non-blocking Warning (inconsistent JSON-read
  error wrapping in 3 call sites) is fixed and regression-tested (see above).
- The reviewer's other observation (the budget-override dedup key can't distinguish a deliberate
  re-application of an identical `(amount, reason)` override from "already honored") is a schema
  limitation inherited from HLD §13.4's `budget-override.json` shape (no nonce/id field), not a
  code defect — noted here for whoever revisits that schema later, not actioned in this task.
- Forward risk (informational, owned by later tasks): the git-derived half of "tracked paths"
  (HLD §8.3) and the M2 detectors are still to come in T-C6uQJW; M1's `path-history.json`/ledger
  shapes are frozen as of this task per the epic's Next actions, so T-C6uQJW should build
  additively against them, not redesign them.

## Next actions
1. None outstanding — done, reviewed (approve with nits, addressed), evidence recorded.
2. Unblocks `T-eGXqXH` (hook argv wiring) and `T-C6uQJW` (M2 detectors, reads the ledger/path-history
   formats frozen here) to proceed next per the epic's dependency graph.
