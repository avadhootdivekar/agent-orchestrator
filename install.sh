#!/usr/bin/env bash
# install.sh — One-command installer / upgrader for AO (Agent Orchestrator).
#
# Usage:
#   bash install.sh              # install if missing; if an OLDER snapshot is
#                                #   installed, ask before upgrading it
#   bash install.sh --yes        # non-interactive: install/upgrade without asking
#   bash install.sh --check      # report status only; exit 1 if missing/stale
#   bash install.sh --force      # reinstall the current source snapshot unconditionally
#   bash install.sh --flavor beta   # install the beta flavor (same as AO_FLAVOR=beta)
#
# Flavors (AO_FLAVOR=stable|beta, default stable; --flavor overrides the env var):
#   stable  commands `ao` / `ao-bench`, uv's default tool dir/bin dir (unchanged
#           from the single-flavor installer -- this is the default and its
#           behavior never changes based on the existence of the beta flavor).
#   beta    commands `ao-beta` / `ao-bench-beta` (thin shims in ~/.local/bin
#           that exec into a fully separate uv tool dir/bin dir, so the two
#           flavors' venvs never collide). Commit stamp file: beta.commit
#           (stable keeps installed.commit).
#   An unrecognized flavor is rejected with a non-zero exit.
#
#   `.ao/config.yaml` and every `AO_*` env var are resolved PER-PROJECT AT
#   RUNTIME (not by this installer), so both flavors intentionally SHARE
#   them -- there is no per-flavor config or env-var prefix, and none should
#   be added. Point a project at either flavor's install the same way.
#
# What this script does:
#   1. Checks for `uv`; installs it via the official installer if missing.
#   2. Installs the `ao` / `ao-beta` CLI globally via `uv tool install` (a
#      NON-editable snapshot -- see the isolation note below).
#   3. On re-run, compares the installed snapshot's source commit to the current
#      source HEAD and OPTIONALLY upgrades — only after you confirm.
#
# ----------------------------------------------------------------------------
# WHY NON-EDITABLE (v1/v2 isolation — do not switch to --editable):
#   `uv tool install <dir>` copies the built package into an isolated venv. That
#   snapshot is unaffected by later edits in the source tree, so you can run a
#   STABLE installed `ao` (v1) to orchestrate development of the source (v2) in
#   this same repo. An --editable install would let a broken source commit break
#   the very `ao` you rely on to recover. Re-running this script promotes the
#   current source to the new snapshot — that is the intended upgrade path.
# ----------------------------------------------------------------------------
# WEB-HOSTED VERSION NOTE:
#   Serve this file as a static asset and users can run:
#     curl -fsSL https://your-domain.example.com/install.sh | bash
#   Once the package is on PyPI, replace the local-path install with:
#     uv tool install agent-orchestrator
# ----------------------------------------------------------------------------

set -euo pipefail

AO_REPO_URL="https://github.com/your-org/agent-orchestrator"  # placeholder
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/ao-install"
# Where beta's launcher shims (and stable's own binaries, via uv) land. Declared once,
# mirroring the STATE_DIR/COMMIT_STAMP pattern, so the literal isn't repeated across
# _beta_shim/_install_beta_shims.
SHIM_BIN_DIR="$HOME/.local/bin"

ASSUME_YES=0
CHECK_ONLY=0
FORCE=0
FLAVOR="${AO_FLAVOR:-stable}"
# `while` (not `for arg in "$@"`) because --flavor NAME needs to consume two
# tokens; a `for`-over-args loop has no clean way to do that.
while [ $# -gt 0 ]; do
    case "$1" in
        --yes|-y)   ASSUME_YES=1; shift ;;
        --check)    CHECK_ONLY=1; shift ;;
        --force|-f) FORCE=1; shift ;;
        --flavor)
            [ $# -ge 2 ] || { printf 'ERROR: --flavor requires an argument: stable|beta\n' >&2; exit 1; }
            FLAVOR="$2"
            shift 2 ;;
        --flavor=*)
            FLAVOR="${1#--flavor=}"
            shift ;;
        -h|--help)
            sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) printf 'ERROR: unknown argument: %s\n' "$1" >&2; exit 1 ;;
    esac
done

# --flavor (CLI) wins over AO_FLAVOR (env); reject anything else outright so a
# typo never silently falls back to stable.
case "$FLAVOR" in
    stable|beta) ;;
    *)
        printf 'ERROR: invalid flavor %s -- AO_FLAVOR/--flavor must be "stable" or "beta"\n' "$FLAVOR" >&2
        exit 1
        ;;
