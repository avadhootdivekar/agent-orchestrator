"""Tests for bench/sandbox.py (T-Bx2Cd3).

Process-sandbox tests run tiny python artefacts written to tmp_path (real subprocesses, no
network beyond loopback, no docker). Docker command construction / image resolution are
tested as pure functions. Tests that need an actual docker daemon carry the `docker` marker
and are skipped unless AO_E2E_DOCKER=1 (see tests/bench/conftest.py).
"""

from __future__ import annotations

import http.client
import json
import os
import resource
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest
from typer.testing import CliRunner

from agent_orchestrator.bench import sandbox as sb
from agent_orchestrator.bench.cli import app
from agent_orchestrator.bench.errors import GraderError
from agent_orchestrator.bench.sandbox import (
    ArtefactFailure,
    ArtefactStartError,
    ArtefactTimeout,
    DockerSandbox,
    ImageSpec,
    ProcessSandbox,
    SandboxConfig,
    SandboxUnavailable,
    build_docker_run_argv,
    create_sandbox,
    parse_memory,
    plan_images,
    render_dockerfile,
    resolve_image_spec,
    run_sh_argv,
)

PY = sys.executable

HTTP_SERVER = dedent(
    """
    import json, os, resource, sys, time
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            if self.path == "/hang":
                time.sleep(30)
            body = b"ok"
            if self.path == "/env":
                body = json.dumps({
                    "PORT": os.environ.get("PORT"), "HOST": os.environ.get("HOST"),
                    "CUSTOM": os.environ.get("CUSTOM"), "SECRET": os.environ.get("AO_SECRET_TEST"),
                    "PYTHONUNBUFFERED": os.environ.get("PYTHONUNBUFFERED"),
                }).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    HTTPServer((os.environ["HOST"], int(os.environ["PORT"])), H).serve_forever()
    """
)

JSONL_APP = dedent(
    """
    import json, sys, time
    for line in sys.stdin:
        req = json.loads(line)
        if req.get("op") == "ping":
            out = {"ok": True}
        elif req.get("op") == "hang":
            time.sleep(30)
            continue
        else:
            out = {"ok": True, "echo": req}
        print(json.dumps(out), flush=True)
    """
)

CLI_APP = dedent(
    """
    import json, os, resource, sys, time
    args = sys.argv[1:]
    if args[:1] == ["limits"]:
        print(json.dumps({
            "as": resource.getrlimit(resource.RLIMIT_AS),
            "data": resource.getrlimit(resource.RLIMIT_DATA),
            "nproc": resource.getrlimit(resource.RLIMIT_NPROC),
        }))
    elif args[:1] == ["spawn-and-hang"]:
        import subprocess
        child = subprocess.Popen(["sleep", "60"])
        open(args[1], "w").write(str(child.pid))
        time.sleep(60)
    elif args[:1] == ["fail"]:
        sys.stderr.write("boom\\n")
        sys.exit(int(args[1]))
    elif args[:1] == ["state"]:
        # state persists in cwd across invocations of the same run dir
        n = int(open("n.txt").read()) if os.path.exists("n.txt") else 0
        open("n.txt", "w").write(str(n + 1))
        print(n + 1)
    else:
        data = sys.stdin.read() if not sys.stdin.isatty() else ""
        print(json.dumps({"args": args, "stdin": data}))
    """
)


def _artefact(tmp_path: Path, main: str, *, name: str = "app.py", shebang: str = "") -> Path:
    d = tmp_path / "artefact"
    d.mkdir(exist_ok=True)
    (d / name).write_text(main)
    (d / "run.sh").write_text(f'{shebang}exec {PY} {name} "$@"\n')
    return d


def _proc(language: str = "python", **cfg: Any) -> ProcessSandbox:
    return ProcessSandbox(SandboxConfig(type="process", language=language, **cfg), language)


def _get(handle: sb.Handle, path: str, timeout: float = 5.0) -> tuple[int, str]:
    assert handle.port is not None
    conn = http.client.HTTPConnection("127.0.0.1", handle.port, timeout=timeout)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.read().decode()
    finally:
        conn.close()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


# ---------------------------------------------------------------------------
# config, memory parsing, run.sh invocation
# ---------------------------------------------------------------------------


