# STATUS

- ID: `T-3FlD46-security-review-hardening`
- Updated At: 2026-09-26
- State: Done
- Owner: dev-security

## This update
- Ticket created by the architect design pass (Rev 2, after the Phase-4 consultations). Sprint: S2 (starts when the checkers merge).

By: architect · Role: architect · Date: 2026-09-26 · Comment: Created from `docs-md/overseer-runner-hld.md` Rev 2. The ACs are pass/fail and agent-executable, and the task is sized ≤3 days. Not started.

By: dev-security · Role: reviewer · Date: 2026-09-26 · Comment: Code-level review complete against HLD §23.3 row 5 and §8.3. Every design-level finding traced by hand (not docstring-only) in the shipped `overseer_tool.py` (post T-HPJcc6/T-tAKBBB, M3b checkers already landed). One new HIGH-equivalent gap found and fixed (unbounded `read_ledger_lines`, a size-cap bypass squarely inside this task's own checklist); two new LOW/MEDIUM hardening notes filed as follow-ups (not fixed here — no CRITICAL/HIGH left open). One genuinely missing test case added (real-byte 1.5 MiB breadcrumb) plus regression coverage for the new fix. All ACs met.

### Findings table (HLD §23.3 row 5 design-level findings)

| # | Severity (design) | Finding | Location | Status |
|---|---|---|---|---|
| 1 | CRITICAL | `instruction`/`agent` not pinned -> a unit's manifest entry could redirect to arbitrary workspace content | `_check_entry_r6_brief` (overseer_tool.py:3229-3288) | **Verified-implemented.** Plain `!=` string equality against `kind_map[kind]["agent"]`/`["instruction"]` loaded from `overseer-config.json` (`load_config`, :604-673), both directions checked, no fuzzy/partial match. Test: `tests/test_overseer_tool_checker_structural.py::test_r6_instruction_points_at_workspace_content_not_kind_map` (ticket AC2's exact scenario) + `test_r6_agent_does_not_match_kind_map`. |
| 2 | HIGH | Hold-request deletion undetectable (could bypass the hold gate) | `hold_gate` (overseer_tool.py:1333-1412) | **Verified-implemented.** Ledger's last `checkpoint` event decided `hold` + no `hold_answered` after it + `hold-request.json` missing -> `Violation("INT-2")`, fail closed. Also handles a corrupted-but-present request file (INT-2) and a stray/forged request with no backing hold decision (INT-4). Tests: `tests/test_overseer_tool_gates.py` (`INT-2` at lines 266/281, `INT-4` at 168/299/565). |
| 3 | MEDIUM | Ledger rewritable | `append_chained`/`verify_ledger_chain` (overseer_tool.py:809-843) | **Verified-implemented as designed (tamper-evident, not tamper-proof — NFR-X11 accepted residual).** Traced the hash chain by hand: each line's `prev_sha256` = sha256(canonical_json(previous line INCLUDING that line's own `prev_sha256` field)), so editing line N invalidates line N's own hash, which line N+1 still references -> mismatch at N+1 unless every later line is also rewritten. Test: `tests/test_overseer_tool_ledger.py::test_verify_ledger_chain_fails_when_an_earlier_line_is_edited` edits a semantic field on an earlier line and confirms `INT-3`. |
| 4 | MEDIUM | Override forgeable | `effective_budget` (overseer_tool.py:1130-1218, was ~1130 pre-edit) | **Verified-implemented.** A `control/budget-override.json` is honored ONLY when `state.breaker_overrides["run-budget-backstop"]` (sourced from the engine-owned `state.json`, loaded via `load_state`) is `>= requested`; a forged override file alone is refused with `reason: "no matching --extend-breaker run-budget-backstop"` and `honored: False`. Test: `tests/test_overseer_tool_budget.py::test_ac1g_refused_override_without_matching_extension`. Note: this tool's own coupling logic is correct; whether `state.json` itself is protected from a rogue task's direct write is an ENGINE-level trust boundary (already called out in NFR-X11 as "hard containment is engine-side") and is out of this tool-level ticket's scope. |
| 5 | MEDIUM | `changed_paths` confinement | `classify_path_entry` (overseer_tool.py:408-433), `_confine_repo_relative` (overseer_tool.py:~1760) | **Verified-implemented.** Rejects (excludes from hashing, non-fail-closed per design intent "one LLM path typo must not halt the run"): unknown `repo_id`, absolute `rel_path`, `..` segment, and symlink-resolved escape of the repo root (`Path.resolve()` + `is_relative_to`). `_confine_repo_relative` applies the same discipline to git-derived paths. Tests: `test_classify_path_entry_rejects_unknown_repo_id`, `_rejects_absolute_path`, `_rejects_path_traversal`, `_rejects_symlink_escaping_repo_root` (ledger.py); `test_confine_repo_relative_rejects_symlink_escape`, `_rejects_traversal_and_accepts_nested_path` (detectors.py). |
| 6 | LOW | Argv injection (template substitution into JSON) | `src/agent_orchestrator/templates/__init__.py::_render`/`_json_escape_value` (core engine file, read-only check per scope boundary — not part of this ticket's editable surface) | **Verified-implemented.** `_render` is called with `escape_json=True` whenever the render target's suffix is `.json` (confirmed at `templates/__init__.py:681,883`), so `workflow.json.tmpl` -> `workflow.json` substitutions are JSON-string-escaped. Confirmed separately that every hook `command` array in `workflow.json.tmpl` is built only from `{{ params.python_bin }}`/`{{ workspace_root }}`/`{{ instance_dir }}` plus literal subcommand/flag strings — no runtime-agent-controlled value ever reaches a hook's argv. |

### New findings from this review (not in the original §23.3 list)

| # | Severity | Finding | Location | Status |
|---|---|---|---|---|
| N1 | **HIGH (fixed here)** | `read_ledger_lines` had NO size cap — every other agent-touchable JSON read in the tool goes through `read_json_bounded`'s stat-before-read discipline (NFR-6: `JSON_MAX_BYTES`=1 MiB, `STATE_MAX_BYTES`=16 MiB), but `outputs/ledger.jsonl` (fully agent-writable, per NFR-X11: no inter-task trust boundary) was read line-by-line with no total-file-size or per-line bound. A single oversized line (no trailing newline) would be materialized fully in memory before any parse/reject could happen; called from every `ckpt-prep`, `hold_gate`, and `effective_budget` invocation. | `read_ledger_lines` (overseer_tool.py, was :789-806, now :798-822) | **Fixed.** Added `LEDGER_MAX_BYTES` (32 MiB, generous headroom over any real run per CFG-3's own fan-out math) and a `path.stat().st_size` check before opening the file, raising `Violation("INT-3")` (fail-closed, consistent with the existing "malformed ledger line -> INT-3" semantics) on an oversized ledger. See diff below. Tests added: `tests/test_overseer_tool_security.py::test_read_ledger_lines_oversize_file_is_int3`, `_many_small_lines_over_cap_is_int3`, `_under_cap_still_parses_normally`. Full existing 6-file suite (332 tests) still passes with zero regressions. |
| N2 | LOW/informational (follow-up, not fixed) | `overseer-config.json`'s `kind_map` (the pin source for OV-R6, finding #1 above) is not integrity-locked the way `charter.json`/`prompt.md` are (`verify_charter_lock`/`charter.lock.json`, INT-1). A task with full workspace write could in principle rewrite `overseer-config.json` between checkpoints so that `kind_map[kind]` itself matches whatever `agent`/`instruction` it wants, defeating the R6 pin at its source rather than at the manifest-entry comparison (which is itself correctly implemented, see #1). This is an instance of the already-accepted NFR-X11 residual (no inter-task trust boundary), not a new capability, and is a config-lock design addition beyond this ticket's "verify + minimal fix" scope. | `load_config`/`verify_charter_lock` (overseer_tool.py) | **Filed as follow-up**, not fixed here (see "Follow-up" below). |
| N3 | LOW/hygiene (not fixed) | `_git_changed_paths`'s `git diff --name-only <prev_head>` call passes `prev_head` (sourced from `outputs/overseer/path-history.json`'s persisted `repo_heads`, itself workspace-writable) as a bare argv element with no `--` separator and no format validation (e.g. `^[0-9a-f]{4,64}$`). A crafted `repo_heads` value starting with `-` (e.g. `--output=<path>`) could in principle be parsed by git as an option rather than a revision. Not exploitable as a NEW privilege escalation in this threat model: any task capable of writing `path-history.json` already has direct Bash/tool execution as the same OS user, i.e. strictly greater capability already, so this doesn't cross a trust boundary the design relies on. Worth closing defensively (add `--` before the revision arg, or validate against a hex-SHA regex) as cheap hardening. | `_git_rev_parse_head`/`_git_changed_paths` (overseer_tool.py:~1699-1758) | **Filed as follow-up**, not fixed here. |

