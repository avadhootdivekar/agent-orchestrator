# STATUS

- ID: `T-Iv3Kq8-flavor-install-script`
- Updated At: `2026-07-30`
- State: `Done`
- Owner: `claude` (install agent)

## This update
- By: architect · Role: architect · Date: 2026-07-30 · Comment: Task ticket scaffolded while the
  install agent implements. Scope: CONTRACT §2 in full — 2.1 flavor selection (`AO_FLAVOR` +
  `--flavor`, flag wins, invalid → non-zero), 2.2 namespacing (commands, uv tool dir/bin, commit
  stamp, `exec`-ing beta shims, flavor-parameterized staleness probe), 2.3 shared-not-namespaced
  `.ao/config.yaml` and `AO_*`, 2.4 fake-`uv` installer tests — plus `README.md` and
  `docs-md/dual-flavor-install.md` (both owned here; the architect deliberately did not write the
  flavors doc).
- By: install agent · Role: developer · Date: 2026-07-30 · Comment: Implementation complete.
  `install.sh` parses `--flavor`/`AO_FLAVOR` (flag wins), rejects invalid values, and derives
  `COMMAND_NAME`/`BENCH_COMMAND_NAME`/`COMMIT_STAMP`/`FLAVOR_LABEL` once from the validated
  flavor. Every stable-branch message is built by substituting these variables into the exact
  original strings (`COMMAND_NAME="ao"`, `FLAVOR_LABEL=""` for stable), so stable output is
  byte-identical by construction rather than by a parallel code path. `_installed_has_routing`
  takes `flavor` as an explicit parameter and is the single definition in the file (`grep -c
  '^_installed_has_routing()' install.sh` → `1`). Beta sets `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR` only
  as an inline prefix on the one `uv tool install` call; stable never sets them. Beta shims
  (`_beta_shim`, `_install_beta_shims`) write+`chmod +x` two heredoc-generated scripts to
  `$HOME/.local/bin/` that `exec` the beta venv's own `ao`/`ao-bench` and forward `"$@"`;
  overwritten (not appended) on every install, so re-running is idempotent. Doc renamed to
  `docs-md/install-flavors.md` to match this ticket/epic's naming (was briefly named
  `docs-md/dual-flavor-install.md`), and now also covers the `$HOME/.local/bin` `PATH`
  requirement and how to tell flavors apart when debugging (AC-24 gaps closed).
  **One real bug found and fixed during testing**: `local shim_name="$1" target="$2"
  shim_path="$HOME/.local/bin/$shim_name"` failed under `set -u` with `shim_name: unbound
  variable` — bash expands all RHS values in a multi-assignment `local` statement before binding
  any of them, so a later assignment cannot see an earlier one in the same statement. Fixed by
  splitting into separate `local` statements (with a comment recording why, so it isn't
  reintroduced). This was caught by the new test suite, not by inspection.

## Evidence
- `pytest -q tests/test_install_script.py` → **11 passed** (0.3–0.45s).
- `pytest -q` full suite → **1662 passed, 7 skipped** (73.3–73.8s).
- `pytest -q --ignore=tests/test_install_script.py` (new file excluded) → **1651 passed, 7
  skipped** — delta is exactly the 11 added tests; the rest of the suite is unaffected (no
  regressions).
- `ruff check .` → **All checks passed** (repo-wide; install.sh is bash and outside ruff's
  Python-file discovery in directory mode — confirmed by NOT passing it explicitly, which
  incorrectly force-lints it as Python and was a self-inflicted false alarm during verification,
  not a real issue).
- `ruff format --check .` → `tests/test_install_script.py` needed one reformat, applied
  (`ruff format tests/test_install_script.py`); re-checked clean. Two unrelated files
  (`src/agent_orchestrator/ui/htmlpreview.py`, `tests/test_e2e_builtin_routed_runner.py`) also
  flagged — both outside this task's file set and untouched by it (`git diff --stat` shows zero
  changes to either), owned by the concurrent Epic-1 backend workstream on the same branch.
- `mypy tests/test_install_script.py` → **Success: no issues found in 1 source file.**
- **Stable-unchanged demonstration (AC-22 — closed):**
  - Ran the pre-change script (`git show HEAD:install.sh`) and the new script side by side, same
    fake-`uv`, same fresh temp `HOME`, both `bash install.sh --yes`, no `AO_FLAVOR`.
  - Fake-`uv` argv/env log: **identical** apart from the incidental scratch-copy directory name
    baked into the `uv tool install <dir>[ui] --force --reinstall` argument (an artifact of
    copying the two script versions to differently-named directories for the comparison, not a
    behavioral difference) — `UV_TOOL_DIR=<unset>` and `UV_TOOL_BIN_DIR=<unset>` in **both** runs,
    for **every** uv invocation (`--version`, `tool dir`, `tool install`).
  - Stamp file path/name: `$STATE_DIR/installed.commit` in both; **content byte-identical**
    (`diff` exit 0).
  - stdout: identical apart from that same incidental path substring; stderr: **byte-identical**.
  - Also covered by `tests/test_install_script.py::test_default_flavor_is_stable_and_matches_pre_flavor_behavior`,
    which asserts the same properties against the real repo on every test run (not just this
    one-off comparison).
