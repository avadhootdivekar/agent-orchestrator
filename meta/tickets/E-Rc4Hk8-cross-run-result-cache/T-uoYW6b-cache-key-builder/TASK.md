# TASK: T-uoYW6b-cache-key-builder

## Metadata
- Task ID: `T-uoYW6b-cache-key-builder`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev A)
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 3; implemented)
- Status: `Done`
- Estimate: `20 focus hours (2.5 days)` · Sprint 1 → 2

## Requirements Mapping
- Requirement IDs: FR-3, FR-4 (path rules), NFR-3, NFR-10 (M-2, M-5, M-14)
- HLD: §8.2.5 (fingerprint), §8.2.6 (`build_cache_key`, `summary_from_doc`, N-1..N-8, residual
  notes), §8.2.7 (key schema v1, GV-1 Rev 2)
- ADR-0019: D3, D4, D5, D6, D7, D21, D29, D30

## Description
Turn "what this agent would be asked to do" into a deterministic sha256 key, with a
non-sensitive summary and per-component digests.

1. **`cache/fingerprint.py`** (HLD §8.2.5):
   - `CLAUDE_FINGERPRINT_ENV_VARS` (deliberately **small and closed**; adding a name needs
     review; never a secret) and `CLAUDE_CONTEXT_PATHS`;
   - `CliVersionReader.version(binary)`: memoized **per binary identity** (resolved path,
     `st_mtime_ns`, `st_size`), so an auto-updated binary is re-read; 10 s timeout; any failure →
     `executor_fingerprint_unavailable`;
   - `claude_cli_fingerprint(req, deps, budget)`;
   - the shared path guards `guarded_resolve` and `guarded_abs` (`RuntimeError`/`ValueError`
     from `Path.resolve` → `path_rejected`).
2. **`cache/keys.py`** (HLD §8.2.6):
   - `build_cache_key(req, deps, *, preseed=None) -> CacheKey`, following the pseudocode
     **verbatim**; it projects `constants.AGENT_KEY_FIELDS` (imported, not redefined);
   - `summary_from_doc(doc, components) -> KeySummary` (never argv, prompt, prompt template or
     `extra_args`; D21);
   - re-export `canonical_json` from `types`.
3. **A-9 TODO.** Check `CLAUDE_FINGERPRINT_ENV_VARS` against the installed Claude CLI's
   documented environment variables; record the result in `STATUS.md`.

## File scope (exclusive)
- `src/agent_orchestrator/cache/fingerprint.py`, `src/agent_orchestrator/cache/keys.py` (new)
- `tests/cache/test_fingerprint.py`, `tests/cache/test_keys.py`, `tests/cache/test_keys_golden.py` (new)

## Inputs / Outputs
- **Inputs:** T-FJH6LI (types, safeio, constants), T-OeRYSO (`build_claude_argv`), T-8tr1H4
  (hashing), `executors.prompt.build_prompt`, `LocalFsArtifactStore.resolve`.
- **Outputs:** `build_cache_key` and `CacheKey`, consumed by T-gDNjN2.

## Acceptance Criteria
Test ids follow HLD §18.1.

1. **U-K1 (GV-1 Rev 2, exact).** Key
   `6646469e94a695fe1a994d35f54ca74e007262911255552b03c54ce2e5d0319f` and **every** component
   digest: agent `ced58570dab6`, argv `315cfbdef1cf`, dynamic_inputs `4f53cda18c2b`,
   executor_fingerprint `620dc66502c3`, general_instructions `80c58b832f5c`, inputs
   `6518d7ac7319`, instruction `8f7ad8e7e8d2`, key_schema `6b86b273ff34`, outputs `2789b49e50b4`,
   prompt `25e61c2662b6`, repo_heads `b1e77f36ac12` (setup per HLD §8.2.7).
2. **U-K2.** 100 builds give one key; the same tree under another absolute path gives the same
   key.
