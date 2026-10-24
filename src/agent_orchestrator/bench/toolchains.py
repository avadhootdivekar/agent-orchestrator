"""Bench-owned language toolchains for the agent under test (E-Bn7Dsc portfolio work).

Generated TypeScript/Go tasks and polyglot exercises are only fair when the agent can build and
test in that language on its own host. The operator's machine may not have the toolchain (Go
missing, Node 18 without `node:sqlite`), and installing one globally would change the operator's
environment for everything else. So a parity bundle names the toolchains it needs
(`ParityBundle.toolchains`, part of its fingerprint) and the harness:

1. installs each pinned, checksum-verified archive under `<bench base>/toolchains/` on first use
   (`ensure_installed`; `ao-bench toolchains --install` does it ahead of time);
2. hands every subject the same environment (`toolchain_env`): `PATH` prefixed with the
   toolchains' `bin` dirs (for the subject process itself, `ao` hooks, graders run by agents) and
   `CLAUDE_ENV_FILE` pointing at a generated script that exports that PATH -- Claude Code sources
   that file before every Bash command, which is what makes the toolchain visible to the agent's
   shell whatever the login profile does.

Graders never use these: grading runs in the per-language Docker images (`bench/sandbox.py`).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tarfile
import tempfile
import urllib.request
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel

from .errors import BenchError

TOOLCHAINS_DIRNAME = "toolchains"
ENV_FILE_PREFIX = "claude-env-"
ENV_CLAUDE_ENV_FILE = "CLAUDE_ENV_FILE"
_DOWNLOAD_TIMEOUT_SECONDS = 600
_INSTALLED_MARKER = ".ao-bench-installed"


class Toolchain(BaseModel):
    name: str
    version: str
    url: str
    sha256: str
    # Path of the `bin` directory relative to the install dir once the archive is extracted.
    bin_dir: str
    # Extra environment for the toolchain (values may use {home} = the install dir).
    env: dict[str, str] = {}


# Pinned: Go matches the grading image (`golang:1.26-alpine`); Node 22 has `node:sqlite`.
TOOLCHAINS: dict[str, Toolchain] = {
    "go": Toolchain(
        name="go",
        version="1.26.10",
        url="https://go.dev/dl/go1.26.10.linux-amd64.tar.gz",
        sha256="ccfe7ae822d2c2f41f4fe7c956199967a4dac22d0ee0d2b2bfdb440aab3b20c5",
        bin_dir="go/bin",
        # Module/build caches inside the bench tree, never the operator's ~/go.
        env={"GOPATH": "{home}/gopath", "GOCACHE": "{home}/gocache", "GOTOOLCHAIN": "local"},
    ),
    "node": Toolchain(
        name="node",
        version="22.23.4",
        url="https://nodejs.org/dist/v22.23.4/node-v22.23.4-linux-x64.tar.xz",
        sha256="1805bcd35e6815559bd2eb97db438d3fdb0eb3a5e6a977b7c1badca76337e9ce",
        bin_dir="node-v22.23.4-linux-x64/bin",
    ),
}


def toolchains_root() -> Path:
    from .workspace import bench_base_dir  # lazy: workspace -> parity -> (no cycle)

    return bench_base_dir() / TOOLCHAINS_DIRNAME


def _resolve(names: Iterable[str]) -> list[Toolchain]:
    out: list[Toolchain] = []
    for name in names:
        if name not in TOOLCHAINS:
            raise BenchError(f"unknown toolchain {name!r}; known: {sorted(TOOLCHAINS)}")
        out.append(TOOLCHAINS[name])
    return out


def install_dir(tc: Toolchain) -> Path:
    return toolchains_root() / f"{tc.name}-{tc.version}"


def is_installed(tc: Toolchain) -> bool:
    return (install_dir(tc) / _INSTALLED_MARKER).is_file()


def _download(url: str, dest: Path) -> str:
    h = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as resp:  # noqa: S310 -- pinned https URL
        with dest.open("wb") as fh:
            while chunk := resp.read(1 << 20):
                h.update(chunk)
                fh.write(chunk)
    return h.hexdigest()


def install(tc: Toolchain) -> Path:
    """Download, verify (sha256) and extract *tc*; idempotent. Raises `BenchError`."""
    target = install_dir(tc)
    if is_installed(tc):
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        archive = Path(tmp) / Path(tc.url).name
        try:
            digest = _download(tc.url, archive)
        except OSError as exc:
            raise BenchError(f"toolchain {tc.name}: download failed: {exc}") from exc
        if digest != tc.sha256:
            raise BenchError(f"toolchain {tc.name}: sha256 mismatch ({digest} != {tc.sha256})")
        staging = Path(tmp) / "x"
        staging.mkdir()
        with tarfile.open(archive) as tf:
            tf.extractall(staging, filter="data")
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(staging), str(target))
    (target / _INSTALLED_MARKER).write_text(f"{tc.name} {tc.version}\n")
    return target


def ensure_installed(names: Iterable[str]) -> list[Toolchain]:
    tcs = _resolve(names)
    for tc in tcs:
        install(tc)
    return tcs


def toolchain_env(names: Iterable[str], *, base_path: str | None = None) -> dict[str, str]:
    """Environment every subject gets for the bundle's toolchains (empty for none)."""
    tcs = _resolve(names)
    if not tcs:
        return {}
    bins = [str(install_dir(tc) / tc.bin_dir) for tc in tcs]
    extra: dict[str, str] = {}
    for tc in tcs:
        for key, value in tc.env.items():
            extra[key] = value.format(home=str(install_dir(tc)))
    path = os.pathsep.join(
        [*bins, base_path if base_path is not None else os.environ.get("PATH", "")]
    )
    key = hashlib.sha256("|".join(f"{t.name}-{t.version}" for t in tcs).encode()).hexdigest()[:12]
    env_file = toolchains_root() / f"{ENV_FILE_PREFIX}{key}.sh"
    lines = [f'export PATH="{os.pathsep.join(bins)}:$PATH"']
    lines += [f'export {k}="{v}"' for k, v in sorted(extra.items())]
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text("\n".join(lines) + "\n")
    return {"PATH": path, ENV_CLAUDE_ENV_FILE: str(env_file), **extra}


__all__ = [
    "TOOLCHAINS",
    "Toolchain",
    "ensure_installed",
    "install",
    "install_dir",
    "is_installed",
    "toolchain_env",
    "toolchains_root",
]
