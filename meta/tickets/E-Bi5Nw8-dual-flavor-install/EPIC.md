# EPIC: E-Bi5Nw8-dual-flavor-install

## Metadata
- Epic ID: `E-Bi5Nw8-dual-flavor-install`
- Title: `Dual-flavor install — side-by-side stable (ao) and beta (ao-beta) tool snapshots`
- Owner: `avadhoot`
- Created: `2026-07-30`
- Last Updated: `2026-07-30`
- Status: `Done`

## Summary
- Goal: Let one machine hold two independent `ao` installs at once — a **stable** flavor
  (`ao`, `ao-bench`) and a **beta** flavor (`ao-beta`, `ao-bench-beta`) in a fully separate uv
  tool venv — so a developer can run bleeding-edge orchestrator code against real work without
  risking the `ao` they depend on to recover from a bad commit. This extends the v1/v2 isolation
  reasoning already documented in `install.sh`'s header: today that isolation is *temporal*
  (a snapshot vs the source tree), and this epic makes it *concurrent*.
- Scope In: `AO_FLAVOR` env var + `--flavor <name>` flag (flag wins) with a clear error on
  anything else; per-flavor namespacing of command names, uv tool dir, uv tool bin dir, and the
  commit stamp; `exec`-ing beta shims in `$HOME/.local/bin`; parameterizing the existing
  staleness/routing probe by flavor instead of duplicating it; `tests/test_install_script.py`
  driven by a **fake `uv`** so nothing is really installed; `README.md` and a short
  `docs-md/install-flavors.md`.
- Scope Out: publishing beta to PyPI or any index; version/branch pinning per flavor (both
  flavors install from the same working tree); more than two flavors; **any per-flavor
  isolation of `.ao/config.yaml` or `AO_*` env vars** — explicitly rejected, see FR-5;
  changing stable behavior in any observable way.

## Requirements
- FR-1: Flavor selection — `AO_FLAVOR` env var accepting `stable` (default) or `beta`, plus
  `--flavor <name>`; **the CLI flag wins over the env var** (consistent with ADR-0003's
  `CLI > env > config` precedence). Anything else exits non-zero with a clear, actionable
  message naming the valid values. Existing flags (`--yes`, `--check`, `--force`, `-h`) keep
  working per flavor.
- FR-2: Namespacing — stable unchanged, beta fully separate:

  | | stable | beta |
  |---|---|---|
  | commands | `ao`, `ao-bench` | `ao-beta`, `ao-bench-beta` |
  | uv tool dir | uv default | `${XDG_DATA_HOME:-$HOME/.local/share}/ao-beta/uv-tools` |
  | uv tool bin | uv default | `${XDG_DATA_HOME:-$HOME/.local/share}/ao-beta/bin` |
  | commit stamp | `$STATE_DIR/installed.commit` (**unchanged filename**) | `$STATE_DIR/beta.commit` |

  Beta sets `UV_TOOL_DIR` / `UV_TOOL_BIN_DIR` for its `uv tool install` call so the two
  flavors' venvs can never collide.
- FR-3: Beta shims in `$HOME/.local/bin/` — `ao-beta` → the beta venv's `ao`, `ao-bench-beta` →
  the beta venv's `ao-bench`. Generated with a heredoc, `chmod +x`, **`exec` and forward
  `"$@"`**, and idempotent on re-run.
- FR-4: `_installed_has_routing` (the staleness/routing probe) is **parameterized by flavor**,
  not duplicated. `CLAUDE.md` requires extracting shared logic rather than copying it, and a
  duplicated probe is how the two flavors' staleness detection silently diverges.
- FR-5: `.ao/config.yaml` and all `AO_*` env vars are **shared, not namespaced** — they resolve
  per-project at runtime, so both flavors read them with no code change. This must be stated
  explicitly in the help text **and** the docs so nobody later "fixes" it into isolation. Do not
  add any per-flavor config file or env prefix.
- FR-6: Both flavors keep the `[ui]` extra and `--force --reinstall` on their `uv tool install`
  call — the existing comment about stale cached wheels applies to both, and `ao ui` needs `[ui]`
  (a bug already fixed once for stable, per `install.sh` history; beta must not reintroduce it).
- NFR-1: **Stable behavior does not change in any observable way** — same commands, same uv
  default dirs, same `installed.commit` filename, same output. This is the acceptance bar the
  whole epic is judged against; a developer who never sets `AO_FLAVOR` must not be able to tell
  this epic shipped.
- NFR-2: `tests/test_install_script.py` (new — no install-script test exists yet, so this
  establishes the pattern) runs `install.sh` in a **temp `HOME`** with a **fake `uv` first on
  `PATH`** that records its argv and env (`UV_TOOL_DIR`, `UV_TOOL_BIN_DIR`) to a file. Nothing
  is really installed and no network is touched. Tests are marked so they cannot mutate the
  developer's real `~`.
- NFR-3: Bash-portable, no non-portable bashisms (carried forward from E-it9xz2 NFR-2).
- NFR-4: No duplication between the flavors' install paths beyond what differs — one code path
  parameterized by flavor.

