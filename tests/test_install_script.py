"""Tests for install.sh's dual-flavor (stable/beta) installer support.

SAFETY: every test here runs install.sh with HOME (and therefore the
XDG_STATE_HOME/XDG_DATA_HOME defaults it derives -- ~/.local/state,
~/.local/share, ~/.local/bin) redirected to a per-test tmp_path, and with a
FAKE `uv` shimmed first onto PATH. The fake `uv` records what it was called
with and exits 0 without installing anything for real or touching the
network. Env is passed explicitly via subprocess `env=` (never process-wide
os.environ / monkeypatch), so these tests can never read or write the real
developer $HOME and never make a network call.

Establishes the pattern for install-script tests (none existed before this
file): fake `uv` first on PATH + temp HOME, subprocess through `bash
install.sh ...` -- the outermost possible boundary, matching how a user
actually invokes it.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win") or shutil.which("bash") is None,
    reason="install.sh is a POSIX/bash script",
)

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"

_FAKE_UV_SCRIPT = """\
#!/usr/bin/env bash
# Fake `uv` for install.sh tests: records this invocation's argv and the
# UV_TOOL_DIR/UV_TOOL_BIN_DIR it sees to $FAKE_UV_LOG, then exits 0 without
# installing anything or touching the network. `tool dir`/`tool dir --bin`
# mirror real uv's env-driven behavior (verified against real uv 0.5.9) so
# install.sh's own path-resolution helpers are exercised faithfully even for
# the stable flavor, which shells out to the real subcommand.
set -euo pipefail
: "${FAKE_UV_LOG:?FAKE_UV_LOG must be set}"
{
    printf 'ARGV:'
    for a in "$@"; do printf ' %q' "$a"; done
    printf '\\n'
    printf 'UV_TOOL_DIR=%s\\n' "${UV_TOOL_DIR-<unset>}"
    printf 'UV_TOOL_BIN_DIR=%s\\n' "${UV_TOOL_BIN_DIR-<unset>}"
    printf '===\\n'
} >> "$FAKE_UV_LOG"

case "${1-}" in
    --version)
        echo "uv 0.0.0 (fake, for tests)"
        exit 0
        ;;
    tool)
        case "${2-}" in
            install)
                exit 0
                ;;
            dir)
                if [ "${3-}" = "--bin" ]; then
                    echo "${UV_TOOL_BIN_DIR:-$HOME/.local/bin}"
                else
                    echo "${UV_TOOL_DIR:-$HOME/.local/share/uv/tools}"
                fi
                ;;
            *)
                exit 0
                ;;
        esac
        ;;
    *)
        exit 0
        ;;
esac
"""


@dataclass(frozen=True)
class UvCall:
    argv: list[str]
    env: dict[str, str]


def _parse_uv_log(log_path: Path) -> list[UvCall]:
    """Parse the fake uv's recorded invocations (see _FAKE_UV_SCRIPT)."""
    if not log_path.exists():
        return []
    calls: list[UvCall] = []
    block: list[str] = []
    for line in log_path.read_text().splitlines():
        if line == "===":
            argv_line = block[0]
            assert argv_line.startswith("ARGV:")
            argv = shlex.split(argv_line[len("ARGV:") :])
            env = dict(entry.split("=", 1) for entry in block[1:])
            calls.append(UvCall(argv=argv, env=env))
            block = []
        else:
            block.append(line)
    return calls


def _tool_install_calls(calls: list[UvCall]) -> list[UvCall]:
    return [c for c in calls if c.argv[:2] == ["tool", "install"]]


@pytest.fixture
def fake_uv_dir(tmp_path: Path) -> Path:
    """A directory containing a fake `uv` executable, for prepending to PATH."""
    bin_dir = tmp_path / "fake-uv-bin"
    bin_dir.mkdir()
    uv_path = bin_dir / "uv"
    uv_path.write_text(_FAKE_UV_SCRIPT)
    uv_path.chmod(0o755)
    return bin_dir


