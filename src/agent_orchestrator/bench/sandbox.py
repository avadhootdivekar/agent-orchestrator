"""Artefact sandbox (HLD §4.1 "Sandbox", bench-probe-dsl.md §1).

Runs an agent-produced artefact for grading. Two interchangeable implementations of the
`Sandbox` ABC:

- `DockerSandbox` -- the artefact runs inside a per-language image (allow-listed deps
  pre-installed) with cpu/memory/pid limits and **no network except the published port**
  (`http`) or none at all (`jsonl`/`cli`).
- `ProcessSandbox` -- plain subprocess + rlimits + process-group kill. An explicit,
  *tagged* fallback (every grade result reports `sandbox: "process"`) for machines without
  Docker and for CI unit tests. It is **not** an isolation boundary.

`SandboxConfig.type == "auto"` (default) picks docker when `docker info` succeeds, else
process.

Failure taxonomy (bench-probe-dsl.md §6): problems that are the *artefact's* fault raise
`ArtefactFailure` subclasses (the grader turns them into failed probes); problems with
the sandbox itself (daemon down, image missing and `build_missing=false`, bad config)
raise `SandboxUnavailable`, a `GraderError` -> task `infra_error`.

Run contract inside the sandbox: `run.sh` in the artefact dir (cwd), started via its
shebang interpreter (or `sh` when it has none), so it needs no exec bit. `http` artefacts
get `PORT` and `HOST` env (docker: `HOST=0.0.0.0`, process: `HOST=127.0.0.1`) and must be
ready (`GET /healthz` -> 200) within `startup_timeout_seconds`; `jsonl` artefacts must
answer `{"op":"ping"}` with `{"ok":true}`.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import resource
import select
import shlex
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import typer
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .errors import GraderError

# ---------------------------------------------------------------------------
# Named constants (CLAUDE.md: no magic literals)
# ---------------------------------------------------------------------------

# Default image per language (HLD §4.1 / §9 #1); a suite `stack` block or an explicit
# `SandboxConfig.image` overrides.
DEFAULT_IMAGES: dict[str, str] = {
    "python": "python:3.12-slim",
    "typescript": "node:22-alpine",
    "go": "golang:1.23-alpine",
}
DEFAULT_STARTUP_TIMEOUT_SECONDS = 30.0
DEFAULT_RUN_TIMEOUT_SECONDS = 30.0
RUN_SCRIPT = "run.sh"
READINESS_PATH = "/healthz"
READINESS_POLL_SECONDS = 0.05
READINESS_REQUEST_TIMEOUT_SECONDS = 1.0
PING_REQUEST = '{"op":"ping"}'
CONTAINER_PORT = 8080
CONTAINER_WORKDIR = "/app"
CONTAINER_HOME = "/tmp"  # writable HOME for the unprivileged container user
HTTP_BIND_HOST_DOCKER = "0.0.0.0"  # noqa: S104 -- inside the container; published to 127.0.0.1 only
HTTP_BIND_HOST_PROCESS = "127.0.0.1"
LOOPBACK = "127.0.0.1"
CONTAINER_NAME_PREFIX = "ao-bench-run-"
DERIVED_TAG_PREFIX = "ao-bench-"
DERIVED_TAG_HASH_LEN = 12
STOP_GRACE_SECONDS = 2.0
DOCKER_INFO_TIMEOUT_SECONDS = 20
DOCKER_PULL_TIMEOUT_SECONDS = 900
DOCKER_BUILD_TIMEOUT_SECONDS = 1800
DOCKER_RUN_START_TIMEOUT_SECONDS = 120
DOCKER_PORT_RETRIES = 3
LOG_TAIL_CHARS = 1500
LOG_TAIL_LINES = 6
LOG_LINE_CHARS = 200
# Process-sandbox fallback when /proc/loadavg is unavailable (non-Linux).
_FALLBACK_BASE_TASKS = 4096
# Env vars the process sandbox passes through from the host (everything else is scrubbed so
# API keys etc. never reach agent-written code).
_PROCESS_ENV_PASSTHROUGH = ("PATH", "LANG", "LC_ALL", "TZ", "TMPDIR")
_MEMORY_UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}

LANGUAGE_NODE_DEPS_DIR = "/opt/deps"  # typescript: global-ish node_modules in derived images
LANGUAGE_GO_DEPS_DIR = "/opt/deps"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SandboxUnavailable(GraderError):
    """Raised when the sandbox itself cannot run (daemon down, image missing and
    `build_missing=false`, invalid sandbox config, docker command failure)."""


class ArtefactFailure(Exception):
    """The artefact (not the sandbox) misbehaved: never ready, crashed, timed out,
    `run.sh` missing. Deliberately NOT a `GraderError` -- these are probe failures."""


class ArtefactStartError(ArtefactFailure):
    """`run.sh` missing, process failed to spawn, or never became ready."""


class ArtefactTimeout(ArtefactFailure):
    """A request / CLI invocation exceeded its timeout."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class StackEntry(BaseModel):
    """One language's entry of a suite `stack` block: `{image?, deps?}`. Unknown keys
    (e.g. informational persistence notes) are ignored so the suite block can carry them."""

    model_config = ConfigDict(extra="ignore")

    image: str | None = None
    deps: list[str] = Field(default_factory=list)


