# STATUS

- ID: `T-C6uQJW-tool-loop-progress-detectors`
- Updated At: 2026-09-26
- State: Done
- Owner: developer

## This update
- Implemented module M2 in `overseer_tool.py`, replacing M1's `detect_signals_stub`/
  `compute_progress_stub`: pure `detect_period`/`detect_mirror`/`returns_to_earlier` (HLD §8.4
  pseudocode verbatim), all 10 detectors (`period_repeat`, `mirror_flipflop`,
  `content_oscillation`, `breadcrumb_integrity`, `stall`, `repeated_failure`, `attempt_cap`,
  `ask_starvation`, `blocked_units`, `prompt_changed` — `wave_signature_repeat` correctly NOT
  implemented, deferred per NFR-X10) wired into `detect_signals`, deterministic `S-KK-NN` id
  assignment (AC7), and `compute_progress` (work-item totals/done/newly-done/stall/
  criteria_met_prev/per-ask).
- Extended `update_path_history` (M1's minimal-scope stub) with its two deferred pieces:
  `repo_heads` via bounded `git rev-parse HEAD`, and git-derived tracked paths via bounded
  `git diff --name-only <prev_head>` + `git status --porcelain` per repo (so a unit can't hide a
  path it omitted from its own breadcrumb — AC3), plus a "most recently changed" trim replacing
  M1's stable-input-order-only trim.
- New `tests/test_overseer_tool_detectors.py`, 51 tests (52 after dev-epic's one addition below),
  covering AC1-9.
- All 127 pre-existing M1 tests continue to pass unmodified — M1's test files were not touched.

By: developer · Role: developer · Date: 2026-09-26 · Comment: All 9 acceptance criteria verified
with real command output. `pytest tests/test_overseer_tool_detectors.py -v` → 51 passed. Combined
with M1: 178 passed. Coverage (combined M1+M2): 1073 stmts/10 miss/99%; the M2 lines specifically
are 342/343 = 99.7%. `ruff`/`mypy` clean. Judgment calls (all commented in-code): (1) `stall`
carries `criteria_met` forward across waves lacking a verdict yet, treating a wave as "productive"
if it has a done+pass/na unit OR its carried-forward `criteria_met` exceeds the historical max;
(2) path-history "recency" ranks older paths by highest last-seen checkpoint, ties alphabetical;
(3) every git subprocess call degrades to "no data" on any error/timeout rather than raising
(diagnostic, not an integrity gate); (4) "latest verdict" for `ask_starvation` = ck-(K-1)
specifically (the only one guaranteed to exist before K's own digest generates).

By: dev-epic · Role: manager · Date: 2026-09-26 · Comment: Independently re-verified before
requesting review: hand-traced `detect_period`/`detect_mirror` against every AC1/AC2 table row
myself by manual execution (including the two "smallest period/mirror wins" trick cases), read
`detect_signals`/`compute_progress` structurally, confirmed the git-subprocess bounding (argv
lists, `timeout=GIT_TIMEOUT_S`, no `shell=True`/`eval`/`exec`/`pickle`/`os.system` anywhere in the
file). **Found and fixed a real cross-task issue** (not a review finding): the new git-subprocess
calls tripped `tests/isolation/test_security_guards.py::TestNoSubprocessGitCall`, a repo-wide
guard requiring git shell-outs to route through `isolation/git.py`'s `GitRepo` or carry a
justified allowlist entry. Added a justified entry (the tool is deliberately standalone/
stdlib-only per NFR-5/ADR-0016 D4 and structurally cannot import `GitRepo`); re-ran that guard's
own suite (11 passed).

By: reviewer · Role: reviewer · Date: 2026-09-26 · Comment: **Verdict: approve with nits, no
blocking issues.** Independently hand-traced every detector against HLD §8.3's rule table (not
just read), and additionally exercised several by direct execution rather than trusting the
shipped tests: wrote an adversarial script proving AC7's determinism holds under 20 random
shuffles plus a full-reverse of tied candidates; hand-traced AC3's git-diff-union mechanism
step-by-step against a real fixture to confirm an omitted-from-breadcrumb path really does get
caught; hand-built the `_consecutive_stall_waves` carry-forward table and could not construct a
false-negative or false-positive; reproduced the 99% coverage claim exactly; directly executed the
`git status --porcelain` parsing logic against real git output.
**Two Warnings, one fixed by dev-epic, one tracked as a handoff note:**
1. **W1 (fixed)**: the path-history trim's union order placed breadcrumb-declared paths before
   git-derived ones, so a busy wave whose declared paths alone reached `MAX_TRACKED_PATHS` would
   silently drop exactly the undeclared/evasive paths this feature exists to catch — inverting the
   intended priority. **Fixed**: the union now orders git-derived paths first
   (`git_derived + accepted` instead of `accepted + git_derived`), so the trim sacrifices a
   declared path before it ever sacrifices an undeclared one. Added a regression test,
   `test_update_path_history_trim_prioritizes_git_derived_over_declared_when_over_cap`, using a
   real git fixture with an undeclared on-disk change plus a breadcrumb that alone fills the cap —
   confirms the undeclared path survives and a declared one is dropped instead. Re-verified: 179
   passed (178+1), coverage still 99%, full suite 4190 passed/8 skipped/0 failed, ruff/mypy clean.
2. **W2 (tracked, not fixed — correctly out of this task's scope)**: `content_oscillation`/
   `period_repeat`/`mirror_flipflop`/`repeated_failure` signals re-fire at every subsequent
   checkpoint once triggered (no expiry/acknowledgement-suppression), which could cause the
   overseer to re-litigate an already-resolved event indefinitely and waste budget on repeated
   `signal_responses`. This doesn't violate any stated AC and is plausibly the semantic-checker's
   (`T-tAKBBB`) concern, since it owns `signal_responses`/`OV-R12` enforcement. **Handoff**:
   `T-tAKBBB`'s brief should explicitly consider whether an `accept`ed high-severity signal at
   checkpoint K should suppress re-emission of the SAME signal (same type/work_item/path) at K+1
   unless the underlying condition changes, and if not addressed there, this is recorded here as a
   known operational-cost gap for `T-gbccdr`'s docs/deviations section.
- Also confirmed W3 (duplicated `unit_lines` filter logic between `detect_signals`/
  `compute_progress`, and a double `_consecutive_stall_waves` computation per checkpoint) — dev-epic
  extracted a shared `_unit_lines_through(inst, k)` helper (removing the literal duplication) but
  left the double stall-computation call as-is (a minor performance nit the reviewer explicitly
  called optional, not a correctness issue — both calls are pure re-derivations of the same on-disk
  state within one synchronous invocation, so results are always consistent).

## Evidence
- `.venv/bin/pytest -q tests/test_overseer_tool_detectors.py` → `52 passed` (post-fix; 51 original
  + 1 W1 regression test).
- `.venv/bin/pytest -q tests/test_overseer_tool_budget.py tests/test_overseer_tool_ledger.py tests/test_overseer_tool_gates.py tests/test_overseer_tool_detectors.py`
  → `179 passed` (127 M1 unchanged + 52 M2).
- `.venv/bin/pytest -q tests/test_overseer_tool_budget.py tests/test_overseer_tool_ledger.py tests/test_overseer_tool_gates.py tests/test_overseer_tool_detectors.py --cov=src/agent_orchestrator/templates/builtin/overseer-runner/tools --cov-report=term-missing`
  → `1075 stmts, 10 miss, 99%`.
- Full repo suite (with T-eGXqXH also landed): `.venv/bin/pytest -q` →
  `4190 passed, 8 skipped, 0 failed`.
- `.venv/bin/ruff check` + `.venv/bin/ruff format --check` on the tool file + test file → clean.
- `.venv/bin/mypy src/agent_orchestrator/templates/builtin/overseer-runner/tools/overseer_tool.py` → clean.
- `.venv/bin/pytest tests/isolation/test_security_guards.py -q` → `11 passed` (post allowlist fix).
- Code: `src/agent_orchestrator/templates/builtin/overseer-runner/tools/overseer_tool.py` (M2
  additions), `tests/isolation/test_security_guards.py` (1-line allowlist entry).
- Tests: `tests/test_overseer_tool_detectors.py`.

## Risks / Blockers
- None outstanding for this task. W1 is fixed and regression-tested. W2 is explicitly handed off
  to `T-tAKBBB` (see above) rather than silently absorbed.
- Forward note for `T-HPJcc6`/`T-tAKBBB`: `path-history.json`'s schema (including the new `info`
  field on trim-drop) and the ledger/digest shapes are now fully frozen as of this task — build
  additively against them.

## Next actions
1. None outstanding — done, reviewed (approve with nits; W1 fixed, W2 handed off to T-tAKBBB).
2. Unblocks `T-HPJcc6`/`T-tAKBBB` (checkers, read the ledger/digest/path-history formats frozen
   here) once `T-ltBLUY` (contract) also lands.
