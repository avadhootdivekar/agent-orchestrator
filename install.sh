#!/usr/bin/env bash
# install.sh — One-command installer for AO (Agent Orchestrator).
#
# Usage:
#   bash install.sh
#
# What this script does:
#   1. Checks for `uv`; installs it via the official installer if missing.
#   2. Installs the `ao` CLI globally using `uv tool install`.
#   3. Prints a success message with next steps.
#
# Idempotent: safe to re-run.  Re-running updates `ao` to the latest version
# pinned in the package.
#
# ----------------------------------------------------------------------------
# WEB-HOSTED VERSION NOTE:
# To host this installer on the web so users can run:
#   curl -fsSL https://your-domain.example.com/install.sh | bash
# or:
#   wget -qO- https://your-domain.example.com/install.sh | bash
#
# Simply serve this file as a static asset (GitHub raw, S3, CDN, etc.) at the
# URL above.  No other changes are needed — the script is self-contained.
# You may also publish the package to PyPI and replace the `--editable` path
# below with the package name:
#   uv tool install agent-orchestrator
# ----------------------------------------------------------------------------

set -euo pipefail

AO_REPO_URL="https://github.com/your-org/agent-orchestrator"  # placeholder
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_info()  { printf '\033[1;34m[ao-install]\033[0m %s\n' "$*"; }
_ok()    { printf '\033[1;32m[ao-install]\033[0m %s\n' "$*"; }
_warn()  { printf '\033[1;33m[ao-install]\033[0m WARNING: %s\n' "$*" >&2; }
_error() { printf '\033[1;31m[ao-install]\033[0m ERROR: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Step 1: Ensure `uv` is available
# ---------------------------------------------------------------------------

_ensure_uv() {
    if command -v uv >/dev/null 2>&1; then
        _info "uv is already installed: $(uv --version)"
        return 0
    fi

    _info "uv not found — installing via the official installer..."
    # Official uv installer: https://docs.astral.sh/uv/getting-started/installation/
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL https://astral.sh/uv/install.sh | sh
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- https://astral.sh/uv/install.sh | sh
    else
        _error "Neither curl nor wget is available.  Install one and re-run this script."
    fi

    # The uv installer places the binary in ~/.local/bin (or ~/.cargo/bin on some systems).
    # Source the shell env update if the installer dropped a script.
    if [ -f "$HOME/.local/bin/uv" ]; then
        export PATH="$HOME/.local/bin:$PATH"
    elif [ -f "$HOME/.cargo/bin/uv" ]; then
        export PATH="$HOME/.cargo/bin:$PATH"
    fi

    if ! command -v uv >/dev/null 2>&1; then
        _error "uv installation succeeded but 'uv' is not on PATH.  Add ~/.local/bin to your PATH and re-run."
    fi

    _ok "uv installed: $(uv --version)"
}

# ---------------------------------------------------------------------------
# Step 2: Install the `ao` CLI globally via `uv tool install`
# ---------------------------------------------------------------------------

_install_ao() {
    _info "Installing AO via uv tool install..."

    # Determine install source:
    # - If running from a local checkout (SCRIPT_DIR contains pyproject.toml),
    #   install from the local source in editable mode.
    # - Otherwise install from PyPI (once the package is published there).
    if [ -f "$SCRIPT_DIR/pyproject.toml" ]; then
        _info "Installing from local source: $SCRIPT_DIR"
        # Non-editable: uv tool copies the built package into an isolated venv.
        # This snapshot is unaffected by subsequent edits in the source tree —
        # critical for the AO-v1/v2 isolation pattern where you run a stable
        # v1 `ao` binary to orchestrate development of v2 in this repo.
        # Re-running install.sh promotes the current source to the new v1.
        uv tool install "$SCRIPT_DIR" --force
    else
        # Future: install from PyPI once the package is published.
        # uv tool install agent-orchestrator
        _error "Could not locate pyproject.toml relative to install.sh.  Please run install.sh from the repository root."
    fi
}

# ---------------------------------------------------------------------------
# Step 3: Verify the install
# ---------------------------------------------------------------------------

_verify() {
    if ! command -v ao >/dev/null 2>&1; then
        _warn "'ao' is not on PATH yet.  You may need to run:"
        _warn "  source \"\$HOME/.local/bin/env\"  (or restart your shell)"
        return 1
    fi
    _ok "ao is available: $(ao --version 2>/dev/null || echo '(version check skipped)')"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    _info "Starting AO installation..."
    _ensure_uv
    _install_ao
    _verify || true

    echo ""
    _ok "Installation complete!"
    echo ""
    echo "  Next steps:"
    echo "    1. If 'ao' is not found, restart your shell or run:"
    echo "         source \"\$HOME/.local/bin/env\""
    echo "    2. In your project directory, run:"
    echo "         ao init"
    echo "       to create a .ao/config.yaml with example fields."
    echo "    3. Edit .ao/config.yaml with your workflow, reposets, and agents paths."
    echo "    4. Run:  ao validate && ao run"
    echo ""
    echo "  Documentation: $AO_REPO_URL"
}

main "$@"