@pytest.fixture
def home_dir(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    # Defensive guard against ever accidentally resolving to the real $HOME.
    assert home.resolve() != Path(os.path.expanduser("~")).resolve()
    return home


@pytest.fixture
def uv_log(tmp_path: Path) -> Path:
    return tmp_path / "uv-calls.log"


def run_install(
    *,
    home_dir: Path,
    fake_uv_dir: Path,
    uv_log: Path,
    args: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run install.sh in full isolation: fake uv first on PATH, temp HOME.

    Builds an explicit env dict (never mutates process-wide os.environ) so no
    state can leak between tests or into the real shell.
    """
    env = {
        "HOME": str(home_dir),
        "PATH": f"{fake_uv_dir}:{_sanitized_real_path()}",
        "FAKE_UV_LOG": str(uv_log),
        # Force deterministic defaulting of the XDG_* dirs install.sh reads.
        "TERM": os.environ.get("TERM", "dumb"),
    }
    for key in ("XDG_STATE_HOME", "XDG_DATA_HOME", "AO_FLAVOR", "UV_TOOL_DIR", "UV_TOOL_BIN_DIR"):
        env.pop(key, None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(INSTALL_SH), *(args or [])],
        env=env,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )


def _source_head() -> str:
    return subprocess.check_output(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"], text=True
    ).strip()


def _sanitized_real_path() -> str:
    """Inherited PATH with the real $HOME's own bin dir stripped out.

    install.sh's presence check (`command -v ao`/`ao-beta`) walks the whole
    PATH, not just the isolated temp $HOME. On a machine that has ever run
    this installer for real, the real $HOME/.local/bin already has `ao`/
    `ao-beta` on it -- inheriting that into the subprocess PATH would leak
    real installed-state into scenarios meant to look "not installed".
    """
    real_local_bin = str(Path(os.path.expanduser("~")).resolve() / ".local" / "bin")
    return os.pathsep.join(
        p
        for p in os.environ.get("PATH", "").split(os.pathsep)
        if p and Path(p).resolve() != Path(real_local_bin)
    )


# ---------------------------------------------------------------------------
# 2.1 / 2.2 -- flavor selection + namespacing
# ---------------------------------------------------------------------------


def test_default_flavor_is_stable_and_matches_pre_flavor_behavior(
    home_dir: Path, fake_uv_dir: Path, uv_log: Path
) -> None:
    """AO_FLAVOR unset (and no --flavor) must behave exactly as before flavors existed."""
    result = run_install(home_dir=home_dir, fake_uv_dir=fake_uv_dir, uv_log=uv_log, args=["--yes"])
    assert result.returncode == 0, result.stderr

    state_dir = home_dir / ".local" / "state" / "ao-install"
    stamp = state_dir / "installed.commit"
    assert stamp.exists()
    assert stamp.read_text().strip() == _source_head()
    assert not (state_dir / "beta.commit").exists()

    # No beta shims for the default flavor.
    assert not (home_dir / ".local" / "bin" / "ao-beta").exists()
    assert not (home_dir / ".local" / "bin" / "ao-bench-beta").exists()

    installs = _tool_install_calls(_parse_uv_log(uv_log))
    assert len(installs) == 1
    call = installs[0]
    assert call.argv[2] == f"{REPO_ROOT}[ui]"
    assert "--force" in call.argv
    assert "--reinstall" in call.argv
    # Stable must NOT set UV_TOOL_DIR/UV_TOOL_BIN_DIR -- uv's own defaults, untouched.
    assert call.env["UV_TOOL_DIR"] == "<unset>"
    assert call.env["UV_TOOL_BIN_DIR"] == "<unset>"


def test_beta_flavor_via_env_var_is_fully_namespaced(
    home_dir: Path, fake_uv_dir: Path, uv_log: Path
) -> None:
    result = run_install(
        home_dir=home_dir,
        fake_uv_dir=fake_uv_dir,
        uv_log=uv_log,
        args=["--yes"],
        extra_env={"AO_FLAVOR": "beta"},
    )
    assert result.returncode == 0, result.stderr

    state_dir = home_dir / ".local" / "state" / "ao-install"
    assert (state_dir / "beta.commit").read_text().strip() == _source_head()
    assert not (state_dir / "installed.commit").exists()

    expected_tool_dir = home_dir / ".local" / "share" / "ao-beta" / "uv-tools"
    expected_bin_dir = home_dir / ".local" / "share" / "ao-beta" / "bin"

    installs = _tool_install_calls(_parse_uv_log(uv_log))
    assert len(installs) == 1
    call = installs[0]
    assert call.argv[2] == f"{REPO_ROOT}[ui]"
    assert call.env["UV_TOOL_DIR"] == str(expected_tool_dir)
    assert call.env["UV_TOOL_BIN_DIR"] == str(expected_bin_dir)

    # Beta shims: exec straight into the beta venv's own (unqualified) entry points.
    ao_shim = home_dir / ".local" / "bin" / "ao-beta"
    bench_shim = home_dir / ".local" / "bin" / "ao-bench-beta"
    assert ao_shim.exists() and os.access(ao_shim, os.X_OK)
    assert bench_shim.exists() and os.access(bench_shim, os.X_OK)
    assert f'exec "{expected_bin_dir}/ao" "$@"' in ao_shim.read_text()
    assert f'exec "{expected_bin_dir}/ao-bench" "$@"' in bench_shim.read_text()


def test_flavor_flag_overrides_env_var(home_dir: Path, fake_uv_dir: Path, uv_log: Path) -> None:
    result = run_install(
        home_dir=home_dir,
        fake_uv_dir=fake_uv_dir,
        uv_log=uv_log,
        args=["--flavor", "stable", "--yes"],
        extra_env={"AO_FLAVOR": "beta"},
    )
    assert result.returncode == 0, result.stderr

    state_dir = home_dir / ".local" / "state" / "ao-install"
    assert (state_dir / "installed.commit").exists()
    assert not (state_dir / "beta.commit").exists()

    installs = _tool_install_calls(_parse_uv_log(uv_log))
    assert installs[0].env["UV_TOOL_DIR"] == "<unset>"


def test_disjoint_paths_between_flavors_under_same_home(
    home_dir: Path, fake_uv_dir: Path, uv_log: Path
) -> None:
    """Both flavors installed under the same $HOME must never share a path."""
    stable = run_install(home_dir=home_dir, fake_uv_dir=fake_uv_dir, uv_log=uv_log, args=["--yes"])
    beta = run_install(
        home_dir=home_dir,
        fake_uv_dir=fake_uv_dir,
        uv_log=uv_log,
        args=["--yes"],
        extra_env={"AO_FLAVOR": "beta"},
    )
    assert stable.returncode == 0, stable.stderr
    assert beta.returncode == 0, beta.stderr

    state_dir = home_dir / ".local" / "state" / "ao-install"
    stable_stamp = state_dir / "installed.commit"
    beta_stamp = state_dir / "beta.commit"
    assert stable_stamp.exists()
    assert beta_stamp.exists()

    calls = _tool_install_calls(_parse_uv_log(uv_log))
    assert len(calls) == 2
    stable_call, beta_call = calls
    assert stable_call.env["UV_TOOL_DIR"] == "<unset>"
    assert beta_call.env["UV_TOOL_DIR"] != "<unset>"
    assert beta_call.env["UV_TOOL_DIR"] != stable_call.env["UV_TOOL_DIR"]
    assert beta_call.env["UV_TOOL_BIN_DIR"] != stable_call.env["UV_TOOL_BIN_DIR"]

    # Only the beta run produces shims.
    assert (home_dir / ".local" / "bin" / "ao-beta").exists()


def test_beta_reinstall_is_idempotent(home_dir: Path, fake_uv_dir: Path, uv_log: Path) -> None:
    first = run_install(
        home_dir=home_dir,
        fake_uv_dir=fake_uv_dir,
        uv_log=uv_log,
        args=["--yes"],
        extra_env={"AO_FLAVOR": "beta"},
    )
    ao_shim = home_dir / ".local" / "bin" / "ao-beta"
    bench_shim = home_dir / ".local" / "bin" / "ao-bench-beta"
    assert first.returncode == 0, first.stderr
    first_ao_content = ao_shim.read_text()
    first_bench_content = bench_shim.read_text()
    first_stamp = (home_dir / ".local" / "state" / "ao-install" / "beta.commit").read_text()

    second = run_install(
        home_dir=home_dir,
        fake_uv_dir=fake_uv_dir,
        uv_log=uv_log,
        args=["--yes"],
        extra_env={"AO_FLAVOR": "beta"},
    )
    assert second.returncode == 0, second.stderr
    assert ao_shim.read_text() == first_ao_content
    assert bench_shim.read_text() == first_bench_content
    assert (home_dir / ".local" / "state" / "ao-install" / "beta.commit").read_text() == first_stamp
    assert os.access(ao_shim, os.X_OK)
    assert os.access(bench_shim, os.X_OK)


# ---------------------------------------------------------------------------
# Invalid flavor handling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args,extra_env",
    [
        ([], {"AO_FLAVOR": "bogus"}),
        (["--flavor", "bogus"], {}),
        (["--flavor=bogus"], {}),
    ],
)
def test_invalid_flavor_rejected(
    home_dir: Path,
    fake_uv_dir: Path,
    uv_log: Path,
    args: list[str],
    extra_env: dict[str, str],
) -> None:
    result = run_install(
        home_dir=home_dir, fake_uv_dir=fake_uv_dir, uv_log=uv_log, args=args, extra_env=extra_env
    )
    assert result.returncode != 0
    assert "invalid flavor" in result.stderr.lower()
    # Nothing should have been touched.
    assert not (home_dir / ".local" / "state").exists()
    assert _tool_install_calls(_parse_uv_log(uv_log)) == []


def test_flavor_flag_missing_value_rejected(
    home_dir: Path, fake_uv_dir: Path, uv_log: Path
) -> None:
    result = run_install(
        home_dir=home_dir, fake_uv_dir=fake_uv_dir, uv_log=uv_log, args=["--flavor"]
    )
    assert result.returncode != 0
    assert "--flavor" in result.stderr


# ---------------------------------------------------------------------------
# Existing flags keep working per-flavor (2.1)
# ---------------------------------------------------------------------------


def test_check_reports_missing_for_beta_without_touching_network(
    home_dir: Path, fake_uv_dir: Path, uv_log: Path
) -> None:
    result = run_install(
        home_dir=home_dir,
        fake_uv_dir=fake_uv_dir,
        uv_log=uv_log,
        args=["--check"],
        extra_env={"AO_FLAVOR": "beta"},
    )
    assert result.returncode == 1
    assert "ao-beta" in result.stderr
    assert "NOT installed" in result.stderr
    # --check never installs.
    assert _tool_install_calls(_parse_uv_log(uv_log)) == []


def test_help_documents_both_flavors_and_shared_config(
    home_dir: Path, fake_uv_dir: Path, uv_log: Path
) -> None:
    result = run_install(home_dir=home_dir, fake_uv_dir=fake_uv_dir, uv_log=uv_log, args=["--help"])
    assert result.returncode == 0
    assert "AO_FLAVOR" in result.stdout
    assert "ao-beta" in result.stdout
    assert "share" in result.stdout.lower()