esac

# ---------------------------------------------------------------------------
# Flavor-derived namespacing.
#   stable: identical to the single-flavor installer this replaced -- same
#           command names, same uv tool dir/bin dir (uv's own defaults), same
#           installed.commit filename. This branch exists so a future edit to
#           the beta branch can't accidentally change stable's paths too.
#   beta:   fully separate command names, uv tool dir/bin dir, and commit
#           stamp filename, so the two flavors' venvs never collide and can
#           be installed/upgraded/removed independently. See the v1/v2
#           isolation note below -- the same "don't let one snapshot's
#           breakage take down the other" reasoning applies to running a
#           trusted stable `ao` alongside an under-test `ao-beta`.
# ---------------------------------------------------------------------------
case "$FLAVOR" in
    beta)
        COMMAND_NAME="ao-beta"
        BENCH_COMMAND_NAME="ao-bench-beta"
        COMMIT_STAMP="$STATE_DIR/beta.commit"
        FLAVOR_LABEL=" (beta)"
        ;;
    *)
        COMMAND_NAME="ao"
        BENCH_COMMAND_NAME="ao-bench"
        COMMIT_STAMP="$STATE_DIR/installed.commit"
        FLAVOR_LABEL=""
        ;;
esac

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_info()  { printf '\033[1;34m[ao-install]\033[0m %s\n' "$*"; }
_ok()    { printf '\033[1;32m[ao-install]\033[0m %s\n' "$*"; }
_warn()  { printf '\033[1;33m[ao-install]\033[0m WARNING: %s\n' "$*" >&2; }
_error() { printf '\033[1;31m[ao-install]\033[0m ERROR: %s\n' "$*" >&2; exit 1; }

_source_commit() {
    git -C "$SCRIPT_DIR" rev-parse --short HEAD 2>/dev/null || echo "unknown"
}

_installed_commit() {
    [ -f "$COMMIT_STAMP" ] && cat "$COMMIT_STAMP" 2>/dev/null || echo "none"
}

_ao_present() { command -v "$COMMAND_NAME" >/dev/null 2>&1; }

# Beta's own data-dir root, namespaced away from uv's default tool dir (mirrors
# the $STATE_DIR pattern above). Not used for stable -- stable always asks uv
# for its own default.
_beta_data_dir() { echo "${XDG_DATA_HOME:-$HOME/.local/share}/ao-beta"; }

# uv's tool-venvs directory for the given flavor. stable: uv's own default,
# untouched. beta: its own isolated root, so `uv tool install` for one flavor
# can never see or overwrite the other's venv.
_tool_root_dir() {
    local flavor="$1"
    if [ "$flavor" = "beta" ]; then
        echo "$(_beta_data_dir)/uv-tools"
    else
        uv tool dir 2>/dev/null || echo "$HOME/.local/share/uv/tools"
    fi
}

# uv's tool-executable-bin directory for the given flavor. stable: uv's own
# default (unchanged). beta: its own isolated bin dir -- note this is NOT the
# ao-beta/ao-bench-beta shim location (that's always $SHIM_BIN_DIR); it's
# where uv links the *unqualified* `ao`/`ao-bench` entry points that the beta
# shims exec into.
_tool_bin_dir() {
    local flavor="$1"
    if [ "$flavor" = "beta" ]; then
        echo "$(_beta_data_dir)/bin"
    else
        uv tool dir --bin 2>/dev/null || echo "$HOME/.local/bin"
    fi
}

# Best-effort routing-capability probe of the INSTALLED tool for the given
# flavor (independent of the commit stamp). Returns 0 = routing-capable, 1 =
# stale/unknown. Parameterized by flavor rather than duplicated per flavor, so
# stable and beta share one implementation (CLAUDE.md: extract shared logic
# instead of copying it).
_installed_has_routing() {
    local flavor="$1" tooldir py
    tooldir="$(_tool_root_dir "$flavor")/agent-orchestrator"
    py="$tooldir/bin/python"
    [ -x "$py" ] || return 1
    "$py" - <<'PY' 2>/dev/null
import importlib
d = importlib.import_module("agent_orchestrator.dag")
raise SystemExit(0 if hasattr(d, "compute_cones") else 1)
PY
}

