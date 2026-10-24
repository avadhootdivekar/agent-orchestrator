"""Bench-owned toolchains (fake tier: a local tarball stands in for the download)."""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import pytest

from agent_orchestrator.bench import toolchains as tcmod
from agent_orchestrator.bench.errors import BenchError
from agent_orchestrator.bench.parity import ParityBundle
from agent_orchestrator.bench.workspace import parity_env


def _fake_toolchain(tmp_path: Path) -> tcmod.Toolchain:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"#!/bin/sh\necho fake 1.0\n"
        info = tarfile.TarInfo("fake/bin/fakec")
        info.size, info.mode = len(data), 0o755
        tf.addfile(info, io.BytesIO(data))
    archive = tmp_path / "fake.tar.gz"
    archive.write_bytes(buf.getvalue())
    return tcmod.Toolchain(
        name="fake",
        version="1.0",
        url=archive.as_uri(),
        sha256=hashlib.sha256(buf.getvalue()).hexdigest(),
        bin_dir="fake/bin",
        env={"FAKE_HOME": "{home}/state"},
    )


def test_install_verifies_extracts_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tc = _fake_toolchain(tmp_path)
    monkeypatch.setitem(tcmod.TOOLCHAINS, "fake", tc)
    target = tcmod.install(tc)
    assert (target / "fake" / "bin" / "fakec").is_file() and tcmod.is_installed(tc)
    assert tcmod.install(tc) == target  # second call: no download


def test_checksum_mismatch_is_refused(tmp_path: Path) -> None:
    tc = _fake_toolchain(tmp_path).model_copy(update={"sha256": "0" * 64, "version": "2.0"})
    with pytest.raises(BenchError, match="sha256 mismatch"):
        tcmod.install(tc)
    assert not tcmod.is_installed(tc)


def test_env_prefixes_path_and_writes_claude_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tc = _fake_toolchain(tmp_path)
    monkeypatch.setitem(tcmod.TOOLCHAINS, "fake", tc)
    env = tcmod.toolchain_env(["fake"], base_path="/usr/bin")
    bin_dir = str(tcmod.install_dir(tc) / "fake" / "bin")
    assert env["PATH"] == f"{bin_dir}:/usr/bin"
    assert env["FAKE_HOME"] == f"{tcmod.install_dir(tc)}/state"
    script = Path(env["CLAUDE_ENV_FILE"]).read_text()
    assert f'export PATH="{bin_dir}:$PATH"' in script and "export FAKE_HOME=" in script
    assert tcmod.toolchain_env([]) == {}


def test_unknown_toolchain_is_an_error() -> None:
    with pytest.raises(BenchError, match="unknown toolchain"):
        tcmod.toolchain_env(["cobol"])


def test_parity_env_merges_toolchains_under_the_bundle_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tc = _fake_toolchain(tmp_path)
    monkeypatch.setitem(tcmod.TOOLCHAINS, "fake", tc)
    bundle = ParityBundle(toolchains=["fake"], env={"FAKE_HOME": "/bundle/wins"})
    env = parity_env(bundle)
    assert env["FAKE_HOME"] == "/bundle/wins" and "CLAUDE_ENV_FILE" in env
