"""Tests for LocalFsArtifactStore path resolution and safety guards, and read_manifest."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_orchestrator.artifacts import (
    LocalFsArtifactStore,
    read_bool_field,
    read_control,
    read_manifest,
    read_routes,
)
from agent_orchestrator.errors import ArtifactPathError, ControlFileError, GateError
from agent_orchestrator.models import MAX_CONTROL_FILE_BYTES


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


class TestReadControl:
    """T-k9r3n8: the shared bounded-JSON control-file reader (AC1, AC5)."""

    def test_valid_control_file(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "control.json").write_text(json.dumps({"routes": ["a", "b"], "halt": False}))
        assert read_control(store, "control.json") == {"routes": ["a", "b"], "halt": False}

    def test_missing_file_raises_naming_path(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        with pytest.raises(ControlFileError, match="not found") as exc_info:
            read_control(store, "no-such-control.json")
        assert "no-such-control.json" in str(exc_info.value)

    def test_invalid_json_raises_naming_path(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "bad.json").write_text("{not valid")
        with pytest.raises(ControlFileError, match="not valid JSON") as exc_info:
            read_control(store, "bad.json")
        assert "bad.json" in str(exc_info.value)

    def test_non_object_root_raises_naming_path(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "array.json").write_text(json.dumps(["a", "b"]))
        with pytest.raises(ControlFileError, match="JSON object") as exc_info:
            read_control(store, "array.json")
        assert "array.json" in str(exc_info.value)

    def test_oversized_file_rejected_by_size_check_before_any_read(
        self, tmp_path, monkeypatch
    ) -> None:
        """AC5: a file reported as MAX_CONTROL_FILE_BYTES + 1 by store.size() must be rejected
        via the size guard alone — content is never opened/parsed. Proven two ways: (1) the
        actual on-disk content is deliberately invalid JSON, so a content-read attempt would
        surface a different error; (2) Path.read_text is patched to raise if ever called."""
        store = LocalFsArtifactStore(str(tmp_path))
        f = tmp_path / "big.json"
        f.write_text("{not valid json")  # would blow up if ever parsed
        monkeypatch.setattr(store, "size", lambda path: MAX_CONTROL_FILE_BYTES + 1)

        def _must_not_be_called(*args: object, **kwargs: object) -> str:
            raise AssertionError("read_control must not read file content once size exceeds cap")

        monkeypatch.setattr(Path, "read_text", _must_not_be_called)
        with pytest.raises(ControlFileError, match=f"exceeds {MAX_CONTROL_FILE_BYTES}"):
            read_control(store, "big.json")

    def test_size_exactly_at_cap_is_allowed(self, tmp_path, monkeypatch) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "ok.json").write_text(json.dumps({"continue": True}))
        monkeypatch.setattr(store, "size", lambda path: MAX_CONTROL_FILE_BYTES)
        assert read_control(store, "ok.json") == {"continue": True}

    def test_missing_file_checked_before_size(self, tmp_path, monkeypatch) -> None:
        """Risk note: store.size() returns 0 for a missing path, so "missing" must win over
        "too big" — assert the missing-file message surfaces even if size() were (incorrectly)
        consulted first by some future refactor returning a huge value."""
        store = LocalFsArtifactStore(str(tmp_path))
        monkeypatch.setattr(store, "size", lambda path: MAX_CONTROL_FILE_BYTES + 1)
        with pytest.raises(ControlFileError, match="not found"):
            read_control(store, "never-existed.json")


class TestReadBoolField:
    """T-k9r3n8 AC2: typed bool helper used by loop gates and verdict breakers."""

    def test_returns_true(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "v.json").write_text(json.dumps({"halt": True}))
        assert read_bool_field(store, "v.json", "halt") is True

    def test_returns_false(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "v.json").write_text(json.dumps({"halt": False}))
        assert read_bool_field(store, "v.json", "halt") is False

    def test_missing_field_raises(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "v.json").write_text(json.dumps({"other": True}))
        with pytest.raises(ControlFileError, match="missing field"):
            read_bool_field(store, "v.json", "halt")

    def test_non_bool_value_raises(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "v.json").write_text(json.dumps({"halt": "yes"}))
        with pytest.raises(ControlFileError, match="must be bool"):
            read_bool_field(store, "v.json", "halt")


class TestReadRoutes:
    """T-k9r3n8 AC2: typed list[str] helper used by the router (downstream: T-m2h5t7)."""

    def test_returns_routes_default_field(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "r.json").write_text(json.dumps({"routes": ["fast", "slow"]}))
        assert read_routes(store, "r.json") == ["fast", "slow"]

    def test_returns_routes_custom_field(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "r.json").write_text(json.dumps({"chosen": ["only-one"]}))
        assert read_routes(store, "r.json", field="chosen") == ["only-one"]

    def test_empty_list_is_valid(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "r.json").write_text(json.dumps({"routes": []}))
        assert read_routes(store, "r.json") == []

    def test_missing_field_raises(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "r.json").write_text(json.dumps({"other": ["a"]}))
        with pytest.raises(ControlFileError, match="missing field"):
            read_routes(store, "r.json")

    def test_non_list_raises(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "r.json").write_text(json.dumps({"routes": "fast"}))
        with pytest.raises(ControlFileError, match="non-empty strings"):
            read_routes(store, "r.json")

    def test_list_with_empty_string_raises(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "r.json").write_text(json.dumps({"routes": ["fast", ""]}))
        with pytest.raises(ControlFileError, match="non-empty strings"):
            read_routes(store, "r.json")

    def test_list_with_non_string_raises(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "r.json").write_text(json.dumps({"routes": [1, 2]}))
        with pytest.raises(ControlFileError, match="non-empty strings"):
            read_routes(store, "r.json")


class TestControlFileErrorHierarchy:
    """T-k9r3n8 AC4: GateError <: ControlFileError, so router/breaker code catching the base
    class and the engine's existing `except GateError` around loop gates both keep working."""

    def test_gate_error_is_a_control_file_error(self) -> None:
        assert issubclass(GateError, ControlFileError)
        assert isinstance(GateError("boom"), ControlFileError)

    def test_read_gate_translates_control_file_error_to_gate_error(
        self, tmp_path, monkeypatch
    ) -> None:
        """read_gate is a thin alias over read_bool_field/read_control; this proves the new
        size guard (previously absent from the loop-gate reader) is now enforced for read_gate
        too, and that the resulting ControlFileError is translated back to GateError so
        engine.py's `except GateError` around the loop-gate call still catches it (AC3, AC4)."""
        from agent_orchestrator.artifacts import read_gate

        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "gate.json").write_text(json.dumps({"continue": True}))
        monkeypatch.setattr(store, "size", lambda path: MAX_CONTROL_FILE_BYTES + 1)
        with pytest.raises(GateError, match=f"exceeds {MAX_CONTROL_FILE_BYTES}"):
            read_gate(store, "gate.json")
