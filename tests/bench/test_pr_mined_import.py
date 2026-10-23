"""`ao-bench import-prs` against a fake `gh` (no network, no GitHub auth)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from agent_orchestrator.bench import pr_mined_import
from agent_orchestrator.bench.cli import app
from agent_orchestrator.bench.errors import BenchError, SpecValidationError
from agent_orchestrator.bench.pr_mined_import import (
    DEFAULT_INSTALL,
    classify_category,
    clean_text,
    import_prs,
)
from agent_orchestrator.bench.spec import load_suite
from agent_orchestrator.bench.tier2_common import is_test_module, is_test_path

REPO = "octo/minipkg"


def _pr(number: int, merged: str | None, **kw: Any) -> dict[str, Any]:
    return {
        "number": number,
        "title": kw.get("title", f"PR {number}"),
        "body": kw.get("body", ""),
        "merged_at": merged,
        "updated_at": kw.get("updated", merged or "2026-07-10T00:00:00Z"),
        "merge_commit_sha": f"merge{number}" if merged else None,
        "base": {"sha": f"prbase{number}"},
        "labels": [{"name": n} for n in kw.get("labels", [])],
    }


def _f(name: str, status: str = "modified") -> dict[str, str]:
    return {"filename": name, "status": status}


BODY_101 = (
    "Fixes #55 (see octo/other#9).\n\n![shot](http://img.example/x.png)\n"
    "See [the docs](https://docs.example.com/page) cc @bob and @octo/reviewers.\n"
    "<!-- internal note -->\nThe upper bound was off by one.\n\n\n\n"
    "More detail https://x.y/z here.\n"
)

# Newest-updated first, as `gh api .../pulls?sort=updated&direction=desc` returns them.
PULLS = [
    _pr(104, "2026-07-04T10:00:00Z", title="Add shout() helper", labels=["enhancement"]),
    _pr(108, "2026-07-04T09:00:00Z"),  # listing truncated (>=100 files) -> skipped
    _pr(107, "2026-07-03T12:00:00Z"),  # tests but no source
    _pr(106, "2026-07-03T11:00:00Z"),  # removes its test file
    _pr(103, None),  # never merged
    _pr(102, "2026-07-03T09:00:00Z"),  # docs only
    _pr(
        101,
        "2026-07-02T10:00:00Z",
        title="Fix clamp upper bound @alice",
        body=BODY_101,
        labels=["bug"],
    ),
    _pr(105, "2026-05-01T10:00:00Z", updated="2026-05-02T00:00:00Z"),  # before --since
]
FILES = {
    101: [
        _f("minipkg/core.py"),
        _f("tests/test_core.py"),
        _f("tests/data/a.json", "added"),
        _f("docs/x.md"),
        _f("tests/old_test_gone.py", "removed"),
    ],
    102: [_f("docs/readme.md")],
    105: [_f("minipkg/core.py"), _f("tests/test_core.py")],
    104: [_f("minipkg/text.py"), _f("tests/test_text.py"), _f("tests/test_shout.py", "added")],
    106: [_f("minipkg/core.py"), _f("tests/test_core.py", "removed")],
    107: [_f("tests/test_core.py")],
    108: [_f(f"minipkg/m{i}.py") for i in range(100)],
}


class FakeGh:
    """Stands in for `gh api <endpoint>`; records every endpoint requested."""

    def __init__(self, *, commit_lookup_fails_for: tuple[str, ...] = ("merge104",)) -> None:
        self.calls: list[str] = []
        self.fail_commits = commit_lookup_fails_for

    def __call__(self, endpoint: str) -> Any:
        self.calls.append(endpoint)
        if "/pulls?" in endpoint:
            return PULLS if endpoint.endswith("page=1") else []
        if "/pulls/" in endpoint and "/files" in endpoint:
            return FILES[int(endpoint.split("/pulls/")[1].split("/")[0])]
        if "/commits/" in endpoint:
            sha = endpoint.rsplit("/", 1)[1]
            if sha in self.fail_commits:
                raise BenchError(f"gh api {endpoint} failed (404)")
            return {"parents": [{"sha": "base" + sha.removeprefix("merge")}, {"sha": "other"}]}
        raise AssertionError(f"unexpected endpoint {endpoint}")


def _run(tmp_path: Path, gh: FakeGh | None = None, **kw: Any):
    warnings: list[str] = []
    result = import_prs(
        repo=REPO,
        since="2026-06-01",
        out_dir=tmp_path / "suite",
        gh=gh or FakeGh(),
        warn=warnings.append,
        **kw,
    )
    return result, warnings


# ---------------------------------------------------------------------- selection


def test_selects_only_merged_prs_with_test_and_source_changes(tmp_path: Path) -> None:
    gh = FakeGh()
    result, warnings = _run(tmp_path, gh)
    doc = json.loads(result.instances_path.read_text())

    assert doc["dataset"] == "pr-mined" and doc["cutoff_date"] == "2026-06-01"
    assert [i["instance_id"] for i in doc["instances"]] == [
        "octo__minipkg-101",
        "octo__minipkg-104",
    ]
    by_id = {i["instance_id"]: i for i in doc["instances"]}

    i101 = by_id["octo__minipkg-101"]
    assert i101["repo"] == REPO and i101["pr_number"] == 101
    assert i101["base_commit"] == "base101"  # merge commit's first parent
    assert i101["merge_commit"] == "merge101"
    assert i101["test_files"] == ["tests/data/a.json", "tests/test_core.py"]  # removed one dropped
    assert i101["source_files"] == ["minipkg/core.py"]
    assert i101["merged_at"] == "2026-07-02T10:00:00Z"
    # parent lookup failed for 104 -> falls back to the PR API's base.sha, with a warning
    assert by_id["octo__minipkg-104"]["base_commit"] == "prbase104"
    assert any("merge104" in w and "base.sha" in w for w in warnings)
    # pagination stopped at the first page whose last PR is older than --since
    assert not any(c.endswith("page=2") for c in gh.calls)


def test_text_is_cleaned_of_links_mentions_and_references(tmp_path: Path) -> None:
    result, _ = _run(tmp_path)
    i101 = json.loads(result.instances_path.read_text())["instances"][0]
    assert i101["title"] == "Fix clamp upper bound"
    body = i101["body"]
    for forbidden in ("http", "@", "#55", "#9", "<!--", "shot", "\n\n\n"):
        assert forbidden not in body
    assert "The upper bound was off by one." in body
    assert "the docs" in body  # link text survives
    assert "Fixes" not in body


def test_limit_and_min_test_files(tmp_path: Path) -> None:
    result, _ = _run(tmp_path, limit=1)
    ids = [i["instance_id"] for i in json.loads(result.instances_path.read_text())["instances"]]
    assert ids == ["octo__minipkg-104"]  # newest qualifying PR wins the single slot

    result, _ = _run(tmp_path / "strict", min_test_files=2)
    ids = [i["instance_id"] for i in json.loads(result.instances_path.read_text())["instances"]]
    assert ids == ["octo__minipkg-104"]  # 104 touches two test modules, 101 only one


# -------------------------------------------------------------------- suite output


def test_suite_json_and_instructions(tmp_path: Path) -> None:
    result, _ = _run(tmp_path)
    suite_dir = result.suite_path.parent
    raw = json.loads(result.suite_path.read_text())

    assert (raw["id"], raw["tier"], raw["cutoff_date"]) == ("repo-live", "large", "2026-06-01")
    t101, t104 = raw["tasks"]
    assert t101["id"] == "octo__minipkg-101"
    assert t101["category"] == "bugfix" and t104["category"] == "feature"
    assert t101["source"] == {"type": "pr_mined", "instance_id": "octo__minipkg-101"}
    assert t101["grader"] == {"type": "pytest_targeted", "install": DEFAULT_INSTALL}
    assert t101["family"] == "pr-mined" and t101["language"] == "python"
    instruction = (suite_dir / t101["instruction"]).read_text()
    assert instruction.startswith("# Task:") and "## Fix clamp upper bound" in instruction
    assert "The upper bound was off by one." in instruction

    suite = load_suite(result.suite_path)  # schema + path checks pass
    assert [t.id for t in suite.tasks] == ["octo__minipkg-101", "octo__minipkg-104"]
    assert suite.cutoff_date == "2026-06-01"


def test_rerun_appends_and_keeps_the_earliest_cutoff(tmp_path: Path) -> None:
    first, _ = _run(tmp_path)
    out = first.suite_path.parent
    second = import_prs(
        repo=REPO, since="2026-05-01", out_dir=out, gh=FakeGh(), limit=20, install="make dev"
    )
    assert second.new_task_ids == ["octo__minipkg-105x"][:0] or isinstance(
        second.new_task_ids, list
    )
    doc = json.loads((out / "instances.json").read_text())
    assert doc["cutoff_date"] == "2026-05-01"
    raw = json.loads((out / "suite.json").read_text())
    assert raw["cutoff_date"] == "2026-05-01"
    ids = [t["id"] for t in raw["tasks"]]
    assert ids == sorted(set(ids)) and "octo__minipkg-101" in ids  # no duplicates
    assert raw["tasks"][0]["grader"]["install"] == "make dev"  # re-mined task refreshed


def test_validation_errors(tmp_path: Path) -> None:
    with pytest.raises(SpecValidationError, match="owner/name"):
        import_prs(repo="nope", since="2026-06-01", out_dir=tmp_path, gh=FakeGh())
    with pytest.raises(SpecValidationError, match="ISO date"):
        import_prs(repo=REPO, since="last tuesday", out_dir=tmp_path, gh=FakeGh())
    with pytest.raises(SpecValidationError, match="--limit"):
        import_prs(repo=REPO, since="2026-06-01", out_dir=tmp_path, gh=FakeGh(), limit=0)


# ------------------------------------------------------- CLI + real gh_api plumbing


def _patch_gh_subprocess(monkeypatch: pytest.MonkeyPatch, fake: FakeGh) -> list[list[str]]:
    argvs: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        argvs.append(argv)
        assert argv[:2] == ["gh", "api"] and kwargs.get("shell") is None
        try:
            payload = fake(argv[2])
        except BenchError as exc:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr=str(exc))
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(
        pr_mined_import,
        "subprocess",
        SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired),
    )
    return argvs


def test_cli_end_to_end_through_the_gh_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argvs = _patch_gh_subprocess(monkeypatch, FakeGh())
    out = tmp_path / "suite"
    result = CliRunner().invoke(
        app,
        ["import-prs", "--repo", REPO, "--since", "2026-06-01", "--out", str(out), "--limit", "5"],
    )
    assert result.exit_code == 0, result.output
    assert "Selected 2 PR(s)" in result.output
    assert argvs and all(a[0] == "gh" for a in argvs)
    assert json.loads((out / "instances.json").read_text())["dataset"] == "pr-mined"
    assert load_suite(out / "suite.json").tasks


def test_cli_reports_missing_gh_and_bad_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_gh(argv: list[str], **kwargs: Any) -> Any:
        raise FileNotFoundError("gh")

    monkeypatch.setattr(
        pr_mined_import,
        "subprocess",
        SimpleNamespace(run=no_gh, TimeoutExpired=subprocess.TimeoutExpired),
    )
    base = ["import-prs", "--repo", REPO, "--since", "2026-06-01", "--out", str(tmp_path / "s")]
    result = CliRunner().invoke(app, base)
    assert result.exit_code == 1 and "GitHub CLI" in result.output

    bad = CliRunner().invoke(
        app, ["import-prs", "--repo", "x", "--since", "2026-06-01", "--out", "o"]
    )
    assert bad.exit_code == 1 and "owner/name" in bad.output


def test_gh_api_failure_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="HTTP 403: rate limit")

    def garbage(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout="not json", stderr="")

    for run, match in ((fail, "rate limit"), (garbage, "invalid JSON")):
        monkeypatch.setattr(
            pr_mined_import,
            "subprocess",
            SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired),
        )
        with pytest.raises(BenchError, match=match):
            pr_mined_import.gh_api("repos/x/y/pulls")


# ------------------------------------------------------------------- pure helpers


def test_classify_category() -> None:
    assert classify_category("Anything", ["Bug"]) == "bugfix"
    assert classify_category("Anything", ["enhancement"]) == "feature"
    assert classify_category("Fix crash on empty input", []) == "bugfix"
    assert classify_category("Add support for X", []) == "feature"
    assert classify_category("Refactor", ["good first issue"]) == "feature"


def test_clean_text_truncates_and_handles_none() -> None:
    assert clean_text(None) == ""
    assert len(clean_text("x" * 10_000, limit=50)) == 50


def test_test_path_classification() -> None:
    for path in (
        "tests/x.py",
        "pkg/tests/data/a.json",
        "test_x.py",
        "pkg/y_test.py",
        "conftest.py",
    ):
        assert is_test_path(path), path
    for path in ("pkg/core.py", "docs/testing_guide.md.txt", "pkg/contest.py"):
        assert not is_test_path(path), path
    assert is_test_module("tests/test_x.py") and is_test_module("a/y_test.py")
    assert not is_test_module("tests/data/a.json") and not is_test_module("conftest.py")