class TestConfig:
    def test_defaults(self) -> None:
        cfg = SandboxConfig()
        assert (cfg.type, cfg.cpus, cfg.memory, cfg.pids) == ("auto", 1.0, "1g", 256)
        assert cfg.startup_timeout_seconds == 30.0 and cfg.build_missing is True
        assert cfg.image is None and cfg.language is None and cfg.stack is None

    def test_parse_accepts_stack_block(self) -> None:
        cfg = SandboxConfig.parse(
            {"type": "docker", "stack": {"python": {"image": "x:1", "deps": ["a==1"], "note": "n"}}}
        )
        assert cfg.stack is not None and cfg.stack["python"].deps == ["a==1"]

    @pytest.mark.parametrize(
        "raw",
        [
            {"typo_key": 1},
            {"type": "vm"},
            {"language": "cobol"},
            {"memory": "lots"},
            {"cpus": 0},
            {"pids": -1},
            {"startup_timeout_seconds": 0},
        ],
    )
    def test_invalid_config_is_infra(self, raw: dict[str, Any]) -> None:
        with pytest.raises(SandboxUnavailable) as err:
            SandboxConfig.parse(raw)
        assert isinstance(err.value, GraderError)

    @pytest.mark.parametrize(
        ("text", "want"),
        [
            ("1g", 1024**3),
            ("512m", 512 * 1024**2),
            ("64k", 65536),
            ("1048576", 1048576),
            ("2GB", 2 * 1024**3),
        ],
    )
    def test_parse_memory(self, text: str, want: int) -> None:
        assert parse_memory(text) == want

    @pytest.mark.parametrize("text", ["", "g", "1.5g", "-1m", "1x"])
    def test_parse_memory_rejects(self, text: str) -> None:
        with pytest.raises(ValueError):
            parse_memory(text)


class TestRunShArgv:
    def test_no_shebang_uses_sh(self, tmp_path: Path) -> None:
        (tmp_path / "run.sh").write_text("echo hi\n")
        assert run_sh_argv(tmp_path) == ["sh", "./run.sh"]

    def test_shebang_interpreter_is_honoured(self, tmp_path: Path) -> None:
        (tmp_path / "run.sh").write_text("#!/usr/bin/env bash\necho hi\n")
        assert run_sh_argv(tmp_path) == ["/usr/bin/env", "bash", "./run.sh"]

    def test_missing_run_sh(self, tmp_path: Path) -> None:
        with pytest.raises(ArtefactStartError, match="run.sh"):
            run_sh_argv(tmp_path)


# ---------------------------------------------------------------------------
# images
# ---------------------------------------------------------------------------


class TestImages:
    @pytest.mark.parametrize(
        ("language", "image"),
        [
            ("python", "python:3.12-slim"),
            ("typescript", "node:22-alpine"),
            ("go", "golang:1.23-alpine"),
        ],
    )
    def test_default_images(self, language: str, image: str) -> None:
        spec = resolve_image_spec(language, SandboxConfig())
        assert (spec.image, spec.base, spec.dockerfile, spec.deps) == (image, image, None, ())

    def test_unknown_language(self) -> None:
        with pytest.raises(SandboxUnavailable, match="unknown language"):
            resolve_image_spec("cobol", SandboxConfig())

    def test_explicit_image_wins_over_everything(self) -> None:
        cfg = SandboxConfig.parse(
            {"image": "mine:1", "stack": {"python": {"image": "other", "deps": ["x"]}}}
        )
        spec = resolve_image_spec("python", cfg)
        assert spec.image == "mine:1" and spec.dockerfile is None

    def test_stack_image_override_without_deps_uses_base_directly(self) -> None:
        cfg = SandboxConfig.parse({"stack": {"go": {"image": "golang:1.22"}}})
        spec = resolve_image_spec("go", cfg)
        assert spec.image == "golang:1.22" and spec.dockerfile is None

    def test_stack_deps_build_a_derived_image_tagged_by_content_hash(self) -> None:
        cfg = SandboxConfig.parse({"stack": {"python": {"deps": ["montydb==2.5.2", "pyyaml"]}}})
        spec = resolve_image_spec("python", cfg)
        assert spec.base == "python:3.12-slim" and spec.deps == ("montydb==2.5.2", "pyyaml")
        assert (
            spec.image.startswith("ao-bench-python-")
            and len(spec.image) == len("ao-bench-python-") + 12
        )
        assert spec.dockerfile == (
            "FROM python:3.12-slim\nRUN pip install --no-cache-dir montydb==2.5.2 pyyaml\n"
        )
        # same inputs -> same tag; different deps or base -> different tag
        assert resolve_image_spec("python", cfg).image == spec.image
        more = SandboxConfig.parse(
            {"stack": {"python": {"deps": ["montydb==2.5.2", "pyyaml", "x"]}}}
        )
        rebased = SandboxConfig.parse(
            {
                "stack": {
                    "python": {"image": "python:3.11-slim", "deps": ["montydb==2.5.2", "pyyaml"]}
                }
            }
        )
        assert resolve_image_spec("python", more).image != spec.image
        assert resolve_image_spec("python", rebased).image != spec.image

    def test_dockerfile_templates_per_language(self) -> None:
        assert "npm install" in render_dockerfile(
            "typescript", "node:22-alpine", ["better-sqlite3@11"]
        )
        ts = render_dockerfile("typescript", "node:22-alpine", ["x"])
        assert (
            "NODE_PATH=" in ts and "ln -s /opt/deps/node_modules /node_modules" in ts
        )  # CJS + ESM
        go = render_dockerfile("go", "golang:1.23-alpine", ["modernc.org/sqlite@v1.33.1"])
        assert "go get modernc.org/sqlite@v1.33.1" in go and "GOPROXY=off" in go
        with pytest.raises(SandboxUnavailable):
            render_dockerfile("cobol", "x", ["y"])

    def test_dockerfile_quotes_hostile_deps(self) -> None:
        text = render_dockerfile("python", "python:3.12-slim", ["a; rm -rf /", "b"])
        assert "'a; rm -rf /'" in text

    def test_plan_images_defaults_and_selection(self) -> None:
        assert [s.language for s in plan_images(None)] == ["go", "python", "typescript"]
        planned = plan_images({"python": {"deps": ["a"]}}, ["python", "go"])
        assert [s.language for s in planned] == ["python", "go"]
        assert planned[0].dockerfile is not None and planned[1].dockerfile is None


