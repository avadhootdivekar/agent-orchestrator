# STATUS

- ID: `T-bdQZW4-cache-docs-refresh`
- Updated At: `2026-10-05`
- State: `In Review` (all ten HLD 25 steps done and grep-verified; **architect sign-off pending**)
- Owner: `developer` (Dev B), `architect` sign-off

## This update
- Delivered the mandatory post-implementation docs reconciliation (HLD 25 steps 1-10) in three
  commits: `643ad11` (HLD + ADR-0019), `eaead55` (README, authoring skill, ROADMAP, the HLD/analytics
  cross-links, learnings), `5748316` (comment-only pointers), plus this ticket-docs commit.
- **Step results.**

| Step | Result | Where |
|------|--------|-------|
| 1 HLD | Done | `docs-md/cross-run-result-cache-hld.md`: new "0. Implementation outcome and deviations" (0.1 shipped, 0.2 resolved assumptions/OQs and G0 status, 0.3 DV-1..DV-24, 0.4 accepted residuals R-A1..R-A8, 0.5 GV-1 re-verified, 0.6 follow-ups); the old section 0 is now 0A; superseded statements corrected in place (D8, D9, D19, D20, D29, 7.7 residual rows, 8.0 modules, 8.1.4 `mode: "on"`, 8.1.5 constants, 8.2.2, 8.2.4, 8.4.3, 8.5, 8.7.5, 8.8.2, 8.9, 8.10, 8.11, 17, 18, 22.5, 23.1/23.2, 24.2 corrected table, 25 status) |
| 2 ADR-0019 | Done: **Accepted** (Rev 4) with an as-built addendum A1-A5 | `docs-md/adr/ADR-0019-cross-run-result-cache.md` |
| 3 `hld-agent-orchestrator.md` | Done: component (2), NFR-1 carve-out (4), lookup step (5), related-docs link | `docs-md/hld-agent-orchestrator.md` |
| 4 authoring skill | Done: "Result cache (opt-in)" section, quick-guide row, failure mode 13; example validated against the schema and `WorkflowSpec` | `.claude/skills/workflow-authoring/SKILL.md` |
| 5 README | Done: "Result cache (opt-in)", contents entry, CLI reference line, `AO_CACHE` row, config block, `cache` task field, release-note line, G0 protocol link (protocol doc untouched) | `README.md` |
| 6 ROADMAP | Done: "2c Just landed", status row, recently delivered, R-15 under 3.4, non-MVP list under 3.6, G0 follow-up, residuals under 4 | `meta/ROADMAP.md` |
| 7 usage + bench docs | Done: `result_cache` usage object, both hit-exclusion sites, `settle_reason: cached`, bench always `--no-cache` | `docs-md/usage-analytics.md` (Update 3), `docs-md/benchmarking-framework-hld.md` (4.2 pseudocode, deviation 7, new section 22) |
| 8 cross-links | Done | `docs-md/cost-caching-optimization-hld.md` (terminology box + pointer), `docs-md/token-budgeting-hld.md` (new section 11) |
| 9 pointer comments | Done, comment-only | `src/agent_orchestrator/models.py` (next to `EFFORT_MAX_TURNS`), `src/agent_orchestrator/executors/claude_cli.py` (above `build_claude_argv`) |
| 10 learnings | Done: 7 genuinely new entries | `meta/learnings.md`, `meta/learning-compact.md` |

## Acceptance criteria
| AC | Result | Evidence |
|----|--------|----------|
| 1 statements match the code | PASS | "Evidence" below (106-check script plus the greps and runs listed) |
| 2 HLD section 0, each deviation with a reason, G0 worded "protocol shipped; execution post-merge" | PASS | HLD 0.2 and 0.3 |
| 3 ADR-0019 Accepted with addendum | PASS | status line and "Addendum (Rev 4, as built)" |
| 4 skill: double opt-in, flip point, EC-20 warning, example | PASS | skill section items 1, 6, 10 |
| 5 README section + release-note line; nothing claims `refresh`, `rm --run/--task`, `verify --repair` exist | PASS | `grep -n 'refresh\|--repair\|rm --run' README.md .claude/skills/workflow-authoring/SKILL.md` shows only the negations (README line 479 and 696, skill lines 334-335) |
| 6 pointer comments exist; full suite still passes | PASS | `git diff 5748316~1 5748316` is comments only; suite figures below |
| 7 architect signs off | **PENDING** | see "Architect sign-off" |

