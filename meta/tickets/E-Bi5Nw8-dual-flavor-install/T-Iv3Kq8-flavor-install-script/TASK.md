# TASK: T-Iv3Kq8-flavor-install-script

## Metadata
- Task ID: `T-Iv3Kq8-flavor-install-script`
- Epic ID: `E-Bi5Nw8-dual-flavor-install`
- Owner: `claude` (install agent)
- Created: `2026-07-30`
- Last Updated: `2026-07-30`
- Status: `Done`
- Estimate: `< 3 days`

## Requirements Mapping
- Epic FR-1 (flavor selection), FR-2 (namespacing), FR-3 (shims), FR-4 (flavor-parameterized
  staleness probe), FR-5 (shared config/env, documented), FR-6 (`[ui]` + `--force --reinstall`
  per flavor).
- Epic NFR-1 (stable observably unchanged), NFR-2 (fake-`uv` tests in a temp `HOME`),
  NFR-3 (bash-portable), NFR-4 (no duplicated install path).
- CONTRACT section 2 (2.1–2.4) in full.

## Description
Add a second, fully separate install flavor to `install.sh` so a machine can hold both a
**stable** `ao` and a **beta** `ao-beta` at once.

The motivation is already written into `install.sh`'s header: the install is a **non-editable
uv tool snapshot** specifically so a stable `ao` can orchestrate development of the source tree
without a broken commit breaking the very tool you need to recover. Today that isolation is
*temporal* — snapshot versus working tree. This task makes it *concurrent*: run bleeding-edge
orchestrator code as `ao-beta` against real work while `ao` stays the known-good tool.

Nothing beta-related exists in the repo today, so this is greenfield — **and stable behavior
must not change in any observable way** (epic NFR-1). That is the acceptance bar: a developer who
never sets `AO_FLAVOR` must not be able to tell this task shipped.

Files owned:
- `install.sh`
- `tests/test_install_script.py` (new — the repo has no install-script test yet, so this
  establishes the pattern)
- `README.md`
- `docs-md/install-flavors.md` (new, short — **owned by this task**, not by the architect)

Explicitly **not** owned: anything under `src/`, anything under `ui/`, and any test outside
`tests/test_install_script.py` — three other agents are editing those concurrently.

## Acceptance Criteria

**Flavor selection (2.1)**
1. Given no `AO_FLAVOR` and no `--flavor`, then the flavor is `stable`.
2. Given `AO_FLAVOR=beta`, then the flavor is `beta`.
3. Given `AO_FLAVOR=beta --flavor stable`, then the flavor is **`stable`** — the CLI flag wins
   over the env var, consistent with ADR-0003's `CLI > env > config` precedence.
4. Given `AO_FLAVOR=nonsense` or `--flavor nonsense`, then the script **exits non-zero** with a
   message naming the valid values (`stable`, `beta`). Not a silent fallback to stable — a typo'd
   flavor must never quietly install over the wrong namespace.
5. `--yes`, `--check`, `--force`, and `-h` all keep working, per flavor. `-h` output documents
   `--flavor` / `AO_FLAVOR`.