3. **U-K3 (sensitivity).** Each of these alters the key: instruction, general instruction, input
   or dynamic-input content; the output set; an output prior; repo heads; model, effort or
   `max_turns`; `prompt_template`, `command_template` or `extra_args`; `disallowed_tools`;
   `working_dir`; argv construction (`EFFORT_MAX_TURNS` monkeypatched); the CLI version; an
   allowlisted env var; `CLAUDE.md`, `.claude/agents/x.md` or `.mcp.json`; input order.
4. **U-K4 (insensitivity).** None of these alters the key: task or run id; `timeout_seconds`,
   `retries`; `depends_on`, `touches`, `skip_if_outputs_exist`, `join`; `forbidden_task_models`;
   `ANTHROPIC_API_KEY` (which never appears in the key document); the workspace path.
5. **U-K5.** Normalization N-1…N-4 and N-6…N-8 (one test each).
6. **U-K6.** Preseed N-5: an input that is also an output uses the prior digest; the recompute
   with the preseed equals the lookup key.
7. **U-K7.** `build_prompt` output is invariant to `run_id`/`task_id` for the normalized context.
8. **U-K8 (tripwire).** `AGENT_KEY_FIELDS | AGENT_NON_KEY_FIELDS == set(AgentSpec.model_fields)`,
   with a message telling the developer to classify the field (`constants.py`) and consider
   `KEY_SCHEMA_VERSION`.
9. **U-K9.** `path_rejected` (traversal, symlink loop, NUL, `CLAUDE.md` symlink out of the
   workspace), `path_in_cache_dir`, `duplicate_output`.
10. **U-K10.** `sensitive_output` (including an output symlinked into `.git/hooks`),
    `control_output`, `output_not_regular_file`.
11. **U-K11.** `prompt_render_error` (unknown placeholder) and `key_encoding`.
12. **U-K12 (D21).** A secret placed in `extra_args` is absent from
    `KeySummary.model_dump_json()`; argv, prompt and templates are absent.
13. **U-F1..F5.** `CliVersionReader` is memoized (one subprocess call for two lookups) **and
    re-reads after the binary's mtime or size changes**; a missing binary, a timeout or a
    non-zero exit → `executor_fingerprint_unavailable`; the env allowlist copies only listed
    vars; absent context files appear as `kind: absent`; a symlink inside `.claude/agents` makes
    the task uncacheable; for `executor: fake`, `argv` and `executor_fingerprint` are `null` and
    no subprocess runs.
14. **Hygiene.** The AST guard passes; ruff (≤ 100 columns) and mypy are clean; `pytest -q` has
    no new failures; `STATUS.md` records the A-9 check.

## Test requirements
- `tests/cache/test_keys_golden.py`: AC-1.
- `tests/cache/test_keys.py`: AC-2..AC-12.
- `tests/cache/test_fingerprint.py`: AC-13.

## Risks
- **GV-1 drift from a legitimate upstream change** (for example `build_prompt` wording).
  Mitigation: update GV-1 in the HLD with the reason and decide on a `KEY_SCHEMA_VERSION` bump.
- **The env allowlist misses a behaviour-relevant variable** (R-17). Mitigation: the A-9 check.

## Dependencies
- T-FJH6LI, T-OeRYSO, T-8tr1H4.

## Pseudocode / Algorithm
```text
HLD §8.2.5 and §8.2.6 verbatim; AGENT_KEY_FIELDS / AGENT_NON_KEY_FIELDS come from constants.
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
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Key builder with GV-1.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2: argv and fingerprint in
  the key; sensitive outputs; GV-1 Rev 2.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 (early-gate C; manager B):
  `claude --version` memoized by binary identity; small closed env allowlist; AgentSpec field
  sets moved to `constants`; every test id U-K1…U-K12 now owned explicitly. GV-1 unchanged.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done. Delivered as commit
  `95dab70`. All 14 acceptance criteria pass (GV-1 exact); evidence and the A-9 check in
  `STATUS.md`, frozen names and the four small deviations in `HANDOFF.md`.
