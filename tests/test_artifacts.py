"""Tests for LocalFsArtifactStore path resolution and safety guards, and read_manifest."""

from __future__ import annotations

import json

import pytest

from agent_orchestrator.artifacts import LocalFsArtifactStore, read_manifest
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


class TestReadManifest:
    def test_valid_manifest(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        m = tmp_path / "manifest.json"
        m.write_text(json.dumps({"artifacts": ["src/foo.py", "tests/test_foo.py"]}))
        result = read_manifest(store, "manifest.json")
        assert result == ["src/foo.py", "tests/test_foo.py"]

    def test_empty_artifacts_list(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        m = tmp_path / "manifest.json"
        m.write_text(json.dumps({"artifacts": []}))
        assert read_manifest(store, "manifest.json") == []

    def test_missing_file_raises(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        with pytest.raises(ValueError, match="Manifest not found"):
            read_manifest(store, "no_such_manifest.json")

    def test_invalid_json_raises(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "bad.json").write_text("not json {")
        with pytest.raises(ValueError, match="not valid JSON"):
            read_manifest(store, "bad.json")

    def test_missing_artifacts_key_raises(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "m.json").write_text(json.dumps({"files": ["a.py"]}))
        with pytest.raises(ValueError, match='must be {"artifacts"'):
            read_manifest(store, "m.json")

    def test_artifacts_not_a_list_raises(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "m.json").write_text(json.dumps({"artifacts": "not-a-list"}))
        with pytest.raises(ValueError, match='must be {"artifacts"'):
            read_manifest(store, "m.json")
