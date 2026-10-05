# STATUS

- ID: `T-uoYW6b-cache-key-builder`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (Dev A)

## This update
Implemented in commit `95dab70` (branch `worktree-agent-a18ce2c08e42a3a5a`):
`src/agent_orchestrator/cache/fingerprint.py` and `keys.py`, with `tests/cache/test_keys_golden.py`
(2 tests), `test_keys.py` (88), `test_fingerprint.py` (37) and the shared fixture module
`tests/cache/keys_fixture.py`. No shared (pre-existing) file was touched. GV-1 Rev 2 reproduced on
the first run. Cache stays OFF by default; the engine is unchanged. T-gDNjN2 may start on this
dependency. Frozen names and four small deviations: `HANDOFF.md`.

## Acceptance criteria
| AC | Result | Evidence |
|----|--------|----------|
| 1 U-K1 | PASS | `test_keys_golden.py`: key `6646469e94a695fe...0319f` and all 11 component digests equal GV-1 Rev 2 (real files, `cli_version_of` = `2.1.278 (Claude Code)`, `environ={}`, one repo head) |
| 2 U-K2 | PASS | 100 builds -> one key; same tree under another absolute path (and a moved workspace) -> same key and components |
| 3 U-K3 | PASS | 26 parametrized sensitivity cases (instruction / general instruction / input content, output set and path, output prior, repo head and set, model, effort, `max_turns`, `prompt_template`, `command_template`, `extra_args`, `disallowed_tools`, `forced_disallowed_tools`, exclude-dynamic flag, `context_window`, `working_dir`, CLI version, allowlisted env var, `CLAUDE.md`, `.claude/agents/x.md`, `.mcp.json`, input order, executor) plus dynamic-input content and order, `EFFORT_MAX_TURNS` monkeypatched (argv component changes, prompt component does not) |
| 4 U-K4 | PASS | task id, `timeout_seconds`, `retries`, `depends_on`, `touches`, `skip_if_outputs_exist`, `join`, `forbidden_task_models`, `ANTHROPIC_API_KEY` / other non-allowlisted env, workspace path: key unchanged; the secret never appears in the summary or repr |
| 5 U-K5 | PASS | one test each: N-1 (symlink, `./..`, absolute spelling -> same key and resolved relative path), N-2, N-3, N-4, N-6 (`.git`, `.orchestrator`, own outputs skipped), N-7, N-8 (root-to-cwd context dirs keyed, a sibling directory is not) |
| 6 U-K6 | PASS | input that is also an output uses the prior digest; recompute with `CacheKey.preseed` equals the lookup key, the naive recompute does not; a `None` (absent) preseed round-trips |
| 7 U-K7 | PASS | `build_prompt` of the normalized context is identical for placeholder and real run / task ids and timeouts |
| 8 U-K8 | PASS | `AGENT_KEY_FIELDS | AGENT_NON_KEY_FIELDS == set(AgentSpec.model_fields)`, disjoint; the failure message names the unclassified fields, `constants.py` and `KEY_SCHEMA_VERSION` |
| 9 U-K9 | PASS | `path_rejected` (traversal on instruction / input / output, absolute outside path, NUL byte, symlink loop, `CLAUDE.md` symlink out of the workspace, guarded general-instruction and repo paths); `path_in_cache_dir` (cache dir, a file in it, instruction, repo path); `duplicate_output` (also via `..`) |
| 10 U-K10 | PASS | `sensitive_output` for 9 paths (`.git/hooks`, `.github`, `.claude`, `.ao`, `.orchestrator`, `CLAUDE.md`, `AGENTS.md`, `.mcp.json`, `.envrc`) and an output symlinked into `.git/hooks`; `docs/claude.md` allowed; `control_output`; `output_not_regular_file` |
| 11 U-K11 | PASS | `prompt_render_error` (`{nope}` -> `KeyError`, `{0}`, unterminated brace); `key_encoding` (a NaN reaching the document) and an over-bound summary (deviation 1) |
| 12 U-K12 | PASS | a secret in `extra_args`, argv flags, the prompt and the templates are absent from `KeySummary.model_dump_json()`; the summary field set is pinned |
| 13 U-F1..F5 | PASS | memoized (one `subprocess.run` for two lookups, argv `[realpath, "--version"]`, 10 s timeout, no shell); re-read after an mtime change and after a size-only change; symlinked binary shares its target's identity; missing binary, timeout, `OSError`, non-zero exit (not memoized), empty output, non-executable -> `executor_fingerprint_unavailable`; env allowlist copies only listed names; absent context files -> `kind: absent` in fixed order; root-to-cwd directories; a symlink inside `.claude/agents` -> `input_not_regular`; `executor: fake` -> `argv` and `executor_fingerprint` are `null`, no subprocess and no version-reader call |
| 14 hygiene | PASS | see Evidence; A-9 check recorded below |

