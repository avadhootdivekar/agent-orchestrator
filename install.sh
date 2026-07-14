#!/usr/bin/env bash
# install.sh — One-command installer / upgrader for AO (Agent Orchestrator).
#
# Usage:
#   bash install.sh              # install if missing; if an OLDER snapshot is
#                                #   installed, ask before upgrading it
#   bash install.sh --yes        # non-interactive: install/upgrade without asking
#   bash install.sh --check      # report status only; exit 1 if missing/stale
#   bash install.sh --force      # reinstall the current source snapshot unconditionally
#
# What this script does:
#   1. Checks for `uv`; installs it via the official installer if missing.
#   2. Installs the `ao` CLI globally via `uv tool install` (a NON-editable
#      snapshot — see the isolation note below).
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
COMMIT_STAMP="$STATE_DIR/installed.commit"

ASSUME_YES=0
CHECK_ONLY=0
FORCE=0
for arg in "$@"; do
    case "$arg" in
        --yes|-y)  ASSUME_YES=1 ;;
        --check)   CHECK_ONLY=1 ;;
        --force|-f) FORCE=1 ;;
        -h|--help)
            sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) printf 'ERROR: unknown argument: %s\n' "$arg" >&2; exit 1 ;;
    esac
done

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

_ao_present() { command -v ao >/dev/null 2>&1; }

# Best-effort routing-capability probe of the INSTALLED tool (independent of the
# commit stamp). Returns 0 = routing-capable, 1 = stale/unknown.
_installed_has_routing() {
    local tooldir py
    tooldir="$(uv tool dir 2>/dev/null || echo "$HOME/.local/share/uv/tools")/agent-orchestrator"
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
    _info "Installing AO snapshot from: $SCRIPT_DIR"
    # --force alone is NOT sufficient: observed uv resolving a stale cached wheel (months
    # old, missing entire epics) despite --force reporting success, because uv's local-path
    # build cache isn't guaranteed to key on live source content on every code path.
    # --reinstall forces uv to actually rebuild/relink rather than reuse a cached resolution.
    uv tool install "$SCRIPT_DIR" --force --reinstall
    mkdir -p "$STATE_DIR"
    _source_commit > "$COMMIT_STAMP"
    _ok "Installed snapshot at source commit $(_installed_commit)."
}

# Decide what to do based on presence + staleness, gating UPGRADES on confirmation.
_install_or_upgrade() {
    local src installed present=1 routing=1
    src="$(_source_commit)"
    installed="$(_installed_commit)"
    _ao_present || present=0
    _installed_has_routing || routing=0

    # A snapshot is "stale" if: no stamp, stamp != current source, or the installed
    # build lacks routing (older than the run-control engine work).
    local stale=0
    { [ "$installed" = "none" ] || [ "$installed" != "$src" ] || [ "$routing" -eq 0 ]; } && stale=1

    if [ "$CHECK_ONLY" -eq 1 ]; then
        if [ "$present" -eq 0 ]; then
            _warn "ao is NOT installed."; return 1
        fi
        _info "installed snapshot: ${installed}  |  source HEAD: ${src}  |  routing: $([ $routing -eq 1 ] && echo yes || echo NO)"
        if [ "$stale" -eq 1 ]; then _warn "ao is STALE — upgrade recommended."; return 1; fi
        _ok "ao is up to date."; return 0
    fi

    if [ "$present" -eq 0 ]; then
        _info "ao is not installed — installing."
        _do_install
        return 0
    fi

    if [ "$FORCE" -eq 1 ]; then
        _info "Reinstalling current source snapshot (--force)."
        _do_install
        return 0
    fi

    if [ "$stale" -eq 0 ]; then
        _ok "ao is already up to date (snapshot ${installed}, routing-capable). Nothing to do."
        return 0
    fi

    _warn "Installed ao snapshot is stale:"
    _warn "  installed: ${installed}   source HEAD: ${src}   routing-capable: $([ $routing -eq 1 ] && echo yes || echo NO)"
    if _confirm "Upgrade the installed 'ao' to the current source snapshot?"; then
        _do_install
    else
        _info "Left the existing 'ao' unchanged. Re-run with --yes to upgrade later."
    fi
}

# ---------------------------------------------------------------------------
# Step 3: Verify
# ---------------------------------------------------------------------------

_verify() {
    if ! command -v ao >/dev/null 2>&1; then
        _warn "'ao' is not on PATH yet. Run:  source \"\$HOME/.local/bin/env\"  (or restart your shell)"
        return 1
    fi
    _ok "ao is available: $(ao --version 2>/dev/null || echo '(version check skipped)')"
    if _installed_has_routing; then
        _ok "routing / circuit-breaker support: ENABLED"
    else
        _warn "routing / circuit-breaker support: NOT detected in the installed ao."
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
    _info "Starting AO installation..."
    _ensure_uv
    _install_or_upgrade
    _verify || true

    echo ""
    _ok "Done."
    echo ""
    echo "  Next steps:"
    echo "    1. If 'ao' is not found, restart your shell or run:"
    echo "         source \"\$HOME/.local/bin/env\""
    echo "    2. In your project directory, run:  ao init"
    echo "    3. Edit .ao/config.yaml, then:  ao validate && ao run"
    echo ""
    echo "  Documentation: $AO_REPO_URL"
}

main "$@"