## Evidence
Run in the task worktree (`.venv/bin/python`), 2026-10-05. Doc-vs-code verification, per AC-1:

- **Constants, defaults, modes, exit codes, schema ids, events, sensitive lists, fingerprint env
  list, command set, release-note text, README links**: a throwaway verification script
  (scratchpad, not committed) imported `agent_orchestrator.cache.constants` / `cache.fingerprint`
  and asserted each documented value against the code and the doc text: **106 checks, 0 failures**.
  It covered `DEFAULT_TASK_CACHE_POLICY is False`, `KEY_SCHEMA_VERSION == 1`, `ENV_CACHE`,
  `ENV_ON_VALUES` / `ENV_OFF_VALUES`, the modes, the config defaults (1 GiB, 30 days, 512 MiB, 20 000,
  64 MiB, 90%), the 1 h graces, `INLINE_PRUNE_MAX_WALK_ITEMS` / `_BLOBS`, `DEFAULT_LS_LIMIT`,
  `LS_SORT_KEYS`, exit codes 0/1/2, the 7 `SCHEMA_*` ids present in HLD 13.4, the 8 `EVENT_*` names
  present in HLD 15, all 13 + 13 sensitive names present in the HLD and ADR, the 9-name fingerprint
  allowlist with `ANTHROPIC_BASE_URL` / `CLAUDE_CODE_USE_BEDROCK` / `CLAUDE_CODE_USE_VERTEX`
  absent and documented as residuals, the 7 `@cache_app.command` names equal to the documented set
  (each named in the README), the release-note line identical in README and HLD 16, and the G0
  protocol link.
- **CLI surface**: `.venv/bin/python -m agent_orchestrator.cli cache --help` and each
  `cache <cmd> --help` (7 commands: ls, stats, show, rm, prune, clear, verify; no `refresh`, no
  `rm --run/--task`, no `verify --repair`; options and exit codes as in README / HLD 8.9);
  `... run --help` and `... resume --help` list `--cache/--no-cache` (`grep -c -- '--cache'` = 1 each).
  `ao cache stats --json -w /nonexistent` -> exit 2 with a JSON `error` (DV-18); no workspace and no
  env -> exit 1.
- **`ao prune` does not touch the result cache** (HLD 0.2 OQ-2, README): a scratch workspace with
  `.orchestrator/cache/entries/marker` and `.orchestrator/runs/old-run`; `ao prune -w <ws> --older-than 0`
  deleted only the run directory; the cache marker survived.
- **GV-1 and component digests**: `.venv/bin/python -m pytest -q -p no:cacheprovider
  tests/cache/test_keys_golden.py tests/test_claude_cli_argv_builder.py` -> `102 passed`. A direct run
  of the GV-1 fixture (`tests.cache.keys_fixture.key_for`) printed key
  `6646469e94a695fe1a994d35f54ca74e007262911255552b03c54ce2e5d0319f` (== `GV1_KEY`, `True`) and
  the eleven digests `agent ced58570dab6`, `argv 315cfbdef1cf`, `dynamic_inputs 4f53cda18c2b`,
  `executor_fingerprint 620dc66502c3`, `general_instructions 80c58b832f5c`, `inputs 6518d7ac7319`,
  `instruction 8f7ad8e7e8d2`, `key_schema 6b86b273ff34`, `outputs 2789b49e50b4`, `prompt
  25e61c2662b6`, `repo_heads b1e77f36ac12`: all equal HLD 8.2.7.
- **Engine and shared-file figures (HLD 0.1, 24.2)**: `git diff --numstat 0b980e3..HEAD --
  src/agent_orchestrator/engine.py ...` -> engine `135 30` (net +105), `cli.py 115 2`, `runstate.py
  12 1`, `ui/files.py 13 0`, `usage.py 109 23`, `models.py 81 1` (before the comment edits),
  `project_config.py 71 1`, `outcomes.py 12 3`, `ui/runs.py 15 0`, `bench/subjects.py 9 0`,
  `ci.yml 8 0`, `tests/ui/test_run_graph_endpoint.py 12 3`; `tests/conftest.py` and
  `tests/test_nfr2_regression_gate.py`: no diff. `grep -n 'open(\|\.read(' src/agent_orchestrator/engine.py`:
  no match. `grep -n 'cache' src/agent_orchestrator/engine.py | grep -i import`: only line 118
  (`TYPE_CHECKING`) and line 1498 (lazy, inside `_result_cache_lookup`).