class SandboxConfig(BaseModel):
    """`grader.sandbox` of a blackbox task. Unknown keys are rejected (a typo must fail
    loudly as a definition error, not silently run with defaults)."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["auto", "docker", "process"] = "auto"
    language: str | None = None
    image: str | None = None  # full override: used as-is, no derived image
    cpus: float = Field(1.0, gt=0)
    memory: str = "1g"
    pids: int = Field(256, gt=0)
    startup_timeout_seconds: float = Field(DEFAULT_STARTUP_TIMEOUT_SECONDS, gt=0)
    # When the image is not present locally: pull (base) / build (derived). False -> infra error.
    build_missing: bool = True
    # The suite `stack` block (language -> {image, deps}); generators embed a copy here.
    stack: dict[str, StackEntry] | None = None

    @field_validator("language")
    @classmethod
    def _known_language(cls, v: str | None) -> str | None:
        if v is not None and v not in DEFAULT_IMAGES:
            raise ValueError(f"unknown language {v!r}; known: {sorted(DEFAULT_IMAGES)}")
        return v

    @field_validator("memory")
    @classmethod
    def _memory_parses(cls, v: str) -> str:
        parse_memory(v)
        return v

    @classmethod
    def parse(cls, raw: Mapping[str, Any] | None) -> SandboxConfig:
        """Validate a raw `sandbox` dict; any problem is a `SandboxUnavailable`
        (definition error -> infra), never a bare pydantic traceback."""
        try:
            return cls.model_validate(dict(raw or {}))
        except (ValidationError, ValueError) as exc:
            raise SandboxUnavailable(f"invalid sandbox config: {exc}") from exc


_MEMORY_RE = re.compile(r"^(\d+)([kmg]?)b?$")


def parse_memory(text: str) -> int:
    """`"1g"`/`"512m"`/`"64k"`/`"1048576"` -> bytes (docker-style suffixes)."""
    m = _MEMORY_RE.match(text.strip().lower())
    if m is None:
        raise ValueError(f"invalid memory size {text!r} (expected e.g. 512m, 1g)")
    return int(m.group(1)) * _MEMORY_UNITS[m.group(2)]


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImageSpec:
    """The image a language's artefact runs in. `dockerfile is None` -> `image` is a plain
    registry image (pull if missing); otherwise `image` is the derived tag built from
    `dockerfile` (cached by content hash)."""

    language: str
    base: str
    deps: tuple[str, ...]
    image: str
    dockerfile: str | None


def render_dockerfile(language: str, base: str, deps: Sequence[str]) -> str:
    """Generated Dockerfile: `base + install deps` (HLD §4.1). Deterministic, so the content
    hash (and therefore the image tag) only changes when base/deps do."""
    quoted = " ".join(shlex.quote(d) for d in deps)
    if language == "python":
        body = f"RUN pip install --no-cache-dir {quoted}\n"
    elif language == "typescript":
        body = (
            f"ENV NODE_PATH={LANGUAGE_NODE_DEPS_DIR}/node_modules\n"
            f"RUN mkdir -p {LANGUAGE_NODE_DEPS_DIR} && cd {LANGUAGE_NODE_DEPS_DIR} "
            f"&& npm init -y >/dev/null && npm install --no-audit --no-fund {quoted}\n"
        )
    elif language == "go":
        # Module cache is populated at build time; offline (GOPROXY=off) at run time.
        body = (
            f"RUN mkdir -p {LANGUAGE_GO_DEPS_DIR} && cd {LANGUAGE_GO_DEPS_DIR} "
            f"&& go mod init aobenchdeps && GOFLAGS=-mod=mod GOSUMDB=off go get {quoted}\n"
            "ENV GOFLAGS=-mod=mod GOPROXY=off GOSUMDB=off GOCACHE=/tmp/gocache\n"
        )
    else:
        raise SandboxUnavailable(f"no Dockerfile template for language {language!r}")
    return f"FROM {base}\n{body}"


def resolve_image_spec(language: str, cfg: SandboxConfig) -> ImageSpec:
    """Precedence: `cfg.image` (used as-is) > suite `stack[language]` (image and/or deps ->
    derived image when deps are non-empty) > `DEFAULT_IMAGES[language]`."""
    if language not in DEFAULT_IMAGES:
        raise SandboxUnavailable(f"unknown language {language!r}; known: {sorted(DEFAULT_IMAGES)}")
    if cfg.image:
        return ImageSpec(language, cfg.image, (), cfg.image, None)
    entry = (cfg.stack or {}).get(language)
    base = entry.image if entry and entry.image else DEFAULT_IMAGES[language]
    deps = tuple(entry.deps) if entry else ()
    if not deps:
        return ImageSpec(language, base, (), base, None)
    dockerfile = render_dockerfile(language, base, deps)
    digest = hashlib.sha256(dockerfile.encode()).hexdigest()[:DERIVED_TAG_HASH_LEN]
    return ImageSpec(language, base, deps, f"{DERIVED_TAG_PREFIX}{language}-{digest}", dockerfile)


def plan_images(
    stack: Mapping[str, Any] | None, languages: Iterable[str] | None = None
) -> list[ImageSpec]:
    """Image specs for `languages` (default: every language) under a suite `stack` block."""
    cfg = SandboxConfig.parse({"stack": dict(stack)} if stack else None)
    wanted = list(languages) if languages else sorted(DEFAULT_IMAGES)
    return [resolve_image_spec(lang, cfg) for lang in wanted]


# ---------------------------------------------------------------------------
# docker helpers
# ---------------------------------------------------------------------------


def _docker(
    args: Sequence[str], *, input_text: str | None = None, timeout: float = 60
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 -- fixed argv, no shell
            ["docker", *args],  # noqa: S607
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SandboxUnavailable("docker CLI not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise SandboxUnavailable(f"docker {' '.join(args[:2])} timed out after {timeout}s") from exc


@lru_cache(maxsize=1)
def docker_available() -> bool:
    """True when the docker CLI exists and `docker info` succeeds. Cached per process;
    tests reset it with `docker_available.cache_clear()`."""
    if shutil.which("docker") is None:
        return False
    try:
        return _docker(["info"], timeout=DOCKER_INFO_TIMEOUT_SECONDS).returncode == 0
    except SandboxUnavailable:
        return False


def image_present(image: str) -> bool:
    return _docker(["image", "inspect", image], timeout=30).returncode == 0


def ensure_image(spec: ImageSpec, *, build_missing: bool = True) -> None:
    """Make `spec.image` present locally: pull a registry image / build a derived one.
    Raises `SandboxUnavailable` if it cannot (including `build_missing=False`)."""
    if image_present(spec.image):
        return
    if not build_missing:
        raise SandboxUnavailable(
            f"sandbox image {spec.image!r} is not present and build_missing is false "
            "(run `ao-bench images --build`)"
        )
    if spec.dockerfile is not None:
        ensure_image(ImageSpec(spec.language, spec.base, (), spec.base, None))
        proc = _docker(
            ["build", "-t", spec.image, "-"],
            input_text=spec.dockerfile,
            timeout=DOCKER_BUILD_TIMEOUT_SECONDS,
        )
        action = "build"
    else:
        proc = _docker(["pull", spec.image], timeout=DOCKER_PULL_TIMEOUT_SECONDS)
        action = "pull"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout)[-LOG_TAIL_CHARS:]
        raise SandboxUnavailable(f"docker {action} {spec.image!r} failed: {detail}")


def build_docker_run_argv(
    image: str,
    artefact_dir: Path,
    command: Sequence[str],
    *,
    cfg: SandboxConfig,
    name: str,
    env: Mapping[str, str],
    detach: bool = False,
    interactive: bool = False,
    publish: tuple[int, int] | None = None,
) -> list[str]:
    """`docker run` argv. `publish=None` -> `--network none`; `publish=(host, container)`
    publishes `127.0.0.1:host -> container` on the default bridge (the only reachable port).
    Pure function so the flag set is unit-testable without docker."""
    argv = ["docker", "run", "--rm", "--name", name]
    if detach:
        argv.append("-d")
    if interactive:
        argv.append("-i")
    argv += [
        # Run as the host user so files the artefact writes to the mounted dir stay removable.
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--cpus",
        str(cfg.cpus),
        "--memory",
        cfg.memory,
        "--memory-swap",
        cfg.memory,
        "--pids-limit",
        str(cfg.pids),
        "-v",
        f"{artefact_dir.resolve()}:{CONTAINER_WORKDIR}",
        "-w",
        CONTAINER_WORKDIR,
        "-e",
        f"HOME={CONTAINER_HOME}",
    ]
    if publish is None:
        argv += ["--network", "none"]
    else:
        argv += ["-p", f"{LOOPBACK}:{publish[0]}:{publish[1]}"]
    for key, value in env.items():
        argv += ["-e", f"{key}={value}"]
    return [*argv, image, *command]


# ---------------------------------------------------------------------------
# shared plumbing
# ---------------------------------------------------------------------------


def free_port() -> int:
    """An ephemeral loopback port that was free a moment ago (small bind race accepted)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((LOOPBACK, 0))
        return int(s.getsockname()[1])


