# Dual-flavor install (`stable` / `beta`)

- Epic: `E-Bi5Nw8-dual-flavor-install` (`meta/tickets/E-Bi5Nw8-dual-flavor-install/`)
- Status: Implemented
- Author: install agent
- Date: 2026-07-30

`install.sh` can install two fully-separate snapshots of `ao` side by side: the default
**stable** flavor (unchanged from the original single-flavor installer) and an opt-in
**beta** flavor, namespaced so the two never collide. This mirrors, one level up, the
same reasoning the script already applies to the non-editable v1/v2 snapshot install (see
the "WHY NON-EDITABLE" note at the top of `install.sh`): don't let one snapshot's breakage
take down the tool you rely on to recover from it. Here that means you can try an
unreleased branch as `ao-beta` while `ao` keeps working exactly as it did before.

## Selecting a flavor

```bash
bash install.sh                       # stable (default, same as before flavors existed)
AO_FLAVOR=beta bash install.sh        # beta
bash install.sh --flavor beta         # same as above; --flavor wins over AO_FLAVOR
```

An unrecognized flavor (`AO_FLAVOR=typo` or `--flavor typo`) is rejected immediately with a
non-zero exit and a clear error — it never silently falls back to stable. All existing flags
(`--yes`, `--check`, `--force`, `-h`/`--help`) work the same way for either flavor.

## Namespacing

| | stable | beta |
|---|---|---|
| commands | `ao`, `ao-bench` | `ao-beta`, `ao-bench-beta` |
| uv tool dir | uv's default (`uv tool dir`) | `${XDG_DATA_HOME:-$HOME/.local/share}/ao-beta/uv-tools` |
| uv tool bin dir | uv's default (`uv tool dir --bin`) | `${XDG_DATA_HOME:-$HOME/.local/share}/ao-beta/bin` |
| commit stamp | `$STATE_DIR/installed.commit` (unchanged) | `$STATE_DIR/beta.commit` |

`$STATE_DIR` (`${XDG_STATE_HOME:-$HOME/.local/state}/ao-install`) is the same directory for
both flavors — only the stamp *filename* differs, so `--check`'s staleness/routing probe can
tell the two apart without needing two state directories.

Beta installs `[ui]` with `--force --reinstall`, exactly like stable — the same "uv can
resolve a stale cached wheel despite reporting success" reasoning documented in `install.sh`
applies to both flavors equally. The only difference for beta is that `UV_TOOL_DIR` /
`UV_TOOL_BIN_DIR` are set (only for that one `uv tool install` invocation) so its venv lands
in the isolated tree above instead of uv's shared default.

### Shims

`uv tool install` always creates entry points named after `pyproject.toml`'s
`[project.scripts]` (`ao`, `ao-bench`) — that doesn't change per flavor, since this design
deliberately makes no packaging changes. For beta, those entry points land inside the
isolated beta bin dir above, which usually isn't on `PATH`. `install.sh` bridges that gap
with two thin launcher shims written to `$HOME/.local/bin/` (which typically *is* on `PATH`):

```bash
$HOME/.local/bin/ao-beta        # exec's straight into <beta-bin-dir>/ao "$@"
$HOME/.local/bin/ao-bench-beta  # exec's straight into <beta-bin-dir>/ao-bench "$@"
```

Each shim is a small generated script that `exec`s the real beta entry point and forwards
every argument, so its behavior always tracks whatever the beta venv actually does — no
logic is duplicated in the shim itself. `exec` (not a plain call) matters beyond style: the
dashboard cancels a run by signalling its process **group**
([ADR-0010](adr/ADR-0010-dashboard-architecture-and-general-instructions.md) D4), and a
wrapper shell left in the tree by a non-`exec`'d call would be a cancellation-correctness
hazard. Shims are regenerated (overwritten) on every beta install, so re-running
`AO_FLAVOR=beta bash install.sh` is idempotent: the same `HOME`/XDG dirs always produce
byte-identical shim files.

**`$HOME/.local/bin` must be on `PATH`** for `ao-beta`/`ao-bench-beta` to resolve — the same
requirement stable already has for `ao`/`ao-bench` from uv's own default bin dir (the
installer's own "Next steps" output reminds you to `source "$HOME/.local/bin/env"` or
restart your shell if a freshly-installed command isn't found yet).

### Telling flavors apart when debugging

- `command -v ao` / `command -v ao-beta` (or `which`) shows which binary each name actually
  resolves to.
- `ao --version` / `ao-beta --version` report the version of the binary you actually typed —
  they are two independent installs and can be at different commits.
- `bash install.sh --check` / `AO_FLAVOR=beta bash install.sh --check` reports staleness and
  routing-capability for **only the flavor you selected** — it never reads the other
  flavor's commit stamp or tool dir, so a stale beta never gets misreported as a stale stable
  (or vice versa).

## What is intentionally SHARED, not namespaced

**`.ao/config.yaml` and every `AO_*` environment variable are shared between flavors, on
purpose.** They are resolved **per-project, at runtime**, by whichever `ao`/`ao-beta` binary
you invoke inside a given project — the installer has no say in that resolution and doesn't
need to. There is no per-flavor config file, no `AO_BETA_*` env prefix, and none should be
added: doing so would solve a problem that doesn't exist (a project's config is already
independent of which installed binary reads it) while adding a second thing to keep in sync.

If you find yourself wanting to "fix" this into per-flavor isolation, don't — re-read this
section first. The two flavors differ only in *which binary you run*; what that binary reads
once invoked is unrelated to how it got installed.

## Testing

`tests/test_install_script.py` establishes the pattern for exercising `install.sh` from
outside: a fake `uv` executable is placed first on `PATH` (it records its argv and the
`UV_TOOL_DIR`/`UV_TOOL_BIN_DIR` it was called with, then exits `0` without installing
anything or touching the network), and `HOME` is redirected to a per-test temp directory so
the real developer home is never read or written. Coverage includes: default-flavor
byte-for-byte parity with the pre-flavor installer, beta namespacing, `--flavor` overriding
`AO_FLAVOR`, invalid-flavor rejection, disjoint paths between flavors under the same `HOME`,
and idempotent beta re-installs.