- **`cache.cli` on the CLI path (DV-16)**: `grep -n 'cache.cli' src/agent_orchestrator/cli.py` ->
  `44:from .cache.cli import cache_app`; allow-list pinned by
  `tests/cache/test_cli_result_cache_wiring.py::TestLazyImports` (`CLI_PATH_ALLOWED_CACHE_MODULES`) and
  `::TestCacheCliStaysImportLight`, and by `tests/test_e2e_cli_result_cache.py` (E-2).
- **`mode: "on"` quoting (DV-3)**: `grep -n 'mode: "on"' src/agent_orchestrator/project_config.py` ->
  line 492 (shipped template text); the README and HLD 8.1.4 blocks match it.
- **Banner trust note, summary line, `report-usage` lines**: `grep -n '_RESULT_CACHE_TRUST_NOTE\|Result cache:'
  src/agent_orchestrator/cli.py`; `cache/report.py::format_summary_line` text equals the README
  example; `usage.py` `ResultCacheUsage` fields equal the usage-analytics list.
- **Usage sites (usage-analytics)**: `usage.py` lines 472-500 (site A, `if tid in rc_hits`) and 555
  (site B, `or pid in rc_hits`); `outcomes.py:130` `SettleReason = Literal["dispatched", "skipped", "cached"]`.
- **Bench (benchmark doc)**: `bench/subjects.py` lines 94-95 (`_AO_NO_CACHE_FLAG`,
  `_AO_CACHE_OFF_VALUE`), 456 (argv), 470 (`env[ENV_CACHE]`); `tests/bench/test_bench_cache_forced_off.py`
  (7 tests; outer values `1`, `shadow`, `on`, `true`).
- **Budget (token-budgeting note)**: `engine.py` `_reverse_stale_charge` (line 1454; emits
  `budget.resume_reverse`), called from the hit path (line 1536) and the budget gate (line 1250).
- **Skill example**: validated with `jsonschema` against `specs/workflow.schema.json` and
  `WorkflowSpec.model_validate` (`schema ok; tasks: [('summarize', True), ('review', None)]`).
- **Pointer comments (step 9)**: `git diff 5748316~1 5748316 --stat` -> `claude_cli.py 7 +`,
  `models.py 5 +`, comments only.
- **Gates**: `.venv/bin/ruff check src tests` -> `All checks passed!`; `.venv/bin/ruff format --check
  src tests` -> only the generated, git-ignored `src/agent_orchestrator/_build_info.py` is flagged
  (pre-existing; `git check-ignore` confirms it is ignored); `.venv/bin/mypy src tests/cache` -> only the
  4 pre-existing `src/agent_orchestrator/_version.py` errors (unchanged).
- **Full suite** (`.venv/bin/python -m pytest -q -p no:cacheprovider`, run on the final code tree
  after commit `5748316`): **6903 passed, 10 skipped, 0 failed** in 698.59 s (0:11:38), exit 0 (equal to the last known
  figure: zero regressions, no skips in `tests/cache`).
  Last known before this task: 6903 passed / 10 skipped / 0 failed (G2).

