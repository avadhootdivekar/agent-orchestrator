"""Unit tests for _version.get_version_string's release-vs-dev-build logic.

These mock the module-level BUILD_* constants and the live-git fallback
independently, since real behavior depends on which one is populated: baked
build info (post `uv tool install`, no .git present) vs. a live source tree
(dev/editable run).
"""

from __future__ import annotations

from agent_orchestrator import _version


def test_tagged_and_clean_build_prints_bare_semver(monkeypatch) -> None:
    monkeypatch.setattr(_version, "BUILD_COMMIT", "abc1234")
    monkeypatch.setattr(_version, "BUILD_DIRTY", False)
    monkeypatch.setattr(_version, "BUILD_TAGGED", True)
    monkeypatch.setattr(_version, "BUILD_TIME", "2026-07-10T00:00:00Z")

    result = _version.get_version_string()

    assert result == f"ao {_version.__version__}"


def test_untagged_build_appends_commit_and_build_time(monkeypatch) -> None:
    monkeypatch.setattr(_version, "BUILD_COMMIT", "abc1234")
    monkeypatch.setattr(_version, "BUILD_DIRTY", False)
    monkeypatch.setattr(_version, "BUILD_TAGGED", False)
    monkeypatch.setattr(_version, "BUILD_TIME", "2026-07-10T00:00:00Z")

    result = _version.get_version_string()

    assert result == f"ao {_version.__version__} (abc1234) built 2026-07-10T00:00:00Z"


def test_dirty_tree_appends_dirty_suffix_even_if_tagged(monkeypatch) -> None:
    monkeypatch.setattr(_version, "BUILD_COMMIT", "abc1234")
    monkeypatch.setattr(_version, "BUILD_DIRTY", True)
    monkeypatch.setattr(_version, "BUILD_TAGGED", True)
    monkeypatch.setattr(_version, "BUILD_TIME", "2026-07-10T00:00:00Z")

    result = _version.get_version_string()

    assert result == f"ao {_version.__version__} (abc1234.dirty) built 2026-07-10T00:00:00Z"


def test_no_baked_info_falls_back_to_live_git(monkeypatch) -> None:
    """No _build_info module (dev/editable run) -> introspect the live source tree."""
    monkeypatch.setattr(_version, "BUILD_COMMIT", None)
    monkeypatch.setattr(_version, "BUILD_DIRTY", None)
    monkeypatch.setattr(_version, "BUILD_TAGGED", None)
    monkeypatch.setattr(_version, "BUILD_TIME", None)

    calls: list[tuple[str, ...]] = []

    def fake_git(*args: str) -> str | None:
        calls.append(args)
        if args[0] == "rev-parse":
            return "deadbee"
        if args[0] == "status":
            return ""  # clean tree
        if args[0] == "describe":
            return None  # no tag at HEAD
        raise AssertionError(f"unexpected git args: {args}")

    monkeypatch.setattr(_version, "_git", fake_git)

    result = _version.get_version_string()

    assert result == f"ao {_version.__version__} (deadbee)"
    assert len(calls) == 3


def test_no_baked_info_and_no_git_available_prints_unknown(monkeypatch) -> None:
    monkeypatch.setattr(_version, "BUILD_COMMIT", None)
    monkeypatch.setattr(_version, "BUILD_DIRTY", None)
    monkeypatch.setattr(_version, "BUILD_TAGGED", None)
    monkeypatch.setattr(_version, "BUILD_TIME", None)
    monkeypatch.setattr(_version, "_git", lambda *args: None)

    result = _version.get_version_string()

    assert result == f"ao {_version.__version__} (unknown)"
