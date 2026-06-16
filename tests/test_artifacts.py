"""Tests for LocalFsArtifactStore path resolution and safety guards."""

from __future__ import annotations

import pytest

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.errors import ArtifactPathError


class TestLocalFsArtifactStore:
    def test_relative_resolve(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        result = store.resolve("output/x.md")
        assert result == str(tmp_path / "output" / "x.md")

    def test_absolute_path_within_root(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        inner = str(tmp_path / "sub" / "file.txt")
        result = store.resolve(inner)
        assert result == inner

    def test_path_traversal_relative_raises(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        with pytest.raises(ArtifactPathError) as exc_info:
            store.resolve("../secret")
        assert exc_info.value.path == "../secret"

    def test_path_traversal_absolute_outside_raises(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        with pytest.raises(ArtifactPathError):
            store.resolve("/etc/passwd")

    def test_exists_true_when_file_present(self, tmp_path) -> None:
        f = tmp_path / "exists.txt"
        f.write_text("hello")
        store = LocalFsArtifactStore(str(tmp_path))
        assert store.exists("exists.txt") is True

    def test_exists_false_when_file_missing(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        assert store.exists("no_such_file.txt") is False

    def test_exists_false_on_path_traversal(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        # Should not raise, just return False
        assert store.exists("../escape.txt") is False

    def test_resolve_root_itself(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        result = store.resolve(".")
        assert result == str(tmp_path)

    def test_nested_relative_path(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        result = store.resolve("a/b/c/d.txt")
        assert result == str(tmp_path / "a" / "b" / "c" / "d.txt")
