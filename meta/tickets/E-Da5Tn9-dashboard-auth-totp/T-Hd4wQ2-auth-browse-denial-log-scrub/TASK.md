# TASK: T-Hd4wQ2-auth-browse-denial-log-scrub

## Metadata
- Task ID: `T-Hd4wQ2-auth-browse-denial-log-scrub`
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Owner: `developer` (lane Q)
- Created: `2026-10-05`
- Last Updated: `2026-10-05`
- Status: `Draft`
- Estimate: `2 days` · Sprint `S1`

**Origin (v2.1, HLD §24 and §28.9):** created when the independent gates were folded in. The gate
fixes pushed three capped tasks over the 3-day limit, and the cross-epic review (design-review M1)
asked for the file-browser `denied_paths` mechanism to **land first** so the approval-gates epic can
reuse it. This task therefore takes:
- the file-browser denial, from `T-jVqH8w-ao-ui-auth-wiring`;
- the denial helpers in `auth/paths.py` (`default_denied_paths`, `entry_is_denied`), from
  `T-8NQP8J-auth-user-store`;
- `auth/scrub.py`, from `T-CsT5gk-auth-throttle-audit-scrub`.

## Requirements Mapping
- Requirement IDs: FR-10 / FR-24 (the store, state directory and `service.env` are never
  browsable), FR-20 (log redaction, defence in depth), NFR-1 (the one deliberate auth-off change),
  NFR-7.
- ACs: AC-13 (denial part, incl. `service.env`); unit support for AC-24 (redaction).
- Invariants: S11 (redaction part), S12.
- Design: HLD §11.4 (`paths.py` denial helpers and the file-browser pseudocode), §11.3.5
  (`denied_paths` rule), §11.13 (`scrub.py`), §16 rows 2 and 3 and cross-epic rows X2 and X5,
  §3.5 NFR-1, §6.4 S12; §28.9 (security L7, design-review M1/M2); ADR-0021 D3.

## Description
1. **`auth/paths.py` additions** (appended after T-8NQP8J has merged the module; HLD §11.4):
   - `default_denied_paths(env=None) -> tuple[Path, ...]`: resolved, de-duplicated
     `{xdg store, xdg state, $AO_AUTH_DIR if set, $AO_AUTH_STATE_DIR if set,
     <home>/.config/ao/service.env, $XDG_CONFIG_HOME/ao/service.env if set}`. The two
     `service.env` entries are new in v2.1 (security L7: that file holds API keys). Build the path
     from `SERVICE_ENV_RELATIVE_PATH` (§12.6); no string literal.
   - `entry_is_denied(parent_resolved, entry, denied) -> bool`: resolves **only** symlink entries
     (`entry.is_symlink()`); for everything else it checks `parent_resolved / entry.name`
     (reviewer R-12).
   - Both use the existing `is_within` (T-8NQP8J). Do not change anything else in `paths.py`.
2. **`ui/files.py`** (ledger row 2; generic and epic-neutral):
   - `FileBrowser.denied_paths: list[str] = field(default_factory=list)`, resolved once in
     `__post_init__`.
   - **One** helper `FileBrowser._is_denied(resolved: Path) -> bool`
     (`any(is_within(resolved, d) for d in self._denied)`), called in `resolve()` **after** the
     existing `target.resolve()` and containment check. A denied path raises the generic
     `PathNotAllowedError(f"path is not browsable: {rel_path}")`; the message never says "auth".
   - `list_dir` omits denied entries through `entry_is_denied`.
   - The docstring of `_is_denied` says that other epics add their predicate **here** (one helper),
     never as a second independent check (cross-epic row X2; the approvals epic's
     `.orchestrator/runs/<id>/approvals/` denial goes through it).
3. **`ui/service.py`** (ledger row 3): `DashboardService.__init__(..., denied_paths: list[str] | None = None)`.
   The default `FileBrowser` gets `denied_paths` when given, otherwise
   `[str(p) for p in default_denied_paths()]`, so every direct construction in tests is protected.
