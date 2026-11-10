"""Exercism / Aider polyglot exercises as bench tasks (T-Pf4Mix).

Design: `docs-md/benchmark-task-portfolio.md`. Source: `Aider-AI/polyglot-benchmark` -- the hard
Exercism practice exercises in C++, Go, Java, JavaScript, Python and Rust, each with instructions, a
stub, the official tests and the track's example solution. Exercism tracks are MIT licensed; the
content is copyright Exercism and is fetched at a pinned commit (never vendored) through the same
shared clone cache the Tier-2 providers use.

Provider (`source.type = "exercism"`): extracts ONE exercise directory at the pinned commit. The
agent gets the stub + build files + a `README.md` rendered from `.docs/`; the official tests go to
`grading_dir/hidden/` and the example solution to `grading_dir/reference/` (so `FakeSubject
copy-reference` must score 1.0 and the untouched stub must not). `source.tests`: `visible`
(Exercism's own setting, the importer default) also copies the official tests into the repo;
`hidden` (the first protocol, `polyglot-mini`) does not -- which left some APIs unguessable (C++
stubs are empty namespaces, so names like `diamond::rows` exist only in the tests).

Grader (`grader.type = "exercism"`): copies the agent's repo (without build artefacts), restores the
hidden tests, enables every test (Rust `--include-ignored`, JS `xtest` -> `test`, C++
`EXERCISM_RUN_ALL_TESTS`), runs the track's test runner in a per-language Docker image with no
network, and scores `passed / total`; `solved` = every test passed. A build failure scores 0.

Contamination: public long before every current model's cutoff -- results are reported as a
separate "contaminated" tier; the grader's own enabled-everything copy of the tests and the
agentic setting (one autonomous attempt) are what keep it informative.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import random
import re
import shutil
import subprocess
import tarfile
import tempfile
import xml.etree.ElementTree as ET  # noqa: S405 -- parses our own test runner's output
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import typer

from .errors import GraderError, SubjectError
from .graders import Grader, GraderConfig, GraderContext, GradeResult
from .registries import register_grader, register_workspace_provider
from .sandbox import ImageSpec, SandboxUnavailable, docker_available, ensure_image
from .spec import BenchTask
from .tier2_common import HIDDEN_DIRNAME, REFERENCE_DIRNAME, _ensure_commit, _ensure_repo_cache
from .workspace import WorkspaceProvider

EXERCISM_PROVIDER_TYPE = "exercism"
EXERCISM_GRADER_TYPE = "exercism"
DEFAULT_REPO = "Aider-AI/polyglot-benchmark"
# Pinned 2024-12-22 commit of Aider-AI/polyglot-benchmark (the set Aider's leaderboard uses).
DEFAULT_COMMIT = "7e0611e77b54e2dea774cdc0aa00cf9f7ed6144f"
README_FILENAME = "README.md"
META_FILENAME = "exercism.json"  # provider -> grader contract in grading_dir
RESULTS_DIRNAME = ".ao-results"
# Never copied from the agent's repo into the grading run (host build artefacts / deps).
_SKIP_DIRS = frozenset({".git", "node_modules", "target", ".ao-build", "build", "__pycache__",
                        ".venv", ".pytest_cache", RESULTS_DIRNAME})  # fmt: skip
_PROVIDER = "exercism"
GRADE_TIMEOUT_SECONDS = 900
_DOCKER_LIMITS = ["--cpus", "2", "--memory", "2g", "--memory-swap", "2g", "--pids-limit", "512"]


# ---------------------------------------------------------------------------
# Per-language runner specs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LangSpec:
    name: str
    base_image: str
    dockerfile_body: str | None  # None = use `base_image` as-is
    # Shell command run in the exercise dir inside the container; `{exe}` = exercise slug.
    command: str
    parse: Callable[[Path], tuple[int, int]]  # results dir -> (passed, total)
    env: dict[str, str] = field(default_factory=dict)
    # How the agent can test on its own host (rendered into the task instruction).
    host_hint: str = ""
    # The same, when the official tests are visible (they are the tests to run).
    visible_hint: str = ""

    def image_spec(self) -> ImageSpec:
        if self.dockerfile_body is None:
            return ImageSpec(self.name, self.base_image, (), self.base_image, None)
        dockerfile = f"FROM {self.base_image}\n{self.dockerfile_body}"
        digest = hashlib.sha256(dockerfile.encode()).hexdigest()[:12]
        return ImageSpec(
            self.name, self.base_image, (), f"ao-bench-exercism-{self.name}-{digest}", dockerfile
        )


def _parse_junit(results: Path) -> tuple[int, int]:
    xml_path = results / "results.xml"
    if not xml_path.is_file():
        return 0, 0
    try:
        root = ET.parse(xml_path).getroot()  # noqa: S314 -- our own runner's output
    except ET.ParseError:
        return 0, 0
    total = passed = 0
    for case in root.iter("testcase"):
        total += 1
        if not any(child.tag in ("failure", "error", "skipped") for child in case):
            passed += 1
    return passed, total


def _parse_go(results: Path) -> tuple[int, int]:
    path = results / "results.json"
    if not path.is_file():
        return 0, 0
    final: dict[str, str] = {}
    for line in path.read_text(errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        name, action = ev.get("Test"), ev.get("Action")
        if name and action in ("pass", "fail", "skip"):
            final[f"{ev.get('Package', '')}::{name}"] = action
    # Count leaf tests only (a parent with subtests reports its own pass/fail too).
    leaves = [n for n in final if not any(o.startswith(n + "/") for o in final)]
    return sum(1 for n in leaves if final[n] == "pass"), len(leaves)


_RUST_RESULT = re.compile(r"test result: \w+\. (\d+) passed; (\d+) failed; (\d+) ignored")


def _parse_rust(results: Path) -> tuple[int, int]:
    path = results / "out.txt"
    if not path.is_file():
        return 0, 0
    passed = total = 0
    for m in _RUST_RESULT.finditer(path.read_text(errors="replace")):
        p, f, i = (int(x) for x in m.groups())
        passed += p
        total += p + f + i
    return passed, total


def _parse_jest(results: Path) -> tuple[int, int]:
    path = results / "results.json"
    if not path.is_file():
        return 0, 0
    try:
        data = json.loads(path.read_text(errors="replace"))
    except json.JSONDecodeError:
        return 0, 0
    if data.get("numRuntimeErrorTestSuites"):
        return 0, int(data.get("numTotalTests") or 0)
    return int(data.get("numPassedTests") or 0), int(data.get("numTotalTests") or 0)


_R = RESULTS_DIRNAME
LANGUAGES: dict[str, LangSpec] = {
    "python": LangSpec(
        "python",
        "python:3.12-slim",
        "RUN pip install --no-cache-dir pytest==8.3.3\n",
        f"python -m pytest -q -p no:cacheprovider --junitxml={_R}/results.xml . "
        f"> {_R}/out.txt 2>&1",
        _parse_junit,
        host_hint="Write your own tests in a new `test_*.py` file and run `python3 -m pytest`.",
        visible_hint="Run the tests with `python3 -m pytest`.",
    ),
    "go": LangSpec(
        "go",
        "golang:1.26-alpine",
        None,
        f"go test -json -count=1 ./... > {_R}/results.json 2> {_R}/out.txt",
        _parse_go,
        env={
            "GOFLAGS": "-mod=mod",
            "GOPROXY": "off",
            "GOCACHE": "/tmp/gocache",
            "GOPATH": "/tmp/go",
            "GOTOOLCHAIN": "local",
            "CGO_ENABLED": "0",
        },  # fmt: skip
        host_hint="Write your own tests in a new `*_test.go` file and run `go test ./...`.",
        visible_hint="Run the tests with `go test ./...`.",
    ),
    "rust": LangSpec(
        "rust",
        "rust:1.93-slim",
        None,
        f"cargo test --offline --no-fail-fast -- --include-ignored > {_R}/out.txt 2>&1",
        _parse_rust,
        env={"CARGO_HOME": "/tmp/cargo", "CARGO_TARGET_DIR": "/tmp/target"},
        host_hint="Write your own tests (e.g. in `tests/`) and run `cargo test`.",
        visible_hint="Run the tests with `cargo test -- --include-ignored`.",
    ),
    "cpp": LangSpec(
        "cpp",
        "gcc:13",
        "RUN apt-get update && apt-get install -y --no-install-recommends cmake "
        "&& rm -rf /var/lib/apt/lists/*\n",
        "mkdir -p .ao-build && cd .ao-build "
        f"&& cmake -DEXERCISM_RUN_ALL_TESTS=ON .. > ../{_R}/out.txt 2>&1 "
        f"&& make >> ../{_R}/out.txt 2>&1 "
        f"&& ./{{exe}} -r junit -o ../{_R}/results.xml >> ../{_R}/out.txt 2>&1",
        _parse_junit,
        host_hint=(
            "The CMakeLists.txt builds `<exercise>_test.cpp` against the bundled Catch main in "
            "`test/`: put your own Catch tests in that file and run "
            "`mkdir -p build && cd build && cmake .. && make`."
        ),
        visible_hint=(
            "Build and run the tests with `mkdir -p build && cd build && cmake "
            "-DEXERCISM_RUN_ALL_TESTS=ON .. && make` (the build runs the test binary)."
        ),
    ),
    "javascript": LangSpec(
        "javascript",
        "node:22-bookworm-slim",
        "RUN mkdir -p /opt/js && cd /opt/js && npm init -y >/dev/null "
        "&& npm install --no-audit --no-fund jest@29.7.0 babel-jest@29.7.0 @babel/core@7.25.2 "
        "@exercism/babel-preset-javascript@0.2.1 core-js@3.37.1\n",
        "ln -sfn /opt/js/node_modules node_modules && "
        "for f in *.spec.js; do "
        "sed -i -E 's/\\bxtest\\(/test(/g; s/\\bxit\\(/it(/g' \"$f\"; done && "
        "/opt/js/node_modules/.bin/jest --ci --json "
        f"--outputFile={_R}/results.json ./ > {_R}/out.txt 2>&1",
        _parse_jest,
        env={"HOME": "/tmp"},
        host_hint="Run `npm install` once, write tests in a `*.spec.js` file and run `npx jest`.",
        visible_hint=(
            "Run `npm install` once, then `npx jest` (`xtest` marks skipped tests: change them to "
            "`test` in your working copy to run them all)."
        ),
    ),
}
KNOWN_EXERCISM_LANGUAGES = frozenset(LANGUAGES)


# ---------------------------------------------------------------------------
# Fetching one exercise at a pinned commit
# ---------------------------------------------------------------------------


def _exercise_prefix(language: str, exercise: str) -> str:
    return f"{language}/exercises/practice/{exercise}"


def _git_bytes(cache: Path, *args: str) -> bytes:
    proc = subprocess.run(  # noqa: S603 -- fixed argv, no shell
        ["git", "-C", str(cache), *args], capture_output=True, check=False, timeout=300
    )
    if proc.returncode != 0:
        err = proc.stderr.decode(errors="replace")[-300:]
        raise SubjectError(f"exercism: git {' '.join(args[:2])} failed: {err}")
    return proc.stdout


def pinned_cache(repo: str, commit: str) -> Path:
    cache = _ensure_repo_cache(repo, provider=_PROVIDER)
    _ensure_commit(cache, commit, provider=_PROVIDER)
    return cache


def list_exercises(repo: str, commit: str, language: str) -> list[str]:
    cache = pinned_cache(repo, commit)
    out = _git_bytes(cache, "ls-tree", "--name-only", commit, f"{language}/exercises/practice/")
    return sorted(Path(line).name for line in out.decode().splitlines() if line.strip())


def extract_exercise(repo: str, commit: str, language: str, exercise: str, dest: Path) -> Path:
    """Extract `<language>/exercises/practice/<exercise>` at *commit* into *dest*; returns it."""
    cache = pinned_cache(repo, commit)
    prefix = _exercise_prefix(language, exercise)
    data = _git_bytes(cache, "archive", "--format=tar", commit, prefix)
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        tf.extractall(dest, filter="data")
    root = dest / prefix
    if not (root / ".meta" / "config.json").is_file():
        raise SubjectError(f"exercism: {prefix} has no .meta/config.json at {commit}")
    return root


def read_blob_text(repo: str, commit: str, path: str) -> str:
    return _git_bytes(pinned_cache(repo, commit), "show", f"{commit}:{path}").decode(
        errors="replace"
    )


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


def _hidden_files(language: str, root: Path, files: dict[str, list[str]]) -> list[str]:
    hidden = set(files.get("test", []))
    if language == "go":  # test data lives in visible "editor" files like cases_test.go
        hidden |= {str(p.relative_to(root)) for p in root.rglob("*_test.go")}
    if language == "javascript":
        hidden |= {str(p.relative_to(root)) for p in root.glob("*.spec.js")}
    return sorted(hidden)


def _reference_map(files: dict[str, list[str]]) -> dict[str, str]:
    """example path -> solution path (matched by extension)."""
    examples, solutions = files.get("example", []), files.get("solution", [])
    out: dict[str, str] = {}
    for ex in examples:
        suffix = Path(ex).suffix
        matches = [s for s in solutions if Path(s).suffix == suffix]
        if len(matches) != 1:
            raise SubjectError(f"exercism: cannot map example {ex!r} onto solutions {solutions}")
        out[ex] = matches[0]
    return out


def render_readme(root: Path, solutions: list[str], tests: list[str] | None = None) -> str:
    """README for the agent; `tests` = the official test files when they are visible."""
    docs = root / ".docs"
    parts: list[str] = []
    for name, heading in (
        ("introduction.md", None),
        ("instructions.md", None),
        ("introduction.append.md", None),
        ("instructions.append.md", None),
        ("hints.md", "Hints"),
    ):
        path = docs / name
        if path.is_file():
            text = path.read_text(errors="replace").strip()
            parts.append(f"## {heading}\n\n{text}" if heading else text)
    files = "## Files\n\nImplement the exercise in: " + ", ".join(f"`{s}`" for s in solutions)
    if tests:
        files += (
            ". The official tests are in " + ", ".join(f"`{t}`" for t in tests) + ": they define "
            "the required names and signatures (follow them over the solution stub if the two "
            "differ). The grader runs every test in them -- including ones marked skipped, ignored "
            "or disabled -- against its own unmodified copy."
        )
    else:
        files += (
            ". Keep the existing names and signatures; hidden tests exercise every requirement."
        )
    parts.append(files)
    return "\n\n".join(parts) + "\n"


def _source(task: BenchTask) -> dict[str, Any]:
    src = task.source.model_dump() if task.source is not None else {}
    extra = getattr(task.source, "model_extra", None) or {}
    src.update(extra)
    language, exercise = src.get("language"), src.get("exercise")
    if language not in LANGUAGES or not isinstance(exercise, str) or not exercise:
        raise SubjectError(
            f"Task {task.id!r}: exercism source needs language in {sorted(LANGUAGES)} and exercise"
        )
    tests = src.get("tests", TESTS_HIDDEN)
    if tests not in (TESTS_HIDDEN, TESTS_VISIBLE):
        raise SubjectError(f"Task {task.id!r}: exercism source.tests must be hidden or visible")
    return {
        "repo": src.get("repo") or DEFAULT_REPO,
        "commit": src.get("commit") or DEFAULT_COMMIT,
        "language": language,
        "exercise": exercise,
        "tests": tests,
    }


class ExercismProvider(WorkspaceProvider):
    def prepare(
        self,
        task: BenchTask,
        repo_dir: Path,
        *,
        suite_base_dir: Path,
        subject_base_dir: Path | None = None,
        grading_dir: Path | None = None,
    ) -> None:
        if grading_dir is None:
            raise SubjectError(f"Task {task.id!r}: exercism provider requires a grading_dir")
        src = _source(task)
        with tempfile.TemporaryDirectory(dir=grading_dir) as tmp:
            root = extract_exercise(
                src["repo"], src["commit"], src["language"], src["exercise"], Path(tmp)
            )
            config = json.loads((root / ".meta" / "config.json").read_text())
            files: dict[str, list[str]] = config.get("files", {})
            hidden = _hidden_files(src["language"], root, files)
            refmap = _reference_map(files)
            for rel in hidden:
                dest = grading_dir / HIDDEN_DIRNAME / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / rel, dest)
            for ex, sol in refmap.items():
                dest = grading_dir / REFERENCE_DIRNAME / sol
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / ex, dest)
            visible = src["tests"] == TESTS_VISIBLE
            skip = set() if visible else set(hidden)  # the grader re-copies its own tests anyway
            repo_dir.mkdir(parents=True)
            for path in sorted(root.rglob("*")):
                relp = path.relative_to(root)
                if relp.parts[0] in (".meta", ".docs") or str(relp) in skip or not path.is_file():
                    continue
                dest = repo_dir / relp
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
            (repo_dir / README_FILENAME).write_text(
                render_readme(root, files.get("solution", []), hidden if visible else None)
            )
            meta = {**src, "hidden": hidden, "solution": files.get("solution", [])}
            (grading_dir / META_FILENAME).write_text(
                json.dumps(meta, indent=2, sort_keys=True) + "\n"
            )


register_workspace_provider(EXERCISM_PROVIDER_TYPE, ExercismProvider)


# ---------------------------------------------------------------------------
# Grader
# ---------------------------------------------------------------------------


def _copy_work(repo_dir: Path, work: Path) -> None:
    for path in sorted(repo_dir.rglob("*")):
        rel = path.relative_to(repo_dir)
        if any(part in _SKIP_DIRS for part in rel.parts) or not path.is_file() or path.is_symlink():
            continue
        dest = work / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)


class ExercismGrader(Grader):
    def _grade(self, cfg: GraderConfig, ctx: GraderContext) -> GradeResult:
        if not ctx.grading_dir:
            raise GraderError("exercism grader needs a grading_dir")
        grading = Path(ctx.grading_dir)
        meta = json.loads((grading / META_FILENAME).read_text())
        spec = LANGUAGES[meta["language"]]
        if not docker_available():
            raise GraderError("exercism grader needs docker (tests run in a per-language image)")
        try:
            ensure_image(spec.image_spec())
        except SandboxUnavailable as exc:
            raise GraderError(f"exercism grader: {exc}") from exc
        with tempfile.TemporaryDirectory(dir=grading) as tmp:
            work = Path(tmp) / meta["exercise"]  # C++ CMake names the target after the dir
            work.mkdir()
            _copy_work(Path(ctx.repo_dir), work)
            for path in (grading / HIDDEN_DIRNAME).rglob("*"):
                if path.is_file():
                    dest = work / path.relative_to(grading / HIDDEN_DIRNAME)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, dest)
            results = work / RESULTS_DIRNAME
            results.mkdir()
            container = f"/src/{meta['exercise']}"
            argv = ["docker", "run", "--rm", "--network", "none", *_DOCKER_LIMITS,
                    "--user", f"{os.getuid()}:{os.getgid()}", "-v", f"{work}:{container}",
                    "-w", container, "-e", "HOME=/tmp"]  # fmt: skip
            for key, value in spec.env.items():
                argv += ["-e", f"{key}={value}"]
            argv += [spec.image_spec().image, "sh", "-c", spec.command.format(exe=meta["exercise"])]
            try:
                proc = subprocess.run(  # noqa: S603 -- fixed argv, no shell on the host
                    argv, capture_output=True, text=True, timeout=GRADE_TIMEOUT_SECONDS, check=False
                )
                timed_out = False
            except subprocess.TimeoutExpired:
                timed_out = True
                proc = None
            passed, total = spec.parse(results)
            out_tail = ""
            out = results / "out.txt"
            if out.is_file():
                out_tail = out.read_text(errors="replace")[-2000:]
        score = passed / total if total else 0.0
        solved = total > 0 and passed == total and not timed_out
        detail = {
            "language": meta["language"],
            "exercise": meta["exercise"],
            "tests_passed": passed,
            "tests_total": total,
            "timed_out": timed_out,
            "exit_code": None if proc is None else proc.returncode,
            "image": spec.image_spec().image,
        }
        return GradeResult(solved=solved, score=score, detail=detail, raw_tail=out_tail)


register_grader(EXERCISM_GRADER_TYPE, ExercismGrader)


# ---------------------------------------------------------------------------
# Importer: a seeded, stratified sample -> suite.json
# ---------------------------------------------------------------------------


_RUST_STD_ROOTS = frozenset({"std", "core", "alloc", "self", "super", "crate"})
_RUST_USE = re.compile(r"^\s*(?:pub\s+)?(?:use|extern\s+crate)\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)


def _rust_example_needs_crates(repo: str, commit: str, exercise: str) -> bool:
    """The track's example may `use` a crate its Cargo.toml does not declare (regex, rand):
    such a reference cannot be built offline, so the instance cannot be validated."""
    text = read_blob_text(repo, commit, f"{_exercise_prefix('rust', exercise)}/.meta/example.rs")
    return any(root not in _RUST_STD_ROOTS for root in _RUST_USE.findall(text))


def _cpp_needs_packages(repo: str, commit: str, exercise: str) -> bool:
    """`find_package(...)` (e.g. Boost for meetup) = a library neither the grading image nor the
    agent host is guaranteed to have."""
    text = read_blob_text(repo, commit, f"{_exercise_prefix('cpp', exercise)}/CMakeLists.txt")
    return "find_package(" in text.replace("EXERCISM_TEST_SUITE", "") and bool(
        re.search(r"^\s*find_package\((?!Catch2|Threads)", text, re.M)
    )


def eligible(repo: str, commit: str, language: str, exercise: str) -> bool:
    """Pool rule (pre-registered, applied before sampling): every eligible exercise can be graded
    offline and its reference can be validated. Rust: no crate dependency in Cargo.toml or the
    example; C++: no external CMake package."""
    if language == "rust":
        return not (
            _rust_has_deps(repo, commit, exercise)
            or _rust_example_needs_crates(repo, commit, exercise)
        )
    if language == "cpp":
        return not _cpp_needs_packages(repo, commit, exercise)
    return True


def _rust_has_deps(repo: str, commit: str, exercise: str) -> bool:
    text = read_blob_text(repo, commit, f"{_exercise_prefix('rust', exercise)}/Cargo.toml")
    in_deps = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_deps = stripped == "[dependencies]"
            continue
        if in_deps and stripped and not stripped.startswith("#"):
            return True
    return False


# Exercism's own setting: the learner sees the official tests. Hidden tests (the first protocol,
# polyglot-mini) left some APIs unguessable -- e.g. C++ stubs are empty namespaces and the required
# function names appear only in the tests -- so they measured name-guessing, not capability.
TESTS_HIDDEN, TESTS_VISIBLE = "hidden", "visible"

VISIBLE_INSTRUCTION_TEMPLATE = """# Task