def run_sh_argv(artefact_dir: Path) -> list[str]:
    """argv that starts `<artefact_dir>/run.sh` (relative, cwd = artefact_dir): through its
    shebang interpreter if it has one, else `sh` -- so no exec bit is required."""
    script = artefact_dir / RUN_SCRIPT
    if not script.is_file():
        raise ArtefactStartError(f"{RUN_SCRIPT} not found in the artefact directory")
    try:
        with open(script, encoding="utf-8", errors="replace") as fh:
            first = fh.readline()
    except OSError as exc:
        raise ArtefactStartError(f"{RUN_SCRIPT} unreadable: {exc}") from exc
    if first.startswith("#!"):
        try:
            parts = shlex.split(first[2:].strip())
        except ValueError:
            parts = []
        if parts:
            return [*parts, f"./{RUN_SCRIPT}"]
    return ["sh", f"./{RUN_SCRIPT}"]


def _tail(path: Path | None, limit: int = LOG_TAIL_CHARS) -> str:
    if path is None:
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-limit:]


def _http_status(port: int, path: str, timeout: float) -> int:
    conn = http.client.HTTPConnection(LOOPBACK, port, timeout=timeout)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        resp.read()
        return resp.status
    finally:
        conn.close()


def _wait_http_ready(
    port: int, timeout: float, alive: Callable[[], bool], diag: Callable[[], str]
) -> None:
    deadline = time.monotonic() + timeout
    while True:
        if not alive():
            raise ArtefactStartError(f"artefact exited before becoming ready. {diag()}".strip())
        try:
            if _http_status(port, READINESS_PATH, READINESS_REQUEST_TIMEOUT_SECONDS) == 200:
                return
        except (OSError, http.client.HTTPException):
            pass  # not listening yet (docker-proxy may reset before the app binds)
        if time.monotonic() >= deadline:
            raise ArtefactStartError(
                f"GET {READINESS_PATH} not 200 within {timeout:g}s. {diag()}".strip()
            )
        time.sleep(READINESS_POLL_SECONDS)


