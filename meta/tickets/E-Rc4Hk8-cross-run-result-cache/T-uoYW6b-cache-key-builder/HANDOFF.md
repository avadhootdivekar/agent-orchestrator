# HANDOFF: T-uoYW6b-cache-key-builder

- Task: `T-uoYW6b-cache-key-builder`
- State: `Done (handoff available)`
- From: `developer` (Dev A)
- To: T-gDNjN2 (and T-JCOAsq for the adversarial key tests)
- Commit: `95dab70` on branch `worktree-agent-a18ce2c08e42a3a5a`.

## What was delivered
- `agent_orchestrator.cache.fingerprint`:
  - `CLAUDE_FINGERPRINT_ENV_VARS` (9 names, closed), `CLAUDE_CONTEXT_PATHS` (8 paths);
  - `CliVersionReader().version(binary) -> str` (memo per `(realpath, mtime_ns, size)`; a failure is
    never memoized); pass the bound method as `KeyDeps(cli_version_of=reader.version)`;
  - `claude_cli_fingerprint(req, deps, budget) -> {"cli_version", "env", "context_files"}`;
  - `guarded_resolve(req, raw)` and `guarded_abs(req, abs_path)` (the shared path guards).
- `agent_orchestrator.cache.keys`:
  - `build_cache_key(req, deps, *, preseed=None) -> CacheKey` (`preseed` is any
    `Mapping[str, Digest | None]`, normally the earlier `CacheKey.preseed`);
  - `summary_from_doc(doc, components) -> KeySummary`;
  - `canonical_json` re-exported from `types` (the same object).
- Tests: `tests/cache/test_keys_golden.py` (2), `test_keys.py` (88), `test_fingerprint.py` (37), plus
  the shared fixture module `tests/cache/keys_fixture.py` (GV-1 workspace, `make_request`,
  `key_for`) that T-gDNjN2 / T-JCOAsq tests may reuse.

## Frozen names / contracts
- Key schema v1 and GV-1 Rev 2: key `6646469e...0319f` and the eleven component digests. A change
  needs an HLD §8.2.7 update and a decision on `KEY_SCHEMA_VERSION`.
- Refusals are `UncacheableError` with the existing `REASON_*` constants only:
  `path_rejected`, `path_in_cache_dir`, `duplicate_output`, `sensitive_output`, `control_output`,
  `output_not_regular_file`, `input_*`, `prompt_render_error`, `key_encoding`,
  `executor_fingerprint_unavailable`.
- `CacheKey.preseed` maps ABSOLUTE output paths to `Digest | None`; `output_abs` maps the relative
  path to the absolute one; `components` has one 12-hex entry per top-level document field.

## Deviations from the HLD code blocks (reason)
1. **`key_encoding` also covers an over-bound summary.** `summary_from_doc` builds a pydantic
   `KeySummary`; a `ValidationError` (for example more than `MAX_LIST_ITEMS` inputs) would otherwise
   escape `build_cache_key` as an unexpected exception. It is mapped to
   `UncacheableError(key_encoding, "summary_out_of_bounds")`: such a key could never be stored
   anyway, so it is fail-closed, never a silent partial entry.
2. **Empty `command_template` -> `executor_fingerprint_unavailable`** (the HLD would raise
   `IndexError` at `command_template[0]`).
3. **A-9 env allowlist unchanged** (the list is closed). The check against the documented CLI
   variables, and candidates for a reviewer decision, are recorded in `STATUS.md`.
4. `tests/cache/keys_fixture.py` is a new shared test helper outside the ticket's three listed
   test files (it only holds fixtures; no test lives in it).

## Verification the receiver should run
- `pytest -q tests/cache/test_keys_golden.py tests/cache/test_keys.py tests/cache/test_fingerprint.py`
  (127 tests)

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: AgentSpec field sets come
  from `constants`.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done; handoff available
  (commit `95dab70`). Deviations 1-4 above are small and documented; GV-1 is reproduced exactly.