### Additional test cases (ticket AC2 checklist)

| Case | Status | Test(s) |
|---|---|---|
| Symlink escape | Already covered | `test_classify_path_entry_rejects_symlink_escaping_repo_root`, `test_confine_repo_relative_rejects_symlink_escape` |
| `repo_id` spoof | Already covered | `test_classify_path_entry_rejects_unknown_repo_id` |
| `..` and absolute paths | Already covered | `test_classify_path_entry_rejects_path_traversal`, `test_classify_path_entry_rejects_absolute_path` |
| 1.5 MiB breadcrumb (rejected, bounded read) | **Missing -> added** | `tests/test_overseer_tool_security.py::test_breadcrumb_over_1mib_real_bytes_is_rejected_bc1` (existing `test_read_breadcrumb_oversize_is_bc1` only monkeypatched `JSON_MAX_BYTES` down to 1 byte, never exercised the real 1 MiB threshold with a real file) |
| Ledger chain rewrite (INT-3) | Already covered | `test_verify_ledger_chain_fails_when_an_earlier_line_is_edited` |
| Deleting `hold-request.json` after a hold decision (INT-2) | Already covered | `tests/test_overseer_tool_gates.py` (line ~277-281) |
| Forged request, no hold decision (INT-4) | Already covered | `tests/test_overseer_tool_gates.py` (lines 168, 299, 565) |
| Forged override without matching extension (refused) | Already covered | `test_ac1g_refused_override_without_matching_extension` |
| `instruction` redirection (OV-R6) | Already covered | `test_r6_instruction_points_at_workspace_content_not_kind_map` |
| *(new, from N1 above)* Unbounded ledger file / DoS | **New gap -> added** | `tests/test_overseer_tool_security.py::test_read_ledger_lines_oversize_file_is_int3`, `_many_small_lines_over_cap_is_int3`, `_under_cap_still_parses_normally` |