# Ask a yes/no question. Auto-yes with --yes; declines (safe) on a non-TTY.
_confirm() {
    local prompt="$1"
    [ "$ASSUME_YES" -eq 1 ] && return 0
    if [ ! -t 0 ]; then
        _warn "not a TTY and --yes not given — skipping. Re-run with --yes to proceed."
        return 1
    fi
    local reply
    read -r -p "$prompt [y/N] " reply
    [[ "$reply" =~ ^[Yy]$ ]]
}

# ---------------------------------------------------------------------------
# Step 1: Ensure `uv` is available
# ---------------------------------------------------------------------------

_ensure_uv() {
    if command -v uv >/dev/null 2>&1; then
        _info "uv is already installed: $(uv --version)"
        return 0
    fi
    _info "uv not found — installing via the official installer..."
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL https://astral.sh/uv/install.sh | sh
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- https://astral.sh/uv/install.sh | sh
    else
        _error "Neither curl nor wget is available. Install one and re-run this script."
    fi
    if [ -f "$HOME/.local/bin/uv" ]; then
        export PATH="$HOME/.local/bin:$PATH"
    elif [ -f "$HOME/.cargo/bin/uv" ]; then
        export PATH="$HOME/.cargo/bin:$PATH"
    fi
    command -v uv >/dev/null 2>&1 || _error "uv installed but not on PATH. Add ~/.local/bin to PATH and re-run."
    _ok "uv installed: $(uv --version)"
}

# ---------------------------------------------------------------------------
# Step 2: Install / upgrade the `ao` CLI snapshot
# ---------------------------------------------------------------------------

_do_install() {
    [ -f "$SCRIPT_DIR/pyproject.toml" ] || _error "pyproject.toml not found next to install.sh — run from the repo root."
    _info "Installing AO${FLAVOR_LABEL} snapshot from: $SCRIPT_DIR"
    # --force alone is NOT sufficient: observed uv resolving a stale cached wheel (months
    # old, missing entire epics) despite --force reporting success, because uv's local-path
    # build cache isn't guaranteed to key on live source content on every code path.
    # --reinstall forces uv to actually rebuild/relink rather than reuse a cached resolution.
    # [ui] pulls in fastapi/uvicorn/httpx so the global snapshot can serve `ao ui` directly —
    # without it the dashboard command exists but fails at runtime asking for the extra.
    # This reasoning applies to BOTH flavors identically. beta additionally redirects
    # UV_TOOL_DIR/UV_TOOL_BIN_DIR (only for this one command) so its venv is installed
    # into its own isolated tree instead of colliding with stable's.
    if [ "$FLAVOR" = "beta" ]; then
        UV_TOOL_DIR="$(_tool_root_dir beta)" UV_TOOL_BIN_DIR="$(_tool_bin_dir beta)" \
            uv tool install "$SCRIPT_DIR[ui]" --force --reinstall
        _install_beta_shims
    else
        uv tool install "$SCRIPT_DIR[ui]" --force --reinstall
    fi
    mkdir -p "$STATE_DIR"
    _source_commit > "$COMMIT_STAMP"
    _ok "Installed${FLAVOR_LABEL} snapshot at source commit $(_installed_commit)."
}

# ---------------------------------------------------------------------------
# Beta-only: ao-beta / ao-bench-beta launcher shims in $SHIM_BIN_DIR.
# Each `exec`s straight into the beta venv's own (unqualified) entry point and
# forwards all args, so the shim's behavior is always whatever the beta venv
# actually does -- no logic duplicated here. Regenerated (overwritten) on
# every beta install, so re-running is idempotent: the same flavor + HOME/XDG
# dirs always produce the exact same shim file.
# ---------------------------------------------------------------------------
_beta_shim() {
    local shim_name="$1"
    local target="$2"
    # NOTE: a later assignment in the same `local` statement cannot see an
    # earlier one under `set -u` (bash expands all RHS values before binding
    # any of them locally) -- keep these as separate statements.
    local shim_path="$SHIM_BIN_DIR/$shim_name"
    mkdir -p "$SHIM_BIN_DIR"
    cat > "$shim_path" <<SHIM
#!/usr/bin/env bash
# Auto-generated by install.sh (AO_FLAVOR=beta). Do not edit by hand --
# re-run 'AO_FLAVOR=beta bash install.sh' (or 'bash install.sh --flavor beta')
# to regenerate.
exec "$target" "\$@"
SHIM
    chmod +x "$shim_path"
}

