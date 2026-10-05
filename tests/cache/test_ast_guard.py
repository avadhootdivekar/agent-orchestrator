"""U-AST (AC-11): static guard over `src/agent_orchestrator/cache/` (M-9, D31).

Fails on: `pickle` / `marshal` / `shelve` imports, `eval` / `exec` calls, any `shell=True`, and any
bare `open(` or `os.open(` outside `safeio.py` (every cache-side open goes through safeio's
O_NOFOLLOW / O_NONBLOCK helpers). A negative self-test proves the guard really detects each one.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import agent_orchestrator.cache as cache_pkg

CACHE_DIR = Path(cache_pkg.__file__).resolve().parent
SAFEIO_NAME = "safeio.py"
BANNED_MODULES = frozenset({"pickle", "marshal", "shelve"})
BANNED_CALLS = frozenset({"eval", "exec"})

PICKLE, EVAL_EXEC, SHELL_TRUE, BARE_OPEN, OS_OPEN = (
    "banned-import",
    "eval-exec",
    "shell=True",
    "bare-open",
    "os.open",
)


@pytest.fixture(autouse=True)
def _cache_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AO_CACHE", raising=False)


def find_violations(source: str, filename: str) -> list[tuple[str, int]]:
    """(rule, line) for every violation in *source*; `safeio.py` may use open / os.open."""
    is_safeio = Path(filename).name == SAFEIO_NAME
    found: list[tuple[str, int]] = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Import):
            found += [
                (PICKLE, node.lineno) for a in node.names if a.name.split(".")[0] in BANNED_MODULES
            ]
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in BANNED_MODULES:
                found.append((PICKLE, node.lineno))
            elif root == "os" and not is_safeio and any(a.name == "open" for a in node.names):
                found.append((OS_OPEN, node.lineno))  # `from os import open` evasion
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                if func.id in BANNED_CALLS:
                    found.append((EVAL_EXEC, node.lineno))
                elif func.id == "open" and not is_safeio:
                    found.append((BARE_OPEN, node.lineno))
            elif (
                isinstance(func, ast.Attribute)
                and func.attr == "open"
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
                and not is_safeio
            ):
                found.append((OS_OPEN, node.lineno))
            for kw in node.keywords:
                if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value:
                    found.append((SHELL_TRUE, node.lineno))
    return sorted(found, key=lambda v: (v[1], v[0]))


def test_guard_scans_a_non_empty_cache_package() -> None:
    names = {p.name for p in CACHE_DIR.rglob("*.py")}
    assert {"__init__.py", "constants.py", "safeio.py", "types.py"} <= names


def test_no_cache_module_violates_the_guard() -> None:
    problems: list[str] = []
    for path in sorted(CACHE_DIR.rglob("*.py")):
        for rule, line in find_violations(path.read_text(), str(path)):
            problems.append(f"{path.relative_to(CACHE_DIR)}:{line}: {rule}")
    assert not problems, "\n".join(problems)


def test_safeio_is_the_only_module_that_opens_files() -> None:
    uses_os_open = [
        p.name
        for p in sorted(CACHE_DIR.rglob("*.py"))
        if "os.open(" in p.read_text() and p.name != SAFEIO_NAME
    ]
    # Text-level cross-check of the AST rule (docstrings may mention it; code must not call it).
    for name in uses_os_open:
        path = next(CACHE_DIR.rglob(name))
        assert not [v for v in find_violations(path.read_text(), str(path)) if v[0] == OS_OPEN]


# ---------------------------------------------------------------- negative self-test
SYNTHETIC = {
    PICKLE: [
        "import pickle",
        "import marshal as m",
        "from shelve import open as op",
        "import os.path\nimport pickle.x",
    ],
    EVAL_EXEC: ["eval('1')", "exec('x = 1')"],
    SHELL_TRUE: ["import subprocess\nsubprocess.run('ls', shell=True)", "f(shell=True)"],
    BARE_OPEN: ["open('f')", "with open('f', 'rb') as fh:\n    pass"],
    OS_OPEN: ["import os\nos.open('f', 0)", "from os import open as o"],
}


@pytest.mark.parametrize(
    ("rule", "snippet"), [(r, s) for r, snippets in SYNTHETIC.items() for s in snippets]
)
def test_guard_detects_each_violation(rule: str, snippet: str) -> None:
    rules = {r for r, _ in find_violations(snippet, "src/agent_orchestrator/cache/synthetic.py")}
    assert rule in rules


def test_guard_reports_every_violation_in_one_module_with_lines() -> None:
    source = "\n".join(
        [
            "import pickle",  # 1
            "import os",  # 2
            "eval('1')",  # 3
            "exec('2')",  # 4
            "open('f')",  # 5
            "os.open('f', 0)",  # 6
            "import subprocess",  # 7
            "subprocess.run('x', shell=True)",  # 8
        ]
    )
    assert find_violations(source, "synthetic.py") == [
        (PICKLE, 1),
        (EVAL_EXEC, 3),
        (EVAL_EXEC, 4),
        (BARE_OPEN, 5),
        (OS_OPEN, 6),
        (SHELL_TRUE, 8),
    ]


def test_safeio_exemption_covers_open_but_nothing_else() -> None:
    source = "import os\nos.open('f', 0)\nopen('g')\nimport pickle\neval('1')\nf(shell=True)\n"
    rules = {r for r, _ in find_violations(source, "src/agent_orchestrator/cache/safeio.py")}
    assert rules == {PICKLE, EVAL_EXEC, SHELL_TRUE}


def test_guard_ignores_safe_lookalikes() -> None:
    source = "\n".join(
        [
            "import os, subprocess",
            "os.path.exists('x')",
            "subprocess.run(['ls'], shell=False)",
            "obj.open('f')",  # a method on another object is not os.open / builtins open
            "x = 'open(\"f\") pickle eval(1)'",
            "def evaluate(): ...",
        ]
    )
    assert find_violations(source, "synthetic.py") == []