class _LineReader:
    """Timeout-capable line reader over a raw fd (a buffered text stream cannot be
    `select`ed reliably)."""

    def __init__(self, fd: int) -> None:
        self._fd = fd
        self._buf = b""
        self._eof = False

    def readline(self, timeout: float) -> bytes | None:
        """Next non-empty line (no newline), or None on EOF. Raises `ArtefactTimeout`."""
        deadline = time.monotonic() + timeout
        while True:
            while (nl := self._buf.find(b"\n")) >= 0:
                line, self._buf = self._buf[:nl], self._buf[nl + 1 :]
                if line.strip():
                    return line
            if self._eof:
                rest, self._buf = self._buf, b""
                return rest if rest.strip() else None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ArtefactTimeout(f"no response line within {timeout:g}s")
            ready, _, _ = select.select([self._fd], [], [], remaining)
            if not ready:
                raise ArtefactTimeout(f"no response line within {timeout:g}s")
            chunk = os.read(self._fd, 65536)
            if not chunk:
                self._eof = True
            else:
                self._buf += chunk


@dataclass
class Handle:
    """A running artefact. `base_url` is set for `http`; `request()` exists on `JsonlHandle`."""

    kind: str
    base_url: str | None = None
    port: int | None = None
    proc: subprocess.Popen[bytes] | None = None
    container: str | None = None
    follower: subprocess.Popen[bytes] | None = None  # docker: `docker logs -f` -> log file
    log_path: Path | None = None
    log_dir: Path | None = None
    log_file: Any = None
    stopped: bool = False

    def log_tail(self) -> str:
        """Last few non-empty log lines on one line (tracebacks end with the useful part)."""
        lines = [ln.strip() for ln in _tail(self.log_path).splitlines() if ln.strip()]
        shown = " | ".join(ln[:LOG_LINE_CHARS] for ln in lines[-LOG_TAIL_LINES:])
        return f"log tail: {shown}" if shown else ""


