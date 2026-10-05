"""U-F1..U-F5: the executor fingerprint, the CLI-version reader and the shared path guards."""

from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.cache import fingerprint
from agent_orchestrator.cache.constants import (
    CLI_VERSION_TIMEOUT_SECONDS,
    KIND_ABSENT,
    KIND_DIR,
    KIND_FILE,
    MAX_CLI_VERSION_CHARS,
    REASON_EXECUTOR_FINGERPRINT_UNAVAILABLE,
    REASON_INPUT_NOT_REGULAR,
    REASON_PATH_IN_CACHE_DIR,
    REASON_PATH_REJECTED,
)
from agent_orchestrator.cache.fingerprint import (
    CLAUDE_CONTEXT_PATHS,
    CLAUDE_FINGERPRINT_ENV_VARS,
    CliVersionReader,
    claude_cli_fingerprint,
    guarded_abs,
    guarded_resolve,
)
from agent_orchestrator.cache.hashing import HashBudget
from agent_orchestrator.cache.keys import build_cache_key
from agent_orchestrator.cache.types import KeyDeps, UncacheableError
from tests.cache.fakes import fake_cli_version_of
from tests.cache.keys_fixture import (
    GV1_CLI_VERSION,
    default_agent,
    make_deps,
    make_request,
    write_files,
)

needs_posix = pytest.mark.skipif(sys.platform == "win32", reason="shell-script binaries / symlinks")


@pytest.fixture(autouse=True)
def _cache_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AO_CACHE", raising=False)


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    write_files(root)
    return root