_install_beta_shims() {
    local bin_dir
    bin_dir="$(_tool_bin_dir beta)"
    _beta_shim "$COMMAND_NAME" "$bin_dir/ao"
    _beta_shim "$BENCH_COMMAND_NAME" "$bin_dir/ao-bench"
    _ok "Beta shims installed: $SHIM_BIN_DIR/$COMMAND_NAME, $SHIM_BIN_DIR/$BENCH_COMMAND_NAME"
}

# Decide what to do based on presence + staleness, gating UPGRADES on confirmation.
_install_or_upgrade() {
    local src installed present=1 routing=1
    src="$(_source_commit)"
    installed="$(_installed_commit)"
    _ao_present || present=0
    _installed_has_routing "$FLAVOR" || routing=0

    # A snapshot is "stale" if: no stamp, stamp != current source, or the installed
    # build lacks routing (older than the run-control engine work).
    local stale=0
    { [ "$installed" = "none" ] || [ "$installed" != "$src" ] || [ "$routing" -eq 0 ]; } && stale=1

    if [ "$CHECK_ONLY" -eq 1 ]; then
        if [ "$present" -eq 0 ]; then
            _warn "$COMMAND_NAME is NOT installed."; return 1
        fi
        _info "installed snapshot: ${installed}  |  source HEAD: ${src}  |  routing: $([ $routing -eq 1 ] && echo yes || echo NO)"
        if [ "$stale" -eq 1 ]; then _warn "$COMMAND_NAME is STALE — upgrade recommended."; return 1; fi
        _ok "$COMMAND_NAME is up to date."; return 0
    fi

    if [ "$present" -eq 0 ]; then
        _info "$COMMAND_NAME is not installed — installing."
        _do_install
        return 0
    fi

    if [ "$FORCE" -eq 1 ]; then
        _info "Reinstalling current source snapshot (--force)."
        _do_install
        return 0
    fi

    if [ "$stale" -eq 0 ]; then
        _ok "$COMMAND_NAME is already up to date (snapshot ${installed}, routing-capable). Nothing to do."
        return 0
    fi

    _warn "Installed $COMMAND_NAME snapshot is stale:"
    _warn "  installed: ${installed}   source HEAD: ${src}   routing-capable: $([ $routing -eq 1 ] && echo yes || echo NO)"
    if _confirm "Upgrade the installed '$COMMAND_NAME' to the current source snapshot?"; then
        _do_install
    else
        _info "Left the existing '$COMMAND_NAME' unchanged. Re-run with --yes to upgrade later."
    fi
}

# ---------------------------------------------------------------------------
# Step 3: Verify
# ---------------------------------------------------------------------------

_verify() {
    if ! command -v "$COMMAND_NAME" >/dev/null 2>&1; then
        _warn "'$COMMAND_NAME' is not on PATH yet. Run:  source \"\$HOME/.local/bin/env\"  (or restart your shell)"
        return 1
    fi
    _ok "$COMMAND_NAME is available: $("$COMMAND_NAME" --version 2>/dev/null || echo '(version check skipped)')"
    if _installed_has_routing "$FLAVOR"; then
        _ok "routing / circuit-breaker support: ENABLED"
    else
        _warn "routing / circuit-breaker support: NOT detected in the installed $COMMAND_NAME."
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    if [ "$CHECK_ONLY" -eq 1 ]; then
        _install_or_upgrade
        exit $?
    fi
    _info "Starting AO${FLAVOR_LABEL} installation..."
    _ensure_uv
    _install_or_upgrade
    _verify || true

    echo ""
    _ok "Done."
    echo ""
    echo "  Next steps:"
    echo "    1. If '$COMMAND_NAME' is not found, restart your shell or run:"
    echo "         source \"\$HOME/.local/bin/env\""
    echo "    2. In your project directory, run:  $COMMAND_NAME init"
    echo "    3. Edit .ao/config.yaml, then:  $COMMAND_NAME validate && $COMMAND_NAME run"
    echo ""
    if [ "$FLAVOR" = "beta" ]; then
        echo "  Note: .ao/config.yaml and AO_* env vars are SHARED with the stable install"
        echo "        (resolved per-project at runtime, not namespaced by flavor)."
        echo ""
    fi
    echo "  Documentation: $AO_REPO_URL"
}

main "$@"