### `grep` evidence — no dangerous patterns

```
$ grep -n "shell=True" overseer_tool.py   -> (no matches)
$ grep -n "eval("      overseer_tool.py   -> (no matches)
$ grep -n "exec("      overseer_tool.py   -> (no matches)
$ grep -n "pickle"     overseer_tool.py   -> (no matches)
$ grep -n "os\.system" overseer_tool.py   -> (no matches)
```
Both `subprocess.run` call sites (`_git_rev_parse_head`, `_git_changed_paths`) use an argv list, `timeout=GIT_TIMEOUT_S`, `check=False` with explicit `returncode`/exception handling, and no shell string anywhere in the file.

### `pip-audit`
Not applicable — `overseer_tool.py` is stdlib-only (`argparse, contextlib, hashlib, json, math, os, re, statistics, subprocess, sys, tempfile, collections.abc, dataclasses, datetime, pathlib, typing`), no third-party dependencies, no `requirements`/lockfile entry to audit.

## Evidence
- `uv run pytest -q tests/test_overseer_tool_security.py -v` — 5 passed.
- `uv run pytest -q tests/test_overseer_tool_budget.py tests/test_overseer_tool_checker_semantic.py tests/test_overseer_tool_checker_structural.py tests/test_overseer_tool_detectors.py tests/test_overseer_tool_gates.py tests/test_overseer_tool_ledger.py` — 332 passed, zero regressions from the `read_ledger_lines` fix.
- `uv run ruff check` / `uv run ruff format --check` / `uv run mypy` / `uv run pyright` on both the new test file and the edited `overseer_tool.py` — all clean.
- Diff: `LEDGER_MAX_BYTES` constant added (overseer_tool.py, near `JSON_MAX_BYTES`/`STATE_MAX_BYTES`) + a `path.stat().st_size` guard at the top of `read_ledger_lines` raising `Violation("INT-3")` on an oversized ledger file.

## Risks / Blockers
- No open CRITICAL/HIGH findings at close (N1 was HIGH-equivalent and is fixed).
- N2 (config-lock gap for `kind_map`) and N3 (git argv hygiene) are LOW/MEDIUM residual, filed as follow-ups below — both are instances of the already-accepted NFR-X11 residual risk (no inter-task trust boundary), not new privilege boundaries.

## Next actions
1. Downstream T-gbccdr: fold this STATUS.md's findings table into the README/HLD security notes.
2. Follow-up (not blocking, file as a new ticket if the epic continues past MVP): extend `charter.lock.json`/`verify_charter_lock` (or a sibling lock file) to also pin `overseer-config.json`'s `kind_map` at intake time, closing N2.
3. Follow-up (cheap hygiene, not blocking): add a `--` separator (or a hex-SHA format check) before the revision argument in `_git_changed_paths`'s `git diff --name-only <prev_head>` call, closing N3.
