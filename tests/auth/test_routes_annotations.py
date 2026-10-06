"""T-rpKCjP: rule R1a, no TYPE_CHECKING-only names in route-handler annotations (HLD 11.18, 28.9).

With ``from __future__ import annotations`` FastAPI resolves a handler's annotations from the
module globals. A name bound only under ``if TYPE_CHECKING:`` is missing there, so FastAPI
silently treats the parameter as a query field and every request answers 422. The AST walk
below keeps that trap out of ``http/routes*.py`` and ``http/hub_routes.py``; a synthetic module
with the trap proves the check can fail, and a live FastAPI app shows what the trap costs.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import agent_orchestrator.auth.http as http_pkg

HTTP_DIR = Path(http_pkg.__file__ or "").parent
CHECKED_GLOBS = ("routes*.py", "hub_routes.py")
TYPE_CHECKING_NAME = "TYPE_CHECKING"


def _is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == TYPE_CHECKING_NAME
    return isinstance(test, ast.Attribute) and test.attr == TYPE_CHECKING_NAME


def _bound_names(statements: list[ast.stmt]) -> set[str]:
    """Names a module-level statement list binds at import time (no function or class bodies)."""
    names: set[str] = set()
    for node in statements:
        if isinstance(node, ast.Import | ast.ImportFrom):
            names.update((a.asname or a.name).split(".")[0] for a in node.names)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.If) and not _is_type_checking(node.test):
            names |= _bound_names(node.body) | _bound_names(node.orelse)
        elif isinstance(node, ast.Try):
            names |= _bound_names(node.body)
            for handler in node.handlers:
                names |= _bound_names(handler.body)
            names |= _bound_names(node.orelse) | _bound_names(node.finalbody)
    return names


def _type_checking_only_names(tree: ast.Module) -> set[str]:
    guarded: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            guarded |= _bound_names(node.body)
    return guarded - _bound_names(tree.body)


def _annotation_names(annotation: ast.expr) -> set[str]:
    """Every name an annotation uses, including inside a string annotation."""
    names: set[str] = set()
    for node in ast.walk(annotation):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                names |= _annotation_names(ast.parse(node.value, mode="eval").body)
            except SyntaxError:
                continue
    return names


def find_violations(source: str) -> list[str]:
    """``"<function>(<parameter>): <name>"`` for each handler parameter annotated with a name that
    exists only under ``if TYPE_CHECKING:``."""
    tree = ast.parse(source)
    guarded = _type_checking_only_names(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        arguments = node.args
        every = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
        every += [a for a in (arguments.vararg, arguments.kwarg) if a is not None]
        for arg in every:
            if arg.annotation is None:
                continue
            for name in sorted(_annotation_names(arg.annotation) & guarded):
                found.append(f"{node.name}({arg.arg}): {name}")
    return found


def checked_files() -> list[Path]:
    return sorted({path for pattern in CHECKED_GLOBS for path in HTTP_DIR.glob(pattern)})


def test_the_checked_file_set_includes_the_route_modules() -> None:
    names = {path.name for path in checked_files()}
    assert {"routes.py", "routes_second_factor.py"} <= names  # hub_routes.py joins when it lands


@pytest.mark.parametrize("path", checked_files(), ids=lambda p: p.name)
def test_no_handler_annotation_depends_on_a_type_checking_only_name(path: Path) -> None:
    assert find_violations(path.read_text(encoding="utf-8")) == []


# --- negative controls: the check can fail ---------------------------------------------------

TRAP = textwrap.dedent(
    """
    from __future__ import annotations
    from typing import TYPE_CHECKING

    from fastapi import FastAPI

    if TYPE_CHECKING:
        from starlette.requests import Request
        from starlette.responses import Response

    async def handler(request: Request) -> Response: ...
    async def other(*, flag: bool, later: "Request | None" = None) -> None: ...
    """
)
SAFE = textwrap.dedent(
    """
    from __future__ import annotations
    from typing import TYPE_CHECKING

    from starlette.requests import Request

    if TYPE_CHECKING:
        from starlette.requests import Request as _Alias  # never used in an annotation
        from agent_orchestrator.auth.runtime import AuthRuntime

    async def handler(request: Request) -> None: ...
    """
)
STRICT = textwrap.dedent(
    """
    from __future__ import annotations
    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from starlette.responses import Response

    def helper(value: Response | None) -> None: ...
    """
)


def test_a_type_checking_only_annotation_is_found() -> None:
    assert find_violations(TRAP) == [
        "handler(request): Request",
        "other(later): Request",
    ]


def test_a_name_bound_at_module_scope_is_fine() -> None:
    assert find_violations(SAFE) == []


def test_the_rule_applies_to_every_function_of_a_route_module() -> None:
    # Deliberately per parameter, not per decorated handler: a helper may become a handler.
    assert find_violations(STRICT) == ["helper(value): Response"]


def test_the_trap_really_breaks_a_fastapi_handler(tmp_path: Path) -> None:
    """What the rule prevents: a TYPE_CHECKING-only ``Request`` makes FastAPI answer 422."""
    module = tmp_path / "trap_app.py"
    module.write_text(
        textwrap.dedent(
            """
            from __future__ import annotations
            from typing import TYPE_CHECKING
            from fastapi import FastAPI

            if TYPE_CHECKING:
                from fastapi import Request

            app = FastAPI()

            @app.get("/probe")
            async def probe(request: Request) -> dict[str, str]:
                return {"ok": "yes"}
            """
        ),
        encoding="utf-8",
    )
    namespace: dict[str, object] = {"__name__": "trap_app"}
    exec(compile(module.read_text(encoding="utf-8"), str(module), "exec"), namespace)  # noqa: S102
    app = namespace["app"]
    assert TestClient(app).get("/probe").status_code == 422  # type: ignore[arg-type]