**Namespacing (2.2)**
6. Stable targets uv's **default** tool dir and bin dir — i.e. it does **not** set `UV_TOOL_DIR`
   or `UV_TOOL_BIN_DIR` at all (setting them to uv's defaults explicitly would be an observable
   change and a fragile assumption about uv's own defaults).
7. Beta sets `UV_TOOL_DIR=${XDG_DATA_HOME:-$HOME/.local/share}/ao-beta/uv-tools` and
   `UV_TOOL_BIN_DIR=${XDG_DATA_HOME:-$HOME/.local/share}/ao-beta/bin` **for the `uv tool install`
   call**, so the two flavors' venvs can never collide.
8. Stable writes `$STATE_DIR/installed.commit` — **the filename is unchanged**. Beta writes
   `$STATE_DIR/beta.commit`.
9. The two flavors' paths are **disjoint**: no directory or stamp file is written by both. A test
   asserts this rather than eyeballing it.
10. Both flavors install with the `[ui]` extra and `--force --reinstall`. The existing header
    comment about stale cached resolutions applies to both — and `ao ui` needs `[ui]`, a bug
    already fixed once for stable that beta must not reintroduce.

**Shims (2.2)**
11. Beta creates `$HOME/.local/bin/ao-beta` → the beta venv's `ao`, and
    `$HOME/.local/bin/ao-bench-beta` → the beta venv's `ao-bench`.
12. Both shims are generated with a heredoc, are `chmod +x`, and **`exec` the target forwarding
    `"$@"`**. `exec` is required, not stylistic: ADR-0010 D4 has the dashboard cancel a run by
    signalling the **process group**, so an extra wrapper shell left in the tree is a
    cancellation-correctness hazard.
13. Re-running the beta install is **idempotent** — shims are recreated/overwritten cleanly, no
    duplicated content, no appended text, still executable, exit code unchanged.
14. Stable creates **no shims** (its commands come from uv's own bin dir, exactly as today).

**Staleness / routing probe (2.2)**
15. `_installed_has_routing` is **parameterized by flavor** — one implementation taking the flavor
    (or its tool dir) as input. Pass/fail: `grep` finds exactly one probe body in `install.sh`.
    `CLAUDE.md` requires extracting shared logic rather than copying, and a duplicated probe is
    how the two flavors' staleness detection silently diverges — which matters because
    stale-snapshot-silently-ignores-new-features is a failure mode this project has already hit
    (`meta/learnings.md`).
16. `--check` reports status for the **selected** flavor and exits non-zero when that flavor is
    missing or stale, without consulting the other flavor's stamp or tool dir.

**Shared, NOT namespaced (2.3)**
17. **No** per-flavor config file and **no** per-flavor env prefix are introduced.
    `.ao/config.yaml` and all `AO_*` vars resolve per-project at runtime and are therefore shared
    by both flavors with zero code change.
18. This sharing is stated **explicitly in the `-h` help text and in `docs-md/install-flavors.md`**
    so nobody later "fixes" it into isolation. It is the single most likely thing a future
    contributor mistakes for a bug.

**Installer tests (2.4)**
19. `tests/test_install_script.py` runs `install.sh` in a **temp `HOME`** with a **fake `uv` first
    on `PATH`** that records its argv and the relevant env (`UV_TOOL_DIR`, `UV_TOOL_BIN_DIR`) to a
    file. Nothing is really installed; **no network is touched**.
20. Tests assert: default flavor targets the stable namespace and the stable stamp; `AO_FLAVOR=beta`
    targets the beta dirs, writes `beta.commit`, and creates **both** executable shims;
    `--flavor` overrides the env var; an invalid flavor exits non-zero with a useful message; the
    two flavors' paths are disjoint; re-running beta is idempotent.
21. Tests are **marked so they cannot mutate the developer's real `~`** — a harness bug that
    escaped the temp `HOME` would clobber the real tool install. Pass/fail: every subprocess
    invocation receives an overridden `HOME` (and `XDG_*`), and there is an assertion or fixture
    guard that fails loudly if it does not.

**Stable unchanged (epic NFR-1)**
22. A **no-`AO_FLAVOR` run is demonstrated to be observably identical to pre-change behavior** —
    same `uv` argv, same absent `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR`, same `installed.commit` path,
    same user-facing output. The cleanest evidence is a fake-`uv` argv/env capture diffed against
    the pre-change script. This is the epic's acceptance bar, so it is a criterion, not a hope.

**Docs + gates**
23. `README.md` documents both flavors, the selection mechanism, and the shared-config behavior.
24. `docs-md/install-flavors.md` exists, is short, and covers: what each flavor is for, the
    namespacing table, how to select a flavor, the `PATH` requirement for `$HOME/.local/bin`, that
    `.ao/config.yaml` and `AO_*` are **shared** (FR-5), and how to tell which flavor you are
    running when debugging.
25. `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .` all run and their **real**
    output is reported in `STATUS.md`, including a full-suite count showing no regressions.
26. Bash-portable, no non-portable bashisms (carried forward from E-it9xz2 NFR-2).

## Risks
- **Regressing stable is the main risk** (AC-22). Refactoring a working script to parameterize by
  flavor is exactly how stable behavior changes as a side effect — a helper that now always sets
  `UV_TOOL_DIR`, a renamed stamp variable, reordered output. Every developer and
  `../ao-runner-finplan` depend on today's behavior.
- **Non-`exec` shims** leave an extra shell in the process tree and break process-group
  signalling, which is how the dashboard cancels runs (ADR-0010 D4).
- **`$HOME/.local/bin` may not be on `PATH`**, or may be shadowed — `ao-beta` then silently
  resolves to nothing, or worse to something else. Warn if the directory is not on `PATH`.
- **Two snapshots means two ways to be stale.** An operator debugging "my feature isn't working"
  must now also ask *which binary am I running*. Per-flavor staleness reporting (AC-16) and clear
  docs (AC-24) are the mitigation.
- **The fake-`uv` harness must not escape the temp `HOME`** (AC-21) — the failure mode is
  clobbering the developer's real install, which is both destructive and confusing to diagnose.
- **Heredoc quoting.** An unquoted heredoc expands variables at generation time; a quoted one does
  not. The shims need the venv path baked in but `"$@"` preserved literally — getting this
  backwards produces a shim that either points nowhere or drops its arguments. Test the generated
  shim's *content*, not just its existence.
- **`XDG_DATA_HOME` may be unset, empty, or relative.** The `${XDG_DATA_HOME:-$HOME/.local/share}`
  default handles unset and empty; a relative value is a real edge case worth a decision.

## Dependencies
- Existing `install.sh` from E-it9xz2 (`T-a1b2c3-install-script`) — the script being extended,
  including its `STATE_DIR` / `COMMIT_STAMP` variables and `_installed_has_routing`.
- `docs-md/adr/ADR-0003-settings-precedence-policy.md` for the flag-beats-env rule (AC-3).
- `docs-md/adr/ADR-0010-...md` D4 for why shims must `exec` (AC-12).
- `meta/learnings.md` for the stale-snapshot failure mode that FR-4/AC-15 exist to keep visible.
- Independent of `E-Fp7Qv2-dashboard-file-preview` (disjoint file set, same branch).

## Pseudocode / Algorithm
```text
# ---- selection (2.1) --------------------------------------------------------
CONSTANTS: VALID_FLAVORS = ("stable", "beta");  DEFAULT_FLAVOR = "stable"

FLAVOR = "${AO_FLAVOR:-$DEFAULT_FLAVOR}"
PARSE ARGS:
  --flavor <v>  -> FLAVOR = v          # CLI beats env (ADR-0003)
  --yes | --check | --force | -h       -> unchanged behavior, per flavor
IF FLAVOR NOT IN VALID_FLAVORS:
   die "unknown flavor '$FLAVOR' (valid: stable, beta)"     # non-zero, never a silent fallback

# ---- per-flavor parameters (2.2) -------------------------------------------
# ONE function returning the flavor's parameters; no duplicated install path (NFR-4).
FUNCTION flavor_params(flavor):
  IF flavor == "stable":
     RETURN cmd="ao"  bench="ao-bench"  stamp="$STATE_DIR/installed.commit" \
            tool_dir=""  tool_bin=""  shims=NO        # empty => do NOT set UV_TOOL_*
                                                      # (AC-6: stable must stay observably identical)
  IF flavor == "beta":
     base="${XDG_DATA_HOME:-$HOME/.local/share}/ao-beta"
     RETURN cmd="ao-beta"  bench="ao-bench-beta"  stamp="$STATE_DIR/beta.commit" \
            tool_dir="$base/uv-tools"  tool_bin="$base/bin"  shims=YES

# ---- staleness probe, parameterized NOT duplicated (2.2 / FR-4) ------------
FUNCTION _installed_has_routing(flavor):
  tool_dir = flavor_params(flavor).tool_dir  OR  uv_default_tool_dir()
  probe the installed snapshot under tool_dir for routing support
  RETURN 0/1
# exactly ONE probe body in the file — CLAUDE.md: extract shared logic, do not copy.

# ---- install ---------------------------------------------------------------
FUNCTION do_install(flavor):
  p = flavor_params(flavor)
  IF p.tool_dir NON-EMPTY:
     mkdir -p "$p.tool_dir" "$p.tool_bin"
     env_prefix = UV_TOOL_DIR="$p.tool_dir" UV_TOOL_BIN_DIR="$p.tool_bin"
  ELSE:
     env_prefix = ""                                   # stable: uv defaults, untouched
  # [ui] and --force --reinstall apply to BOTH flavors (FR-6): [ui] for `ao ui`,
  # --reinstall because a cached resolution can otherwise satisfy a stale wheel.
  $env_prefix uv tool install "$SCRIPT_DIR[ui]" --force --reinstall
  mkdir -p "$STATE_DIR";  write current source commit -> "$p.stamp"
  IF p.shims == YES:  write_shims(p)
  IF p.shims == YES AND "$HOME/.local/bin" NOT ON PATH:
     warn "add $HOME/.local/bin to PATH or '$p.cmd' will not resolve"

FUNCTION write_shims(p):
  mkdir -p "$HOME/.local/bin"
  FOR (shim_name, target_name) IN ((p.cmd, "ao"), (p.bench, "ao-bench")):
     # Quoted heredoc so "$@" survives literally; target path interpolated by hand.
     cat > "$HOME/.local/bin/$shim_name" <<'EOF'
#!/usr/bin/env bash
exec "@TARGET@" "$@"        # exec: no wrapper shell left in the process tree, because
                            # the dashboard cancels runs by signalling the process GROUP
                            # (ADR-0010 D4). A wrapper would survive/alter that.
EOF
     substitute @TARGET@ -> "$p.tool_bin/$target_name"
     chmod +x "$HOME/.local/bin/$shim_name"
  # Overwrite (>) not append (>>) => re-running is idempotent (AC-13).

# ---- shared config/env (2.3) ----------------------------------------------
# NOTHING to implement. .ao/config.yaml and AO_* resolve per-project at runtime, so both
# flavors read them unchanged. State this in -h and in docs-md/install-flavors.md (AC-18)
# so it is not "fixed" into isolation later.
```

## Schemas / Interface Notes
- Interface (CLI):
  ```text
  bash install.sh [--flavor stable|beta] [--yes] [--check] [--force] [-h]
  AO_FLAVOR=stable|beta bash install.sh          # --flavor wins over AO_FLAVOR
  exit 0  = installed / already current
  exit !0 = missing or stale (with --check), or an invalid flavor
  ```
- Installed surface:
  | flavor | commands | uv tool dir | uv tool bin | commit stamp | shims |
  |---|---|---|---|---|---|
  | stable | `ao`, `ao-bench` | uv default | uv default | `$STATE_DIR/installed.commit` | none |
  | beta | `ao-beta`, `ao-bench-beta` | `${XDG_DATA_HOME:-$HOME/.local/share}/ao-beta/uv-tools` | `…/ao-beta/bin` | `$STATE_DIR/beta.commit` | `$HOME/.local/bin/{ao-beta,ao-bench-beta}` |
- Config / env: `AO_FLAVOR` (installer-time only). **`.ao/config.yaml` and every `AO_*` runtime
  var are shared across flavors by design** — no new key, no new prefix (FR-5).
- Spec / data schema (JSON/YAML): `N/A` — no workflow-spec or `ProjectConfig` change.
- Triggers / events (cron/event): `N/A`.
- Artifacts (inputs/outputs by path): writes the flavor's uv tool venv, its commit stamp under
  `$STATE_DIR`, and (beta only) two shims under `$HOME/.local/bin/`. Tests write only inside a
  temp `HOME`.

## Handoff Boundary
- Upstream: the existing `install.sh` (E-it9xz2); ADR-0003 (precedence); ADR-0010 D4 (`exec` /
  process groups).
- Downstream: `../ao-runner-finplan` consumes `install.sh` (non-editable snapshot, see that
  script's header). It is **out of scope for edits**, but epic NFR-1 exists so it keeps working
  untouched — if anything about the stable path must change, that is a cross-repo impact to flag
  loudly, not to absorb silently.
- Sibling: `E-Fp7Qv2-dashboard-file-preview` is on the same branch with a disjoint file set. Do
  not touch `src/`, `ui/`, or any test other than `tests/test_install_script.py`.

## Artifacts
- Docs/comments: `meta/tickets/E-Bi5Nw8-dual-flavor-install/T-Iv3Kq8-flavor-install-script/`
- Code (planned): `install.sh`
- Tests (planned): `tests/test_install_script.py`
- Docs (planned, owned here): `README.md`, `docs-md/install-flavors.md`

## Comments
- By: architect · Role: architect · Date: 2026-07-30 · Comment: Ticket opened for work already in
  progress; CONTRACT §2 in full. Four criteria are worth treating as design constraints rather
  than checkboxes. (AC-22) **Stable-unchanged is the acceptance bar** — the parameterizing refactor
  is precisely how stable behavior drifts, so demonstrate it with a fake-`uv` argv/env diff rather
  than by inspection; AC-6 deliberately requires stable to *not set* `UV_TOOL_*` at all rather
  than set them to uv's defaults, since the latter is both observable and a fragile assumption
  about uv. (AC-12) **`exec` in the shims is a correctness requirement**, not style: ADR-0010 D4
  cancels runs by signalling the process group, so a lingering wrapper shell is a real hazard.
  (AC-15) **One probe body, parameterized** — a copied probe is how the two flavors' staleness
  detection diverges, and stale-snapshot-silently-ignoring-features is a failure this project has
  already hit. (AC-18) **Shared `.ao/config.yaml` and `AO_*` must be documented as intentional in
  two places**, because it is the single most likely thing a future contributor mistakes for a bug
  and "fixes." `docs-md/install-flavors.md` is owned by this task; the architect deliberately did
  not write it.