## A-9 check (`CLAUDE_FINGERPRINT_ENV_VARS` against the Claude CLI's documented variables)
Checked 2026-10-05 against `https://code.claude.com/docs/en/env-vars` (WebFetch summary) with the
installed CLI at `2.1.289 (Claude Code)`.
- Documented there: `ANTHROPIC_MODEL`, `ANTHROPIC_SMALL_FAST_MODEL` (deprecated),
  `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_HAIKU_MODEL`,
  `CLAUDE_CODE_MAX_OUTPUT_TOKENS`.
- Not found on that page by the summary: `CLAUDE_CODE_SUBAGENT_MODEL`, `MAX_THINKING_TOKENS`,
  `CLAUDE_CONFIG_DIR` (the last is mentioned as not settable in an `env` block). They are kept:
  an unused name in the allowlist is harmless (a false miss at worst); the page may be a partial
  rendering. Not independently verified against the CLI source.
- **Candidates for the G1a reviewer (list left unchanged, because it is closed and adding a name
  needs review):** `ANTHROPIC_BASE_URL`, `ANTHROPIC_DEFAULT_MODEL`, `CLAUDE_CODE_EFFORT_LEVEL`,
  `CLAUDE_CODE_USE_BEDROCK`, `ANTHROPIC_BEDROCK_BASE_URL`, `ANTHROPIC_VERTEX_BASE_URL`,
  `ANTHROPIC_FOUNDRY_BASE_URL`, `ANTHROPIC_BETAS`, `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS`,
  `CLAUDE_CODE_AUTO_COMPACT_WINDOW`. Residual risk R-17: a variable outside the list that changes
  behaviour can cause a stale hit.

## Evidence
- `.venv/bin/python -m pytest -q tests/cache/test_keys_golden.py tests/cache/test_keys.py tests/cache/test_fingerprint.py` -> 127 passed (2 + 88 + 37).
- `.venv/bin/python -m pytest -q tests/cache` -> **630 passed** (503 after T-8tr1H4).
- `.venv/bin/ruff check src tests` -> All checks passed.
- `.venv/bin/ruff format --check src tests` -> only the pre-existing generated
  `src/agent_orchestrator/_build_info.py` would be reformatted.
- `.venv/bin/mypy src tests/cache` -> only the 4 pre-existing `_version.py` errors.
- AST guard (`tests/cache/test_ast_guard.py`) passes; `claude_cli.py` still imports nothing from
  `cache/` (T-OeRYSO's guard test).
- **Full suite** (group T-8tr1H4 + T-uoYW6b, run after both commits):
  `.venv/bin/python -m pytest -q -rs -p no:cacheprovider` -> **5787 passed, 10 skipped, 0 failed**
  in 633 s. Baseline before this group: 5583 passed / 10 skipped / 0 failed; the delta is exactly
  +204 = 77 (T-8tr1H4) + 127 (this task). The 10 skips are the same environment gates as before
  (`real_llm`, `swebench`, `playwright` not installed, one isolation layout skip).

## Risks / Blockers
- No blockers.
- Risk: GV-1 drift from an upstream change (for example `build_prompt` wording, argv
  construction): the golden test fails loudly; update GV-1 in the HLD with the reason and decide on
  a `KEY_SCHEMA_VERSION` bump.
- Risk R-17: env allowlist (see the A-9 check).
- Heads-up for T-gDNjN2: build one `CliVersionReader` per process and pass `reader.version` in
  `KeyDeps`; pass `CacheKey.preseed` back as `preseed=` for the settle-time recompute.

## Next actions
1. T-gDNjN2 builds `ResultCache` on `build_cache_key`, `RepoHeadReader` and `WorktreeProbe`.
2. T-U7ckfd / T-u3jG8F / T-HjxNQ0 complete the core set; then gate G1a (T-fXWbqg).

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD §23.5).
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done (commit `95dab70`).
  All 14 acceptance criteria pass, GV-1 Rev 2 reproduced exactly; four small deviations in
  `HANDOFF.md`; A-9 check recorded above. Matches `TASK.md`, `HANDOFF.md` and the epic rollup.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: G1a remediation touched this task's code (SEC-06, S-11, S-10 (key builder / sensitive paths)) in commit `763375f`; findings and regression tests are listed in `T-fXWbqg-cache-review-gates/STATUS.md` (G1a remediation) from `output/E-Rc4Hk8-cross-run-result-cache/review-g1a.md` and `review-g1a-security.md`. Task state stays Done; matches `HANDOFF.md`.
By: developer · Role: developer · Date: 2026-10-05 · Comment: G2 remediation: the D29 sensitive-path lists were extended (sec G2-S3 / G1a SEC-15: `.githooks .circleci .vscode .devcontainer .cursor .idea`; `.gitlab-ci.yml Jenkinsfile .travis.yml azure-pipelines.yml bitbucket-pipelines.yml .pre-commit-config.yaml .gitmodules .gitattributes`; ADR-0019 D29 addendum). The key schema and GV-1 do not read the lists; unchanged. Task state unchanged. See `T-fXWbqg-cache-review-gates/STATUS.md` (G2 remediation).