Implement the programming exercise described in `README.md` in this directory, in {language}.

- Edit the solution file(s) named in the README's "Files" section; do not rename or move files.
- The official tests are in this directory (see the README's "Files" section). They define the
  required API; make every one of them pass, including tests marked skipped, ignored or disabled
  (the grader enables all of them and runs its own unmodified copy, so editing the tests has no
  effect).
- {host_hint}
"""

INSTRUCTION_TEMPLATE = """# Task

Implement the programming exercise described in `README.md` in this directory, in {language}.

- Edit the solution file(s) named in the README's "Files" section; keep the existing function /
  type names and signatures, and do not rename or move files.
- Hidden tests check every requirement in `README.md`, including edge cases and error behaviour.
  They are not in this directory: write your own tests and make sure they pass before you finish.
- {host_hint}
"""

_LANGUAGE_NAMES = {"python": "Python", "go": "Go", "rust": "Rust", "cpp": "C++",
                   "javascript": "JavaScript"}  # fmt: skip


def build_suite(
    *,
    out: Path,
    suite_id: str,
    languages: list[str],
    per_language: int,
    seed: int,
    repo: str = DEFAULT_REPO,
    commit: str = DEFAULT_COMMIT,
    tier: str = "medium",
    parity: str = "../../parity/default",
    exclude: frozenset[tuple[str, str]] = frozenset(),
    tests: str = TESTS_VISIBLE,
) -> dict[str, Any]:
    """Draw `per_language` exercises per language with `random.Random(seed)` from the sorted pool
    of `eligible` exercises (offline-gradeable, reference-validatable) and write the suite.
    `exclude` holds (language, exercise) pairs already used elsewhere (a disjoint sample)."""
    rng = random.Random(seed)
    tasks: list[dict[str, Any]] = []
    sample: dict[str, list[str]] = {}
    instructions = out / "instructions"
    instructions.mkdir(parents=True, exist_ok=True)
    for language in languages:
        if language not in LANGUAGES:
            raise SubjectError(
                f"exercism: unknown language {language!r}; known: {sorted(LANGUAGES)}"
            )
        pool = [
            e
            for e in list_exercises(repo, commit, language)
            if (language, e) not in exclude and eligible(repo, commit, language, e)
        ]
        picked = sorted(rng.sample(pool, min(per_language, len(pool))))
        sample[language] = picked
        (instructions / f"{language}.md").write_text(
            (
                VISIBLE_INSTRUCTION_TEMPLATE if tests == TESTS_VISIBLE else INSTRUCTION_TEMPLATE
            ).format(
                language=_LANGUAGE_NAMES[language],
                host_hint=(
                    LANGUAGES[language].visible_hint
                    if tests == TESTS_VISIBLE
                    else LANGUAGES[language].host_hint
                ),
            )
        )
        for ex in picked:
            tasks.append(
                {
                    "id": f"ex-{language}-{ex}",
                    "category": "feature",
                    "instruction": f"instructions/{language}.md",
                    "source": {
                        "type": EXERCISM_PROVIDER_TYPE,
                        "repo": repo,
                        "commit": commit,
                        "language": language,
                        "exercise": ex,
                        "tests": tests,
                    },  # fmt: skip
                    "grader": {"type": EXERCISM_GRADER_TYPE},
                    "family": "exercism",
                    "language": language,
                    "timeout_seconds": 2400,
                    "tags": ["exercism", language, "contaminated", "algorithms"],
                }
            )
    suite = {
        "version": "1.0",
        "id": suite_id,
        "domain": "software",
        "description": (
            f"Exercism practice exercises from {repo}@{commit[:12]} (Aider polyglot set), "
            f"sampled {per_language}/language with seed {seed} from {', '.join(languages)}; "
            f"official tests ({tests} to the agent; the grader runs its own copy with every test "
            "enabled) run in per-language Docker images, score = fraction of tests "
            "passed. Contaminated tier (public exercises). Exercism tracks are MIT licensed; "
            "content (c) Exercism."
        ),
        "tier": tier,
        "parity": parity,
        "defaults": {"timeout_seconds": 2400},
        "tasks": tasks,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "suite.json").write_text(json.dumps(suite, indent=2) + "\n")
    (out / "README.md").write_text(
        f"# {suite_id}\n\nGenerated by `ao-bench import-exercism --seed {seed} "
        f"--per-language {per_language} --languages {','.join(languages)}` from `{repo}` at "
        f"`{commit}`"
        + (f", excluding {len(exclude)} exercise(s) used by earlier suites" if exclude else "")
        + ".\n\nSample (drawn before any agent ran; never re-drawn after results):\n\n"
        + "\n".join(f"- {lang}: {', '.join(exs)}" for lang, exs in sample.items())
        + "\n\nAttribution: exercises (c) Exercism (https://exercism.org), MIT-licensed language "
        "tracks, curated by Aider (https://github.com/Aider-AI/polyglot-benchmark).\n"
    )
    return {"sample": sample, "tasks": len(tasks)}


def used_exercises(suite_paths: list[str]) -> frozenset[tuple[str, str]]:
    """(language, exercise) pairs of the exercism tasks in the given suite.json files."""
    used: set[tuple[str, str]] = set()
    for path in suite_paths:
        suite = json.loads(Path(path).read_text())
        for task in suite.get("tasks", []):
            src = task.get("source", {})
            if src.get("type") == EXERCISM_PROVIDER_TYPE:
                used.add((src["language"], src["exercise"]))
    return frozenset(used)


def import_exercism_command(
    out: str = typer.Option(..., "--out", help="Suite dir to write."),
    suite_id: str = typer.Option(..., "--id", help="Suite id."),
    languages: str = typer.Option("python,go,rust,cpp,javascript", "--languages"),
    per_language: int = typer.Option(6, "--per-language", min=1),
    seed: int = typer.Option(..., "--seed", help="Sample seed (record it; never re-draw)."),
    repo: str = typer.Option(DEFAULT_REPO, "--repo"),
    commit: str = typer.Option(DEFAULT_COMMIT, "--commit"),
    tier: str = typer.Option("medium", "--tier"),
    exclude_suites: list[str] = typer.Option(
        [], "--exclude-suite", help="Suite whose exercises to leave out (repeatable)."
    ),
    tests: str = typer.Option(
        TESTS_VISIBLE,
        "--tests",
        help="visible (Exercism's setting) or hidden (API may be unguessable).",
    ),
) -> None:
    """Import a seeded sample of Exercism (Aider polyglot) exercises as a suite."""
    try:
        res = build_suite(
            exclude=used_exercises(exclude_suites),
            tests=tests,
            out=Path(out),
            suite_id=suite_id,
            languages=languages.split(","),
            per_language=per_language,
            seed=seed,
            repo=repo,
            commit=commit,
            tier=tier,
        )
    except SubjectError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1) from exc
    for lang, exs in res["sample"].items():
        typer.echo(f"{lang}: {', '.join(exs)}")
    typer.echo(f"wrote {res['tasks']} task(s) to {out}/suite.json")
