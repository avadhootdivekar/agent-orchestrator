"""E-Wk9Tz3 T-Ee3Mn8 matrix item D: structural security guards over `src/`, plus a real
dispatch proving the ADR-0005 planted-secret pass-through / `GIT_ASKPASS` override
behavior.

Three structural guards, each with an honestly stated scope:

1. **No `subprocess` git invocation anywhere under `src/agent_orchestrator/`** outside
   `_GIT_CALL_ALLOWED` -- a named, per-file, justified allowlist (see its comments).
   The scan is **alias-aware**: `import subprocess as _sp`, `from subprocess import run
   as _r`, an argv held in a local variable, and the `shell=True` string form are all
   detected, because the pre-rework version of this guard matched only the single
   literal spelling `subprocess.run(["git", ...])` and a 2026-09-07 review defeated it
   by mutation with each of those four variants. `_scan_for_subprocess_git_argv` is the
   one implementation, and `test_the_guard_catches_every_known_bypass_spelling` runs
   **that same function** over fixture modules containing exactly those bypasses.
   Scope note: this guard is deliberately `src/`-wide, not `isolation/`-only, so a git
   shell-out added to `engine.py` (or anywhere else off the isolation package) trips it
   too. Non-git subprocess uses -- `isolation/resolvers.py`'s spec-supplied
   `RegenerateResolver` command and `isolation/integrator.py`'s `verify_command` runner
   -- are correctly *not* flagged, and `_clean.py` in the bypass fixture proves the
   guard does not simply flag every `subprocess` call.

2. **The exact set of modules under `isolation/` that import `subprocess` at all**
   equals `_ISOLATION_SUBPROCESS_IMPORTERS`. This one is alias-proof by construction:
   it does not look at call sites, so no spelling of a call can slip past it. Its job
   is to force a review whenever a *new* module inside the isolation package acquires
   the raw primitive at all, instead of going through `GitRepo`'s forbidden-verb /
   timeout / hook-scrubbing guarantees.

3. **No caller of `LocalFsArtifactStore.resolve_unchecked` outside `isolation/view.py`**
   (AST-based, over every `.py` file under `src/agent_orchestrator/`).

Known limits, stated rather than implied. Guard 1's allowlist is per-FILE, so a *new* git call
added inside one of the six allowlisted modules is not flagged (all six are outside the isolation
boundary except `git.py`, which is the sanctioned runner). And an argv assembled fully at runtime
from non-literal parts is not statically decidable by any AST scan. Guard 2 closes the second hole
for the isolation package specifically, by refusing new `subprocess` importers there at all.

`tests/isolation/test_git.py::TestStructuralNoNetworkSurface::
test_public_method_surface_never_reaches_a_forbidden_verb` ALREADY implements "every
public `GitRepo` method appears in the forbidden-verb sweep list" (T-Ib5Qy9,
self-enforcing via `assert set(call_args) >= set(public_methods)`) -- re-verified as part
of the full-suite gate, not reimplemented here.

Finally, a planted parent-environment secret (`AWS_SECRET_ACCESS_KEY`) reaches a REAL T2
resolver dispatch (`ClaudeCliExecutor`, `subprocess.Popen` mocked so no real process
spawns) exactly as ADR-0005's documented pass-through behavior requires (`{**os.environ,
**ctx.env}` -- the process environment is inherited, never stripped), while `GIT_ASKPASS`
(part of `escalation.resolver_env`'s S-2 overlay) is forced to ``/bin/false`` regardless
of what the parent process had set.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_orchestrator.isolation import escalation
from agent_orchestrator.models import AgentSpec, IntegrationSpec, TaskContext

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "agent_orchestrator"
_ISOLATION_ROOT = _SRC_ROOT / "isolation"

# The `subprocess` module attributes that actually spawn a process. `subprocess.run`'s
# first parameter is named `args`, so the scan checks that keyword as well as argv[0].
_SUBPROCESS_SPAWN_CALLABLES = frozenset({"run", "Popen", "call", "check_call", "check_output"})
_SUBPROCESS_ARGV_KEYWORD = "args"

# Files allowed to build a literal git argv, each for a documented reason. Anything NOT
# listed here that shells out to git is a violation. `test_every_allowlisted_file_still_
# calls_git` keeps the list from going stale.
_GIT_CALL_ALLOWED: dict[Path, str] = {
    # The ONE sanctioned git runner: `_SubprocessRunner`, the sole production
    # implementation of `GitRepo`'s injected `Runner` protocol. Every forbidden-verb,
    # timeout and hook-scrubbing guarantee in the isolation subsystem funnels through it.
    _ISOLATION_ROOT / "git.py": "GitRepo's sole sanctioned runner (_SubprocessRunner)",
    # Pre-epic, outside the isolation boundary: a read-only `git rev-parse` probe used to
    # validate that a reposet entry really is a git checkout (spec.py V8).
    _SRC_ROOT / "spec.py": "pre-epic reposet V8 `git rev-parse` validation probe",
    # Pre-epic: `git describe`-style version stamping for `ao --version`.
    _SRC_ROOT / "_version.py": "pre-epic version stamping",
    # Pre-epic bench harness: records the subject repo's `git rev-parse --short HEAD`.
    _SRC_ROOT / "bench" / "runner.py": "pre-epic bench harness commit stamping",
    # Pre-epic SWE-bench import/grade harness: clones and diffs third-party subject repos.
    _SRC_ROOT / "bench" / "swebench_provider.py": "pre-epic SWE-bench subject checkout",
    _SRC_ROOT / "bench" / "swebench_grader.py": "pre-epic SWE-bench patch extraction",
}

# The complete set of modules INSIDE the isolation package that may import `subprocess`
# at all (guard 2). Alias-proof: it is an import-binding fact, not a call-site pattern.
_ISOLATION_SUBPROCESS_IMPORTERS = frozenset(
    {
        "git.py",  # _SubprocessRunner -- the sanctioned git runner
        "resolvers.py",  # RegenerateResolver runs the spec-supplied, non-git `rule.command`
        "integrator.py",  # _default_exec_runner runs the non-git `verify_command`
    }
)


def _iter_src_files() -> list[Path]:
    return sorted(_SRC_ROOT.rglob("*.py"))


def _iter_isolation_files() -> list[Path]:
    return sorted(_ISOLATION_ROOT.rglob("*.py"))


# --------------------------------------------------------------------------------------
# Guard 1: alias-aware "no subprocess git argv" scan
# --------------------------------------------------------------------------------------


def _subprocess_bindings(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Returns (names bound to the `subprocess` MODULE, names bound to one of its spawn
    callables). Covers `import subprocess`, `import subprocess as _sp`, `from subprocess
    import run` and `from subprocess import run as _r` -- the alias forms that defeated
    the pre-rework guard. `"subprocess"` is always treated as a module binding so a call
    is still flagged in the pathological case of a missing/lazy import."""
    modules = {"subprocess"}
    callables: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                if alias.name in _SUBPROCESS_SPAWN_CALLABLES:
                    callables.add(alias.asname or alias.name)
    return modules, callables