class TestEnsureImage:
    class FakeDocker:
        def __init__(self, present: set[str], fail: set[str] | None = None) -> None:
            self.present, self.fail, self.calls = present, fail or set(), []

        def __call__(self, args: Any, *, input_text: str | None = None, timeout: float = 0) -> Any:
            self.calls.append((list(args), input_text))
            verb = args[0]
            image = args[-1] if verb != "build" else args[2]
            if verb == "image":
                rc = 0 if args[2] in self.present else 1
            else:
                rc = 1 if image in self.fail else 0
                if rc == 0:
                    self.present.add(image)
            return subprocess.CompletedProcess(args, rc, "", "denied" if rc else "")

    def test_present_image_is_untouched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = self.FakeDocker({"python:3.12-slim"})
        monkeypatch.setattr(sb, "_docker", fake)
        sb.ensure_image(resolve_image_spec("python", SandboxConfig()))
        assert [c[0][0] for c in fake.calls] == ["image"]

    def test_missing_registry_image_is_pulled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = self.FakeDocker(set())
        monkeypatch.setattr(sb, "_docker", fake)
        sb.ensure_image(resolve_image_spec("python", SandboxConfig()))
        assert ["pull", "python:3.12-slim"] in [c[0] for c in fake.calls]

    def test_build_missing_false_is_infra(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sb, "_docker", self.FakeDocker(set()))
        with pytest.raises(SandboxUnavailable, match="build_missing"):
            sb.ensure_image(resolve_image_spec("python", SandboxConfig()), build_missing=False)

    def test_pull_failure_is_infra(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sb, "_docker", self.FakeDocker(set(), fail={"python:3.12-slim"}))
        with pytest.raises(SandboxUnavailable, match="pull"):
            sb.ensure_image(resolve_image_spec("python", SandboxConfig()))

    def test_derived_image_pulls_base_then_builds_from_generated_dockerfile(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = self.FakeDocker(set())
        monkeypatch.setattr(sb, "_docker", fake)
        spec = resolve_image_spec(
            "python", SandboxConfig.parse({"stack": {"python": {"deps": ["a"]}}})
        )
        sb.ensure_image(spec)
        verbs = [c[0][0] for c in fake.calls]
        assert verbs.index("pull") < verbs.index("build")
        build = next(c for c in fake.calls if c[0][0] == "build")
        assert build[0] == ["build", "-t", spec.image, "-"] and build[1] == spec.dockerfile
        assert spec.image in fake.present


# ---------------------------------------------------------------------------
# docker command construction + sandbox selection (no daemon needed)
# ---------------------------------------------------------------------------


class TestDockerArgv:
    CFG = SandboxConfig(cpus=1.5, memory="512m", pids=64)

    def test_http_run_flags(self, tmp_path: Path) -> None:
        argv = build_docker_run_argv(
            "python:3.12-slim",
            tmp_path,
            ["sh", "./run.sh"],
            cfg=self.CFG,
            name="n1",
            env={"PORT": "8080", "K": "v"},
            publish=(40001, 8080),
        )
        text = " ".join(argv)
        assert argv[:5] == ["docker", "run", "--rm", "--name", "n1"]
        assert "-d" not in argv  # foreground: attached from the first instant (see start_http)
        assert "--cpus 1.5" in text and "--memory 512m" in text and "--memory-swap 512m" in text
        assert "--pids-limit 64" in text and "--cap-drop ALL" in text
        assert "no-new-privileges" in text
        assert f"-v {tmp_path.resolve()}:/app" in text and "-w /app" in text
        assert "-p 127.0.0.1:40001:8080" in text and "--network none" not in text
        assert "-e PORT=8080" in text and "-e K=v" in text and "-e HOME=/tmp" in text
        assert f"--user {os.getuid()}:{os.getgid()}" in text
        assert argv[-3:] == ["python:3.12-slim", "sh", "./run.sh"]  # image, then command

    def test_jsonl_and_cli_have_no_network(self, tmp_path: Path) -> None:
        argv = build_docker_run_argv(
            "img",
            tmp_path,
            ["sh", "./run.sh", "add"],
            cfg=self.CFG,
            name="n",
            env={},
            interactive=True,
        )
        assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
        assert "-i" in argv and "-d" not in argv and "-p" not in argv

    def test_mount_path_translates_to_container(self, tmp_path: Path) -> None:
        sandbox = DockerSandbox(SandboxConfig(), "python")
        f = tmp_path / "sub" / "x.json"
        assert sandbox.mount_path(tmp_path, f) == "/app/sub/x.json"
        assert ProcessSandbox(SandboxConfig(), "python").mount_path(tmp_path, f) == str(f)


class TestCreateSandbox:
    def test_auto_prefers_docker_when_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sb, "docker_available", lambda: True)
        s = create_sandbox(SandboxConfig(), "python")
        assert isinstance(s, DockerSandbox) and s.kind == "docker" and s.image == "python:3.12-slim"

    def test_auto_falls_back_to_process(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sb, "docker_available", lambda: False)
        s = create_sandbox(SandboxConfig(), "python")
        assert isinstance(s, ProcessSandbox) and s.kind == "process" and s.image is None

    def test_explicit_process_never_probes_docker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom() -> bool:
            raise AssertionError("docker probed")

        monkeypatch.setattr(sb, "docker_available", boom)
        assert create_sandbox(SandboxConfig(type="process"), "go").kind == "process"

    def test_explicit_docker_without_daemon_is_infra(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sb, "docker_available", lambda: False)
        with pytest.raises(SandboxUnavailable, match="unreachable"):
            create_sandbox(SandboxConfig(type="docker"), "python")

    def test_docker_available_is_false_without_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sb.docker_available.cache_clear()
        monkeypatch.setattr(shutil, "which", lambda name: None)
        try:
            assert sb.docker_available() is False
        finally:
            sb.docker_available.cache_clear()


