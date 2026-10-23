"""A small, test-only evaluator of the probe DSL (docs-md/bench-probe-dsl.md).

This is NOT the grader (that is ``bench/blackbox.py``, a separate track) -- it exists so the
generators can prove, in the unit tests, that every instance's reference passes every probe it
emits.  It interprets the DSL independently: templating, JSONPath-lite, the matcher language,
sequential / parallel / after phases, and a small perf smoke (load + a few mix ops).

Targets: the reference runs either in-process (fast; a fresh store per ``fresh_state``) or as the
real ``run.sh`` subprocess.
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import os
import random
import re
import socket
import subprocess
import threading
import time
import types
import uuid
from collections.abc import Callable
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

_TEMPLATE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


class ProbeDefinitionError(Exception):
    """Unknown variable, bad matcher ... (the grader would raise GraderError)."""


# ---------------------------------------------------------------------------------------------
# templating, JSONPath-lite, matcher
# ---------------------------------------------------------------------------------------------


_WHOLE_TEMPLATE = re.compile(r"^\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}$")


def render(value: Any, variables: dict[str, Any], index: int | None = None) -> Any:
    """Template substitution.  A string that is exactly one ``{{var}}`` keeps the saved value's
    JSON type; embedded in a longer string it is stringified.  ``{{i}}``, ``{{rand}}`` and
    ``{{now_iso}}`` are strings."""
    if isinstance(value, str):
        whole = _WHOLE_TEMPLATE.match(value)
        if whole and whole.group(1) in variables and whole.group(1) not in ("i",):
            return variables[whole.group(1)]

        def sub(m: re.Match[str]) -> str:
            name = m.group(1)
            if name == "rand":
                return uuid.uuid4().hex[:8]
            if name == "now_iso":
                return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            if name == "i" and index is not None:
                return str(index)
            if name in variables:
                return str(variables[name])
            raise ProbeDefinitionError(f"unknown variable {name!r}")

        return _TEMPLATE.sub(sub, value)
    if isinstance(value, dict):
        return {render(k, variables, index): render(v, variables, index) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, variables, index) for v in value]
    return value


def jsonpath(doc: Any, path: str) -> Any:
    if not re.match(r"^\$(?:\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])*$", path):
        raise ProbeDefinitionError(f"bad JSONPath-lite {path!r}")
    cur = doc
    for key, idx in re.findall(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]", path):
        try:
            cur = cur[key] if key else cur[int(idx)]
        except (KeyError, IndexError, TypeError) as exc:
            raise KeyError(f"path {path} not found in response") from exc
    return cur


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    return bool(a == b)


_TYPES: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}


def match(expected: Any, actual: Any, variables: dict[str, Any], path: str = "$") -> list[str]:
    if isinstance(expected, dict):
        dkeys = [k for k in expected if k.startswith("$")]
        if dkeys:
            return _directive(expected, dkeys[0], actual, variables, path)
        if not isinstance(actual, dict):
            return [f"{path}: expected object, got {type(actual).__name__}"]
        out: list[str] = []
        for k, v in expected.items():
            if k not in actual:
                out.append(f"{path}.{k}: missing")
            else:
                out += match(v, actual[k], variables, f"{path}.{k}")
        return out
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            n = len(actual) if isinstance(actual, list) else type(actual).__name__
            return [f"{path}: expected array of {len(expected)}, got {n}"]
        out = []
        for i, (e, a) in enumerate(zip(expected, actual, strict=True)):
            out += match(e, a, variables, f"{path}[{i}]")
        return out
    return [] if _same(expected, actual) else [f"{path}: expected {expected!r}, got {actual!r}"]


def _directive(
    m: dict[str, Any], d: str, actual: Any, variables: dict[str, Any], path: str
) -> list[str]:
    arg = m[d]
    if d == "$any":
        return []
    if d == "$type":
        return [] if _TYPES[arg](actual) else [f"{path}: expected type {arg}, got {actual!r}"]
    if d in ("$len", "$len_min", "$len_max"):
        if not isinstance(actual, (list, str)):
            return [f"{path}: {d} on non-sequence {actual!r}"]
        ok = {"$len": len(actual) == arg, "$len_min": len(actual) >= arg,
              "$len_max": len(actual) <= arg}[d]  # fmt: skip
        return [] if ok else [f"{path}: {d} {arg} but length is {len(actual)}"]
    if d == "$regex":
        ok = isinstance(actual, str) and re.search(arg, actual) is not None
        return [] if ok else [f"{path}: {actual!r} !~ {arg}"]
    if d == "$in":
        return [] if any(_same(actual, x) for x in arg) else [f"{path}: {actual!r} not in {arg}"]
    if d in ("$gte", "$lte"):
        ok = isinstance(actual, (int, float)) and (actual >= arg if d == "$gte" else actual <= arg)
        return [] if ok else [f"{path}: {actual!r} fails {d} {arg}"]
    if d == "$sorted_by":
        if not isinstance(actual, list):
            return [f"{path}: $sorted_by on non-array"]
        field, desc = arg, m["order"] == "desc"
        tie = m.get("tiebreak")
        keys = [(x[field], x[tie] if tie else "") for x in actual]
        for prev, cur in zip(keys, keys[1:], strict=False):
            if desc:
                bad = prev[0] < cur[0] or (prev[0] == cur[0] and prev[1] < cur[1])
            else:
                bad = prev[0] > cur[0] or (prev[0] == cur[0] and prev[1] > cur[1])
            if bad:
                return [
                    f"{path}: not sorted by {field} {m['order']} (tiebreak {tie}): {prev} {cur}"
                ]
        return []
    if d == "$set_equals":
        a, e = (
            sorted(json.dumps(x, sort_keys=True) for x in actual),
            sorted(json.dumps(x, sort_keys=True) for x in arg),
        )
        return [] if a == e else [f"{path}: multiset differs"]
    if d == "$each":
        out: list[str] = []
        for i, x in enumerate(actual):
            out += match(arg, x, variables, f"{path}[{i}]")
        return out
    if d == "$contains":
        ok = any(not match(arg, x, variables, path) for x in actual)
        return [] if ok else [f"{path}: no element matches {arg}"]
    if d == "$var":
        if arg not in variables:
            raise ProbeDefinitionError(f"unknown variable {arg!r} in $var")
        return (
            []
            if _same(variables[arg], actual)
            else [f"{path}: expected ${arg}={variables[arg]!r}, got {actual!r}"]
        )
    if d == "$not":
        return (
            [f"{path}: unexpectedly matched {arg}"]
            if not match(arg, actual, variables, path)
            else []
        )
    raise ProbeDefinitionError(f"unknown directive {d}")


# ---------------------------------------------------------------------------------------------
# targets
# ---------------------------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _load_module(path: Path, name: str, patch: Callable[[str], str] | None = None) -> Any:
    """Import the reference program; ``patch`` rewrites its source first (mutation controls)."""
    if patch is None:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    mod = types.ModuleType(name)
    mod.__file__ = str(path)
    exec(compile(patch(path.read_text(encoding="utf-8")), str(path), "exec"), mod.__dict__)
    return mod


def replace_once(old: str, new: str) -> Callable[[str], str]:
    """A source patch that replaces exactly one occurrence (fails loudly if the reference moved)."""

    def apply(source: str) -> str:
        assert source.count(old) == 1, f"mutation anchor not found exactly once: {old!r}"
        return source.replace(old, new)

    return apply


def chain_patches(*patches: Callable[[str], str]) -> Callable[[str], str]:
    def apply(source: str) -> str:
        for p in patches:
            source = p(source)
        return source

    return apply


class Target:
    """The artefact under test; ``fresh()`` returns a ready instance with empty state."""

    def start(self) -> None: ...
    def stop(self) -> None: ...

    def request(self, op: dict[str, Any]) -> tuple[int | None, Any]:
        raise NotImplementedError


class InProcessHttp(Target):
    def __init__(
        self, ref_dir: Path, instance_json: Path, patch: Callable[[str], str] | None = None
    ) -> None:
        self.mod = _load_module(ref_dir / "server.py", f"ref_crud_{uuid.uuid4().hex}", patch)
        self.inst = json.loads(instance_json.read_text())
        self.server: ThreadingHTTPServer | None = None
        self.port = 0
        self.local = threading.local()

    def start(self) -> None:
        store = self.mod.Store(self.inst)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.mod.make_handler(store))
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        ).start()

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()

    def request(self, op: dict[str, Any]) -> tuple[int | None, Any]:
        return http_request("127.0.0.1", self.port, op)


def http_request(host: str, port: int, op: dict[str, Any]) -> tuple[int | None, Any]:
    conn = http.client.HTTPConnection(host, port, timeout=20)
    try:
        headers = dict(op.get("headers") or {})
        body = None
        if op.get("json") is not None:
            body = json.dumps(op["json"]).encode()
            headers.setdefault("Content-Type", "application/json")
        conn.request(op["method"], op["path"], body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        return resp.status, (json.loads(raw) if raw else None)
    finally:
        conn.close()


class InProcessJsonl(Target):
    def __init__(
        self,
        ref_dir: Path,
        instance_json: Path,
        patch: Callable[[str], str] | None = None,
        limits: dict[str, Any] | None = None,
    ) -> None:
        self.mod = _load_module(ref_dir / "server.py", f"ref_queue_{uuid.uuid4().hex}", patch)
        self.limits = {**json.loads(instance_json.read_text())["limits"], **(limits or {})}
        self.queue: Any = None
        self.lock = threading.Lock()

    def start(self) -> None:
        self.queue = self.mod.Queue(self.limits)

    def request(self, op: dict[str, Any]) -> tuple[int | None, Any]:
        with self.lock:
            try:
                return None, self.queue.handle(op["json"])
            except self.mod.OpError as err:
                return None, {"ok": False, "error": err.code, "message": err.message}


class ProcessTarget(Target):
    """The real thing: ``reference/run.sh`` with PORT / INSTANCE_JSON, readiness per DSL §1."""

    def __init__(self, grading_dir: Path, contract: str, startup_timeout: float = 30) -> None:
        self.run_sh = grading_dir / "reference" / "run.sh"
        self.env_instance = str(grading_dir / "instance.json")
        self.contract = contract
        self.timeout = startup_timeout
        self.proc: subprocess.Popen[str] | None = None
        self.port = 0
        self.lock = threading.Lock()

    def start(self) -> None:
        # The grader's environment: INSTANCE_JSON for the reference, PORT/HOST (http), unbuffered.
        env = {**os.environ, "INSTANCE_JSON": self.env_instance, "PYTHONUNBUFFERED": "1"}
        if self.contract == "http":
            self.port = _free_port()
            env["PORT"] = str(self.port)
            env["HOST"] = "127.0.0.1"
        self.proc = subprocess.Popen(
            [str(self.run_sh)], env=env, text=True, bufsize=1,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )  # fmt: skip
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"reference exited early: {self.proc.stderr.read()}")  # type: ignore[union-attr]
            try:
                if self.contract == "http":
                    status, _ = http_request(
                        "127.0.0.1", self.port, {"method": "GET", "path": "/healthz"}
                    )
                    if status == 200:
                        return
                else:
                    _, resp = self.request({"json": {"op": "ping"}})
                    if resp == {"ok": True}:
                        return
            except (OSError, http.client.HTTPException):
                time.sleep(0.05)
        raise RuntimeError("reference did not become ready")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.proc:
            for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                if stream:
                    stream.close()

    def request(self, op: dict[str, Any]) -> tuple[int | None, Any]:
        if self.contract == "http":
            return http_request("127.0.0.1", self.port, op)
        assert self.proc and self.proc.stdin and self.proc.stdout
        with self.lock:
            self.proc.stdin.write(json.dumps(op["json"]) + "\n")
            self.proc.stdin.flush()
            return None, json.loads(self.proc.stdout.readline())


# ---------------------------------------------------------------------------------------------
# evaluator
# ---------------------------------------------------------------------------------------------


class Runner:
    def __init__(self, probes: dict[str, Any], make_target: Callable[[], Target]) -> None:
        self.probes = probes
        self.make_target = make_target

    def _exec_step(
        self, target: Target, step: dict[str, Any], variables: dict[str, Any], where: str
    ) -> list[str]:
        count = step.get("repeat", 1)
        for i in range(count):
            op = render(step["op"], variables, i if "repeat" in step else None)
            status, body = target.request(op)
            expect = step.get("expect") or {}
            problems: list[str] = []
            want = expect.get("status")
            if want is not None and status not in (want if isinstance(want, list) else [want]):
                problems.append(f"status {status} != {want}")
            if "json" in expect:
                exp = render(expect["json"], variables, i if "repeat" in step else None)
                problems += match(exp, body, variables)
            if problems:
                return [
                    f"{where} (iteration {i}): {op} -> {status} "
                    f"{json.dumps(body)[:300]}: {problems}"
                ]
            for name, path in (step.get("save") or {}).items():
                try:
                    variables[name] = jsonpath(body, path)
                except KeyError as exc:
                    return [f"{where}: save {name}: {exc}"]
            if step.get("sleep_ms"):
                time.sleep(step["sleep_ms"] / 1000)
        return []

    def _run_list(
        self, target: Target, steps: list[dict[str, Any]], variables: dict[str, Any], where: str
    ) -> list[str]:
        for n, st in enumerate(steps):
            problems = self._exec_step(target, st, variables, f"{where}[{n}]")
            if problems:
                return problems
        return []

    def run_probe(self, p: dict[str, Any], target: Target) -> list[str]:
        variables: dict[str, Any] = {}
        problems = self._run_list(target, p["steps"], variables, f"{p['id']}.steps")
        if problems:
            return problems
        if p.get("parallel"):
            results: list[list[str]] = [[] for _ in p["parallel"]]

            def work(k: int, lst: list[dict[str, Any]]) -> None:
                results[k] = self._run_list(
                    target, lst, dict(variables), f"{p['id']}.parallel[{k}]"
                )

            threads = [
                threading.Thread(target=work, args=(k, lst)) for k, lst in enumerate(p["parallel"])
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            for r in results:
                problems += r
        if p.get("after") and not problems:
            problems += self._run_list(target, p["after"], dict(variables), f"{p['id']}.after")
        return problems

    def run_groups(self) -> dict[str, list[str]]:
        """{probe id: problems} for every core + trap probe, honouring ``fresh_state``."""
        out: dict[str, list[str]] = {}
        target: Target | None = None
        try:
            for group in ("core", "trap"):
                for p in self.probes["groups"][group]:
                    if target is None or p.get("fresh_state", True):
                        if target is not None:
                            target.stop()
                        target = self.make_target()
                        target.start()
                    out[p["id"]] = self.run_probe(p, target)
        finally:
            if target is not None:
                target.stop()
        return out

    def run_perf_smoke(self, ops: int = 40) -> list[str]:
        """Load a few records with the perf `load` step and draw `ops` mix steps round-robin
        (weights ignored): proves templating/expects are executable for every mix entry."""
        perf = self.probes["groups"]["perf"]
        target = self.make_target()
        target.start()
        try:
            size = 25
            rng = random.Random(0)
            variables: dict[str, Any] = {}
            load = dict(perf["load"])
            load["repeat"] = size
            problems = self._exec_step(target, load, variables, "perf.load")
            for n in range(ops):
                entry = perf["mix"][n % len(perf["mix"])]
                # In a mix step `{{i}}` is a random index in [0, N) and `{{N}}` the current size;
                # variables saved by the load step are deliberately NOT carried over.
                mix_vars = {"i": str(rng.randrange(size)), "N": str(size)}
                problems += self._exec_step(
                    target, dict(entry["step"]), mix_vars, f"perf.mix[{entry['name']}]"
                )
            return problems
        finally:
            target.stop()
