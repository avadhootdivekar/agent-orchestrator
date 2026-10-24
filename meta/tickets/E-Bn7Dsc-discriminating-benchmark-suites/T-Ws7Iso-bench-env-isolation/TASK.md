# TASK: T-Ws7Iso-bench-env-isolation

- Epic: `E-Bn7Dsc-discriminating-benchmark-suites`
- Title: Isolate the agent-under-test from the repo's own instructions, skills and hidden assets
- Status: Done (2026-10-24) — ranking campaigns are unblocked
- Design: `docs-md/benchmark-discriminating-suites-hld.md` §2b (parity), §2c (isolation, as built), §7 step 6 (held-out seeds), §12 (finding + resolution)

## Finding (2026-10-24)
`BENCH_WORKSPACE_ROOT` is `<repo>/playground/.tmp/bench`, so every task workspace sits *inside* the
agent-orchestrator checkout, itself inside the `ao-runner-ao` control-plane workspace. Claude Code loads
`CLAUDE.md`/`.claude/` from every ancestor directory, so the agent under test — for **both** subjects — ran with:
- `ao-runner-ao/CLAUDE.md` + `agent-orchestrator/CLAUDE.md` in context (on top of the parity `<ws>/CLAUDE.md`);
- the repo's skills (`benchmarking`, `repo-intel`, `workflow-authoring`) and agents (`architect`, `developer`,
  `tester`, …) plus the control-plane skills (`git-commit-push`, `memorize`, `verify`, `debug`, …).
Evidence: the `system/init` event of every 2026-10-23 transcript
(`playground/.tmp/bench/2026-10-23-gen-services-mini-*/**/transcript.jsonl`, fields `skills`/`agents`) lists them.
One ao `verify` agent also ran `cat ../../.gitignore`, i.e. agents do probe parents; `_grading/` is reachable at
`../../../_grading/<subject>/<task>` with `bypassPermissions`. No transcript shows a `_grading` access.
Parity still held (both subjects got the same leak) and the run was saturated anyway, but the "same instructions"
guarantee of §2b is violated and the `benchmarking` skill describes how grading works.

## Acceptance
1. Default `BENCH_WORKSPACE_ROOT` outside any directory tree that contains `CLAUDE.md`, `CLAUDE.local.md`,
   `AGENTS.md` or `.claude/` (e.g. `${AO_BENCH_WORKSPACE_ROOT:-/var/tmp/ao-bench/<uid>}`); `ao-bench run`
   refuses (or, with an explicit flag, stamps `env_contaminated=true`) when any ancestor of the chosen root has one.
   Keep the existing path guard; move `_grading/` to a sibling root (not under the workspace root).
2. Environment fingerprint: parse each transcript's `system/init` event (`skills`, `agents`, `slash_commands`,
   `mcp_servers`) — for `ao_workflow` subjects every `.orchestrator/runs/*/*/attempt-*/transcript.jsonl` — and
   record it per task in `run.json`; `compare` refuses when the set differs between subjects or from the parity
   bundle's expected set (Claude Code built-ins allow-listed).
3. Hidden-asset audit: any `tool_use` input mentioning the grading root marks the task `suspect` with
   `suspect_reason="grading_access"`; such tasks are excluded from `pairwise`/`subject_stats`.
4. Decide and document `CLAUDE_CONFIG_DIR` handling: user-level `~/.claude/settings.json` (`model`, `effortLevel`,
   `enabledPlugins`), user skills and auto-memory apply to every subject today and are not fingerprinted.
   Either run under a bench-owned config dir (credentials copied/linked) or record those keys in the fingerprint.
5. Held-out seeds policy in the skill + HLD: ranking campaigns use a `--seed` not present in any committed suite
   and record it; keep ≥ 1 generated family unseen when tuning `ao` templates/instructions.
6. `make bench-gen-smoke` 2/2 from the new root; annotate the 2026-10-23 results as environment-contaminated
   (done in README on 2026-10-24); fake-tier tests for 1–3; `ruff`, `mypy src/agent_orchestrator/bench`,
   `pytest tests/bench` green.

## Notes
- Running the bench from the same branch/checkout is *required*, not a problem: `AoWorkflowSubject` shells
  `uv run ao` from `REPO_ROOT`, so the branch's editable `ao` is what gets measured (`run.json` records
  `ao_version`, e.g. `ao 0.1.0 (a6b569a.dirty)`). Run from a clean tree so the stamp is reproducible.
- Model-side contamination (the public repo's trap catalogue in future training data) is not fixable by layout;
  instance randomisation + hidden probes + fresh seeds are the defence.

## As built (2026-10-24)
| AC | Delivered |
|---|---|
| 1 | `bench_workspace_root()` = `${AO_BENCH_WORKSPACE_ROOT:-/var/tmp/ao-bench/<uid>}/ws`, grading under the sibling `<base>/_ao_bench_grading`; `run_suite` refuses agent subjects on a root with an instruction-file ancestor, `--allow-contaminated-env` (run/campaign/calibrate) stamps `env_contaminated`; path guard kept; `ao-bench env [--probe]` preflight |
| 2 | `bench/envcheck.py`: per-task union of init events (`skills`, `agents`, `slash_commands`, `mcp_servers` by name, `plugins` by source) for both transcript layouts; per-run **baseline probe** (empty parity-applied workspace, `claude -p` killed at init) is the "expected set", so built-ins need no hand-kept list; `build_comparison` raises `EnvMismatchError` on foreign entries, contaminated runs, differing fingerprint/baseline/user-config digests or legacy+fingerprinted mixes; campaigns stop repeats (`environment_mismatch`) and write a stamped report |
| 3 | `audit_grading_access`: `tool_use` input naming `_ao_bench_grading` → `suspect=true, suspect_reason="grading_access"`; `results.is_attempted` excludes it from cells, rates, roll-ups, pairwise and calibration |
| 4 | Decision: do **not** relocate `CLAUDE_CONFIG_DIR` (OAuth credential fork risk); record user settings keys, user `CLAUDE.md` hash and `CLAUDE_*`/`ANTHROPIC_*` env (secrets redacted) as `user_config`; default bundle env adds `ENABLE_CLAUDEAI_MCP_SERVERS=false` + `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`; subject env scrubs the operator's Claude Code session vars (`envcheck.subject_env`, shared by both subjects) |
| 5 | Policy in HLD §7 step 6, the `benchmarking` skill rule 6 and README |
| 6 | `make bench-gen-smoke` 2/2 from `/var/tmp/ao-bench/1000/ws`; README caveat + resolution note; `tests/bench/test_env_isolation.py` (34 fake-tier tests); ruff/format clean repo-wide, mypy bench clean, `pytest tests/bench` 1178 passed / 21 skipped |
