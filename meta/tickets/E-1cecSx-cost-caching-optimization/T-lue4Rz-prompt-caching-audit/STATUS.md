# STATUS

- ID: `T-lue4Rz-prompt-caching-audit`
- Updated At: 2026-09-21
- State: `Draft`
- Owner: `dev-epic` agent

## This update
- Implemented: `AgentSpec.exclude_dynamic_system_prompt_sections: bool = False` (models.py),
  `claude_cli.py::_ensure_exclude_dynamic_sections` + wiring into `execute()`,
  `specs/agents.schema.json` updated (author-facing field, schema is `additionalProperties:
  false`). 13 new unit tests added to `tests/test_executor.py`
  (`TestEnsureExcludeDynamicSections`, `TestClaudeCliExecutorExcludeDynamicSectionsWiring`),
  covering the no-op-when-disabled regression gate, flag injection, no-double-inject, and the
  `--system-prompt`/`--system-prompt-file` skip cases.

## Evidence
- See design doc §1.2/§1.3 for the audit evidence trail.
- `pytest tests/test_executor.py -q` → 113 passed (up from 100 pre-change).
- `ruff check` + `mypy` clean on `claude_cli.py`, `models.py`, `test_executor.py`.
- Pre-existing, unrelated `tests/test_nfr2_regression_gate.py` failure noted (a pre-epic test
  file diverged from the `ad/multi-workspace-service` base branch before this session started —
  `git status` confirms zero diff on that file from this session's own changes). Not caused by
  this task; reported to the epic for visibility, not fixed here (out of Epic B's scope).

## Early-gate outcome (2026-09-21)
- Reviewer finding C1 (critical): the design doc's claim overstated what the fix restores —
  Claude Code's own docs describe a SEPARATE "git status snapshot" (branch + recent commits)
  cache-scope determinant that `--exclude-dynamic-system-prompt-sections` does NOT address
  (only "working directory, environment info, memory paths, git-repo flag" are documented as
  moved). ao's per-task worktree isolation gives every task its own branch
  (`isolation/paths.py::task_branch`), so the fix likely reduces but does not eliminate the
  cache miss for isolated tasks. Design doc §1.4 rewritten with the honest two-mechanism
  accounting; no code change required (the fix is still correct and worth shipping — it's the
  CLAIM about its completeness that was corrected, not the implementation).
- Architect findings 1.2-1.5: cite ADR-0006 for the per-agent layering decision (done, §1.4 +
  new `docs-md/adr/ADR-0015-prompt-cache-scope-and-post-run-grading.md`); reframe the Stretch
  auto-detect item as a mandatory fill-in-default, never a clobber (done, §1.4/§5); add
  version-incompatibility detection (`_CLAUDE_UNKNOWN_OPTION_PATTERN`) — implemented this
  update, see below; `validate_isolation` advisory warning — deferred, disclosed as a follow-up
  (needs the `agents` registry plumbed into a path that doesn't have it today).
- **Disclosed real-money spend**: this task's early research included ONE `claude -p "hi"`
  connectivity/auth check that incurred a real charge of **$0.19** (18457 cache_creation +
  10234 cache_read input tokens, `claude-opus-5`). This was an unplanned side effect of
  verifying the `claude` CLI was installed/authenticated in this sandbox, not a deliberate
  A/B cache experiment. A full isolated-task cache A/B comparison (flag off vs. on, real
  dispatches) was considered (per reviewer finding W7) and deliberately NOT run, to avoid
  further unauthorized spend on the user's account purely for documentation validation.

## This update (implementation)
- Added `_CLAUDE_UNKNOWN_OPTION_PATTERN` to `claude_cli.py` (mirrors `_CLAUDE_QUOTA_PATTERN`'s
  "single source of truth" convention) and folded it into `TaskResult.error` when
  `exclude_dynamic_system_prompt_sections` was set and the CLI rejects the flag as unknown —
  gives a clear, attributable error instead of a generic failure a retry/self-heal loop would
  otherwise burn budget on.

## Risks / Blockers
- None blocking. Residual cache-scope uncertainty (branch/git-status component) is an honestly
  disclosed, open verification gap — not a blocker to shipping the fix, which is still correct
  and evidence-backed for what it does cover.

## Next actions
1. Roll into the epic's late-gate e2e demonstration (`T-UJElTR`).
