"""`ao-bench import-prs` (HLD §4.2 Tier 2a "Fresh PR mining").

Mines recently merged PRs of a public GitHub repo (via the `gh` CLI -- `gh api`, so
GitHub auth/rate limits are the user's own) into a `pr_mined` suite:

    <out>/instances.json                      pinned per-PR metadata (provider input)
    <out>/suite.json                          one task per instance (appended to if it exists)
    <out>/tasks/<task-id>/instruction.md      generated from the cleaned PR title/body

Selection: merged on/after `--since`, with at least `--min-test-files` changed pytest
test modules AND at least one changed non-test `.py` source file -- i.e. PRs whose tests
can be held out and whose behaviour change lives in source. `base_commit` is the merge
commit's first parent (the exact pre-merge state of the base branch), falling back to the
PR API's `base.sha` when that lookup fails; both are recorded.

Contamination control: only PRs merged on/after the cutoff are mined; the cutoff is
recorded in `suite.json` (`cutoff_date`) and `instances.json` (re-mine quarterly).

The PR text is *cleaned* (links, @mentions, `#123` references, HTML comments removed) but
it is still PR prose: review the generated `instruction.md` files for solution hints
before committing a suite.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer

from .errors import BenchError, SpecValidationError
from .tier2_common import is_test_module, is_test_path

PR_MINED_DATASET = "pr-mined"
FAMILY = "pr-mined"

_GH_BIN = "gh"
_GH_TIMEOUT_SECONDS = 120
_PRS_PER_PAGE = 50
_FILES_PER_PAGE = 100  # a PR listing this many files is truncated -> too large to use
_MAX_BODY_CHARS = 6000
_MAX_PAGES = 40  # hard stop on pagination (2000 PRs) so a quiet --since can't loop forever

_SUITE_FILENAME = "suite.json"
_INSTANCES_FILENAME = "instances.json"
_TASKS_DIRNAME = "tasks"
_INSTRUCTION_FILENAME = "instruction.md"
_DEFAULT_SUITE_ID = "repo-live"
_SUITE_VERSION = "1.0"
_SUITE_TIER = "large"
_SUITE_DEFAULT_TIMEOUT_SECONDS = 5400  # tiers.json large default; suite.json cannot reference it
DEFAULT_INSTALL = "uv venv .venv && uv pip install -e . pytest pytest-timeout"

_BUG_WORDS = re.compile(r"\b(fix(es|ed)?|bug|regression|crash(es)?|incorrect|wrong|broken)\b", re.I)
_FEATURE_LABELS = ("enhancement", "feature", "feat")
_BUG_LABELS = ("bug", "regression", "fix")

_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL_RE = re.compile(r"https?://\S+")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_ISSUE_REF_RE = re.compile(
    r"(?:\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+)?(?:[\w.-]+/[\w.-]+)?#\d+", re.I
)
_MENTION_RE = re.compile(r"(?<![\w`])@[A-Za-z0-9][A-Za-z0-9/_-]*")

GhApi = Callable[[str], Any]


@dataclass
class ImportResult:
    suite_path: Path
    instances_path: Path
    task_count: int
    new_task_ids: list[str]


# ------------------------------------------------------------------------------ gh


def gh_api(endpoint: str) -> Any:
    """`gh api <endpoint>` -> parsed JSON. Every failure is a `BenchError` (never a raw
    OSError/JSONDecodeError): missing `gh`, non-zero exit (auth, rate limit, 404), bad JSON."""
    try:
        result = subprocess.run(  # noqa: S603 -- fixed argv, no shell, bounded timeout
            [_GH_BIN, "api", endpoint],
            capture_output=True,
            text=True,
            timeout=_GH_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise BenchError(
            "import-prs needs the GitHub CLI (`gh`) on PATH and authenticated (`gh auth login`)"
        ) from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BenchError(f"`gh api {endpoint}` failed to run: {exc}") from exc
    if result.returncode != 0:
        raise BenchError(
            f"`gh api {endpoint}` failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise BenchError(f"`gh api {endpoint}` returned invalid JSON: {exc}") from exc


# --------------------------------------------------------------------- text helpers


def clean_text(text: str | None, *, limit: int = _MAX_BODY_CHARS) -> str:
    """Strip what would make a PR description a pointer to its own answer or a mention of
    people: images/links (keep link text), bare URLs, `#123`/`Fixes #123` references,
    @mentions, HTML comments; collapse whitespace and cap the length."""
    out = text or ""
    out = _HTML_COMMENT_RE.sub("", out)
    out = _IMAGE_RE.sub("", out)
    out = _MD_LINK_RE.sub(r"\1", out)
    out = _URL_RE.sub("", out)
    out = _ISSUE_REF_RE.sub("", out)
    out = _MENTION_RE.sub("", out)
    out = "\n".join(line.rstrip() for line in out.replace("\r\n", "\n").split("\n"))
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out[:limit].rstrip()


def classify_category(title: str, labels: list[str]) -> str:
    """`bugfix` | `feature` by label first, then title heuristic (default feature)."""
    lowered = [name.lower() for name in labels]
    if any(b in name for name in lowered for b in _BUG_LABELS):
        return "bugfix"
    if any(f in name for name in lowered for f in _FEATURE_LABELS):
        return "feature"
    return "bugfix" if _BUG_WORDS.search(title) else "feature"


def _task_id(instance_id: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", instance_id.lower()).strip("-")


def instruction_text(title: str, body: str) -> str:
    return (
        "# Task: implement the change described below\n\n"
        "You are working inside a real, checked-out copy of an open-source project's\n"
        "repository, at the commit just before the change below was merged upstream.\n\n"
        "- Make the source-code change(s) the description asks for; keep the change focused.\n"
        "- Explore the repository and run its existing tests as you work. Tests covering the\n"
        "  new behaviour are held out and are run after you finish.\n"
        "- Leave your changes in the working tree (no need to commit).\n\n"
        f"## {title}\n\n{body}\n"
    ).rstrip() + "\n"


# --------------------------------------------------------------------------- mining


def _parse_since(since: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SpecValidationError(
            f"import-prs: --since must be an ISO date, got {since!r}"
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _merge_base_commit(
    repo: str, merge_sha: str, fallback: str, gh: GhApi, warn: Callable[[str], None]
) -> str:
    """First parent of the merge commit = the base branch exactly as the PR landed."""
    try:
        commit = gh(f"repos/{repo}/commits/{merge_sha}")
        return str(commit["parents"][0]["sha"])
    except (BenchError, KeyError, IndexError, TypeError) as exc:
        warn(f"merge commit {merge_sha[:10]}: parent lookup failed ({exc}); using PR base.sha")
        return fallback


def mine_instances(
    repo: str,
    since: datetime,
    *,
    limit: int,
    min_test_files: int,
    gh: GhApi = gh_api,
    warn: Callable[[str], None] = lambda _m: None,
) -> list[dict[str, Any]]:
    """Select up to *limit* qualifying merged PRs (newest first), as instance records."""
    owner, _, name = repo.partition("/")
    if not owner or not name or "/" in name:
        raise SpecValidationError(f"import-prs: --repo must be owner/name, got {repo!r}")
    instances: list[dict[str, Any]] = []
    for page in range(1, _MAX_PAGES + 1):
        pulls = gh(
            f"repos/{repo}/pulls?state=closed&sort=updated&direction=desc"
            f"&per_page={_PRS_PER_PAGE}&page={page}"
        )
        if not pulls:
            break
        for pull in pulls:
            merged_at = _ts(pull.get("merged_at"))
            if merged_at is None or merged_at < since or not pull.get("merge_commit_sha"):
                continue
            instance = _instance_for(repo, pull, min_test_files, gh, warn)
            if instance is not None:
                instances.append(instance)
                if len(instances) >= limit:
                    return instances
        last_updated = _ts(pulls[-1].get("updated_at"))
        # Sorted by updated desc, and updated >= merged: nothing older can still qualify.
        if last_updated is not None and last_updated < since:
            break
    return instances


def _instance_for(
    repo: str,
    pull: dict[str, Any],
    min_test_files: int,
    gh: GhApi,
    warn: Callable[[str], None],
) -> dict[str, Any] | None:
    number = int(pull["number"])
    files = gh(f"repos/{repo}/pulls/{number}/files?per_page={_FILES_PER_PAGE}")
    if len(files) >= _FILES_PER_PAGE:
        return None  # listing truncated: PR too large to be a focused task
    changed = [f["filename"] for f in files if f.get("status") != "removed"]
    test_files = sorted(p for p in changed if is_test_path(p))
    source_files = sorted(p for p in changed if p.endswith(".py") and not is_test_path(p))
    if sum(1 for p in test_files if is_test_module(p)) < min_test_files or not source_files:
        return None
    merge_sha = str(pull["merge_commit_sha"])
    pr_base = str((pull.get("base") or {}).get("sha") or "")
    base_commit = _merge_base_commit(repo, merge_sha, pr_base, gh, warn)
    if not base_commit:
        warn(f"PR #{number}: no base commit available; skipped")
        return None
    labels = [str(label.get("name", "")) for label in pull.get("labels") or []]
    owner, _, name = repo.partition("/")
    return {
        "instance_id": f"{owner}__{name}-{number}",
        "repo": repo,
        "pr_number": number,
        "base_commit": base_commit,
        "pr_base_sha": pr_base,
        "merge_commit": merge_sha,
        "title": clean_text(pull.get("title"), limit=300),
        "body": clean_text(pull.get("body")),
        "labels": labels,
        "test_files": test_files,
        "source_files": source_files,
        "merged_at": pull.get("merged_at"),
    }


# ---------------------------------------------------------------------------- output


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SpecValidationError(f"import-prs: cannot read existing {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SpecValidationError(f"import-prs: {path} must contain a JSON object")
    return data


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _task_for(instance: dict[str, Any], install: str) -> dict[str, Any]:
    task_id = _task_id(instance["instance_id"])
    return {
        "id": task_id,
        "category": classify_category(instance["title"], instance.get("labels", [])),
        "instruction": f"{_TASKS_DIRNAME}/{task_id}/{_INSTRUCTION_FILENAME}",
        "source": {"type": "pr_mined", "instance_id": instance["instance_id"]},
        "grader": {"type": "pytest_targeted", "install": install},
        "family": FAMILY,
        "language": "python",
        "tags": [instance["repo"].split("/")[-1], FAMILY],
    }


def import_prs(
    *,
    repo: str,
    since: str,
    out_dir: Path | str,
    limit: int = 20,
    min_test_files: int = 1,
    install: str = DEFAULT_INSTALL,
    suite_id: str = _DEFAULT_SUITE_ID,
    gh: GhApi = gh_api,
    warn: Callable[[str], None] = lambda _m: None,
) -> ImportResult:
    """Mine *repo* and write/extend `<out_dir>/{instances.json,suite.json,tasks/**}`.

    Re-running appends: existing instances/tasks keep their entries (matched by id, new
    data wins), `cutoff_date` becomes the earliest cutoff seen. Output order is by PR
    number so repeated runs over the same data are byte-stable."""
    if limit < 1:
        raise SpecValidationError("import-prs: --limit must be >= 1")
    since_dt = _parse_since(since)
    since_str = since_dt.date().isoformat()
    found = mine_instances(
        repo, since_dt, limit=limit, min_test_files=min_test_files, gh=gh, warn=warn
    )
    out = Path(out_dir)
    instances_path = out / _INSTANCES_FILENAME
    suite_path = out / _SUITE_FILENAME

    instances_doc = _read_json(instances_path) or {}
    by_id = {i["instance_id"]: i for i in instances_doc.get("instances", [])}
    by_id.update({i["instance_id"]: i for i in found})
    merged_instances = sorted(by_id.values(), key=lambda i: (i["repo"], i["pr_number"]))
    cutoff = min(filter(None, [instances_doc.get("cutoff_date"), since_str]))
    _write_json(
        instances_path,
        {"dataset": PR_MINED_DATASET, "cutoff_date": cutoff, "instances": merged_instances},
    )

    suite = _read_json(suite_path) or {
        "version": _SUITE_VERSION,
        "id": suite_id,
        "domain": "software",
        "description": (
            "Fresh-PR-mined brownfield tasks (Tier 2a): repo at the pre-merge commit, PR text as "
            "the instruction, the PR's tests held out. Regenerate/extend via `ao-bench import-prs`."
        ),
        "tier": _SUITE_TIER,
        "defaults": {"timeout_seconds": _SUITE_DEFAULT_TIMEOUT_SECONDS},
        "tasks": [],
    }
    suite["cutoff_date"] = cutoff
    tasks = {t["id"]: t for t in suite.get("tasks", [])}
    new_ids: list[str] = []
    for instance in found:
        task = _task_for(instance, install)
        if task["id"] not in tasks:
            new_ids.append(task["id"])
        tasks[task["id"]] = task
        instruction = out / task["instruction"]
        instruction.parent.mkdir(parents=True, exist_ok=True)
        instruction.write_text(instruction_text(instance["title"], instance["body"]))
    suite["tasks"] = sorted(tasks.values(), key=lambda t: t["id"])
    _write_json(suite_path, suite)
    return ImportResult(suite_path, instances_path, len(found), new_ids)


def import_prs_command(
    repo: str = typer.Option(
        ..., "--repo", help="GitHub repo (owner/name) to mine merged PRs from."
    ),
    since: str = typer.Option(..., "--since", help="ISO date; only PRs merged on/after it."),
    out: str = typer.Option(
        ..., "--out", help="Suite dir to write instances.json/suite.json into."
    ),
    limit: int = typer.Option(20, "--limit", help="Maximum number of PRs to select."),
    min_test_files: int = typer.Option(
        1, "--min-test-files", help="Minimum changed pytest test modules a PR must have."
    ),
    install: str = typer.Option(
        DEFAULT_INSTALL,
        "--install",
        help="Shell command the grader runs once in the repo before testing "
        "(written into each task's grader block).",
    ),
    suite_id: str = typer.Option(
        _DEFAULT_SUITE_ID, "--suite-id", help="Suite id used when creating a new suite.json."
    ),
) -> None:
    """Mine recent merged PRs with test changes into a pr_mined suite (needs `gh`)."""
    try:
        result = import_prs(
            repo=repo,
            since=since,
            out_dir=Path(out),
            limit=limit,
            min_test_files=min_test_files,
            install=install,
            suite_id=suite_id,
            warn=lambda msg: typer.echo(f"WARNING: {msg}", err=True),
        )
    except (SpecValidationError, BenchError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Selected {result.task_count} PR(s) from {repo} ({len(result.new_task_ids)} new):")
    typer.echo(f"  {result.suite_path}")
    typer.echo(f"  {result.instances_path}")
    typer.echo("Review each tasks/*/instruction.md for solution hints before committing.")


__all__ = [
    "DEFAULT_INSTALL",
    "ImportResult",
    "classify_category",
    "clean_text",
    "gh_api",
    "import_prs",
    "import_prs_command",
    "instruction_text",
    "mine_instances",
]
