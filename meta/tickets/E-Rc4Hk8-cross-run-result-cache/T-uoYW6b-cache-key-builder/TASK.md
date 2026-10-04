# TASK: T-uoYW6b-cache-key-builder

## Metadata
- Task ID: `T-uoYW6b-cache-key-builder`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev A)
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 2)
- Status: `Draft`
- Estimate: `20 focus hours (2.5 days)` · Sprint 1, Wave 2

## Requirements Mapping
- Requirement IDs: FR-3, FR-4 (path rules), NFR-3, NFR-10 (M-2, M-5, M-14)
- HLD: §8.2.5 (fingerprint), §8.2.6 (`build_cache_key`, `summary_from_doc`, N-1..N-8), §8.2.7
  (key schema v1, GV-1 Rev 2)
- ADR-0019: D3, D4, D5, D6, D7, D21, D29, D30

## Description
Turn "what this agent would be asked to do" into a deterministic sha256 key. The key comes with a
non-sensitive summary and per-component digests.

1. **`cache/fingerprint.py`** (HLD §8.2.5):
   - `CLAUDE_FINGERPRINT_ENV_VARS` and `CLAUDE_CONTEXT_PATHS`;
   - `CliVersionReader.version(binary)`: memoized per resolved path, with a 10 s timeout; any
     failure raises `executor_fingerprint_unavailable`;
   - `claude_cli_fingerprint(req, deps, budget)`;
   - the shared path guards `guarded_resolve(req, raw)` and `guarded_abs(req, a)`. `Path.resolve`
     can raise `RuntimeError` (symlink loop) or `ValueError` (NUL); both map to `path_rejected`
     (developer #5).
2. **`cache/keys.py`** (HLD §8.2.6):
   - `AGENT_KEY_FIELDS` and `AGENT_NON_KEY_FIELDS`;
   - `build_cache_key(req, deps, *, preseed=None) -> CacheKey`, following the pseudocode
     **verbatim**: guards, sensitive and control outputs, duplicates, priors, digests, the
     normalized `TaskContext`, the real `build_prompt`, `build_claude_argv`, the fingerprint, the
     key document, `canonical_json`, the components and `cli_version`;
   - `summary_from_doc(doc, components) -> KeySummary`. It must never include argv, prompt,
     prompt template or `extra_args` (D21).
   - Re-export `canonical_json` from `types`; do not redefine it.
3. **A-9 TODO.** Check `CLAUDE_FINGERPRINT_ENV_VARS` against the installed Claude CLI's
   documented environment variables. Record the result in `STATUS.md`. Never add a secret such as
   an API key or auth token.

## File scope (exclusive)
- `src/agent_orchestrator/cache/fingerprint.py`, `src/agent_orchestrator/cache/keys.py` (new)
- `tests/cache/test_fingerprint.py`, `tests/cache/test_keys.py`, `tests/cache/test_keys_golden.py` (new)

## Inputs / Outputs
- **Inputs:**
  - T-FJH6LI: `types` (`KeyRequest`, `KeyDeps`, `CacheKey`, `KeySummary`, `canonical_json`,
    `UncacheableError`) and `safeio`;
  - T-OeRYSO: `build_claude_argv`;
  - T-8tr1H4: `HashBudget`, `digest_path`, `hash_regular_file`;
  - `executors.prompt.build_prompt` and `artifacts.LocalFsArtifactStore.resolve`.
- **Outputs:** `build_cache_key` and `CacheKey`, consumed by T-gDNjN2.

## Acceptance Criteria
1. **U-K1 (GV-1 Rev 2, exact).** The HLD §8.2.7 worked example reproduces:
   - key `6646469e94a695fe1a994d35f54ca74e007262911255552b03c54ce2e5d0319f`;
   - **every** component digest: agent `ced58570dab6`, argv `315cfbdef1cf`, dynamic_inputs
     `4f53cda18c2b`, executor_fingerprint `620dc66502c3`, general_instructions `80c58b832f5c`,
     inputs `6518d7ac7319`, instruction `8f7ad8e7e8d2`, key_schema `6b86b273ff34`, outputs
     `2789b49e50b4`, prompt `25e61c2662b6`, repo_heads `b1e77f36ac12`.

   Setup: real files in `tmp_path`, `cli_version_of` returning `"2.1.278 (Claude Code)"`,
   `environ={}`, no context files, and the given `repo_heads`.
2. **U-K2 (determinism).**
   - 100 builds give one key.
   - The same tree under a different absolute workspace path gives the same key (N-3).
3. **U-K3 (sensitivity).** Changing each of these alters the key:
   - the content of the instruction, a general instruction, an input or a dynamic input;
   - the output set;
   - an output prior (absent vs present);
   - repo heads;
   - model, effort or `max_turns`;
   - `prompt_template`, `command_template` or `extra_args`;
   - `disallowed_tools`;
   - `working_dir`;
   - argv construction, with `EFFORT_MAX_TURNS` monkeypatched;
   - the CLI version;
   - an allowlisted env var;
   - `CLAUDE.md`, `.claude/agents/x.md` or `.mcp.json`;
   - the input order (N-2).
4. **U-K4 (insensitivity).** None of these alters the key:
   - task id or run id;
   - `timeout_seconds` or `retries`;
   - `depends_on`, `touches`, `skip_if_outputs_exist` or `join`;
   - `forbidden_task_models`;
   - a non-allowlisted env var (`ANTHROPIC_API_KEY`), which also never appears anywhere in the
     key document;
   - the workspace path.
5. **U-K7.** `build_prompt` output is invariant to `run_id`/`task_id` for the normalized
   context.
6. **U-K8 (tripwire).** `set(AgentSpec.model_fields) == AGENT_KEY_FIELDS | AGENT_NON_KEY_FIELDS`.
   The failure message tells the developer to classify the new field and consider
   `KEY_SCHEMA_VERSION`.
7. **Normalization N-1…N-8.** Each rule has a test:
   - symlink-collapsed paths;
   - POSIX separators;
   - output sort order in the structured field;
   - the N-5 preseed reuse (an input that is also an output uses its prior digest);
   - the directory walk excluding outputs;
   - root-relative dynamic inputs;
   - context-file listing from the root down to the cwd.
8. **Path and output rules.** Each of these raises `UncacheableError` with exactly the stated
   reason:
   - `sensitive_output`, including an output symlinked into `.git/hooks`;
   - `control_output`;
   - `path_rejected`: a traversal, a symlink loop, a NUL byte, or a `CLAUDE.md` symlink pointing
     outside the workspace;
   - `path_in_cache_dir`;
   - `duplicate_output`;
   - `output_not_regular_file`;
   - `prompt_render_error`, for an unknown template placeholder;
   - `key_encoding`.
9. **U-F1..F5 (fingerprint).**
   - `CliVersionReader` is memoized: two calls make one subprocess call.
   - A missing binary, a timeout and a non-zero exit each give
     `executor_fingerprint_unavailable`.
   - The env allowlist copies only listed vars.
   - Absent context files are listed as `kind: absent`.
   - A symlink inside `.claude/agents` makes the task uncacheable.
   - For `executor: fake`, `argv` and `executor_fingerprint` are `null` and no subprocess runs.
10. **D21.** `summary_from_doc` output contains no argv, prompt, template or `extra_args` text.
    Assert that a secret placed in `extra_args` is absent from `KeySummary.model_dump_json()`.
11. **Hygiene.**
    - The AST guard passes.
    - `ruff` and `mypy` are clean.
    - `pytest -q` has no new failures.
    - `STATUS.md` records the A-9 verification.

## Test requirements
- `tests/cache/test_keys_golden.py`: AC-1.
- `tests/cache/test_keys.py`: AC-2..AC-8, AC-10.
- `tests/cache/test_fingerprint.py`: AC-9.

## Risks
- **GV-1 drift caused by a legitimate upstream change** (for example, `build_prompt` wording).
  Mitigation: update GV-1 in the HLD with the reason, and decide on a `KEY_SCHEMA_VERSION` bump.
  The task's reviewer signs off.
- **The env allowlist misses a behaviour-relevant variable** (R-17). Mitigation: the A-9 check.

## Dependencies
- T-FJH6LI (types, safeio), T-OeRYSO (argv) and T-8tr1H4 (hashing).
- Development can start against the contracts. The GV-1 test needs all three.

## Pseudocode / Algorithm
```text
HLD §8.2.5 (CliVersionReader, claude_cli_fingerprint, guarded_resolve/guarded_abs) and §8.2.6
(build_cache_key, summary_from_doc) verbatim. canonical_json comes from types.
```

## Schemas / Interface Notes
- **Interface:** `build_cache_key(req: KeyRequest, deps: KeyDeps, *, preseed=None) -> CacheKey`.
- **Spec / data schema:** key schema v1 (HLD §8.2.7); `KeySummary` (HLD §8.4.2).

## Handoff Boundary
- **Upstream:** T-FJH6LI, T-OeRYSO, T-8tr1H4.
- **Downstream:** T-gDNjN2.

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-uoYW6b-cache-key-builder/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Key builder with golden vector
  GV-1.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 changes:
  - **Moved out:** hashing and repo state moved to T-8tr1H4.
  - **Added to the key:** argv (via T-OeRYSO) and the executor fingerprint (critic #4,
    reviewer R7).
  - **New path rules:** sensitive outputs (security S3); `guarded_resolve` catches
    `RuntimeError`/`ValueError` (developer #5); `summary_from_doc` is defined here.
  - **GV-1 is now Rev 2** (`6646469e…`). Rev 1 `536533b0…` is superseded.