## Carry-over reconciliation (every STATUS carry-over comment, G1b and G2)
| Carry-over | Where it is documented |
|------------|-----------------------|
| G1b (1) `filter.<x>.clean` residual (S-3) in HLD 7.7 and the guide, `--no-cache` for untrusted repos | HLD 7.7 residual row, 0.4 R-A2; ADR A4.2; skill item 8; README |
| G1b (2) agent-writable cache dir and the on-mode banner clause (S-5) | HLD 8.1.7 as-built note, DV-15, 7.7 residual row, 0.4 R-A4; ADR A4.4; skill item 8 |
| G1b (3) HLD 8.7.5 allow-list includes `cache.cli` (N-4) | HLD 8.7.5, 18.1 E-2, 0A, NFR-1 row, D9, DV-16; ADR A3 |
| G1b (4) skip-worktree / assume-unchanged, partial-attempt baselines (N-7, N-9), restore temp files hashing ignores (S-1) | HLD 7.7 dirty-tracked-edit row and 0.4 R-A3; the hashing exemption was later removed (DV-7, ADR D8 addendum), leftovers 0.4 R-A6 |
| G2 (1) provider env not in the key (rev S4 / SEC-11) | HLD 7.7, 0.4 R-A1, A-9, R-22; ADR A4.1; skill item 8; README |
| G2 (2) guard-3 probe executes `filter.<x>.clean` with its precondition | HLD 7.7, 0.4 R-A2, R-23; ADR A4.2; skill item 8; README |
| G2 (3) HLD 24.2 merge notes: `tests/ui/test_run_graph_endpoint.py`, `status` `except (ValueError, KeyError)` and eager `cache.cli` import, `runstate.py` +12/-1, `ui/files.py` +13, new `cache.constants` imports, E-2 allow-list | HLD 24.2 corrected table and "Also at merge" note, DV-21 |
| G2 (4) D8 (no restore-temp exemption), D29 / SEC-15 extended lists, HLD 7.7 M-14 and the constants snippet, G0 protocol Step 9 (shadow stores copies; `ao cache clear --yes`), retention statement, sweep residual after `clear`, HLD 18 baseline sentence | HLD 7.6 D8/D29, 7.7 M-14, 8.1.5 snippet, 0.4 R-A6, 18 coverage-and-baseline; ADR D8/D29 addenda and A4.6; the G0 protocol (Steps 0-8, owned by T-nPMuz4, untouched) has **no** Step 9 / cleanup note, so the HLD records that as a gap in R-A6 and R-24 and recommends `ao cache clear --yes` after a shadow run |
| HANDOFF deviations of the earlier tasks (T-FJH6LI, T-28J9oR, T-8tr1H4, T-uoYW6b, T-U7ckfd, T-HjxNQ0, T-u3jG8F, T-gDNjN2, T-XpF1pF, T-eyn5UG, T-o95l1M, T-ZTxN1x, T-6tRKml, T-bLpoze, T-JCOAsq) | HLD 0.3 DV-1..DV-24 |
| T-6tRKml notes: module split, prune JSON additions, `unreadable`, `--older-than 0` future `created_at`, sweep ADR addendum | HLD 8.0, 8.9, DV-17, DV-18; ADR A5 |
| T-uoYW6b A-9 check | HLD A-9 and 0.2 |
| T-JCOAsq: Part 1 rejection, vacuous `find_spec` finder, I-1 vs E-2 allow-lists | HLD DV-24, DV-16; learning `LRN-20261005-import-poison-finder-must-implement-find-spec` |

## Architect sign-off
- **PENDING.** Awaiting the architect's line here (the manager will arrange it). Until then this
  task and the epic stay `In Review`, not `Done`.

## Risks / Blockers
- No blockers. Items the architect should check (uncertain or judgement calls):
  1. HLD 0A (old section 0) was renamed rather than renumbered, so two top-level "0" sections exist
     (0 and 0A); external links to "section 0" now mean the as-built outcome.
  2. HLD 8.9 documents the workspace resolver as implemented (`--workspace`, `AO_WORKSPACE_ROOT`, then
     config discovery) although `--help` mentions only the env var (DV-18); no code was changed.
  3. The G0 protocol doc (not edited, owned by T-nPMuz4) has no "Step 9" / cleanup or retention
     note (G2 sec N4 asked for one): I recorded this as a gap in HLD 0.4 R-A6 and 23.1 R-24 instead of
     editing the doc; the architect may want T-nPMuz4's owner to add it.
  4. A few accepted-residual statements (R-A8 items) are quoted from the G1b/G2 reports without a
     fresh code reproduction (documented as such: "as reported").
  5. HLD 0.2 OQ-5 is marked "not revisited".

## Next actions
1. Architect: review and add the sign-off line (AC-7); then the manager moves T-bdQZW4 and the epic
   to Done and syncs `EPIC.md` / `STATUS.md`.
