"""`pytest_targeted` grader (HLD §4.2 "Grading cost control", §8).

Restores the agent-hidden tests from `grading_dir/hidden/**` into the repo, runs
`fail_to_pass` + a bounded `pass_to_pass` sample in ONE pytest invocation, and reports
collateral-diff statistics. Grader keys live in `GraderConfig.model_extra` (suite.json
`grader` block); anything omitted is read from the `recipe.resolved.json` the Tier-2
provider wrote into `grading_dir`:

    fail_to_pass             list[str]  node ids that must pass            (default: resolved)
    pass_to_pass             list[str]  node ids that must keep passing    (default: resolved)
    test_targets             list[str]  files/dirs run whole; every test in them must pass
                                        (used when node ids are unknown, e.g. pr_mined)
    pass_to_pass_sample      int        max pass_to_pass ids actually run  (default 30,
                                        deterministic seeded sample, same for every subject)
    install                  str|None   shell command run once in repo_dir (default: resolved)
    test_command             str|None   template, placeholders {python} {targets} {repo_dir}
                                        {grading_dir} (default: resolved, then built-in)
    per_test_timeout_seconds int        enforced via pytest-timeout when importable  (60)
    total_timeout_seconds    int        hard bound on the single pytest run         (900)
    install_timeout_seconds  int        hard bound on `install`                     (900)
    baseline_commit          str|None   git ref collateral is diffed against (default: resolved)

Outcome:
    solved = every fail_to_pass id (and every test in `test_targets`) passed AND every
             sampled pass_to_pass id passed
    score  = 0.7 * f2p_rate + 0.3 * p2p_rate   (weights renormalise if a group is empty)

Errors vs grades: a failing `install` (or an environment that cannot run pytest at all) is
an *infrastructure* problem -> `GraderError`; failing/missing/timed-out tests are an
ordinary unsolved grade. `detail` carries per-test results, install/test-run facts,
timeouts, `collateral` ({files_touched, lines_added, lines_deleted} of the agent's diff
vs the snapshot baseline, excluding restored hidden paths and `.claude/`) and
`agent_tests_added` -- both reported, never scored (HLD §4.2 "reported but not scored").
"""

from __future__ import annotations

import json
import os
import random
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import GraderError
from .graders import Grader, GraderContext, GradeResult, _tail
from .registries import register_grader
from .spec import GraderConfig
from .tier2_common import HIDDEN_DIRNAME, RESOLVED_RECIPE_FILENAME, is_test_path

PYTEST_TARGETED_GRADER_TYPE = "pytest_targeted"

# Scoring weights (HLD §4.2): hidden fail-to-pass dominates, regressions are secondary.
F2P_WEIGHT = 0.7
P2P_WEIGHT = 0.3
# Fixed seed so the pass_to_pass sample is identical for every subject (paired design).
P2P_SAMPLE_SEED = 20261023

DEFAULT_P2P_SAMPLE = 30
DEFAULT_PER_TEST_TIMEOUT_SECONDS = 60
DEFAULT_TOTAL_TIMEOUT_SECONDS = 900
DEFAULT_INSTALL_TIMEOUT_SECONDS = 900
DEFAULT_TEST_COMMAND = "{python} -m pytest -q -p no:cacheprovider {targets}"

# Flags the grader always appends: junit for robust parsing, -rA so raw_tail is readable,
# continue-on-collection-errors so ONE broken test module cannot blank the whole run.
_PYTEST_FIXED_FLAGS = ("-rA", "--continue-on-collection-errors")
JUNIT_FILENAME = "junit.xml"
COLLATERAL_INDEX_FILENAME = "collateral.index"
# pytest exits 4 on usage errors, incl. "not found: <node id>" for a vanished test.
_PYTEST_USAGE_ERROR = 4
_MAX_NOT_FOUND_RETRIES = 25
_KILL_GRACE_SECONDS = 5
_GIT_TIMEOUT_SECONDS = 120
_MAX_LISTED_FILES = 20
_REPO_VENV_PYTHON = Path(".venv") / "bin" / "python"