- **Beta-path evidence (AC-7/8/11/13 — closed), from
  `tests/test_install_script.py::test_beta_flavor_via_env_var_is_fully_namespaced`:**
  - `UV_TOOL_DIR=<HOME>/.local/share/ao-beta/uv-tools`,
    `UV_TOOL_BIN_DIR=<HOME>/.local/share/ao-beta/bin` captured on the `tool install` call.
  - `beta.commit` written with the correct source commit; `installed.commit` absent.
  - Both shims (`ao-beta`, `ao-bench-beta`) exist, are executable
    (`os.access(path, os.X_OK)` true), and their content is exactly
    `exec "<beta-bin-dir>/ao" "$@"` / `exec "<beta-bin-dir>/ao-bench" "$@"`.
  - `test_beta_reinstall_is_idempotent`: shim byte content and `beta.commit` content identical
    across two consecutive `--yes` runs.
  - `test_disjoint_paths_between_flavors_under_same_home`: both stamp files coexist under one
    `$STATE_DIR`; beta's `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR` differ from stable's (`<unset>`) and from
    each other's beta-vs-stable values.
- Manual runs against the real environment (never installs anything — `--help` and `--check`
  only), output captured verbatim:
  - `bash install.sh --help` — documents `AO_FLAVOR`/`--flavor`, the namespacing table, and the
    shared-config note.
  - `AO_FLAVOR=beta bash install.sh --check` → `WARNING: ao-beta is NOT installed.`, exit 1.
  - `bash install.sh --flavor nope` and `AO_FLAVOR=nope bash install.sh` → both print
    `ERROR: invalid flavor nope -- AO_FLAVOR/--flavor must be "stable" or "beta"`, exit 1.

## Risks / Blockers
- Not blocked. All live risks from the original plan, with resolution:
  - **Regressing stable behavior** — mitigated by construction (stable messages are built by
    substituting `COMMAND_NAME="ao"`/`FLAVOR_LABEL=""` into the exact original string templates,
    not a parallel branch) and demonstrated concretely (see the stable-unchanged evidence above).
    AC-6 verified: stable's `_do_install` branch never sets `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR`.
  - **Shims must `exec`** — done (`exec "$target" "\$@"` in the generated shim); tests assert on
    the shim's *content* string, not just its existence, per the risk note.
  - **Heredoc quoting** — used an *unquoted* heredoc (`<<SHIM`, not `<<'SHIM'`) so `$target`
    interpolates at generation time, with `\$@` escaped to stay literal in the output; verified by
    asserting the exact generated string in tests.
  - **`$HOME/.local/bin` may not be on `PATH`** — covered by the existing (not duplicated) `_verify`
    step: it does `command -v "$COMMAND_NAME"` for whichever flavor was selected and prints the
    same "source ~/.local/bin/env or restart your shell" hint stable already relied on — beta gets
    this for free since its shims land in the same directory.
  - **The fake-`uv` harness must not escape the temp `HOME`** (AC-21) — every test builds an
    explicit `env=` dict for `subprocess.run` (never mutates process-wide `os.environ`), and the
    `home_dir` fixture asserts `home.resolve() != real-home.resolve()` before any test body runs.
  - **`XDG_DATA_HOME` unset/empty/relative** — unset/empty handled by `:-`; relative is an
    intentional non-goal, see Judgment calls below (matches existing `STATE_DIR` precedent).
  - **Found during implementation, not anticipated**: a `local a=1 b=$a`-style multi-assignment
    bug in `_beta_shim` (`shim_name`/`target`/`shim_path` on one `local` line) failed under
    `set -u` — fixed by splitting into separate `local` statements. Caught by the test suite on
    first run, not by inspection — recorded here since it's exactly the class of subtle bash
    correctness issue this task's risk list was trying to anticipate, just not this specific one.

## Judgment calls flagged
- **Stable's tool-dir discovery**: `_tool_root_dir`/`_tool_bin_dir` call the real `uv tool dir` /
  `uv tool dir --bin` for stable (with the same `$HOME/.local/share/uv/tools` /
  `$HOME/.local/bin` fallback the original script already had if that call fails) — never a
  hardcoded path, so it tracks whatever uv's own default is/becomes.
- **`--check` scope**: reports only the *selected* flavor (AC-16), confirmed by
  `test_check_reports_missing_for_beta_without_touching_network` and by the manual
  `AO_FLAVOR=beta bash install.sh --check` run below — it never reads `installed.commit` or
  stable's tool dir.
- **Relative `XDG_DATA_HOME`**: intentionally *not* special-cased, matching the existing
  precedent — `STATE_DIR`'s `${XDG_STATE_HOME:-$HOME/.local/state}` has never guarded against a
  relative value either. Treating beta's `${XDG_DATA_HOME:-$HOME/.local/share}/ao-beta` the same
  way keeps the two dirs' semantics symmetric rather than introducing an inconsistency where only
  the new path is hardened. If this is ever revisited, it should be revisited for both dirs
  together, not just the new one.
- **`UV_TOOL_DIR`/`UV_TOOL_BIN_DIR` behavior**: empirically confirmed against the real `uv`
  binary (0.5.9) in this environment that `uv tool dir` / `uv tool dir --bin` honor these two env
  vars exactly as assumed (`UV_TOOL_DIR=/tmp/x UV_TOOL_BIN_DIR=/tmp/y uv tool dir` /
  `... --bin` print `/tmp/x` / `/tmp/y`). **Not independently verified against a real `uv tool
  install`** (only the fake-`uv` harness exercises that call, by design — a real one would
  actually install a package and touch the network, which is exactly what the tests must not do).
  This is a reasonable residual gap for a future one-time manual smoke test
  (`AO_FLAVOR=beta bash install.sh --yes` against the real network once) rather than something
  the automated suite can close without violating NFR-2.

## Next actions
- None outstanding for this task. Recommended before epic close (carried from the epic's "Next
  actions", not blocking): a `reviewer` pass focused on the flavor-parameterized probe for
  accidental stable-path changes, and a `dev-security` glance at the generated shims (quoting,
  `PATH` handling, heredoc expansion) — both epic-level follow-ups, not required by this task's
  acceptance criteria.