2. Parent / operator (post-merge): execute G0 (protocol in `docs-md/result-cache-g0-protocol.md`);
   merge with E-Ag7Pw3 and E-Da5Tn9 per HLD 24.2; rebuild the UI bundle.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD §23.5). State stays `Draft`
  (last); this file, `TASK.md`, `HANDOFF.md` (when present) and the epic `STATUS.md` rollup
  agree.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: G1b carry-over for the docs refresh: (1) list sec S-3 (git `filter.<x>.clean` executed by the guard-3 probe; precondition: an in-.git write by a task agent) as a named residual in HLD 7.7 and the authoring guide, recommending --no-cache for untrusted repos; (2) document the agent-writable cache dir and the on-mode banner clause (sec S-5); (3) HLD 8.7.5 allow-list includes cache.cli (rev N-4); (4) residual rows for skip-worktree / assume-unchanged and partial-attempt baselines (sec N-7, N-9) and the orphaned restore temp files that directory hashing ignores (sec S-1). See `T-fXWbqg-cache-review-gates/STATUS.md` (G1b remediation).
By: developer · Role: developer · Date: 2026-10-05 · Comment: G2 carry-over for the docs refresh (state stays Draft). (1) ACCEPTED RESIDUAL, rev G2-S4 / G1a SEC-11: "`ANTHROPIC_BASE_URL`, `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX` and the provider region are not in the key's env allowlist, so a hit can be served across a backend or endpoint switch with an unchanged model alias; same model name, operator-controlled, non-secret; changing it would change GV-1. Mitigation: pass `--no-cache` (or run `ao cache clear`) when switching provider or endpoint." List it in HLD 7.7 and the authoring guide. (2) ACCEPTED RESIDUAL, sec G2-S2 / G1b S-3 (same family as G1a SEC-03): "`git status` in the guard-3 probe still executes a `filter.<x>.clean` command named in the agent-writable git config (re-confirmed live, git 2.39.5; `core.fsmonitor` is closed). Precondition: cache on AND the task opted in AND a writer of the git config / `.gitattributes` that cannot already run code (a tool-restricted agent without Bash); an agent with Bash already has the same power, and the same primitive pre-exists elsewhere in the code base. Mitigation: use `--no-cache` for untrusted repositories." List it in HLD 7.7 and the authoring guide with that precondition. (3) rev G2-S3: correct the HLD 24.2 merge notes: add the unlisted file `tests/ui/test_run_graph_endpoint.py` (+12/-3, adds `result_cache` to two exact key-set assertions; merge guidance: "take both sides; the key set must contain every sibling's key"); `cli.py` `status` also changes `except (json.JSONDecodeError, KeyError)` to `except (ValueError, KeyError)` and imports `cache.cli` eagerly at module level; `runstate.py` is +12/-1 (not "about 6"; it also retypes `snapshot`); `ui/files.py` was +9 before G2 and is now a few lines more (casefolded deny-list, NUL guard); the new module-level imports of `cache.constants` in `project_config.py`, `bench/subjects.py` and `ui/files.py`; the HLD 8.7.5 / 18.1 E-2 allow-list must include `cache.cli`. (4) HLD text owed by the G2 fixes: D8 (no restore-temp exemption in directory hashing; ADR-0019 D8 addendum) and D29 / SEC-15 (extended sensitive lists; ADR-0019 D29 addendum; HLD 7.7 M-14 and the constants snippet near line 1043); G0 protocol Step 9 (shadow mode stores output copies; `ao cache clear --yes` cleanup) (sec G2-N4); retention statement (outputs persist in `.orchestrator/cache` until `rm/clear/prune`; shadow also stores them); the sweep residual after `clear` (sec G2-N7); the HLD 18 baseline sentence is stale (rev G2-N8). See `T-fXWbqg-cache-review-gates/STATUS.md` (G2 remediation).
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Docs reconciliation delivered (commits `643ad11`, `eaead55`, `5748316`, `7d73af8` and the ticket-docs commit). All ten HLD 25 steps done and grep-verified (Evidence above); every G1b and G2 carry-over comment in this file is reflected (table "Carry-over reconciliation"). State -> `In Review`: AC-7 (architect sign-off) is pending, so the task and the epic are not `Done`. `TASK.md`, `HANDOFF.md` and the epic `EPIC.md` / `STATUS.md` rollup agree.