def _is_git_argv(node: ast.expr) -> bool:
    """True if `node` is syntactically an argv whose program is `git`: a `["git", ...]` /
    `("git", ...)` literal, such a literal extended with `+`, or the `shell=True` string
    form (`"git push ..."`, including an f-string with a literal `git ` prefix)."""
    if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
        head = node.elts[0]
        return isinstance(head, ast.Constant) and head.value == "git"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _is_git_argv(node.left)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        # `shell=True` string form; tolerate an absolute path such as "/usr/bin/git push".
        return node.value.split(" ")[0].rsplit("/", 1)[-1] == "git"
    if isinstance(node, ast.JoinedStr) and node.values:
        head = node.values[0]
        return (
            isinstance(head, ast.Constant)
            and isinstance(head.value, str)
            and head.value.split(" ")[0].rsplit("/", 1)[-1] == "git"
        )
    return False


def _git_argv_variable_names(tree: ast.Module) -> set[str]:
    """Names assigned a git argv anywhere in the module, so `argv = ["git", "push"]`
    followed by `subprocess.run(argv)` is still caught. Deliberately module-wide and
    flow-insensitive: over-approximating here can only make the guard stricter, and a
    name that ever holds a git argv is exactly what we want reviewed."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_git_argv(node.value):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
            and _is_git_argv(node.value)
        ):
            names.add(node.target.id)
    return names


def _find_subprocess_git_argv(tree: ast.Module) -> list[int]:
    """Line numbers of every process-spawning `subprocess` call in `tree` whose argv is a
    git invocation, resolved through import aliases and local argv variables."""
    modules, callables = _subprocess_bindings(tree)
    argv_names = _git_argv_variable_names(tree)
    violations: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        spawns = (
            isinstance(func, ast.Attribute)
            and func.attr in _SUBPROCESS_SPAWN_CALLABLES
            and isinstance(func.value, ast.Name)
            and func.value.id in modules
        ) or (isinstance(func, ast.Name) and func.id in callables)
        if not spawns:
            continue
        argv: ast.expr | None = node.args[0] if node.args else None
        if argv is None:
            argv = next(
                (kw.value for kw in node.keywords if kw.arg == _SUBPROCESS_ARGV_KEYWORD), None
            )
        if argv is None:
            continue
        if _is_git_argv(argv) or (isinstance(argv, ast.Name) and argv.id in argv_names):
            violations.append(node.lineno)
    return sorted(violations)


def _scan_for_subprocess_git_argv(
    paths: list[Path], *, allowed: set[Path], relative_to: Path
) -> dict[str, list[int]]:
    """The guard itself, factored out so its non-vacuity proof can run the REAL scan over
    fixture modules rather than re-checking one hand-picked AST pattern."""
    offenders: dict[str, list[int]] = {}
    for path in paths:
        if path in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        violations = _find_subprocess_git_argv(tree)
        if violations:
            offenders[str(path.relative_to(relative_to))] = violations
    return offenders


# Bypass spellings a future edit could reach for, each mapped to the fixture module body
# that spells it. The 2026-09-07 review defeated the pre-rework guard with #2, #3 and #4
# (and showed #5's scope hole); #6 is the negative control that keeps the guard from
# being trivially true by flagging every subprocess call.
_BYPASS_FIXTURES: dict[str, str] = {
    "plain": "import subprocess\nsubprocess.run(['git', 'status'])\n",
    "module_alias": "import subprocess as _sp\n_sp.run(['git', 'status'])\n",
    "from_import_alias": "from subprocess import run as _r\n_r(['git', 'status'])\n",
    "variable_argv": (
        "import subprocess\n\n\ndef go():\n    argv = ['git', 'push', '--force']\n"
        "    return subprocess.run(argv)\n"
    ),
    "shell_string": "import subprocess\nsubprocess.run('git push --force', shell=True)\n",
    "argv_keyword": "import subprocess\nsubprocess.Popen(args=('git', 'fetch'))\n",
}
_CLEAN_FIXTURE = (
    "import subprocess\n\n\ndef go(rule, tool):\n"
    "    subprocess.run(rule.command, timeout=5)\n"
    "    return subprocess.run([tool, '--version'])\n"
)


class TestNoSubprocessGitCall:
    def test_no_subprocess_git_argv_outside_the_allowlist(self) -> None:
        """Guard 1, at its real (src-wide) scope: no module under
        `src/agent_orchestrator/` spawns git except the documented allowlist."""
        offenders = _scan_for_subprocess_git_argv(
            _iter_src_files(), allowed=set(_GIT_CALL_ALLOWED), relative_to=_SRC_ROOT
        )
        assert offenders == {}, (
            "subprocess git call(s) found outside the documented allowlist "
            f"({ {str(p.relative_to(_SRC_ROOT)): why for p, why in _GIT_CALL_ALLOWED.items()} }): "
            f"{offenders}. Route git through isolation/git.py's GitRepo, or add a "
            "justified allowlist entry."
        )

    def test_the_guard_catches_every_known_bypass_spelling(self, tmp_path: Path) -> None:
        """W-6: prove the guard catches REAL bypasses, not just the one literal pattern
        it was originally written against. Each fixture module is scanned by the same
        `_scan_for_subprocess_git_argv` the gate above calls; every bypass must be
        flagged and the non-git control must not be."""
        for name, body in _BYPASS_FIXTURES.items():
            (tmp_path / f"{name}.py").write_text(body)
        (tmp_path / "_clean.py").write_text(_CLEAN_FIXTURE)

        offenders = _scan_for_subprocess_git_argv(
            sorted(tmp_path.rglob("*.py")), allowed=set(), relative_to=tmp_path
        )

        assert set(offenders) == {f"{name}.py" for name in _BYPASS_FIXTURES}, (
            f"guard missed a bypass spelling (or over-flagged the control): {offenders}"
        )
        assert "_clean.py" not in offenders, (
            "the guard flagged a non-git subprocess call -- it would break "
            "isolation/resolvers.py's regenerate runner and integrator.py's verify runner"
        )

    def test_the_allowlist_is_honoured_but_not_a_blanket_exemption(self, tmp_path: Path) -> None:
        """The allowlist suppresses exactly the files named in it, and nothing else --
        a second offender in the same scan is still reported."""
        (tmp_path / "exempt.py").write_text(_BYPASS_FIXTURES["plain"])
        (tmp_path / "other.py").write_text(_BYPASS_FIXTURES["module_alias"])
        offenders = _scan_for_subprocess_git_argv(
            sorted(tmp_path.rglob("*.py")),
            allowed={tmp_path / "exempt.py"},
            relative_to=tmp_path,
        )
        assert offenders == {"other.py": [2]}

    def test_every_allowlisted_file_still_calls_git(self) -> None:
        """Anti-stale-allowlist: every exemption must still name a live git caller, so a
        file that stops shelling out to git gets dropped from the allowlist instead of
        silently widening it (mirrors `test_isolation_view_py_does_call_resolve_unchecked`)."""
        stale = [
            str(path.relative_to(_SRC_ROOT))
            for path in _GIT_CALL_ALLOWED
            if not _find_subprocess_git_argv(
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            )
        ]
        assert stale == [], f"stale _GIT_CALL_ALLOWED entries (no git call remains): {stale}"

    def test_isolation_git_py_is_the_sole_runner_implementation(self) -> None:
        """Sanity: confirms `isolation/git.py` is exempted for a REAL, structural reason.
        `GitRepo` never calls `subprocess.run(["git", ...])` from its own methods -- every
        git invocation goes through the injected `Runner` protocol, whose sole production
        implementation, `_SubprocessRunner.__call__`, is the ONE place in the isolation
        package that spawns a git-prefixed argv -- which is what makes `GitRepo`'s
        forbidden-verb/timeout/hook-scrubbing guarantees fully unit-testable via
        `tests/isolation/conftest.py::RecordingFakeRunner` in the first place.
        """
        git_py = _ISOLATION_ROOT / "git.py"
        src = git_py.read_text(encoding="utf-8")
        assert "class _SubprocessRunner" in src
        assert '["git", "--version"]' in src  # a real, literal git-prefixed argv exists
        tree = ast.parse(src, filename=str(git_py))
        assert len(_find_subprocess_git_argv(tree)) == 1, (
            "expected exactly one git-spawning subprocess call in git.py (_SubprocessRunner)"
        )


# --------------------------------------------------------------------------------------
# Guard 2: alias-proof "who imports subprocess at all" set equality, isolation/ only
# --------------------------------------------------------------------------------------


def _modules_importing_subprocess(paths: list[Path], *, relative_to: Path) -> set[str]:
    """Names (relative to `relative_to`) of modules that bind `subprocess` in ANY form."""
    importers: set[str] = set()
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        modules, callables = _subprocess_bindings(tree)
        # `_subprocess_bindings` always seeds {"subprocess"}; a real import is anything
        # beyond that seed, or any `from subprocess import ...` binding.
        if modules != {"subprocess"} or callables:
            importers.add(str(path.relative_to(relative_to)))
            continue
        if any(
            (isinstance(n, ast.Import) and any(a.name == "subprocess" for a in n.names))
            for n in ast.walk(tree)
        ):
            importers.add(str(path.relative_to(relative_to)))
    return importers


class TestIsolationSubprocessImporters:
    def test_importer_set_matches_the_known_good_set(self) -> None:
        """Alias-proof companion to guard 1: because it inspects import bindings rather
        than call sites, no spelling of a subprocess call can evade it. A new isolation
        module reaching for the raw primitive must be reviewed and added here."""
        importers = _modules_importing_subprocess(
            _iter_isolation_files(), relative_to=_ISOLATION_ROOT
        )
        assert importers == set(_ISOLATION_SUBPROCESS_IMPORTERS), (
            "modules under isolation/ importing `subprocess` changed: "
            f"expected {sorted(_ISOLATION_SUBPROCESS_IMPORTERS)}, found {sorted(importers)}. "
            "Isolation code must go through GitRepo unless there is a reviewed reason not to."
        )

    def test_importer_detection_sees_every_import_spelling(self, tmp_path: Path) -> None:
        """Non-vacuity: the detector must recognise aliased and `from`-imports, which is
        the whole reason this guard exists alongside the call-site scan."""
        (tmp_path / "plain.py").write_text("import subprocess\n")
        (tmp_path / "aliased.py").write_text("import subprocess as _sp\n")
        (tmp_path / "from_import.py").write_text("from subprocess import run\n")
        (tmp_path / "from_alias.py").write_text("from subprocess import Popen as _P\n")
        (tmp_path / "innocent.py").write_text("import os\n\nSUBPROCESS = 'subprocess'\n")
        found = _modules_importing_subprocess(sorted(tmp_path.rglob("*.py")), relative_to=tmp_path)
        assert found == {"plain.py", "aliased.py", "from_import.py", "from_alias.py"}


# --------------------------------------------------------------------------------------
# Guard 3: resolve_unchecked containment (AC-11a)
# --------------------------------------------------------------------------------------


class _ResolveUncheckedCallVisitor(ast.NodeVisitor):
    """Flags every `<anything>.resolve_unchecked(...)` call site (attribute-call form --
    the only form this codebase uses it in, confirmed by grep)."""

    def __init__(self) -> None:
        self.violations: list[int] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "resolve_unchecked":
            self.violations.append(node.lineno)
        self.generic_visit(node)


class TestResolveUncheckedOnlyCalledFromView:
    def test_no_caller_outside_isolation_view_py(self) -> None:
        allowed = {
            _SRC_ROOT / "artifacts.py",  # defines it, and its own resolve() wraps it
            _SRC_ROOT / "isolation" / "view.py",  # the ONE sanctioned external caller
        }
        offenders: dict[str, list[int]] = {}
        for path in _iter_src_files():
            if path in allowed:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            visitor = _ResolveUncheckedCallVisitor()
            visitor.visit(tree)
            if visitor.violations:
                offenders[str(path.relative_to(_SRC_ROOT))] = visitor.violations
        assert offenders == {}, f"resolve_unchecked caller(s) found outside view.py: {offenders}"

    def test_isolation_view_py_does_call_resolve_unchecked(self) -> None:
        """Sanity: the exemption is for a real, active caller, not a stale allowlist entry."""
        view_py = _SRC_ROOT / "isolation" / "view.py"
        tree = ast.parse(view_py.read_text(encoding="utf-8"), filename=str(view_py))
        visitor = _ResolveUncheckedCallVisitor()
        visitor.visit(tree)
        assert visitor.violations, "expected isolation/view.py to call resolve_unchecked"

    def test_the_guard_itself_is_not_vacuous(self, tmp_path: Path) -> None:
        fixture = tmp_path / "fixture.py"
        fixture.write_text("store.resolve_unchecked('x')\n")
        tree = ast.parse(fixture.read_text(), filename=str(fixture))
        visitor = _ResolveUncheckedCallVisitor()
        visitor.visit(tree)
        assert visitor.violations == [1]


def _popen_factory(*, env_out: dict) -> object:
    """Drop-in `subprocess.Popen` replacement (mirrors `tests/test_executor.py`'s own
    `_popen_factory` pattern) that records the `env=` kwarg it was called with, instead of
    spawning a real process."""

    def factory(argv, cwd=None, stdin=None, stdout=None, stderr=None, env=None, **kwargs):  # noqa: ANN001
        env_out["env"] = env
        if stdout is not None:
            stdout.write('{"type": "result", "subtype": "success", "result": "ok"}\n')
        proc = MagicMock()
        proc.returncode = 0
        proc.wait.return_value = 0
        return proc

    return factory


class TestPlantedSecretPassThrough:
    def test_parent_env_secret_passes_through_git_askpass_overridden(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ADR-0005: a resolver dispatch (`ClaudeCliExecutor`, the real `subprocess.Popen`
        call site) inherits the FULL parent process environment (`{**os.environ,
        **ctx.env}`) -- a planted `AWS_SECRET_ACCESS_KEY` is NOT stripped or filtered --
        while `GIT_ASKPASS` (S-2's `escalation.resolver_env` overlay) is forced to
        `/bin/false` regardless of anything the parent process set.
        """
        from agent_orchestrator.executors.claude_cli import ClaudeCliExecutor

        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "planted-fake-secret-value")
        monkeypatch.setenv("GIT_ASKPASS", "/usr/bin/some-real-askpass-helper")

        spec = IntegrationSpec(resolver_agent="merge-resolver")
        resolver_env = escalation.resolver_env(spec)
        assert resolver_env["GIT_ASKPASS"] == "/bin/false"

        ctx = TaskContext(
            run_id="run1",
            task_id="t1",
            agent=AgentSpec(executor="claude_cli"),
            instruction_path=str(tmp_path / "instr.md"),
            input_paths=[],
            output_paths=[],
            repo_paths={},
            timeout_seconds=60,
            output_dir=str(tmp_path / "out"),
            env=resolver_env,
        )
        (tmp_path / "instr.md").write_text("# resolve\n")

        captured: dict = {}
        with patch("subprocess.Popen", new=_popen_factory(env_out=captured)):
            ClaudeCliExecutor().execute(ctx)

        env = captured["env"]
        assert env is not None, "ctx.env was non-empty -- Popen must have received env="
        # Pass-through (ADR-0005): the planted secret reached the dispatch unmodified.
        assert env["AWS_SECRET_ACCESS_KEY"] == "planted-fake-secret-value"
        # Override (S-2): GIT_ASKPASS is the resolver overlay's value, not the parent's.
        assert env["GIT_ASKPASS"] == "/bin/false"