@dataclass
class JsonlHandle(Handle):
    """Interactive jsonl artefact: one request line -> one response line."""

    _reader: _LineReader | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _broken: str | None = None

    def request(self, line: str, timeout: float) -> str:
        if self.proc is None or self.proc.stdin is None or self._reader is None:
            raise ArtefactFailure("jsonl handle is not running")
        with self._lock:
            if self._broken:
                raise ArtefactFailure(f"jsonl channel unusable: {self._broken}")
            try:
                self.proc.stdin.write(line.encode("utf-8") + b"\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._broken = f"write failed: {exc}"
                raise ArtefactFailure(f"artefact closed stdin: {exc}. {self.log_tail()}") from exc
            try:
                raw = self._reader.readline(timeout)
            except ArtefactTimeout:
                self._broken = "an earlier request timed out (late response would misalign)"
                raise
            if raw is None:
                self._broken = "stdout closed"
                raise ArtefactFailure(f"artefact exited without answering. {self.log_tail()}")
            return raw.decode("utf-8", errors="replace")


def _new_log(handle: Handle) -> None:
    handle.log_dir = Path(tempfile.mkdtemp(prefix="ao-bench-log-"))
    handle.log_path = handle.log_dir / "artefact.log"
    handle.log_file = open(handle.log_path, "wb")  # noqa: SIM115 -- closed in _release_logs


def _release_logs(handle: Handle) -> None:
    if handle.log_file is not None:
        try:
            handle.log_file.close()
        except OSError:
            pass
        handle.log_file = None
    if handle.log_dir is not None:
        shutil.rmtree(handle.log_dir, ignore_errors=True)
        handle.log_dir = None


def _kill_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _ping_jsonl(handle: JsonlHandle, timeout: float) -> None:
    """Readiness for `jsonl`: `{"op":"ping"}` -> `{"ok":true}`."""
    try:
        answer = json.loads(handle.request(PING_REQUEST, timeout))
    except ArtefactTimeout as exc:
        raise ArtefactStartError(
            f"no ping answer within {timeout:g}s. {handle.log_tail()}".strip()
        ) from exc
    except ArtefactFailure as exc:
        raise ArtefactStartError(str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise ArtefactStartError(f"ping answer is not JSON: {exc}") from exc
    if not (isinstance(answer, dict) and answer.get("ok") is True):
        raise ArtefactStartError(f'ping answered {answer!r}, expected {{"ok": true}}')


# ---------------------------------------------------------------------------
# Sandbox ABC
# ---------------------------------------------------------------------------


class Sandbox(ABC):
    """Runs an artefact directory. Results/diagnostics carry `kind` and `image`."""

    kind: Literal["docker", "process"]

    def __init__(self, cfg: SandboxConfig, language: str) -> None:
        self.cfg = cfg
        self.language = language

    @property
    @abstractmethod
    def image(self) -> str | None:
        """Image tag the artefact runs in (None for the process sandbox)."""

    @abstractmethod
    def mount_path(self, artefact_dir: Path, path: Path) -> str:
        """How the artefact sees `path` (which lives under `artefact_dir`)."""

    @abstractmethod
    def start_http(self, artefact_dir: Path, env: Mapping[str, str] | None = None) -> Handle:
        """Start `run.sh` serving HTTP; return once `GET /healthz` -> 200 (`handle.base_url`)."""

    @abstractmethod
    def start_jsonl(self, artefact_dir: Path, env: Mapping[str, str] | None = None) -> JsonlHandle:
        """Start `run.sh` as a stdin/stdout JSON-lines process; return once it answers a ping."""

    @abstractmethod
    def run_cli(
        self,
        artefact_dir: Path,
        args: Sequence[str],
        stdin: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_RUN_TIMEOUT_SECONDS,
    ) -> tuple[int, str, str]:
        """Run `run.sh <args>` once -> (returncode, stdout, stderr); raises `ArtefactTimeout`."""

    @abstractmethod
    def stop(self, handle: Handle) -> None:
        """Stop a handle returned by `start_http`/`start_jsonl` (idempotent)."""

    def run_jsonl(
        self,
        artefact_dir: Path,
        lines: Sequence[str],
        env: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_RUN_TIMEOUT_SECONDS,
    ) -> list[str]:
        """Batch convenience over `start_jsonl`: send each line, return the response lines."""
        handle = self.start_jsonl(artefact_dir, env)
        try:
            return [handle.request(line, timeout) for line in lines]
        finally:
            self.stop(handle)


# ---------------------------------------------------------------------------
# Process sandbox
# ---------------------------------------------------------------------------


def _task_budget(pids: int) -> int:
    """RLIMIT_NPROC counts every task of the real UID (threads included), so a bare
    `pids` limit would stop the artefact forking at all on a busy host. Allow `pids` more
    than what currently exists (system-wide upper bound from /proc/loadavg)."""
    try:
        with open("/proc/loadavg", encoding="ascii") as fh:
            total = int(fh.read().split()[3].split("/")[1])
    except (OSError, IndexError, ValueError):
        total = _FALLBACK_BASE_TASKS
    return total + pids


def _clamped_setrlimit(which: int, value: int) -> None:
    _soft, hard = resource.getrlimit(which)
    if hard != resource.RLIM_INFINITY:
        value = min(value, hard)
    resource.setrlimit(which, (value, value))


class ProcessSandbox(Sandbox):
    """Subprocess + rlimits + process-group kill. Tagged fallback, not an isolation boundary."""

    kind: Literal["docker", "process"] = "process"

    @property
    def image(self) -> str | None:
        return None

    def mount_path(self, artefact_dir: Path, path: Path) -> str:
        return str(path)

    # -- limits ------------------------------------------------------------------------

    def _preexec(self) -> Callable[[], None]:
        mem = parse_memory(self.cfg.memory)
        tasks = _task_budget(self.cfg.pids)
        # V8 / the Go runtime reserve huge virtual ranges that RLIMIT_AS would kill, so
        # non-python artefacts get RLIMIT_DATA (heap + private writable mappings) instead.
        mem_limit = resource.RLIMIT_AS if self.language == "python" else resource.RLIMIT_DATA

        def apply() -> None:  # runs in the forked child: setrlimit only, nothing else
            for which, value in ((mem_limit, mem), (resource.RLIMIT_NPROC, tasks)):
                try:
                    _clamped_setrlimit(which, value)
                except (ValueError, OSError):
                    pass

        return apply

    def _env(self, artefact_dir: Path, extra: Mapping[str, str]) -> dict[str, str]:
        base = {k: os.environ[k] for k in _PROCESS_ENV_PASSTHROUGH if k in os.environ}
        base["HOME"] = str(artefact_dir)
        base.update(extra)
        return base

    def _spawn(
        self,
        artefact_dir: Path,
        argv: list[str],
        env: Mapping[str, str],
        *,
        stdin: Any,
        stdout: Any,
        stderr: Any,
        bufsize: int = -1,
    ) -> subprocess.Popen[bytes]:
        try:
            return subprocess.Popen(  # noqa: S603 -- argv from run.sh shebang / `sh`, no shell
                argv,
                cwd=artefact_dir,
                env=self._env(artefact_dir, env),
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                bufsize=bufsize,
                start_new_session=True,  # own process group -> killpg takes the whole tree
                preexec_fn=self._preexec(),  # noqa: PLW1509 -- rlimits; setrlimit-only, thread-safe enough
            )
        except OSError as exc:
            raise ArtefactStartError(f"failed to spawn {argv[0]!r}: {exc}") from exc

    # -- contract entry points ---------------------------------------------------------

    def start_http(self, artefact_dir: Path, env: Mapping[str, str] | None = None) -> Handle:
        argv = run_sh_argv(artefact_dir)
        port = free_port()
        handle = Handle(kind=self.kind, base_url=f"http://{LOOPBACK}:{port}", port=port)
        _new_log(handle)
        proc = None
        try:
            proc = self._spawn(
                artefact_dir,
                argv,
                {**(env or {}), "PORT": str(port), "HOST": HTTP_BIND_HOST_PROCESS},
                stdin=subprocess.DEVNULL,
                stdout=handle.log_file,
                stderr=subprocess.STDOUT,
            )
            handle.proc = proc
            _wait_http_ready(
                port,
                self.cfg.startup_timeout_seconds,
                alive=lambda: proc.poll() is None,
                diag=handle.log_tail,
            )
        except BaseException:
            self.stop(handle)
            raise
        return handle

    def start_jsonl(self, artefact_dir: Path, env: Mapping[str, str] | None = None) -> JsonlHandle:
        argv = run_sh_argv(artefact_dir)
        handle = JsonlHandle(kind=self.kind)
        _new_log(handle)
        try:
            proc = self._spawn(
                artefact_dir,
                argv,
                dict(env or {}),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=handle.log_file,
                bufsize=0,
            )
            handle.proc = proc
            assert proc.stdout is not None
            handle._reader = _LineReader(proc.stdout.fileno())
            _ping_jsonl(handle, self.cfg.startup_timeout_seconds)
        except BaseException:
            self.stop(handle)
            raise
        return handle

    def run_cli(
        self,
        artefact_dir: Path,
        args: Sequence[str],
        stdin: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_RUN_TIMEOUT_SECONDS,
    ) -> tuple[int, str, str]:
        argv = [*run_sh_argv(artefact_dir), *args]
        proc = self._spawn(
            artefact_dir,
            argv,
            dict(env or {}),
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            out, err = proc.communicate(
                input=stdin.encode("utf-8") if stdin is not None else None, timeout=timeout
            )
        except subprocess.TimeoutExpired as exc:
            _kill_group(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise ArtefactTimeout(f"run.sh {' '.join(args)} exceeded {timeout:g}s") from exc
        finally:
            _kill_group(proc.pid, signal.SIGKILL)  # reap anything the script left behind
        return (
            int(proc.returncode),
            out.decode("utf-8", errors="replace"),
            err.decode("utf-8", errors="replace"),
        )

    def stop(self, handle: Handle) -> None:
        if handle.stopped:
            return
        handle.stopped = True
        proc = handle.proc
        if proc is not None:
            if proc.poll() is None:
                _kill_group(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=STOP_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    pass
            _kill_group(proc.pid, signal.SIGKILL)  # SIGKILL stragglers in the group
            try:
                proc.wait(timeout=STOP_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                pass
            for stream in (proc.stdin, proc.stdout):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
        _release_logs(handle)


# ---------------------------------------------------------------------------
# Docker sandbox
# ---------------------------------------------------------------------------


class DockerSandbox(Sandbox):
    """Per-language image, resource limits, no network except the published port."""

    kind: Literal["docker", "process"] = "docker"

    def __init__(self, cfg: SandboxConfig, language: str) -> None:
        super().__init__(cfg, language)
        self.spec = resolve_image_spec(language, cfg)
        self._image_ok = False

    @property
    def image(self) -> str | None:
        return self.spec.image

    def mount_path(self, artefact_dir: Path, path: Path) -> str:
        return f"{CONTAINER_WORKDIR}/{path.resolve().relative_to(artefact_dir.resolve())}"

    def ensure_image(self) -> None:
        if not self._image_ok:
            ensure_image(self.spec, build_missing=self.cfg.build_missing)
            self._image_ok = True

    @staticmethod
    def _name() -> str:
        return f"{CONTAINER_NAME_PREFIX}{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _rm(name: str) -> None:
        try:
            _docker(["rm", "-f", name], timeout=30)
        except SandboxUnavailable:
            pass  # best effort; the container dies with --rm when its process ends

    def _follow_logs(self, handle: Handle) -> None:
        """Stream the container's output into the handle's log file (survives `--rm`)."""
        assert handle.container is not None
        handle.follower = subprocess.Popen(  # noqa: S603
            ["docker", "logs", "-f", handle.container],  # noqa: S607
            stdout=handle.log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )

    def start_http(self, artefact_dir: Path, env: Mapping[str, str] | None = None) -> Handle:
        command = run_sh_argv(artefact_dir)
        self.ensure_image()
        full_env = {**(env or {}), "PORT": str(CONTAINER_PORT), "HOST": HTTP_BIND_HOST_DOCKER}
        last_err = ""
        for _ in range(DOCKER_PORT_RETRIES):
            port = free_port()
            name = self._name()
            argv = build_docker_run_argv(
                self.spec.image,
                artefact_dir,
                command,
                cfg=self.cfg,
                name=name,
                env=full_env,
                detach=True,
                publish=(port, CONTAINER_PORT),
            )
            proc = subprocess.run(  # noqa: S603
                argv,
                capture_output=True,
                text=True,
                timeout=DOCKER_RUN_START_TIMEOUT_SECONDS,
                check=False,
            )
            if proc.returncode == 0:
                break
            last_err = (proc.stderr or proc.stdout)[-LOG_TAIL_CHARS:]
            if "already allocated" not in last_err and "address already in use" not in last_err:
                raise SandboxUnavailable(f"docker run failed: {last_err}")
        else:
            raise SandboxUnavailable(f"docker run failed after port retries: {last_err}")
        handle = Handle(
            kind=self.kind, base_url=f"http://{LOOPBACK}:{port}", port=port, container=name
        )
        _new_log(handle)
        try:
            self._follow_logs(handle)
            follower = handle.follower
            assert follower is not None
            _wait_http_ready(
                port,
                self.cfg.startup_timeout_seconds,
                alive=lambda: follower.poll() is None,  # `logs -f` ends when the container does
                diag=handle.log_tail,
            )
        except BaseException:
            self.stop(handle)
            raise
        return handle

    def start_jsonl(self, artefact_dir: Path, env: Mapping[str, str] | None = None) -> JsonlHandle:
        command = run_sh_argv(artefact_dir)
        self.ensure_image()
        name = self._name()
        argv = build_docker_run_argv(
            self.spec.image,
            artefact_dir,
            command,
            cfg=self.cfg,
            name=name,
            env=dict(env or {}),
            interactive=True,
        )
        handle = JsonlHandle(kind=self.kind, container=name)
        _new_log(handle)
        try:
            proc = subprocess.Popen(  # noqa: S603
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=handle.log_file,
                bufsize=0,
            )
            handle.proc = proc
            assert proc.stdout is not None
            handle._reader = _LineReader(proc.stdout.fileno())
            _ping_jsonl(handle, self.cfg.startup_timeout_seconds)
        except BaseException:
            self.stop(handle)
            raise
        return handle

    def run_cli(
        self,
        artefact_dir: Path,
        args: Sequence[str],
        stdin: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_RUN_TIMEOUT_SECONDS,
    ) -> tuple[int, str, str]:
        command = [*run_sh_argv(artefact_dir), *args]
        self.ensure_image()
        name = self._name()
        argv = build_docker_run_argv(
            self.spec.image,
            artefact_dir,
            command,
            cfg=self.cfg,
            name=name,
            env=dict(env or {}),
            interactive=stdin is not None,
        )
        try:
            proc = subprocess.run(  # noqa: S603
                argv,
                input=stdin if stdin is not None else None,
                stdin=None if stdin is not None else subprocess.DEVNULL,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            self._rm(name)
            raise ArtefactTimeout(f"run.sh {' '.join(args)} exceeded {timeout:g}s") from exc
        return proc.returncode, proc.stdout or "", proc.stderr or ""

    def stop(self, handle: Handle) -> None:
        if handle.stopped:
            return
        handle.stopped = True
        if handle.container is not None:
            self._rm(handle.container)
        for proc in (handle.proc, handle.follower):
            if proc is None:
                continue
            if proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=STOP_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                pass
            for stream in (proc.stdin, proc.stdout):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
        _release_logs(handle)


# ---------------------------------------------------------------------------
# factory
# ---------------------------------------------------------------------------


def create_sandbox(cfg: SandboxConfig, language: str) -> Sandbox:
    """Resolve `type: auto` (docker if reachable, else process) and build the sandbox."""
    kind = cfg.type
    if kind == "auto":
        kind = "docker" if docker_available() else "process"
    elif kind == "docker" and not docker_available():
        raise SandboxUnavailable(
            "sandbox type 'docker' requested but the docker daemon is unreachable"
        )
    return DockerSandbox(cfg, language) if kind == "docker" else ProcessSandbox(cfg, language)


# ---------------------------------------------------------------------------
# `ao-bench images`
# ---------------------------------------------------------------------------


def _suite_plan(suite_path: Path, languages: list[str] | None) -> list[ImageSpec]:
    from .spec import load_suite

    suite = load_suite(suite_path)
    wanted = languages or sorted(
        {t.language for t in suite.tasks if t.language} | set((suite.stack or {}).keys())
    )
    return plan_images(suite.stack, [lang for lang in wanted if lang in DEFAULT_IMAGES] or None)


def images_command(
    build: bool = typer.Option(
        False, "--build", help="Pull base images and build derived ones (cached by content hash)."
    ),
    list_: bool = typer.Option(
        False, "--list", help="List the images the suite/languages resolve to and whether present."
    ),
    suite: Path | None = typer.Option(
        None, "--suite", help="suite.json whose `stack` block and task languages select the images."
    ),
    language: list[str] | None = typer.Option(
        None, "--language", "-l", help="Restrict to this language (repeatable)."
    ),
) -> None:
    """Manage the sandbox images used to grade generated-task artefacts."""
    try:
        specs = _suite_plan(suite, language) if suite is not None else plan_images(None, language)
    except (GraderError, ValueError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1) from exc
    except Exception as exc:  # noqa: BLE001 -- SpecValidationError etc. -> usage error
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1) from exc

    have_docker = docker_available()
    if build and not have_docker:
        typer.echo("ERROR: docker is not available; cannot build/pull images", err=True)
        raise typer.Exit(1)

    failed = False
    for spec in specs:
        if build:
            try:
                ensure_image(spec)
                status = "ready"
            except SandboxUnavailable as exc:
                status = f"FAILED: {exc}"
                failed = True
        elif have_docker:
            status = "present" if image_present(spec.image) else "missing"
        else:
            status = "unknown (docker unavailable)"
        deps = f" deps={list(spec.deps)}" if spec.deps else ""
        derived = f" (derived from {spec.base})" if spec.dockerfile is not None else ""
        typer.echo(f"{spec.language:<11} {spec.image}{derived}{deps}  [{status}]")
    if not build and not list_:
        typer.echo("(listing only; pass --build to pull/build)")
    if failed:
        raise typer.Exit(1)