def make_binary(path: Path, version: str = "9.9.9 (Claude Code)", *, exit_code: int = 0) -> Path:
    path.write_text(f"#!/bin/sh\necho '{version}'\nexit {exit_code}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class RunSpy:
    """Counts `subprocess.run` calls made through the fingerprint module, then delegates."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[list[str]] = []
        self.kwargs: list[dict[str, Any]] = []
        real = subprocess.run

        def spy(argv: list[str], **kw: Any) -> Any:
            self.calls.append(list(argv))
            self.kwargs.append(kw)
            return real(argv, **kw)

        monkeypatch.setattr(fingerprint.subprocess, "run", spy)


def unavailable(fn: Any, *args: Any) -> UncacheableError:
    with pytest.raises(UncacheableError) as info:
        fn(*args)
    assert info.value.reason == REASON_EXECUTOR_FINGERPRINT_UNAVAILABLE
    return info.value


# ------------------------------------------------------------------------------------ U-F1
@needs_posix
def test_the_version_is_read_once_per_binary_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = make_binary(tmp_path / "claude")
    spy = RunSpy(monkeypatch)
    reader = CliVersionReader()
    assert reader.version(str(binary)) == "9.9.9 (Claude Code)"
    assert reader.version(str(binary)) == "9.9.9 (Claude Code)"
    assert len(spy.calls) == 1
    assert spy.calls[0] == [os.path.realpath(binary), "--version"]
    assert spy.kwargs[0]["timeout"] == CLI_VERSION_TIMEOUT_SECONDS
    assert spy.kwargs[0].get("shell") is not True


@needs_posix
def test_the_version_is_reread_after_the_binary_mtime_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = make_binary(tmp_path / "claude", "1.0.0 (Claude Code)")
    spy = RunSpy(monkeypatch)
    reader = CliVersionReader()
    assert reader.version(str(binary)) == "1.0.0 (Claude Code)"
    make_binary(binary, "2.0.0 (Claude Code)")  # same size, new content
    st = binary.stat()
    os.utime(binary, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert reader.version(str(binary)) == "2.0.0 (Claude Code)"
    assert len(spy.calls) == 2


@needs_posix
def test_the_version_is_reread_after_the_binary_size_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = make_binary(tmp_path / "claude", "1.0.0 (Claude Code)")
    st = binary.stat()
    spy = RunSpy(monkeypatch)
    reader = CliVersionReader()
    reader.version(str(binary))
    make_binary(binary, "1.0.1-longer-version-text (Claude Code)")
    os.utime(binary, ns=(st.st_atime_ns, st.st_mtime_ns))  # SAME mtime: only the size differs
    assert reader.version(str(binary)) == "1.0.1-longer-version-text (Claude Code)"
    assert len(spy.calls) == 2


@needs_posix
def test_a_symlinked_binary_shares_the_identity_of_its_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = make_binary(tmp_path / "claude-real")
    link = tmp_path / "claude"
    link.symlink_to(real)
    spy = RunSpy(monkeypatch)
    reader = CliVersionReader()
    reader.version(str(real))
    reader.version(str(link))
    assert len(spy.calls) == 1


@needs_posix
def test_the_binary_is_found_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_binary(tmp_path / "claude-on-path", "3.3.3 (Claude Code)")
    monkeypatch.setenv("PATH", str(tmp_path))
    assert CliVersionReader().version("claude-on-path") == "3.3.3 (Claude Code)"


@needs_posix
def test_a_long_version_string_is_clipped(tmp_path: Path) -> None:
    binary = make_binary(tmp_path / "claude", "v" * (MAX_CLI_VERSION_CHARS + 100))
    assert len(CliVersionReader().version(str(binary))) == MAX_CLI_VERSION_CHARS


# ------------------------------------------------------------------------------------ U-F2
def test_a_missing_binary_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    err = unavailable(CliVersionReader().version, "no-such-claude-binary")
    assert err.detail == "no-such-claude-binary"


@needs_posix
def test_a_timeout_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary = make_binary(tmp_path / "claude")

    def slow(argv: list[str], **kw: Any) -> Any:
        raise subprocess.TimeoutExpired(argv, kw["timeout"])

    monkeypatch.setattr(fingerprint.subprocess, "run", slow)
    unavailable(CliVersionReader().version, str(binary))


@needs_posix
def test_an_oserror_on_spawn_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = make_binary(tmp_path / "claude")

    def boom(argv: list[str], **kw: Any) -> Any:
        raise OSError("exec format error")

    monkeypatch.setattr(fingerprint.subprocess, "run", boom)
    unavailable(CliVersionReader().version, str(binary))


@needs_posix
def test_a_non_zero_exit_is_unavailable_and_not_memoized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = make_binary(tmp_path / "claude", exit_code=3)
    spy = RunSpy(monkeypatch)
    reader = CliVersionReader()
    unavailable(reader.version, str(binary))
    unavailable(reader.version, str(binary))
    assert len(spy.calls) == 2  # a failure is never cached


@needs_posix
def test_empty_output_is_unavailable(tmp_path: Path) -> None:
    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    unavailable(CliVersionReader().version, str(binary))


@needs_posix
def test_a_non_executable_file_is_unavailable(tmp_path: Path) -> None:
    plain = tmp_path / "claude"
    plain.write_text("#!/bin/sh\necho 1\n")
    plain.chmod(0o644)
    unavailable(CliVersionReader().version, str(plain))


def test_a_stat_error_after_which_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fingerprint.shutil, "which", lambda b: str(tmp_path / "gone"))
    unavailable(CliVersionReader().version, "claude")


# ------------------------------------------------------------------------------------ U-F3
def test_the_env_allowlist_copies_only_listed_variables(ws: Path) -> None:
    environ = {name: f"value-of-{name}" for name in CLAUDE_FINGERPRINT_ENV_VARS}
    environ.update({"ANTHROPIC_API_KEY": "sk-secret", "PATH": "/bin", "HOME": "/root"})
    req = make_request(ws, environ=environ)
    fp = claude_cli_fingerprint(req, make_deps(), HashBudget(10**9, 10**6))
    assert fp["env"] == {name: f"value-of-{name}" for name in CLAUDE_FINGERPRINT_ENV_VARS}
    assert "sk-secret" not in repr(fp)


def test_the_env_allowlist_is_small_closed_and_secret_free() -> None:
    assert len(CLAUDE_FINGERPRINT_ENV_VARS) == len(set(CLAUDE_FINGERPRINT_ENV_VARS)) == 9
    # Credential-shaped names never belong here (MAX_THINKING_TOKENS is a count, not a secret).
    for name in CLAUDE_FINGERPRINT_ENV_VARS:
        assert not re.search(r"(API_KEY|AUTH_TOKEN|SECRET|PASSWORD|CREDENTIAL)", name)


def test_unset_allowlisted_variables_are_omitted(ws: Path) -> None:
    req = make_request(ws, environ={"MAX_THINKING_TOKENS": "4000"})
    fp = claude_cli_fingerprint(req, make_deps(), HashBudget(10**9, 10**6))
    assert fp["env"] == {"MAX_THINKING_TOKENS": "4000"}


# ------------------------------------------------------------------------------------ U-F4
def test_absent_context_files_appear_as_absent_in_the_fixed_order(ws: Path) -> None:
    fp = claude_cli_fingerprint(make_request(ws), make_deps(), HashBudget(10**9, 10**6))
    assert fp["context_files"] == [{"path": p, "kind": KIND_ABSENT} for p in CLAUDE_CONTEXT_PATHS]
    assert fp["cli_version"] == GV1_CLI_VERSION


def test_present_context_files_and_directories_are_digested(ws: Path) -> None:
    (ws / "CLAUDE.md").write_text("rules\n")
    (ws / ".claude" / "agents").mkdir(parents=True)
    (ws / ".claude" / "agents" / "x.md").write_text("an agent\n")
    fp = claude_cli_fingerprint(make_request(ws), make_deps(), HashBudget(10**9, 10**6))
    by_path = {c["path"]: c for c in fp["context_files"]}
    assert by_path["CLAUDE.md"] == {
        "path": "CLAUDE.md",
        "kind": KIND_FILE,
        "sha256": sha256(b"rules\n").hexdigest(),
        "size": 6,
    }
    assert by_path[".claude/agents"]["kind"] == KIND_DIR
    assert by_path[".claude/agents"]["size"] == 1
    assert by_path[".mcp.json"]["kind"] == KIND_ABSENT


def test_context_directories_run_from_the_workspace_root_down_to_the_cwd(ws: Path) -> None:
    (ws / "a" / "b").mkdir(parents=True)
    req = make_request(ws, agent=default_agent(working_dir="a/b"))
    fp = claude_cli_fingerprint(req, make_deps(), HashBudget(10**9, 10**6))
    paths = [c["path"] for c in fp["context_files"]]
    expected = (
        list(CLAUDE_CONTEXT_PATHS)
        + [f"a/{p}" for p in CLAUDE_CONTEXT_PATHS]
        + [f"a/b/{p}" for p in CLAUDE_CONTEXT_PATHS]
    )
    assert paths == expected


@needs_posix
def test_a_symlink_inside_a_context_directory_makes_the_task_uncacheable(ws: Path) -> None:
    (ws / ".claude" / "agents").mkdir(parents=True)
    (ws / ".claude" / "agents" / "evil.md").symlink_to(ws / "docs" / "notes.md")
    with pytest.raises(UncacheableError) as info:
        claude_cli_fingerprint(make_request(ws), make_deps(), HashBudget(10**9, 10**6))
    assert info.value.reason == REASON_INPUT_NOT_REGULAR


@needs_posix
def test_a_symlinked_context_file_is_followed_only_inside_the_workspace(
    ws: Path, tmp_path: Path
) -> None:
    (ws / "shared.md").write_text("shared rules\n")
    (ws / "CLAUDE.md").symlink_to("shared.md")
    fp = claude_cli_fingerprint(make_request(ws), make_deps(), HashBudget(10**9, 10**6))
    assert fp["context_files"][0]["sha256"] == sha256(b"shared rules\n").hexdigest()
    outside = tmp_path / "outside.md"
    outside.write_text("x")
    (ws / "CLAUDE.md").unlink()
    (ws / "CLAUDE.md").symlink_to(outside)
    with pytest.raises(UncacheableError) as info:
        claude_cli_fingerprint(make_request(ws), make_deps(), HashBudget(10**9, 10**6))
    assert info.value.reason == REASON_PATH_REJECTED


def test_the_cwd_must_stay_inside_the_workspace(ws: Path) -> None:
    req = make_request(ws, agent=default_agent(working_dir="../elsewhere"))
    with pytest.raises(UncacheableError) as info:
        claude_cli_fingerprint(req, make_deps(), HashBudget(10**9, 10**6))
    assert info.value.reason == REASON_PATH_REJECTED


def test_context_hashing_is_charged_to_the_shared_budget(ws: Path) -> None:
    (ws / "CLAUDE.md").write_text("x" * 100)
    with pytest.raises(UncacheableError) as info:
        claude_cli_fingerprint(make_request(ws), make_deps(), HashBudget(10, 10**6))
    assert info.value.reason == "input_too_large"


def test_the_version_reader_is_called_with_the_command_binary(ws: Path) -> None:
    fake = fake_cli_version_of(versions={"my-claude": "7.7.7 (Claude Code)"})
    agent = default_agent(command_template=["my-claude", "-p", "{prompt}"])
    fp = claude_cli_fingerprint(
        make_request(ws, agent=agent), KeyDeps(cli_version_of=fake), HashBudget(10**9, 10**6)
    )
    assert fp["cli_version"] == "7.7.7 (Claude Code)"
    assert fake.calls == [("my-claude",)]


def test_a_failing_version_reader_makes_the_task_uncacheable(ws: Path) -> None:
    err = UncacheableError(REASON_EXECUTOR_FINGERPRINT_UNAVAILABLE, "claude")
    deps = KeyDeps(cli_version_of=fake_cli_version_of(error=err))
    with pytest.raises(UncacheableError) as info:
        build_cache_key(make_request(ws), deps)
    assert info.value.reason == REASON_EXECUTOR_FINGERPRINT_UNAVAILABLE


def test_an_empty_command_template_is_unavailable(ws: Path) -> None:
    agent = default_agent().model_copy(update={"command_template": []})
    unavailable(
        claude_cli_fingerprint, make_request(ws, agent=agent), make_deps(), HashBudget(10**9, 10**6)
    )


@needs_posix
def test_the_real_reader_plugs_into_key_deps(
    ws: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = make_binary(tmp_path / "claude", "5.5.5 (Claude Code)")
    agent = default_agent(command_template=[str(binary), "-p", "{prompt}"])
    deps = KeyDeps(cli_version_of=CliVersionReader().version)
    got = build_cache_key(make_request(ws, agent=agent), deps)
    assert got.cli_version == "5.5.5 (Claude Code)"


# ------------------------------------------------------------------------------------ U-F5
def test_the_fake_executor_has_null_argv_and_fingerprint_and_runs_no_subprocess(
    ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*a: Any, **k: Any) -> Any:
        raise AssertionError("a subprocess ran for executor: fake")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    exploding = KeyDeps(
        cli_version_of=fake_cli_version_of(error=AssertionError("version reader called"))
    )
    got = build_cache_key(make_request(ws, agent=default_agent(executor="fake")), exploding)
    null_digest = sha256(b"null").hexdigest()[:12]
    assert got.components["argv"] == got.components["executor_fingerprint"] == null_digest
    assert got.cli_version is None


def test_claude_cli_agents_do_have_argv_and_fingerprint(ws: Path) -> None:
    got = build_cache_key(make_request(ws), make_deps())
    null_digest = sha256(b"null").hexdigest()[:12]
    assert got.components["argv"] != null_digest
    assert got.components["executor_fingerprint"] != null_digest


# ------------------------------------------------------------------------------ the path guards
def test_guarded_resolve_returns_the_resolved_absolute_path(ws: Path) -> None:
    req = make_request(ws)
    assert guarded_resolve(req, "docs/../docs/notes.md") == str(ws.resolve() / "docs" / "notes.md")


@pytest.mark.parametrize("raw", ["../x", "/etc/passwd", "a\0b"])
def test_guarded_resolve_rejects_hostile_paths(ws: Path, raw: str) -> None:
    with pytest.raises(UncacheableError) as info:
        guarded_resolve(make_request(ws), raw)
    assert info.value.reason == REASON_PATH_REJECTED


def test_guarded_resolve_maps_runtime_and_value_errors(
    ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    req = make_request(ws)
    for exc in (RuntimeError("Symlink loop"), ValueError("embedded null"), OSError("ELOOP")):

        def boom(path: str, exc: BaseException = exc) -> str:
            raise exc

        monkeypatch.setattr(req.artifact_store, "resolve", boom)
        with pytest.raises(UncacheableError) as info:
            guarded_resolve(req, "x")
        assert info.value.reason == REASON_PATH_REJECTED


def test_guarded_resolve_refuses_the_cache_root_and_anything_below_it(ws: Path) -> None:
    req = make_request(ws)
    for raw in (".orchestrator/cache", ".orchestrator/cache/entries/v1/ab/x.json"):
        with pytest.raises(UncacheableError) as info:
            guarded_resolve(req, raw)
        assert info.value.reason == REASON_PATH_IN_CACHE_DIR
    assert guarded_resolve(req, ".orchestrator/cache-lookalike").endswith("cache-lookalike")
    assert guarded_resolve(req, ".orchestrator/state.json").endswith("state.json")


def test_guarded_abs_enforces_containment_and_the_cache_root(ws: Path, tmp_path: Path) -> None:
    req = make_request(ws)
    root = req.workspace_root
    assert guarded_abs(req, root) == root
    assert guarded_abs(req, os.path.join(root, "docs")) == os.path.join(root, "docs")
    for outside in (str(tmp_path), root + "-evil", "/"):
        with pytest.raises(UncacheableError) as info:
            guarded_abs(req, outside)
        assert info.value.reason == REASON_PATH_REJECTED
    with pytest.raises(UncacheableError) as info2:
        guarded_abs(req, os.path.join(req.cache_root, "blobs"))
    assert info2.value.reason == REASON_PATH_IN_CACHE_DIR


def test_the_fingerprint_module_does_not_import_keys() -> None:
    assert "agent_orchestrator.cache.keys" not in Path(fingerprint.__file__).read_text()
