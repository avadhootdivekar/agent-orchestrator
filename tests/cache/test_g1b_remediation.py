"""G1b remediation (E-Rc4Hk8): S-2 hostile `ao status` block (end to end), S-1 restore-temp name
predicate, and the reviewer S-3 / S-4 refactors (one clip helper, one resolve helper, `ttl_days`
typed `int | None`)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.cache import coordinator, fingerprint, records, safeio, types
from agent_orchestrator.cache.constants import MAX_TEXT_CHARS, RESTORE_TMP_PREFIX
from agent_orchestrator.cache.settings import ResultCacheSettings
from agent_orchestrator.cache.store import LocalFsCacheStore
from agent_orchestrator.cli import app
from tests.cache.test_report import GOOD_BLOCK, HOSTILE_BLOCKS

runner = CliRunner()

ESCAPE = "\x1b]0;PWNED\x07\x1b[2J"
# `json.dumps` itself refuses the 5000-digit integer; it gets its own raw-text test below.
JSON_BLOCKS = [b for b in HOSTILE_BLOCKS if not (isinstance(b, dict) and 10**5000 in b.values())]


def _write_status(ws: Path, run_id: str, block: object) -> None:
    run_dir = ws / ".orchestrator" / "runs" / run_id
    run_dir.mkdir(parents=True)
    snap = {"run_id": run_id, "status": "succeeded", "tasks": [], "result_cache": block}
    (run_dir / "status.json").write_text(json.dumps(snap), encoding="utf-8")


class TestAoStatusWithHostileBlock:
    """SEC G1b S-2: `ao status` reads an agent-writable `status.json`; it must never raise."""

    @pytest.mark.parametrize("block", JSON_BLOCKS, ids=range(len(JSON_BLOCKS)))
    def test_exit_zero_no_summary_line_and_no_escape_echoed(
        self, tmp_path: Path, block: object
    ) -> None:
        _write_status(tmp_path, "r1", block)
        res = runner.invoke(app, ["status", "--run-id", "r1", "--workspace", str(tmp_path)])
        assert res.exit_code == 0, res.output
        assert "Result cache:" not in res.stdout
        assert "\x1b" not in res.stdout and "PWNED" not in res.stdout
        assert "Run:          r1" in res.stdout  # the rest of the snapshot still prints

    def test_an_integer_beyond_the_json_digit_limit_does_not_crash(self, tmp_path: Path) -> None:
        run_dir = tmp_path / ".orchestrator" / "runs" / "r4"
        run_dir.mkdir(parents=True)
        raw = (
            '{"run_id": "r4", "status": "succeeded", "result_cache": {"hits": ' + "9" * 5000 + "}}"
        )
        (run_dir / "status.json").write_text(raw, encoding="utf-8")
        res = runner.invoke(app, ["status", "--run-id", "r4", "--workspace", str(tmp_path)])
        # The file is unparsable, so `ao status` falls back to state.json (absent here): a clean
        # error exit, never an uncaught ValueError traceback.
        assert res.exit_code == 1 and isinstance(res.exception, SystemExit), res.output
        assert "Run state not found" in res.output
        assert "Result cache:" not in res.stdout

    def test_a_well_formed_block_still_prints_the_line(self, tmp_path: Path) -> None:
        _write_status(tmp_path, "r2", GOOD_BLOCK)
        res = runner.invoke(app, ["status", "--run-id", "r2", "--workspace", str(tmp_path)])
        assert res.exit_code == 0, res.output
        assert (
            "Result cache: hits=1 (saved ~$0.1000 est., ~2 tokens, ~3s) "
            "would_hits=0 misses=0 stored=0 ineligible=0"
        ) in res.stdout

    def test_control_characters_are_stripped_at_the_echo_boundary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Defence in depth: even if the formatter ever let one through, the echo strips it."""
        from agent_orchestrator.cache import report

        monkeypatch.setattr(report, "format_summary_line", lambda block: f"Result cache: {ESCAPE}")
        _write_status(tmp_path, "r3", GOOD_BLOCK)
        res = runner.invoke(app, ["status", "--run-id", "r3", "--workspace", str(tmp_path)])
        assert res.exit_code == 0, res.output
        assert "\x1b" not in res.stdout and "\x07" not in res.stdout
        assert "Result cache: ]0;PWNED[2J" in res.stdout


class TestRestoreTmpNamePredicate:
    """SEC G1b S-1: the one definition of 'a restore staging name'."""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            (f"{RESTORE_TMP_PREFIX}1-2-abc.tmp", True),
            (f"{RESTORE_TMP_PREFIX}1-2-abc.tmp.bak", True),
            (f"{RESTORE_TMP_PREFIX}notes.md", False),
            (f"{RESTORE_TMP_PREFIX}1-2-abc.bak", False),
            ("1-2-abc.tmp", False),
            ("report.tmp", False),
        ],
    )
    def test_matches_exactly_the_staging_and_backup_names(self, name: str, expected: bool) -> None:
        assert safeio.is_restore_tmp_name(name) is expected


class TestSharedHelpers:
    """Reviewer S-3: one clip helper and one resolve helper, no module-private copies."""

    def test_clip_text_clips_and_passes_none(self) -> None:
        assert types.clip_text(None) is None
        assert types.clip_text("x" * (MAX_TEXT_CHARS + 5)) == "x" * MAX_TEXT_CHARS
        assert types.clip_text("short") == "short"

    def test_no_module_keeps_its_own_copy(self) -> None:
        for mod in (types, records, coordinator):
            assert not hasattr(mod, "_clip") and not hasattr(mod, "_text")
        assert not hasattr(coordinator, "_try_resolve")
        assert not hasattr(coordinator, "_RESOLVE_FAILURES")
        assert not hasattr(fingerprint, "_RESOLVE_FAILURES")
        assert coordinator.try_resolve is fingerprint.try_resolve
        assert coordinator.clip_text is types.clip_text is records.clip_text

    def test_try_resolve_returns_the_path_or_none(self, tmp_path: Path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        assert fingerprint.try_resolve(store, "a/b.md") == str(tmp_path.resolve() / "a" / "b.md")
        for hostile in ("../x", "/etc/passwd", "a\0b"):
            assert fingerprint.try_resolve(store, hostile) is None


class TestTtlNever:
    """Reviewer S-4: `ttl_days=None` ("never expire") is a first-class store argument."""

    def test_for_workspace_accepts_none_and_never_expires(self, tmp_path: Path) -> None:
        store = LocalFsCacheStore.for_workspace(str(tmp_path), max_bytes=1 << 20, ttl_days=None)
        assert store.ttl_days is None

    def test_from_settings_passes_none_through_without_a_cast(self, tmp_path: Path) -> None:
        settings = ResultCacheSettings(
            mode="on",
            source="cli",
            max_bytes=1 << 20,
            max_entry_bytes=1 << 10,
            ttl_days=None,
            include_repo_heads=True,
            max_input_bytes=1 << 20,
            max_input_files=100,
        )
        rc = coordinator.ResultCache.from_settings(workspace_root=tmp_path, settings=settings)
        assert rc._ttl is None
        assert 'cast("int"' not in Path(coordinator.__file__).read_text(encoding="utf-8")