# ---------------------------------------------------------------------------
# process sandbox: http
# ---------------------------------------------------------------------------


class TestProcessHttp:
    def test_start_serve_stop(self, tmp_path: Path) -> None:
        art = _artefact(tmp_path, HTTP_SERVER, name="server.py")
        sandbox = _proc()
        handle = sandbox.start_http(art, {"CUSTOM": "yes"})
        try:
            assert handle.base_url and handle.base_url.startswith("http://127.0.0.1:")
            assert _get(handle, "/healthz") == (200, "ok")
            status, body = _get(handle, "/env")
            env = json.loads(body)
            assert status == 200 and env["PORT"] == str(handle.port) and env["HOST"] == "127.0.0.1"
            assert env["CUSTOM"] == "yes"
        finally:
            sandbox.stop(handle)
        assert handle.proc is not None and handle.proc.poll() is not None
        with pytest.raises(OSError):
            _get(handle, "/healthz", timeout=1)
        sandbox.stop(handle)  # idempotent

    def test_host_env_is_scrubbed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AO_SECRET_TEST", "hunter2")
        art = _artefact(tmp_path, HTTP_SERVER, name="server.py")
        sandbox = _proc()
        handle = sandbox.start_http(art, {})
        try:
            env = json.loads(_get(handle, "/env")[1])
        finally:
            sandbox.stop(handle)
        assert env["SECRET"] is None

    def test_never_ready_times_out_and_kills_the_process(self, tmp_path: Path) -> None:
        art = tmp_path / "a"
        art.mkdir()
        (art / "run.sh").write_text("sleep 60\n")
        sandbox = _proc(startup_timeout_seconds=0.6)
        t0 = time.monotonic()
        with pytest.raises(ArtefactStartError, match=r"/healthz not 200 within 0\.6s"):
            sandbox.start_http(art, {})
        assert time.monotonic() - t0 < 10  # SIGTERM'd group, did not wait for `sleep 60`

    def test_crash_before_ready_reports_log_tail(self, tmp_path: Path) -> None:
        art = tmp_path / "a"
        art.mkdir()
        (art / "run.sh").write_text("echo 'kaboom: no module named x' >&2\nexit 3\n")
        with pytest.raises(ArtefactStartError, match="exited before becoming ready.*kaboom"):
            _proc().start_http(art, {})

    def test_missing_run_sh_is_an_artefact_failure_not_infra(self, tmp_path: Path) -> None:
        with pytest.raises(ArtefactStartError) as err:
            _proc().start_http(tmp_path, {})
        assert not isinstance(err.value, GraderError) and isinstance(err.value, ArtefactFailure)

    def test_port_race_is_retried_on_a_fresh_port(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        art = _artefact(tmp_path, HTTP_SERVER, name="server.py")
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        busy_port = busy.getsockname()[1]
        offered: list[int] = []
        real = sb.free_port

        def flaky() -> int:
            port = busy_port if not offered else real()  # first offer is already taken
            offered.append(port)
            return port

        monkeypatch.setattr(sb, "free_port", flaky)
        sandbox = _proc()
        try:
            handle = sandbox.start_http(art, {})
            try:
                assert handle.port != busy_port and len(offered) == 2
                assert _get(handle, "/healthz") == (200, "ok")
            finally:
                sandbox.stop(handle)
        finally:
            busy.close()

    def test_port_race_gives_up_after_the_retry_budget(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        art = _artefact(tmp_path, HTTP_SERVER, name="server.py")
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        monkeypatch.setattr(sb, "free_port", lambda: busy.getsockname()[1])
        try:
            with pytest.raises(ArtefactStartError, match="exited before becoming ready"):
                _proc().start_http(art, {})
        finally:
            busy.close()

    def test_stop_kills_grandchildren_in_the_process_group(self, tmp_path: Path) -> None:
        art = tmp_path / "a"
        art.mkdir()
        (art / "server.py").write_text(HTTP_SERVER)
        # run.sh does NOT exec: python is a child of sh; both must die on stop.
        (art / "run.sh").write_text(f"{PY} server.py\n")
        sandbox = _proc()
        handle = sandbox.start_http(art, {})
        assert handle.proc is not None
        pgid = handle.proc.pid
        sandbox.stop(handle)
        with pytest.raises(ProcessLookupError):
            os.killpg(pgid, 0)


# ---------------------------------------------------------------------------
# process sandbox: jsonl
# ---------------------------------------------------------------------------


class TestProcessJsonl:
    def test_run_jsonl_batch(self, tmp_path: Path) -> None:
        art = _artefact(tmp_path, JSONL_APP)
        out = _proc().run_jsonl(art, ['{"op":"a","n":1}', '{"op":"b"}'], {})
        assert [json.loads(x) for x in out] == [
            {"ok": True, "echo": {"op": "a", "n": 1}},
            {"ok": True, "echo": {"op": "b"}},
        ]

    def test_interactive_handle_and_late_response_is_unusable(self, tmp_path: Path) -> None:
        art = _artefact(tmp_path, JSONL_APP)
        sandbox = _proc()
        handle = sandbox.start_jsonl(art, {})
        try:
            assert json.loads(handle.request('{"op":"x"}', 5))["ok"] is True
            with pytest.raises(ArtefactTimeout):
                handle.request('{"op":"hang"}', 0.4)
            with pytest.raises(ArtefactFailure, match="unusable"):
                handle.request('{"op":"x"}', 1)
        finally:
            sandbox.stop(handle)

    def test_bad_ping_answer_is_a_start_error(self, tmp_path: Path) -> None:
        art = _artefact(
            tmp_path, "import sys\nfor l in sys.stdin: print('{\"ok\": false}', flush=True)\n"
        )
        with pytest.raises(ArtefactStartError, match="expected"):
            _proc().start_jsonl(art, {})

    def test_silent_artefact_never_answers_ping(self, tmp_path: Path) -> None:
        art = _artefact(tmp_path, "import time\ntime.sleep(60)\n")
        with pytest.raises(ArtefactStartError, match="no ping answer within 0.5s"):
            _proc(startup_timeout_seconds=0.5).start_jsonl(art, {})

    def test_exit_after_start_is_reported(self, tmp_path: Path) -> None:
        art = _artefact(tmp_path, "import sys\nsys.exit(1)\n")
        with pytest.raises(ArtefactStartError):
            _proc().start_jsonl(art, {})


# ---------------------------------------------------------------------------
# process sandbox: cli, limits
# ---------------------------------------------------------------------------


class TestProcessCli:
    def test_args_stdin_and_exit_codes(self, tmp_path: Path) -> None:
        art = _artefact(tmp_path, CLI_APP)
        sandbox = _proc()
        rc, out, err = sandbox.run_cli(art, ["add", "--name", "x"], "payload", {})
        assert rc == 0 and json.loads(out) == {"args": ["add", "--name", "x"], "stdin": "payload"}
        rc, out, err = sandbox.run_cli(art, ["fail", "7"], None, {})
        assert (rc, out, err.strip()) == (7, "", "boom")

    def test_state_persists_in_the_run_dir_across_invocations(self, tmp_path: Path) -> None:
        art = _artefact(tmp_path, CLI_APP)
        sandbox = _proc()
        assert [sandbox.run_cli(art, ["state"])[1].strip() for _ in range(3)] == ["1", "2", "3"]

    def test_timeout_kills_the_whole_process_group(self, tmp_path: Path) -> None:
        art = _artefact(tmp_path, CLI_APP)
        pid_file = tmp_path / "child.pid"
        t0 = time.monotonic()
        with pytest.raises(ArtefactTimeout, match="exceeded 0.8s"):
            _proc().run_cli(art, ["spawn-and-hang", str(pid_file)], None, {}, timeout=0.8)
        assert time.monotonic() - t0 < 15
        child = int(pid_file.read_text())
        deadline = time.monotonic() + 5
        while _pid_alive(child) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _pid_alive(child), "grandchild survived the group kill"

    def test_missing_run_sh(self, tmp_path: Path) -> None:
        with pytest.raises(ArtefactStartError):
            _proc().run_cli(tmp_path, [])

    def test_limits_are_applied_python(self, tmp_path: Path) -> None:
        art = _artefact(tmp_path, CLI_APP)
        rc, out, _ = _proc("python", memory="768m", pids=100).run_cli(art, ["limits"])
        limits = json.loads(out)
        assert rc == 0
        soft_as, hard_as = limits["as"]
        assert soft_as == hard_as == 768 * 1024**2
        nproc_soft, _hard = limits["nproc"]
        # NPROC counts every task of the uid, so the budget is "existing + pids", never below pids
        assert nproc_soft >= 100 and nproc_soft != resource.RLIM_INFINITY

    def test_non_python_languages_limit_data_not_address_space(self, tmp_path: Path) -> None:
        art = _artefact(tmp_path, CLI_APP)
        rc, out, _ = _proc("typescript", memory="768m").run_cli(art, ["limits"])
        limits = json.loads(out)
        assert rc == 0 and limits["data"][0] == 768 * 1024**2
        assert limits["as"][0] in (
            resource.RLIM_INFINITY,
            resource.getrlimit(resource.RLIMIT_AS)[0],
        )


# ---------------------------------------------------------------------------
# `ao-bench images`
# ---------------------------------------------------------------------------


class TestImagesCommand:
    def _no_docker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sb, "docker_available", lambda: False)

    def test_list_without_docker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._no_docker(monkeypatch)
        result = CliRunner().invoke(app, ["images", "--list"])
        assert result.exit_code == 0, result.output
        for image in ("python:3.12-slim", "node:22-alpine", "golang:1.23-alpine"):
            assert image in result.output
        assert "unknown (docker unavailable)" in result.output

    def test_language_filter_and_present_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sb, "docker_available", lambda: True)
        monkeypatch.setattr(sb, "image_present", lambda image: image == "python:3.12-slim")
        result = CliRunner().invoke(app, ["images", "--list", "--language", "python", "-l", "go"])
        assert result.exit_code == 0, result.output
        assert "[present]" in result.output and "[missing]" in result.output
        assert "node:22-alpine" not in result.output

    def test_suite_stack_block_selects_and_derives_images(
        self, monkeypatch: pytest.MonkeyPatch, suite_factory: Any
    ) -> None:
        self._no_docker(monkeypatch)
        suite = suite_factory(
            extra_top_level={"stack": {"python": {"deps": ["montydb==2.5.2"]}, "go": {}}}
        )
        result = CliRunner().invoke(app, ["images", "--list", "--suite", str(suite)])
        assert result.exit_code == 0, result.output
        derived = resolve_image_spec(
            "python", SandboxConfig.parse({"stack": {"python": {"deps": ["montydb==2.5.2"]}}})
        )
        assert derived.image in result.output and "(derived from python:3.12-slim)" in result.output
        assert "golang:1.23-alpine" in result.output and "node:22-alpine" not in result.output

    def test_build_without_docker_exits_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._no_docker(monkeypatch)
        result = CliRunner().invoke(app, ["images", "--build"])
        assert result.exit_code == 1 and "docker is not available" in result.output

    def test_build_ensures_each_image(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sb, "docker_available", lambda: True)
        seen: list[ImageSpec] = []
        monkeypatch.setattr(sb, "ensure_image", lambda spec, **kw: seen.append(spec))
        result = CliRunner().invoke(app, ["images", "--build", "-l", "python"])
        assert result.exit_code == 0, result.output
        assert [s.image for s in seen] == ["python:3.12-slim"] and "[ready]" in result.output

    def test_build_failure_exits_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sb, "docker_available", lambda: True)

        def fail(spec: ImageSpec, **kw: Any) -> None:
            raise SandboxUnavailable("registry down")

        monkeypatch.setattr(sb, "ensure_image", fail)
        result = CliRunner().invoke(app, ["images", "--build", "-l", "python"])
        assert result.exit_code == 1 and "FAILED: registry down" in result.output

    def test_bad_suite_path_exits_1(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            app, ["images", "--list", "--suite", str(tmp_path / "nope.json")]
        )
        assert result.exit_code == 1 and "ERROR" in result.output

    def test_bad_stack_block_exits_1(
        self, monkeypatch: pytest.MonkeyPatch, suite_factory: Any
    ) -> None:
        self._no_docker(monkeypatch)
        suite = suite_factory(extra_top_level={"stack": {"python": {"deps": "not-a-list"}}})
        result = CliRunner().invoke(app, ["images", "--list", "--suite", str(suite)])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# docker (opt-in: AO_E2E_DOCKER=1; needs a daemon and python:3.12-slim)
