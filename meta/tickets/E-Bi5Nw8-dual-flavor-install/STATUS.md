# STATUS: E-Bi5Nw8-dual-flavor-install

- Status: `Done`
- Last Updated: `2026-07-30`
- Owner: `avadhoot`

## Rollup
| Task | Status |
|------|--------|
| T-Iv3Kq8-flavor-install-script | Done |

Epic status is `Done` because its single task is `Done`, with evidence for every FR/NFR reported
in the task's `STATUS.md` and summarized below.

## This update
- By: architect · Role: architect · Date: 2026-07-30 · Comment: Ticket scaffolding created for an
  epic already in flight. One task opened and marked In Progress. `docs-md/install-flavors.md` is
  owned by the install agent (epic FR-5 requires it to state the shared-config/env behavior
  explicitly) and is cross-referenced from `EPIC.md` rather than written here.
- By: install agent · Role: developer · Date: 2026-07-30 · Comment: Implementation complete and
  verified; flipping this rollup and `EPIC.md` together per the "Next actions" plan below. The doc
  was briefly saved as `docs-md/dual-flavor-install.md` mid-implementation and has been renamed to
  `docs-md/install-flavors.md` to match this ticket's naming — every cross-reference in `EPIC.md`,
  `TASK.md`, `README.md`, and this file now points at the real, final filename.

## Evidence
- Epic + task tickets: `EPIC.md`, this file, `T-Iv3Kq8-flavor-install-script/{TASK.md,STATUS.md}`
  (full evidence log lives in the task's `STATUS.md`; summarized here).
- `pytest -q tests/test_install_script.py`: **11 passed**. `pytest -q` full suite: **1662 passed,
  7 skipped**; with the new test file ignored: **1651 passed, 7 skipped** — delta is exactly the
  11 added tests, zero regressions.
- `ruff check .`: all checks passed. `ruff format --check .`: clean on every file this task owns
  (one reformat needed and applied to the new test file; two flagged files belong to the
  concurrent Epic-1 backend workstream and are untouched by this task). `mypy
  tests/test_install_script.py`: success, no issues.
- **NFR-1 (stable observably unchanged) — demonstrated concretely**: the pre-epic `install.sh`
  (via `git show HEAD:install.sh`, i.e. before this epic's commit) and the new script were run
  side by side against the same fake `uv` and a fresh temp `HOME`, no `AO_FLAVOR`. Fake-`uv`
  argv/env log, stamp file content, and stderr were byte-identical; stdout differed only by the
  incidental scratch-directory path baked into the comparison harness itself (not a behavioral
  difference). `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR` were `<unset>` in both, for every uv invocation.
- `docs-md/install-flavors.md`: written, covers what each flavor is for, the namespacing table,
  selection, the `$HOME/.local/bin` `PATH` requirement, telling flavors apart when debugging, and
  states explicitly that `.ao/config.yaml`/`AO_*` are shared (FR-5/AC-18/AC-24).

## Risks / Blockers
- Not blocked; epic closed. Original risks and their resolution (detail in the task's
  `STATUS.md`):
  1. **Regressing stable behavior** (NFR-1) — mitigated by building stable's messages from the
     same string templates with `COMMAND_NAME="ao"`/`FLAVOR_LABEL=""` substituted in (not a
     parallel branch), and demonstrated with the concrete diff above.
  2. **Shims that do not `exec`** — the generated shims `exec` and forward `"$@"`; tests assert on
     shim *content*, not just existence.
  3. **Doubled stale-snapshot confusion** — mitigated by per-flavor staleness reporting (`--check`
     only ever reports the selected flavor) and explicit help text.
  4. **The fake-`uv` harness escaping the temp `HOME`** — every subprocess call uses an explicit
     `env=` dict (never process-wide `os.environ`), plus a fixture assertion that fails loudly if
     the temp home ever resolved to the real one.
  5. **Shared `.ao/config.yaml` / `AO_*` looking like a bug** — documented explicitly in both the
     `-h` help text and `docs-md/install-flavors.md`, with an explicit "don't fix this" callout.

## Next actions
- None outstanding for this epic. Recommended, not blocking: a `reviewer` pass on the
  flavor-parameterized probe for accidental stable-path changes, and a `dev-security` glance at
  the generated shims (quoting, `PATH` handling, heredoc expansion of anything
  attacker-influenced) — carried over from the original plan as optional hardening, not required
  by any FR/NFR.
