"""Per-language project skeletons + run-contract prose (HLD §4.1, bench-probe-dsl.md §1).

The agent receives ``run.sh``, one manifest limited to the dependency allow-list and a stub entry
point.  ``run.sh`` is the *only* interface the grader uses.

Dependency source: the suite ``stack`` block, ``stack.<language>.deps`` (third-party packages
only -- exactly what the sandbox installs into the language image; an entry may be pinned, e.g.
``montydb==2.5.3``).  ``ao-bench gen`` copies it into each task's ``source.deps``; the provider
turns it into the full allow-list (:func:`allow_from_deps`: the language built-ins in
:data:`BUILTIN_ALLOW` first, then the deps).  The allow-list is what SPEC.md states and what the
manifest is limited to (built-ins are documented, never written into a manifest).  Absent =>
:data:`DEFAULT_DEPS`.

TypeScript note: the task language is called ``typescript`` but ``run.sh`` executes ``node app.js``
(Node 22, ES modules, built-ins only).  The agent may write plain JavaScript or TypeScript that it
compiles to ``app.js`` (``tsconfig.json`` is provided for that); no compiler or runner such as
``tsx`` is guaranteed to exist in the grading image.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

DEFAULT_ALLOW: dict[str, tuple[str, ...]] = {
    "python": ("sqlite3", "montydb"),
    "typescript": ("node:sqlite",),
    "go": ("modernc.org/sqlite",),
}  # == BUILTIN_ALLOW + DEFAULT_DEPS per language

# Language built-ins the specs allow (embedded SQL), documented but never installed.
BUILTIN_ALLOW: dict[str, tuple[str, ...]] = {
    "python": ("sqlite3",),
    "typescript": ("node:sqlite",),
    "go": (),
}
# Default third-party deps per language == the default `stack.<language>.deps`.
DEFAULT_DEPS: dict[str, tuple[str, ...]] = {
    "python": ("montydb",),
    "typescript": (),
    "go": ("modernc.org/sqlite",),
}

PYTHON_BUILTIN_MODULES = frozenset({"sqlite3"})
PORT_ENV = "PORT"
HOST_ENV = "HOST"
RUN_SH_MODE = 0o755

_RUN_SH: dict[str, str] = {
    "python": '#!/bin/sh\ncd "$(dirname "$0")" || exit 1\nexec python3 -m app\n',
    "typescript": '#!/bin/sh\ncd "$(dirname "$0")" || exit 1\nexec node app.js\n',
    "go": '#!/bin/sh\ncd "$(dirname "$0")" || exit 1\nexec go run .\n',
}

_RUNTIME: dict[str, str] = {
    "python": "Python 3.12",
    "typescript": "Node.js 22 (ES modules; plain JavaScript or TypeScript compiled to `app.js`)",
    "go": "Go 1.22 or newer (module `app`, `package main` in the repository root)",
}


def allow_from_deps(language: str, deps: Sequence[str]) -> list[str]:
    """Full allow-list for *language* given its third-party ``deps`` (built-ins first)."""
    if language not in BUILTIN_ALLOW:
        raise ValueError(f"unknown language {language!r}; known: {sorted(BUILTIN_ALLOW)}")
    return [*BUILTIN_ALLOW[language], *deps]


def resolve_allow(language: str, allow: Sequence[str] | None) -> list[str]:
    if language not in DEFAULT_ALLOW:
        raise ValueError(f"unknown language {language!r}; known: {sorted(DEFAULT_ALLOW)}")
    return list(DEFAULT_ALLOW[language] if allow is None else allow)


def _is_builtin(language: str, entry: str) -> bool:
    if language == "python":
        return entry in PYTHON_BUILTIN_MODULES
    if language == "typescript":
        return entry.startswith("node:")
    return False


def third_party(language: str, allow: Sequence[str]) -> list[str]:
    return [a for a in allow if not _is_builtin(language, a)]


def allow_list_text(language: str, allow: Sequence[str]) -> str:
    """Prose for SPEC.md: what the artefact may depend on."""
    builtin = [a for a in allow if _is_builtin(language, a)]
    external = third_party(language, allow)
    if language == "python":
        base = "the Python standard library" + (" (including `sqlite3`)" if builtin else "")
    elif language == "typescript":
        names = ", ".join(f"`{b}`" for b in builtin)
        base = "the Node.js built-in modules" + (f" (including {names})" if builtin else "")
    else:
        base = "the Go standard library"
    bullets = "\n".join([f"- {base}", *(f"- `{dep}`" for dep in external)])
    return (
        f"Language/runtime: {_RUNTIME[language]}.\n\n"
        "The service runs in an environment with no network access and only the following "
        f"preinstalled:\n\n{bullets}\n\n"
        "Do not use any other third-party package, and do not depend on an external service "
        "(database server, cache, message broker). Persistence beyond process memory is optional."
    )


def run_contract_text(contract: str, language: str) -> str:
    """Prose for SPEC.md: how the artefact is started and what it must serve."""
    start = {
        "python": "`run.sh` runs `python3 -m app` from the repository root "
        "(entry: `app/__main__.py`).",
        "typescript": "`run.sh` runs `node app.js` from the repository root, so `app.js` must "
        "exist when you are done: write plain JavaScript there, or write TypeScript and compile "
        "it to `app.js` (a `tsconfig.json` is provided; no compiler is guaranteed to be "
        "available where the service is run).",
        "go": "`run.sh` runs `go run .` from the repository root (`package main`, module `app`).",
    }[language]
    if contract == "http":
        body = (
            f"{start}\n\n"
            f"- The service binds `${HOST_ENV}:${PORT_ENV}`: the address in the `{HOST_ENV}` "
            f"environment variable and the TCP port in the `{PORT_ENV}` environment variable "
            "(both are always set), and speaks HTTP/1.1.\n"
            '- `GET /healthz` returns `200` with body `{"status": "ok"}` as soon as the '
            "service is ready; it must be ready within 30 seconds of start.\n"
            "- Requests and responses with a body use `Content-Type: application/json`.\n"
            "- The process runs until it receives SIGTERM, then exits promptly."
        )
    elif contract == "jsonl":
        body = (
            f"{start}\n\n"
            "- The process reads one JSON request per line from standard input and writes "
            "exactly one JSON response per line to standard output, in request order, flushing "
            "after every response.\n"
            "- Standard output carries only responses; write logs to standard error.\n"
            '- `{"op": "ping"}` must be answered with `{"ok": true}` as soon as the process '
            "is ready.\n"
            "- The process runs until standard input is closed or it receives SIGTERM."
        )
    else:
        raise ValueError(f"unsupported contract {contract!r}")
    return body


def skeleton_files(
    contract: str, language: str, allow: Sequence[str], *, title: str
) -> dict[str, str]:
    """The files written to ``repo_dir`` (besides SPEC.md): run.sh, manifest, stub entry."""
    if language not in _RUN_SH:
        raise ValueError(f"unknown language {language!r}; known: {sorted(_RUN_SH)}")
    external = third_party(language, allow)
    stub_note = f"{title}: implement SPEC.md. Contract: see 'Run contract' in SPEC.md."
    if contract == "http":
        stub_note += f" Bind ${HOST_ENV}:${PORT_ENV}."
    run_sh = _RUN_SH[language]
    if contract == "http":
        shebang, rest = run_sh.split("\n", 1)
        note = f"# Contract: the service must bind ${HOST_ENV}:${PORT_ENV} (see SPEC.md)."
        run_sh = f"{shebang}\n{note}\n{rest}"
    files: dict[str, str] = {"run.sh": run_sh}
    if language == "python":
        deps = ", ".join(json.dumps(dep) for dep in external)
        files["pyproject.toml"] = (
            "[project]\n"
            'name = "app"\n'
            'version = "0.1.0"\n'
            'requires-python = ">=3.12"\n'
            "# Allow-listed dependencies only (see SPEC.md); the standard library is always "
            "available.\n"
            f"dependencies = [{deps}]\n"
        )
        files["app/__init__.py"] = ""
        files["app/__main__.py"] = (
            f'"""{stub_note}"""\n\n\n'
            "def main() -> None:\n"
            '    raise NotImplementedError("implement SPEC.md")\n\n\n'
            'if __name__ == "__main__":\n'
            "    main()\n"
        )
    elif language == "typescript":
        files["package.json"] = (
            json.dumps(
                {
                    "name": "app",
                    "version": "0.1.0",
                    "private": True,
                    "type": "module",
                    "engines": {"node": ">=22"},
                    "dependencies": {dep: "*" for dep in external},
                },
                indent=2,
            )
            + "\n"
        )
        files["tsconfig.json"] = (
            json.dumps(
                {
                    "compilerOptions": {
                        "target": "ES2023",
                        "module": "NodeNext",
                        "moduleResolution": "NodeNext",
                        "strict": True,
                        "rootDir": "src",
                        "outDir": ".",
                    },
                    "include": ["src"],
                },
                indent=2,
            )
            + "\n"
        )
        files["app.js"] = (
            f"// {stub_note}\n"
            "// run.sh executes `node app.js`: keep this file as plain JavaScript (ES module),\n"
            '// or compile TypeScript from src/ to it (tsconfig.json: outDir ".").\n'
            'throw new Error("implement SPEC.md");\n'
        )
    else:
        mod = "module app\n\ngo 1.22\n"
        if external:
            mod += "\n// Allow-listed third-party modules (add with `go get <module>`):\n"
            mod += "".join(f"//   {dep}\n" for dep in external)
        files["go.mod"] = mod
        files["main.go"] = (
            f'// {stub_note}\npackage main\n\nfunc main() {{\n\tpanic("implement SPEC.md")\n}}\n'
        )
    return files
