"""Import-boundary skeleton for ``agent_orchestrator.auth`` (HLD section 11.1, rules R1/R4/R5).

An AST walk over every ``auth/**.py`` compares each module's layer (the ``LAYERS`` table, which
already lists modules that later tasks create) against what it imports. The checker functions
take the table and package root as arguments so they can be proven against synthetic modules.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
import textwrap
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import NamedTuple

import pytest

import agent_orchestrator.auth as auth_pkg

PACKAGE = "agent_orchestrator.auth"
ROOT_PACKAGE = "agent_orchestrator"
AUTH_ROOT = Path(auth_pkg.__file__ or "").parent
REPO_ROOT = Path(__file__).resolve().parents[2]


class Layer(NamedTuple):
    level: int
    group: str | None = None  # L4 only: "http" and "cli" never import each other


# Every module of HLD section 11.1, including modules later tasks create (v2.1: also
# http/routes_second_factor.py and http/hub_routes.py). A new auth/**.py file must be added here.
LAYERS: dict[str, Layer] = {
    "__init__.py": Layer(4, "pkg"),
    # L0
    "constants.py": Layer(0),
    "errors.py": Layer(0),
    "seams.py": Layer(0),
    "model.py": Layer(0),
    "principal.py": Layer(0),
    # L1
    "passwords.py": Layer(1),
    "totp.py": Layer(1),
    "recovery.py": Layer(1),
    "policy.py": Layer(1),
    "paths.py": Layer(1),
    # L2
    "store.py": Layer(2),
    "lockouts.py": Layer(2),
    "audit.py": Layer(2),
    "scrub.py": Layer(2),
    "settings.py": Layer(2),
    # L3
    "sessions.py": Layer(3),
    "throttle.py": Layer(3),
    "guard.py": Layer(3),
    "provider.py": Layer(3),
    "local_provider.py": Layer(3),
    "totp_service.py": Layer(3),
    "runtime.py": Layer(3),
    "launch.py": Layer(3),
    # L4 http
    "http/__init__.py": Layer(4, "http"),
    "http/origin.py": Layer(4, "http"),
    "http/responses.py": Layer(4, "http"),
    "http/middleware.py": Layer(4, "http"),
    "http/routes.py": Layer(4, "http"),
    "http/routes_second_factor.py": Layer(4, "http"),
    "http/hub_routes.py": Layer(4, "http"),
    "http/hub_page.py": Layer(4, "http"),
    # L4 cli
    "cli.py": Layer(4, "cli"),
}

# Shared modules any auth module may import (R4).
SHARED_MODULES = frozenset(
    f"{ROOT_PACKAGE}.{name}" for name in ("errors", "fsutil", "xdg", "project_config")
)
UI_SECURITY_MODULE = f"{ROOT_PACKAGE}.ui.security"
UI_SECURITY_IMPORTER = "http/middleware.py"
# R1: the only modules that may import the web framework.
FRAMEWORK_IMPORTERS = frozenset(
    {
        "http/middleware.py",
        "http/routes.py",
        "http/routes_second_factor.py",
        "http/hub_routes.py",
    }
)
FRAMEWORK_TOPLEVELS = frozenset({"fastapi", "starlette"})
BLOCKED_IN_SUBPROCESS = ("fastapi", "starlette", "uvicorn")
HEAVY_TOPLEVELS = ("pydantic", "yaml", *BLOCKED_IN_SUBPROCESS)
# R5: named keys that only constants.py may spell.
R5_LITERALS = frozenset({"ao_auth", "auth_session", "auth_enabled", "auth_proof_ok"})
R5_OWNER = "constants.py"
# Packages whose `from <pkg> import name` may name a submodule.
PACKAGE_BASES = frozenset({ROOT_PACKAGE, f"{ROOT_PACKAGE}.ui", PACKAGE, f"{PACKAGE}.http"})


# ---------------------------------------------------------------------------
# Checkers (pure functions over a layers table; unit-tested on synthetic modules below)
# ---------------------------------------------------------------------------


def _module_key(target: str, layers: Mapping[str, Layer]) -> str | None:
    """Map a dotted ``agent_orchestrator.auth...`` target to its LAYERS key, if known."""
    if target == PACKAGE:
        return "__init__.py"
    rel = target[len(PACKAGE) + 1 :].replace(".", "/")
    for key in (f"{rel}.py", f"{rel}/__init__.py"):
        if key in layers:
            return key
    return None


def _imports(rel: str, tree: ast.AST) -> Iterator[tuple[int, str, bool]]:
    """Yield ``(lineno, absolute dotted target, is_name_candidate)`` for every import.

    ``from pkg import name`` yields ``pkg`` and, for the known package bases, ``pkg.name`` as a
    candidate submodule (``is_name_candidate=True``; it may just be an attribute).
    """
    package_parts = [*PACKAGE.split("."), *rel.split("/")[:-1]]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name, False
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                anchor = package_parts[: len(package_parts) - (node.level - 1)]
                base = ".".join([*anchor, *([node.module] if node.module else [])])
            else:
                base = node.module or ""
            yield node.lineno, base, False
            if base in PACKAGE_BASES:
                for alias in node.names:
                    if alias.name != "*":
                        yield node.lineno, f"{base}.{alias.name}", True


def check_module(rel: str, tree: ast.AST, layers: Mapping[str, Layer]) -> list[str]:
    """Violations of R1, R2, R4 and the ``ui.security`` exception for one module."""
    own = layers[rel]
    problems: list[str] = []

    def bad(lineno: int, message: str) -> None:
        problems.append(f"{rel}:{lineno}: {message}")

    for lineno, target, is_candidate in _imports(rel, tree):
        top = target.split(".")[0]
        if top in FRAMEWORK_TOPLEVELS:
            if rel not in FRAMEWORK_IMPORTERS:
                bad(lineno, f"R1: imports {target!r}; only {sorted(FRAMEWORK_IMPORTERS)} may")
            continue
        if top != ROOT_PACKAGE:
            continue
        if target in (ROOT_PACKAGE, f"{ROOT_PACKAGE}.ui", PACKAGE):
            continue  # bare package roots; their submodules are checked via name candidates
        if target.startswith(PACKAGE + "."):
            key = _module_key(target, layers)
            if key is None:
                if not is_candidate:  # a candidate that is no known module is just an attribute
                    bad(lineno, f"imports unknown auth module {target!r}; add it to LAYERS")
                continue
            other = layers[key]
            if other.level > own.level:
                bad(lineno, f"R4: L{own.level} module imports L{other.level} {target!r}")
            elif {own.group, other.group} == {"http", "cli"}:
                bad(lineno, f"R4: http and cli never import each other ({target!r})")
            continue
        if target in SHARED_MODULES or any(target.startswith(m + ".") for m in SHARED_MODULES):
            continue
        if target == UI_SECURITY_MODULE or target.startswith(UI_SECURITY_MODULE + "."):
            if rel != UI_SECURITY_IMPORTER:
                bad(lineno, f"R2: only {UI_SECURITY_IMPORTER} may import {target!r}")
            continue
        # Includes `from agent_orchestrator import x` / `from agent_orchestrator.ui import x`.
        bad(lineno, f"R2: imports {target!r}; only {sorted(SHARED_MODULES)} are allowed")
    return problems


def check_empty_http_init(rel: str, tree: ast.AST) -> list[str]:
    """``http/__init__.py`` stays import-free, so a pure http module never pulls fastapi in."""
    if rel != "http/__init__.py":
        return []
    return [
        f"{rel}:{node.lineno}: R1: http/__init__.py must not import anything"
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
    ]


def check_named_keys(rel: str, tree: ast.AST) -> list[str]:
    """R5: only constants.py spells the app-state / scope-state key literals."""
    if rel == R5_OWNER:
        return []
    return [
        f"{rel}:{node.lineno}: R5: string literal {node.value!r} belongs in constants.py"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value in R5_LITERALS
    ]


def check_tree(root: Path, layers: Mapping[str, Layer]) -> list[str]:
    """Run every static check over every ``*.py`` under ``root``."""
    problems: list[str] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel not in layers:
            problems.append(f"{rel}: not in LAYERS; add it to LAYERS")
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        problems += check_module(rel, tree, layers)
        problems += check_empty_http_init(rel, tree)
        problems += check_named_keys(rel, tree)
    return problems


def existing_non_http_modules(root: Path) -> list[str]:
    """Dotted names of every existing auth module outside ``http/`` (including the package)."""
    names = [PACKAGE]
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if rel.parts[0] == "http" or path.name == "__init__.py":
            continue
        names.append(".".join([PACKAGE, *rel.with_suffix("").parts]))
    return names


# ---------------------------------------------------------------------------
# The real tree
# ---------------------------------------------------------------------------


def test_layers_table_covers_hld_modules() -> None:
    expected = {
        *("constants", "errors", "seams", "model", "principal"),
        *("passwords", "totp", "recovery", "policy", "paths"),
        *("store", "lockouts", "audit", "scrub", "settings"),
        *("sessions", "throttle", "guard", "provider", "local_provider"),
        *("totp_service", "runtime", "launch", "cli"),
    }
    expected_http = {
        *("origin", "responses", "middleware", "routes"),
        *("routes_second_factor", "hub_routes", "hub_page"),
    }
    keys = set(LAYERS)
    assert {f"{m}.py" for m in expected} <= keys
    assert {f"http/{m}.py" for m in expected_http} <= keys
    assert {"__init__.py", "http/__init__.py"} <= keys
    for key, layer in LAYERS.items():
        if key.startswith("http/"):
            assert layer == Layer(4, "http"), key


def test_real_tree_respects_layers_and_framework_boundary() -> None:
    problems = check_tree(AUTH_ROOT, LAYERS)
    assert problems == [], "\n".join(problems)


def test_real_tree_has_python_modules() -> None:
    """Guards against the walk silently scanning nothing."""
    rels = {p.relative_to(AUTH_ROOT).as_posix() for p in AUTH_ROOT.rglob("*.py")}
    assert {"constants.py", "errors.py", "seams.py", "model.py", "__init__.py"} <= rels


# ---------------------------------------------------------------------------
# Subprocess checks: blocked frameworks and heavy-import absence (R1, AC-9)
# ---------------------------------------------------------------------------


def _run_python(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_non_http_modules_import_with_frameworks_blocked() -> None:
    modules = existing_non_http_modules(AUTH_ROOT)
    code = f"""
        import importlib, sys
        for name in {BLOCKED_IN_SUBPROCESS!r}:
            sys.modules[name] = None  # any `import <name>` now raises ImportError
        for module in {modules!r}:
            importlib.import_module(module)
    """
    proc = _run_python(code)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_blocked_framework_import_is_detected_by_the_subprocess_harness() -> None:
    """Proof the blocking works: a module that imports fastapi fails under the same harness."""
    code = f"""
        import sys
        for name in {BLOCKED_IN_SUBPROCESS!r}:
            sys.modules[name] = None
        try:
            import fastapi
        except ImportError:
            sys.exit(0)
        sys.exit(1)
    """
    assert _run_python(code).returncode == 0


def test_import_auth_package_pulls_no_heavy_dependency() -> None:
    # Only the L0 vocabulary is promised to be dependency-free; later layers may use pydantic/yaml.
    l0 = [PACKAGE, *(f"{PACKAGE}.{name}" for name in ("constants", "errors", "seams", "model"))]
    code = f"""
        import importlib, sys
        for module in {l0!r}:
            importlib.import_module(module)
        leaked = [n for n in {HEAVY_TOPLEVELS!r} if n in sys.modules]
        sys.exit(1 if leaked else 0)
    """
    proc = _run_python(code)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_import_auth_package_alone_leaves_heavy_modules_absent() -> None:
    code = f"""
        import sys
        import agent_orchestrator.auth
        leaked = [n for n in {HEAVY_TOPLEVELS!r} if n in sys.modules]
        print(leaked)
        sys.exit(1 if leaked else 0)
    """
    proc = _run_python(code)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_lazy_getattr_rejects_unknown_names() -> None:
    with pytest.raises(AttributeError):
        _ = auth_pkg.DoesNotExist


# ---------------------------------------------------------------------------
# The checkers catch violations (synthetic modules in tmp_path)
# ---------------------------------------------------------------------------

SYNTHETIC_LAYERS: dict[str, Layer] = {
    "__init__.py": Layer(4, "pkg"),
    "constants.py": Layer(0),
    "model.py": Layer(0),
    "store.py": Layer(2),
    "guard.py": Layer(3),
    "http/__init__.py": Layer(4, "http"),
    "http/origin.py": Layer(4, "http"),
    "http/routes.py": Layer(4, "http"),
    "http/middleware.py": Layer(4, "http"),
    "cli.py": Layer(4, "cli"),
}


def _write_tree(tmp_path: Path, files: Mapping[str, str]) -> Path:
    # A fresh directory per call, so tests can check several trees in one test function.
    root = Path(tempfile.mkdtemp(dir=tmp_path))
    for rel, source in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source))
    return root


def _problems(tmp_path: Path, files: Mapping[str, str]) -> list[str]:
    return check_tree(_write_tree(tmp_path, files), SYNTHETIC_LAYERS)


def test_checker_accepts_clean_modules(tmp_path: Path) -> None:
    files = {
        "constants.py": "X = 1\n",
        "model.py": "from .constants import X\nfrom agent_orchestrator.errors import ConfigError\n",
        "store.py": "from . import model\nfrom .model import X\nimport agent_orchestrator.xdg\n",
        "guard.py": "from .store import X\nfrom agent_orchestrator.auth.model import X as Y\n",
        "http/routes.py": (
            "from fastapi import APIRouter\nfrom .origin import f\nfrom ..guard import g\n"
        ),
        "http/origin.py": "import re\n",
    }
    assert _problems(tmp_path, files) == []


def test_checker_flags_l0_importing_l2(tmp_path: Path) -> None:
    problems = _problems(tmp_path, {"model.py": "from .store import Store\n"})
    assert len(problems) == 1 and "R4" in problems[0] and "L0" in problems[0]


def test_checker_flags_upward_import_via_absolute_and_from_package(tmp_path: Path) -> None:
    problems = _problems(
        tmp_path,
        {
            "constants.py": "import agent_orchestrator.auth.store\n",
            "model.py": "from agent_orchestrator.auth import guard\n",
        },
    )
    assert len(problems) == 2 and all("R4" in p for p in problems)


def test_checker_flags_fastapi_in_non_http_module(tmp_path: Path) -> None:
    problems = _problems(tmp_path, {"store.py": "import fastapi\n"})
    assert len(problems) == 1 and "R1" in problems[0]
    problems = _problems(
        tmp_path, {"guard.py": "def f():\n    from starlette.requests import Request\n"}
    )
    assert len(problems) == 1 and "R1" in problems[0]


def test_checker_flags_framework_in_pure_http_module(tmp_path: Path) -> None:
    problems = _problems(tmp_path, {"http/origin.py": "from fastapi import Request\n"})
    assert len(problems) == 1 and "R1" in problems[0]


def test_checker_allows_framework_in_listed_http_modules(tmp_path: Path) -> None:
    files = {
        "http/middleware.py": "import starlette\nfrom agent_orchestrator.ui.security import M\n",
        "http/routes.py": "import fastapi\n",
    }
    assert _problems(tmp_path, files) == []


def test_checker_flags_http_and_cli_importing_each_other(tmp_path: Path) -> None:
    problems = _problems(
        tmp_path,
        {"http/routes.py": "from ..cli import app\n", "cli.py": "from .http import routes\n"},
    )
    assert all("never import each other" in p for p in problems)
    assert {p.split(":")[0] for p in problems} == {"http/routes.py", "cli.py"}


def test_checker_flags_ui_security_outside_middleware(tmp_path: Path) -> None:
    problems = _problems(
        tmp_path, {"http/routes.py": "from agent_orchestrator.ui import security\n"}
    )
    assert len(problems) == 1 and "R2" in problems[0]
    problems = _problems(tmp_path, {"store.py": "from agent_orchestrator.ui.security import M\n"})
    assert len(problems) == 1 and "R2" in problems[0]


def test_checker_flags_other_agent_orchestrator_imports(tmp_path: Path) -> None:
    for source in (
        "from agent_orchestrator.engine import Orchestrator\n",
        "import agent_orchestrator.ui.app\n",
        "from agent_orchestrator import service\n",
    ):
        problems = _problems(tmp_path, {"cli.py": source})
        assert len(problems) == 1 and "R2" in problems[0], source


def test_checker_ignores_third_party_and_stdlib(tmp_path: Path) -> None:
    assert _problems(tmp_path, {"store.py": "import json, pydantic, yaml\nimport typer\n"}) == []


def test_checker_flags_module_missing_from_layers(tmp_path: Path) -> None:
    problems = _problems(tmp_path, {"mystery.py": "X = 1\n"})
    assert problems == ["mystery.py: not in LAYERS; add it to LAYERS"]


def test_checker_flags_unknown_auth_import(tmp_path: Path) -> None:
    problems = _problems(tmp_path, {"model.py": "from .mystery import X\n"})
    assert len(problems) == 1 and "add it to LAYERS" in problems[0]


def test_checker_flags_imports_in_http_init(tmp_path: Path) -> None:
    problems = _problems(tmp_path, {"http/__init__.py": "from .origin import f\n"})
    assert len(problems) == 1 and "http/__init__.py must not import" in problems[0]


def test_checker_flags_r5_literals_outside_constants(tmp_path: Path) -> None:
    for literal in sorted(R5_LITERALS):
        problems = _problems(tmp_path, {"model.py": f"KEY = {literal!r}\n"})
        assert len(problems) == 1 and "R5" in problems[0], literal
    assert _problems(tmp_path, {"constants.py": "KEY = 'ao_auth'\n"}) == []
    # A longer string merely containing a key name is not a literal use of the key.
    assert _problems(tmp_path, {"model.py": "DOC = 'the ao_auth runtime'\n"}) == []


# ---------------------------------------------------------------------------
# `ao auth` (T-j9dfsw, AC-27 CLI part): works without the web framework, imports lazily
# ---------------------------------------------------------------------------

LAZY_AUTH_MODULES = ("store", "passwords", "totp", "lockouts", "audit", "settings")


def test_ao_auth_runs_with_frameworks_blocked(tmp_path: Path) -> None:
    code = f"""
        import sys
        for name in {BLOCKED_IN_SUBPROCESS!r}:
            sys.modules[name] = None  # any `import <name>` now raises ImportError
        from typer.testing import CliRunner
        from agent_orchestrator.cli import app
        runner = CliRunner()
        auth_dir = {str(tmp_path / "auth")!r}
        for args in (["auth", "--help"], ["auth", "status", "--auth-dir", auth_dir]):
            result = runner.invoke(app, args)
            assert result.exit_code == 0, (args, result.output)
        assert "store:" in result.stdout, result.output
    """
    proc = _run_python(code)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_importing_the_root_cli_does_not_load_heavy_auth_modules() -> None:
    names = [f"{PACKAGE}.{name}" for name in LAZY_AUTH_MODULES]
    code = f"""
        import sys
        import agent_orchestrator.cli
        loaded = [name for name in {names!r} if name in sys.modules]
        print(loaded)
        sys.exit(1 if loaded else 0)
    """
    proc = _run_python(code)
    assert proc.returncode == 0, proc.stdout + proc.stderr