4. **`auth/scrub.py`** (moved from T-CsT5gk; HLD §11.13, unchanged content):
   - `REDACTED`, `SECRET_PATTERNS` (the v2 list: cookie values, `X-AO-Session-Proof` values,
     `otpauth://` URIs, `$scrypt$` hashes, 32-character Base32 secrets, recovery codes and
     enrollment tokens, and the `password` / `current_password` / `new_password` / `code` /
     `recovery_code` / `enrollment_token` / `secret` / `token` / `session_proof` key-value forms),
     `redact()`;
   - `SecretRedactingFilter` and `auth_logger(name)` (idempotent: one filter per logger);
   - `install_log_redaction()`: a process-wide `logging.setLogRecordFactory` wrapper that redacts
     `record.getMessage()` once per record, chains to the previous factory, and is idempotent
     through a marker attribute (dev-security #11). An unformattable message becomes the fixed text
     `[unformattable log message]` plus one ERROR.
   - It needs only T-kzEzwy, so it can start on S1 day 2 while lane Q waits for T-8NQP8J.

## Inputs / Outputs
- **Inputs:** T-kzEzwy (`constants`, `errors`, `tests/auth/helpers/core.py`); T-8NQP8J (`paths.py`
  with `is_within`, `xdg` with `environ=`) for items 1–3 only.
- **Outputs:**
  - additions to `src/agent_orchestrator/auth/paths.py`
  - `src/agent_orchestrator/auth/scrub.py`
  - edits to `src/agent_orchestrator/ui/files.py` and `src/agent_orchestrator/ui/service.py`
  - `tests/ui/test_file_browser_denial.py`, `tests/auth/test_paths_denial.py`,
    `tests/auth/test_scrub.py`

## Acceptance Criteria
1. **Unchanged suites:** `tests/ui/test_files.py` and `tests/ui/test_service.py` pass
   **unmodified**; the whole `tests/ui` suite stays green.
2. **`default_denied_paths`** (`test_paths_denial.py`): with an injected env, the result is the
   resolved, de-duplicated set of the six sources listed in Description 1; `AO_AUTH_DIR` and
   `AO_AUTH_STATE_DIR` appear only when set; the `service.env` path is built from
   `SERVICE_ENV_RELATIVE_PATH`.
3. **`entry_is_denied`:** 0 `Path.resolve` calls for 100 regular entries (patched counter); a
   symlink entry that points into the store is denied; a sibling with a shared prefix
   (`/a/b` vs `/a/bc`) is not.
4. **Denial (AC-13 denial part; S12)**, with auth **off and on** (the auth-on variant builds the app
   with a stub runtime from `tests/auth/helpers/stub_runtime.py` if T-G7qByZ has landed, otherwise
   it constructs `DashboardService` directly). The workspace contains the store and state
   directories (`AO_AUTH_DIR` inside the workspace; state = `<store>/state`) and a
   `<ws>/.config/ao/service.env` reached through `$HOME` pointed at the workspace:
   - listing the store → 403 (the existing `PathNotAllowedError` mapping);
   - reading `users.json`, `state/lockouts.json`, `state/audit.jsonl` or `service.env` → 403;
   - an absolute path → 403;
   - a symlink `ws/link → store`, then `ws/link/users.json` → 403;
   - an HTML preview of a markup file inside the store → 403;
   - listing the parent omits the denied directories and the `service.env` file;
   - no error detail contains `auth`.
5. **Defaults:** `DashboardService(ws)` with no `denied_paths` denies the hermetic `AO_AUTH_DIR`,
   its `state` subdirectory, the XDG defaults and `service.env` (point `XDG_CONFIG_HOME`,
   `XDG_STATE_HOME` and `HOME` into the workspace to observe this).
6. **One helper (cross-epic X2):** `FileBrowser.resolve` calls `_is_denied` exactly once per
   request (spy), and no other deny check exists in `ui/files.py` (string check in the test).
7. **`redact()`** (`test_scrub.py`; moved from T-CsT5gk AC14): each sentinel is replaced with
   `[REDACTED]`, keeping its prefix: `ao_sid_8765=<43>` and `__Host-ao_sid_8765=<43>`;
   `X-AO-Session-Proof: <43>`; an `otpauth://…` URI; a `$scrypt$…` hash; a 32-character Base32
   secret; a recovery code and an enrollment token (`XXXX-XXXX-XXXX-XXXX`); `"password": "…"`,
   `password=…`, `current_password=…`, `new_password=…`, `code=123456`, `"session_proof": "…"`,
   `"enrollment_token": "…"`. Ordinary text (run ids such as `run-20261004-abc`, file paths) is
   byte-identical.
8. **Filters** (moved from T-CsT5gk AC15): `SecretRedactingFilter` attached through
   `auth_logger(__name__)` redacts a record logged with arguments; calling `auth_logger` twice
   attaches exactly one filter; after `install_log_redaction()`, a record from
   `logging.getLogger("uvicorn.error")` (with `propagate=False`) contains `[REDACTED]` and not the
   sentinel; a second `install_log_redaction()` does not double-wrap (marker) and the previous
   factory is still called (spy); an argument whose `__str__` raises yields
   `[unformattable log message]` and one ERROR.
9. **Layering:** `scrub.py` and the `paths.py` additions import only stdlib and L0/L1 modules
   (T-kzEzwy's AST test passes); `ui/files.py` and `ui/service.py` import only `auth.paths`
   (rule R3).
10. ruff and mypy are clean. Coverage of `scrub.py` and the new `paths.py` functions is ≥ 90 %.

## Risks
- **Cross-epic merge (X2).** The approval-gates epic also edits `FileBrowser.resolve`. Landing this
  task first, with one documented helper, is the agreed resolution; the manager relays it.
- **Regex false positives in `redact()`**, for example a 32-character upper-case run id. Accepted:
  redaction is defence in depth; document the false-positive classes in the module docstring.
- **`$HOME` workspace case.** The default store lives inside a `--workspace ~` dashboard; the
  denial makes it safe (AC 4–5).

## Dependencies
- **Upstream:** T-kzEzwy (all items); T-8NQP8J (items 1–3: `paths.py`, `is_within`, `xdg`).
- **Downstream:** T-jVqH8w (`AuthLaunch.denied_paths` uses `default_denied_paths`; `ao ui` passes
  them to `DashboardService`); T-U2ERMo (the scrub sweep uses `scrub.py`); T-otjIkJ (README); the
  approval-gates epic (adds its predicate to `_is_denied`, §16 X2).

## Pseudocode / Algorithm
HLD §11.4 (file-browser denial) and §11.13 (`scrub.py`). Task-level steps:

```text
default_denied_paths(env):
  env = env OR os.environ
  out = [xdg_default_store_dir(env), xdg_default_state_dir(env)]
  IF env.get(AO_AUTH_DIR_ENV): out.append(Path(env[AO_AUTH_DIR_ENV]).expanduser())
  IF env.get(AO_AUTH_STATE_DIR_ENV): out.append(Path(env[AO_AUTH_STATE_DIR_ENV]).expanduser())
  out.append(Path.home() / SERVICE_ENV_RELATIVE_PATH)
  IF env.get("XDG_CONFIG_HOME"):                              # same file under an XDG config home
     out.append(Path(env["XDG_CONFIG_HOME"]) / Path(SERVICE_ENV_RELATIVE_PATH).relative_to(".config"))
  RETURN tuple(dict.fromkeys(p.resolve() FOR p IN out))      # resolved, order-preserving de-dup

FileBrowser._is_denied(resolved):  RETURN any(is_within(resolved, d) FOR d IN self._denied)
FileBrowser.list_dir(...):         skip entry IF entry_is_denied(parent_resolved, entry, self._denied)
```

## Schemas / Interface Notes
- Signatures: HLD §11.4. `DashboardService(..., denied_paths=None)` is keyword-only and additive.
- No new constants beyond `SERVICE_ENV_RELATIVE_PATH` (T-kzEzwy defines it, §12.6).

## Handoff Boundary
- **Upstream:** the foundation and the store's `paths.py`.
- **Downstream:** a generic, early-landed browse-denial mechanism and the log redaction that
  `prepare_auth` installs.

## Verification

```
python -m pytest -q tests/auth/test_paths_denial.py tests/auth/test_scrub.py tests/ui/test_file_browser_denial.py tests/ui
python -m pytest -q tests/auth --cov=agent_orchestrator.auth.scrub --cov=agent_orchestrator.auth.paths --cov-report=term-missing
ruff check src tests && ruff format --check src tests && mypy src
```

## Artifacts
- `meta/tickets/E-Da5Tn9-dashboard-auth-totp/T-Hd4wQ2-auth-browse-denial-log-scrub/`