# ---------------------------------------------------------------------------

docker = pytest.mark.docker


@pytest.fixture()
def docker_sandbox() -> DockerSandbox:
    sb.docker_available.cache_clear()
    if not sb.docker_available():
        pytest.skip("docker daemon not reachable")
    sandbox = DockerSandbox(SandboxConfig(type="docker", memory="256m", pids=64), "python")
    if not sb.image_present(sandbox.spec.image):
        pytest.skip(f"{sandbox.spec.image} not pulled (docker pull {sandbox.spec.image})")
    return sandbox


def _docker_artefact(tmp_path: Path, main: str, name: str = "app.py") -> Path:
    d = tmp_path / "dartefact"
    d.mkdir()
    (d / name).write_text(main)
    # the container's interpreter, not sys.executable
    (d / "run.sh").write_text(f'#!/bin/sh\nexec python3 {name} "$@"\n')
    return d


@docker
class TestDocker:
    def test_http_in_container_with_limits(
        self, tmp_path: Path, docker_sandbox: DockerSandbox
    ) -> None:
        art = _docker_artefact(tmp_path, HTTP_SERVER, "server.py")
        handle = docker_sandbox.start_http(art, {"CUSTOM": "yes"})
        try:
            assert _get(handle, "/healthz") == (200, "ok")
            env = json.loads(_get(handle, "/env")[1])
            assert env["PORT"] == "8080" and env["HOST"] == "0.0.0.0" and env["CUSTOM"] == "yes"
            assert handle.container is not None
            inspect = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "-f",
                    "{{.HostConfig.Memory}} {{.HostConfig.PidsLimit}} {{.HostConfig.NetworkMode}}",
                    handle.container,
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.split()
            assert inspect[0] == str(256 * 1024**2) and inspect[1] == "64"
        finally:
            docker_sandbox.stop(handle)
        assert handle.container is not None
        gone = subprocess.run(["docker", "inspect", handle.container], capture_output=True)
        assert gone.returncode != 0  # --rm + rm -f: nothing left behind

    def test_http_never_ready_reports_container_logs(
        self, tmp_path: Path, docker_sandbox: DockerSandbox
    ) -> None:
        art = _docker_artefact(
            tmp_path, "import sys\nprint('import failed: nope', file=sys.stderr)\nsys.exit(2)\n"
        )
        # the crash output must reach the error every time (it was lost in 8/10 runs with -d --rm)
        for _ in range(5):
            with pytest.raises(
                ArtefactStartError, match="exited before becoming ready.*import failed"
            ):
                docker_sandbox.start_http(art, {})

    def test_jsonl_roundtrip_and_no_network(
        self, tmp_path: Path, docker_sandbox: DockerSandbox
    ) -> None:
        net = dedent(
            """
            import json, socket, sys
            for line in sys.stdin:
                req = json.loads(line)
                if req.get("op") == "net":
                    try:
                        socket.create_connection(("1.1.1.1", 53), timeout=2)
                        out = {"ok": True, "net": True}
                    except OSError:
                        out = {"ok": True, "net": False}
                else:
                    out = {"ok": True, "echo": req}
                print(json.dumps(out), flush=True)
            """
        )
        art = _docker_artefact(tmp_path, net)
        out = docker_sandbox.run_jsonl(art, ['{"op":"echo"}', '{"op":"net"}'], {})
        assert json.loads(out[0])["echo"] == {"op": "echo"}
        assert json.loads(out[1])["net"] is False  # --network none

    def test_cli_runs_as_host_user_with_cgroup_limits(
        self, tmp_path: Path, docker_sandbox: DockerSandbox
    ) -> None:
        probe = dedent(
            """
            import json, os, sys
            def rd(p):
                try:
                    return open(p).read().strip()
                except OSError:
                    return None
            print(json.dumps({
                "uid": os.getuid(),
                "mem": rd("/sys/fs/cgroup/memory.max"),
                "pids": rd("/sys/fs/cgroup/pids.max"),
                "args": sys.argv[1:],
                "stdin": sys.stdin.read(),
            }))
            """
        )
        art = _docker_artefact(tmp_path, probe)
        rc, out, _ = docker_sandbox.run_cli(art, ["a", "b"], "in", {})
        data = json.loads(out)
        assert rc == 0 and data["uid"] == os.getuid() and data["args"] == ["a", "b"]
        assert data["stdin"] == "in"
        assert data["mem"] in (str(256 * 1024**2), None) and data["pids"] in ("64", None)
        assert (art / "run.sh").is_file()

    def test_cli_timeout_removes_the_container(
        self, tmp_path: Path, docker_sandbox: DockerSandbox
    ) -> None:
        art = _docker_artefact(tmp_path, "import time\ntime.sleep(60)\n")
        t0 = time.monotonic()
        with pytest.raises(ArtefactTimeout):
            docker_sandbox.run_cli(art, [], None, {}, timeout=2)
        assert time.monotonic() - t0 < 30
        listing = subprocess.run(
            ["docker", "ps", "-q", "--filter", f"name={sb.CONTAINER_NAME_PREFIX}"],
            capture_output=True,
            text=True,
        ).stdout.split()
        assert listing == []

    def test_files_written_by_the_artefact_stay_removable(
        self, tmp_path: Path, docker_sandbox: DockerSandbox
    ) -> None:
        art = _docker_artefact(
            tmp_path, "open('made-in-container.txt', 'w').write('x')\nprint('ok')\n"
        )
        assert docker_sandbox.run_cli(art, [], None, {})[0] == 0
        made = art / "made-in-container.txt"
        assert made.stat().st_uid == os.getuid()
        shutil.rmtree(art)  # would raise PermissionError if the container wrote as root