## Task List
- [x] `T-Iv3Kq8-flavor-install-script` — **Done** — CONTRACT section 2 in full (2.1
  flavor selection, 2.2 namespacing + shims, 2.3 shared config/env, 2.4 installer tests) plus
  `README.md` and `docs-md/install-flavors.md`. Maps FR-1..FR-6, NFR-1..NFR-4. Evidence in the
  task's `STATUS.md`: 11 new tests, 1662 passed/7 skipped full suite (delta exactly +11, zero
  regressions), `ruff check .` / `ruff format --check .` / `mypy` clean on all owned files, and a
  concrete byte-for-byte stable-unchanged diff against the pre-epic script (NFR-1).

Single-task epic by design: the flavor split is one coherent change to one script plus its first
test harness, and splitting it would create artificial handoffs inside a single file.

## Risks and Dependencies
- **Silently changing stable behavior is the primary risk** (NFR-1). Every developer and the
  sibling `ao-runner-finplan` repo depend on today's exact `install.sh` behavior, including the
  `installed.commit` filename and the uv default tool dir. Refactoring to parameterize by flavor
  is exactly the kind of change that alters stable as a side effect.
- **Shim correctness.** A shim that does not `exec` leaves an extra shell in the process tree;
  ADR-0010 D4 has the dashboard cancel a run by **signalling the process group**, so a
  non-`exec` wrapper is a real correctness hazard for cancellation, not a style nit.
- **Stale-snapshot confusion, now doubled.** `ao` being a non-editable snapshot that silently
  ignores new features until `install.sh` is re-run is a failure mode this project has already
  hit (ADR-0010 D4; `meta/learnings.md`). Two flavors means two snapshots that can each be stale,
  and an operator debugging "why doesn't my feature work" now has to ask *which binary am I
  running*. Per-flavor staleness reporting (FR-4) and clear help text (FR-5) are the mitigation.
- **`PATH` ordering.** Beta shims land in `$HOME/.local/bin`; if that is not on `PATH`, or is
  shadowed, `ao-beta` silently resolves to nothing or to the wrong thing.
- **Shared config is a feature, and will look like a bug.** Running `ao-beta` in a project picks
  up the same `.ao/config.yaml` and `AO_*` vars as `ao` — intended (FR-5), and the single most
  likely thing a future contributor "fixes."
- **Fake-`uv` tests must not be able to touch the real `~`** (NFR-2). A bug in the harness that
  leaks out of the temp `HOME` would reinstall or clobber the developer's actual tool.
- Greenfield: nothing beta-related exists in the repo today. Depends on the existing `install.sh`
  from E-it9xz2 (`T-a1b2c3-install-script`). Shares branch `ad/workflow-templates` with
  `E-Fp7Qv2-dashboard-file-preview`, which touches an entirely disjoint file set.

## Links
- Design doc: **`docs-md/install-flavors.md`** — written by the install agent as part of
  `T-Iv3Kq8-flavor-install-script` (FR-5 requires it to state the shared-config/env behavior
  explicitly). Cross-referenced here; **not** authored by the architect role.
- Prior epic that created `install.sh`: `meta/tickets/E-it9xz2-installable-ao-tool/EPIC.md`
  (see `T-a1b2c3-install-script`)
- Settings-precedence policy the flavor flag follows:
  `docs-md/adr/ADR-0003-settings-precedence-policy.md`
- Why the install is a non-editable snapshot, and the process-group/cancel reasoning the shims
  must not break: `install.sh` header · `docs-md/adr/ADR-0010-dashboard-architecture-and-general-instructions.md` D4
- Stale-snapshot failure mode already hit once: `meta/learnings.md`
- Primary consumer affected by install changes: `../ao-runner-finplan` (out of scope for edits)
- Sibling epic on the same branch: `meta/tickets/E-Fp7Qv2-dashboard-file-preview/EPIC.md`

## Comments
- By: architect · Role: architect · Date: 2026-07-30 · Comment: Epic scaffolded from the shared
  implementation contract §2 while the install agent implements. Framed as one task rather than
  several: it is a single coherent change to one script plus the repo's first install-script test
  harness, and splitting it would manufacture handoffs inside a single file. Two things flagged
  as the real risks rather than the obvious ones: (a) **NFR-1 is the acceptance bar** — the
  refactor to parameterize by flavor is precisely how stable behavior gets altered as a side
  effect, so "a developer who never sets `AO_FLAVOR` cannot tell this shipped" is the test to
  hold it to; (b) **shims must `exec`** — not for tidiness, but because ADR-0010 D4 cancels runs
  by signalling the process group, so a non-`exec` wrapper is a cancellation-correctness hazard.
  Also noted that FR-5 (shared `.ao/config.yaml` and `AO_*`) is deliberate and will read as a
  bug to a future contributor, which is why the contract requires it stated in both the help text
  and the docs. `docs-md/install-flavors.md` is owned by the install agent and is deliberately
  **not** written here — only cross-referenced.
- By: install agent · Role: developer · Date: 2026-07-30 · Comment: Epic closed. All FR-1..FR-6
  and NFR-1..NFR-4 verified with real command output (see
  `T-Iv3Kq8-flavor-install-script/STATUS.md` for the full evidence log, including a byte-for-byte
  fake-`uv` argv/env diff of the pre-epic script vs. the new stable path — NFR-1's acceptance
  bar). One real bash correctness bug (multi-assignment `local` under `set -u`) was found and
  fixed by the new test suite during implementation, not by inspection. Recommended-but-not-
  blocking follow-ups carried over from the original plan: a `reviewer` pass on the
  flavor-parameterized probe, and a `dev-security` glance at the generated shims.