# Paths never counted as agent collateral (parity/instruction files, envs, caches).
_COLLATERAL_EXCLUDES = (
    ":(exclude).claude",
    ":(exclude)CLAUDE.md",
    ":(exclude)INSTRUCTION.md",
    ":(exclude).venv",
    ":(exclude)venv",
    ":(exclude)node_modules",
    ":(exclude).pytest_cache",
    ":(exclude,glob)**/__pycache__/**",
    ":(exclude,glob)**/*.egg-info/**",
)

_NOT_FOUND_RE = re.compile(
    r"ERROR: (?:file or directory )?not found: (.+?)(?:\n\(no match|\n|$)", re.MULTILINE
)
_RA_LINE_RE = re.compile(r"^(PASSED|FAILED|ERROR)\s+(\S.*?)(?: - .*)?$", re.MULTILINE)

PASSED, FAILED, SKIPPED, MISSING = "passed", "failed", "skipped", "missing"


class _Settings(BaseModel):
    """Effective grader settings: suite.json `grader` extras over `recipe.resolved.json`."""

    model_config = ConfigDict(extra="ignore")

    fail_to_pass: list[str] = []
    pass_to_pass: list[str] = []
    test_targets: list[str] = []
    pass_to_pass_sample: int = Field(default=DEFAULT_P2P_SAMPLE, ge=0)
    install: str | None = None
    test_command: str | None = None
    per_test_timeout_seconds: int = Field(default=DEFAULT_PER_TEST_TIMEOUT_SECONDS, ge=1)
    total_timeout_seconds: int = Field(default=DEFAULT_TOTAL_TIMEOUT_SECONDS, ge=1)
    install_timeout_seconds: int = Field(default=DEFAULT_INSTALL_TIMEOUT_SECONDS, ge=1)
    baseline_commit: str | None = None
    base_commit: str | None = None


@dataclass
class _Proc:
    returncode: int
    output: str
    timed_out: bool = False
    seconds: float = 0.0


@dataclass
class _Outcomes:
    """Per-test statuses parsed from one pytest run (node id -> passed/failed/skipped)."""

    statuses: dict[str, str] = field(default_factory=dict)
    timeouts: list[str] = field(default_factory=list)

    def record(self, node_id: str, status: str) -> None:
        # A node id seen twice (e.g. targeted by file AND by id) keeps its worst status.
        previous = self.statuses.get(node_id)
        if previous == FAILED or (previous == PASSED and status == SKIPPED):
            return
        self.statuses[node_id] = status


# --------------------------------------------------------------------------- settings


def _load_resolved(grading_dir: Path) -> dict[str, Any]:
    path = grading_dir / RESOLVED_RECIPE_FILENAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise GraderError(f"pytest_targeted: unreadable {path}: {exc}") from exc
    return data if isinstance(data, dict) else {}


def _settings(cfg: GraderConfig, resolved: dict[str, Any]) -> _Settings:
    merged = {k: v for k, v in resolved.items() if k in _Settings.model_fields and v is not None}
    merged.update({k: v for k, v in (cfg.model_extra or {}).items() if k in _Settings.model_fields})
    if cfg.timeout_seconds and "total_timeout_seconds" not in merged:
        merged["total_timeout_seconds"] = cfg.timeout_seconds
    try:
        settings = _Settings.model_validate(merged)
    except ValidationError as exc:
        raise GraderError(f"pytest_targeted: invalid grader config: {exc}") from exc
    if not (settings.fail_to_pass or settings.test_targets):
        raise GraderError(
            "pytest_targeted: neither fail_to_pass nor test_targets given (grader config or "
            f"{RESOLVED_RECIPE_FILENAME})"
        )
    return settings


def sample_pass_to_pass(ids: list[str], limit: int) -> list[str]:
    """Deterministic bounded sample of *ids* (sorted for stability, fixed seed)."""
    unique = sorted(set(ids))
    if len(unique) <= limit:
        return unique
    return sorted(random.Random(P2P_SAMPLE_SEED).sample(unique, limit))


# ------------------------------------------------------------------------- processes


def _clean_env() -> dict[str, str]:
    """Environment for install/test subprocesses: the *grader's* venv must never leak in
    (`uv pip install` would otherwise target `$VIRTUAL_ENV` -- i.e. ao-bench's own env)."""
    env = {k: v for k, v in os.environ.items() if k not in {"VIRTUAL_ENV", "PYTEST_ADDOPTS"}}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run_bounded(argv: list[str], cwd: Path, timeout: int, *, shell: bool = False) -> _Proc:
    """Run *argv* (or `sh -c argv[0]`) bounded by *timeout*; on timeout the whole process
    group is killed so a hung test's children cannot outlive the grade. Never raises for a
    spawn failure -- returns rc 127 so callers decide what it means."""
    cmd = ["sh", "-c", argv[0]] if shell else argv
    started = time.monotonic()
    try:
        proc = subprocess.Popen(  # noqa: S603 -- argv/sh -c from trusted suite config
            cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            env=_clean_env(),
            start_new_session=True,
        )
    except OSError as exc:
        return _Proc(returncode=127, output=f"failed to start {cmd[0]!r}: {exc}")
    try:
        output, _ = proc.communicate(timeout=timeout)
        return _Proc(proc.returncode, output or "", seconds=time.monotonic() - started)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        try:
            output, _ = proc.communicate(timeout=_KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            output = ""
        return _Proc(124, output or "", timed_out=True, seconds=time.monotonic() - started)


# ----------------------------------------------------------------------------- git


def _git(repo_dir: Path, args: list[str], *, index: Path | None = None) -> tuple[int, str]:
    env = dict(os.environ)
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)  # keep repo_dir's real index untouched
    try:
        result = subprocess.run(  # noqa: S603 -- fixed argv, no shell
            ["git", "-C", str(repo_dir), *args],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_GIT_TIMEOUT_SECONDS,
            env=env,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    return result.returncode, result.stdout if result.returncode == 0 else result.stderr


def _hidden_files(grading_dir: Path) -> list[str]:
    root = grading_dir / HIDDEN_DIRNAME
    if not root.is_dir():
        return []
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def collateral_stats(
    repo_dir: Path, grading_dir: Path, baseline: str | None, hidden: list[str]
) -> dict[str, Any]:
    """The agent's diff vs *baseline* (reported, never scored). Computed through a private
    index file so the repo's own index/worktree are not touched. Never raises: any git
    failure degrades to `{"error": ...}`."""
    if baseline is None:
        return {"error": "no baseline commit known"}
    index = grading_dir / COLLATERAL_INDEX_FILENAME
    try:
        rc, out = _git(repo_dir, ["read-tree", baseline], index=index)
        if rc != 0:
            return {"error": f"git read-tree {baseline}: {out.strip()}"}
        # No pathspec on `add`: git errors out when an exclude pathspec names an ignored path
        # (INSTRUCTION.md / .claude live in .git/info/exclude). Ignore rules already keep
        # runner-owned files out; _COLLATERAL_EXCLUDES are applied on the diff below.
        rc, out = _git(repo_dir, ["add", "--all"], index=index)
        if rc != 0:
            return {"error": f"git add: {out.strip()}"}
        hidden_excludes = [f":(exclude,literal){p}" for p in hidden]
        spec = ["--", ".", *_COLLATERAL_EXCLUDES, *hidden_excludes]
        rc, numstat = _git(
            repo_dir,
            ["diff", "--cached", "--numstat", "--no-renames", baseline, *spec],
            index=index,
        )
        if rc != 0:
            return {"error": f"git diff --numstat: {numstat.strip()}"}
        added = deleted = files = 0
        try:
            for line in numstat.splitlines():
                plus, minus, _path = line.split("\t", 2)
                files += 1
                added += int(plus) if plus.isdigit() else 0  # binary files show "-"
                deleted += int(minus) if minus.isdigit() else 0
        except ValueError:
            return {"error": f"unparsable git numstat output: {numstat[:200]!r}"}
        # New test files the agent wrote -- hidden paths included (it still authored them).
        rc, names = _git(
            repo_dir,
            [
                "diff",
                "--cached",
                "--name-only",
                "--diff-filter=A",
                "--no-renames",
                baseline,
                "--",
                ".",
                *_COLLATERAL_EXCLUDES,
            ],
            index=index,
        )
        new_tests = [n for n in names.splitlines() if is_test_path(n)] if rc == 0 else []
        return {
            "files_touched": files,
            "lines_added": added,
            "lines_deleted": deleted,
            "agent_tests_added": len(new_tests),
            "agent_test_files": new_tests[:_MAX_LISTED_FILES],
            "baseline": baseline,
        }
    finally:
        index.unlink(missing_ok=True)


# ------------------------------------------------------------------- run + parsing


def restore_hidden(grading_dir: Path, repo_dir: Path) -> list[str]:
    """Copy `grading_dir/hidden/**` over *repo_dir* (overwriting whatever the agent left)."""
    restored = _hidden_files(grading_dir)
    root = grading_dir / HIDDEN_DIRNAME
    for rel in restored:
        dest = repo_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink() or dest.is_file():
            dest.unlink()  # never write THROUGH a symlink the agent planted
        shutil.copy2(root / rel, dest)
    return restored


def _module_for_classname(parts: list[str], repo_dir: Path) -> tuple[str | None, list[str]]:
    """Longest dotted prefix of *parts* that is a real `.py` file under *repo_dir*; the
    remainder are class names (junit's `classname` is dotted module + classes)."""
    for i in range(len(parts), 0, -1):
        candidate = "/".join(parts[:i]) + ".py"
        if (repo_dir / candidate).is_file():
            return candidate, parts[i:]
    return None, parts


def _junit_node_id(classname: str, name: str, repo_dir: Path) -> str:
    if not classname:  # collection error: `name` is the dotted module path
        module, _rest = _module_for_classname(name.split("."), repo_dir)
        return module or name
    parts = classname.split(".")
    module, classes = _module_for_classname(parts, repo_dir)
    if module is None:
        return "::".join([classname, name])
    return "::".join([module, *classes, name])


def parse_junit(path: Path, repo_dir: Path) -> _Outcomes | None:
    """Per-test statuses from a junit xml, or None if absent/unparsable."""
    if not path.is_file():
        return None
    try:
        root = ET.parse(path).getroot()  # noqa: S314 -- our own pytest's output
    except (ET.ParseError, OSError):
        return None
    out = _Outcomes()
    for case in root.iter("testcase"):
        node_id = _junit_node_id(case.get("classname", ""), case.get("name", ""), repo_dir)
        failure = case.find("failure")
        if failure is None:
            failure = case.find("error")
        if failure is not None:
            out.record(node_id, FAILED)
            if "Timeout >" in (failure.get("message") or ""):
                out.timeouts.append(node_id)
        elif case.find("skipped") is not None:
            out.record(node_id, SKIPPED)
        else:
            out.record(node_id, PASSED)
    return out


def parse_report_lines(output: str) -> _Outcomes:
    """Fallback parser over `-rA` summary lines (used only when junit is unavailable)."""
    out = _Outcomes()
    for status, node_id in _RA_LINE_RE.findall(output):
        out.record(node_id, PASSED if status == "PASSED" else FAILED)
    return out


def _relative_id(raw: str, repo_dir: Path) -> str:
    """Strip the repo_dir prefix pytest prints on 'not found' errors."""
    for prefix in {str(repo_dir), str(repo_dir.resolve())}:
        if raw.startswith(prefix + "/"):
            return raw[len(prefix) + 1 :]
    return raw


def _python_for(repo_dir: Path) -> str:
    venv_python = repo_dir / _REPO_VENV_PYTHON
    return str(venv_python) if venv_python.exists() else sys.executable


def _has_pytest_timeout(python: str, repo_dir: Path) -> bool:
    return _run_bounded([python, "-c", "import pytest_timeout"], repo_dir, 60).returncode == 0


def _build_argv(
    template: str,
    python: str,
    targets: list[str],
    repo_dir: Path,
    grading_dir: Path,
    junit: Path,
    per_test_timeout: int | None,
) -> list[str]:
    rendered = (
        template.replace("{python}", shlex.quote(python))
        .replace("{repo_dir}", shlex.quote(str(repo_dir)))
        .replace("{grading_dir}", shlex.quote(str(grading_dir)))
        .replace("{targets}", shlex.join(targets))
    )
    argv = shlex.split(rendered)
    argv += [*_PYTEST_FIXED_FLAGS, "-p", "no:cacheprovider", f"--rootdir={repo_dir}"]
    argv.append(f"--junitxml={junit}")
    if per_test_timeout is not None:
        argv.append(f"--timeout={per_test_timeout}")
    return argv


@dataclass
class _Run:
    outcomes: _Outcomes
    proc: _Proc
    not_found: list[str]
    runs: int


def _run_targets(
    argv_for: Callable[[list[str]], list[str]],
    targets: list[str],
    repo_dir: Path,
    junit: Path,
    total_timeout: int,
) -> _Run:
    """One pytest invocation over *targets*. A vanished node id makes pytest exit 4 before
    running anything; drop it (it will grade as `missing`) and re-run -- bounded, cheap."""
    not_found: list[str] = []
    remaining = list(targets)
    deadline = time.monotonic() + total_timeout
    proc = _Proc(returncode=_PYTEST_USAGE_ERROR, output="")
    outcomes = _Outcomes()
    runs = 0
    while remaining and runs <= _MAX_NOT_FOUND_RETRIES:
        runs += 1
        junit.unlink(missing_ok=True)
        budget = max(1, int(deadline - time.monotonic()))
        proc = _run_bounded(argv_for(remaining), repo_dir, budget)
        if proc.timed_out:
            break
        parsed = parse_junit(junit, repo_dir)
        outcomes = parsed if parsed is not None else parse_report_lines(proc.output)
        if proc.returncode == _PYTEST_USAGE_ERROR and not outcomes.statuses:
            match = _NOT_FOUND_RE.search(proc.output)
            if match:
                gone = _relative_id(match.group(1).strip(), repo_dir)
                kept = [t for t in remaining if t != gone]
                if len(kept) < len(remaining):
                    not_found.append(gone)
                    remaining = kept
                    continue
        break
    return _Run(outcomes, proc, not_found, runs)


# ------------------------------------------------------------------------- grading


def _file_of(node_id: str) -> str:
    return node_id.split("::", 1)[0]


def _group_stats(statuses: dict[str, str], *, strict: frozenset[str]) -> dict[str, Any]:
    """Pass-rate over one group. A `skipped` test is excluded from the denominator (an
    environment-dependent skip is not evidence either way) -- except ids in *strict*
    (explicit fail_to_pass ids), where a skip means the behaviour was never verified and so
    counts as a failure."""
    passed = sum(1 for s in statuses.values() if s == PASSED)
    excluded = sum(1 for n, s in statuses.items() if s == SKIPPED and n not in strict)
    total = len(statuses) - excluded
    return {
        "total": total,
        "passed": passed,
        "rate": (passed / total) if total > 0 else None,
        "results": statuses,
    }


def _status_of(outcomes: _Outcomes, node_id: str) -> str:
    return outcomes.statuses.get(node_id, MISSING)


class PytestTargetedGrader(Grader):
    def _grade(self, cfg: GraderConfig, ctx: GraderContext) -> GradeResult:
        if not ctx.grading_dir:
            raise GraderError("pytest_targeted grader requires a grading_dir (none was set)")
        repo_dir = Path(ctx.repo_dir)
        grading_dir = Path(ctx.grading_dir)
        resolved = _load_resolved(grading_dir)
        settings = _settings(cfg, resolved)
        detail: dict[str, Any] = {}

        # 1. Collateral FIRST: before hidden tests are restored or an env is installed.
        hidden = _hidden_files(grading_dir)
        baseline = settings.baseline_commit or settings.base_commit or "HEAD"
        detail["collateral"] = collateral_stats(repo_dir, grading_dir, baseline, hidden)
        detail["agent_tests_added"] = detail["collateral"].pop("agent_tests_added", None)
        detail["agent_test_files"] = detail["collateral"].pop("agent_test_files", [])

        # 2. Restore hidden tests, install once (failure => infra).
        detail["restored_hidden"] = restore_hidden(grading_dir, repo_dir)
        if settings.install:
            detail["install"] = self._install(settings, repo_dir)
        python = _python_for(repo_dir)
        detail["python"] = python

        # 3. Choose targets: whole files (test_targets) + f2p ids + a p2p sample.
        p2p_ids = sample_pass_to_pass(settings.pass_to_pass, settings.pass_to_pass_sample)
        whole = list(dict.fromkeys(settings.test_targets))
        whole_roots = {t.rstrip("/") for t in whole}
        ids = [
            i
            for i in dict.fromkeys([*settings.fail_to_pass, *p2p_ids])
            if _file_of(i) not in whole_roots
        ]
        targets = [*whole, *ids]

        enforce_timeout = _has_pytest_timeout(python, repo_dir)
        template = settings.test_command or DEFAULT_TEST_COMMAND
        if "{targets}" not in template:
            raise GraderError(
                f"pytest_targeted: test_command lacks a {{targets}} placeholder: {template!r}"
            )
        junit = grading_dir / JUNIT_FILENAME

        def argv_for(current: list[str]) -> list[str]:
            return _build_argv(
                template,
                python,
                current,
                repo_dir,
                grading_dir,
                junit,
                settings.per_test_timeout_seconds if enforce_timeout else None,
            )

        run = _run_targets(argv_for, targets, repo_dir, junit, settings.total_timeout_seconds)
        outcomes = run.outcomes
        if not outcomes.statuses and "No module named pytest" in run.proc.output:
            raise GraderError(f"pytest_targeted: interpreter {python} cannot import pytest")

        # 4. Per-group results.
        f2p_statuses = {i: _status_of(outcomes, i) for i in settings.fail_to_pass}
        for root in whole:
            in_root = {
                n: s
                for n, s in outcomes.statuses.items()
                if _file_of(n) == root.rstrip("/") or _file_of(n).startswith(root.rstrip("/") + "/")
            }
            f2p_statuses.update(in_root or {root: MISSING})
        p2p_statuses = {i: _status_of(outcomes, i) for i in p2p_ids}
        f2p = _group_stats(f2p_statuses, strict=frozenset(settings.fail_to_pass))
        p2p = _group_stats(p2p_statuses, strict=frozenset())

        f2p_ok = f2p["total"] > 0 and f2p["passed"] == f2p["total"]
        p2p_ok = p2p["total"] == 0 or p2p["passed"] == p2p["total"]
        solved = f2p_ok and p2p_ok and not run.proc.timed_out
        score = _score(f2p["rate"], p2p["rate"], timed_out=run.proc.timed_out)

        detail.update(
            {
                "mode": "test_targets" if whole else "node_ids",
                "fail_to_pass": f2p,
                "pass_to_pass": {
                    **p2p,
                    "population": len(set(settings.pass_to_pass)),
                    "sampled": len(p2p_ids),
                },
                "test_run": {
                    "returncode": run.proc.returncode,
                    "seconds": round(run.proc.seconds, 3),
                    "invocations": run.runs,
                    "targets": len(targets),
                    "not_found": run.not_found,
                    # modules that failed to import: junit/-rA report them as `<file>` (no `::`)
                    "collection_errors": sorted(
                        n for n, st in outcomes.statuses.items() if "::" not in n and st == FAILED
                    ),
                    "junit_parsed": junit.is_file(),
                },
                "timeouts": {
                    "total_timeout_hit": run.proc.timed_out,
                    "per_test_enforced": enforce_timeout,
                    "per_test": outcomes.timeouts,
                },
            }
        )
        return GradeResult(
            solved=solved, score=score, detail=detail, raw_tail=_tail(run.proc.output)
        )

    @staticmethod
    def _install(settings: _Settings, repo_dir: Path) -> dict[str, Any]:
        """Run `install` once in repo_dir; any failure is infrastructure (`GraderError`)."""
        install = settings.install or ""
        if ".venv" in install:
            # Re-create the env from scratch: whatever venv the agent left behind must not
            # influence (or break) the grade.
            shutil.rmtree(repo_dir / ".venv", ignore_errors=True)
        proc = _run_bounded([install], repo_dir, settings.install_timeout_seconds, shell=True)
        if proc.timed_out or proc.returncode != 0:
            reason = "timed out" if proc.timed_out else f"exit {proc.returncode}"
            raise GraderError(f"pytest_targeted: install failed ({reason}): {_tail(proc.output)}")
        return {"command": install, "seconds": round(proc.seconds, 3)}


def _score(f2p_rate: float | None, p2p_rate: float | None, *, timed_out: bool) -> float:
    """0.7*f2p + 0.3*p2p; an empty group's weight is redistributed to the other. A run
    that hit the total timeout scores 0 (nothing could be trusted)."""
    if timed_out:
        return 0.0
    parts = [(F2P_WEIGHT, f2p_rate), (P2P_WEIGHT, p2p_rate)]
    live = [(w, r) for w, r in parts if r is not None]
    if not live:
        return 0.0
    weight = sum(w for w, _ in live)
    return round(sum(w * r for w, r in live) / weight, 6)


register_grader(PYTEST_TARGETED_GRADER_TYPE, PytestTargetedGrader)
