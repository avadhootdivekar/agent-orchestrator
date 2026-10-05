"""AC-4..AC-9 (U-T1..T5): entry models, the total parse boundary, canonical_json, ABCs, errors."""

from __future__ import annotations

import ast
import copy
import dataclasses
import inspect
import json
import random
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import agent_orchestrator.cache.types as types_mod
from agent_orchestrator.cache import constants as c
from agent_orchestrator.cache import safeio
from agent_orchestrator.cache.types import (
    CacheAdmin,
    CacheBlobMissingError,
    CacheEntry,
    CacheError,
    CacheIntegrityError,
    CacheKey,
    CacheLayoutError,
    CacheStore,
    CacheTooLargeError,
    CacheUnsafePathError,
    KeySummary,
    PruneReport,
    RestoreMiss,
    ResultCacheHook,
    StoreSkip,
    UncacheableError,
    VerifyReport,
    canonical_json,
    parse_entry_bytes,
)

CORPUS_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "result_cache" / "corpus"
KEY = "6646469e94a695fe1a994d35f54ca74e007262911255552b03c54ce2e5d0319f"
SHA = "9c1f" + "0" * 60
SHA_B = "11d2" + "0" * 60


@pytest.fixture(autouse=True)
def _cache_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AO_CACHE", raising=False)


def hld_example() -> dict[str, Any]:
    """The HLD 8.4.2 example entry with valid 64-hex shas."""
    return {
        "schema": "ao.result-cache.entry/v1",
        "key": KEY,
        "key_schema": 1,
        "created_at": "2026-10-05T10:15:02+00:00",
        "source": {
            "workflow_id": "doc-pipeline",
            "task_id": "summarize",
            "run_id": "doc-pipeline-20261005T101450Z",
            "agent": "writer",
            "ao_version": "0.1.0",
            "cli_version": "2.1.278 (Claude Code)",
        },
        "usage": {
            "cost_usd": 0.4123,
            "input_tokens": 12000,
            "output_tokens": 3400,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 8000,
            "duration_seconds": 95.2,
            "attempts": 1,
            "model": "sonnet",
            "effort": "medium",
            "actuals_available": True,
        },
        "outputs": [
            {"path": "out/summary.md", "kind": "file", "sha256": SHA, "size": 1834, "mode": 420}
        ],
        "key_summary": {
            "key_schema": 1,
            "executor": "claude_cli",
            "model": "sonnet",
            "effort": "medium",
            "max_turns": None,
            "instruction": "specs/instr/summarize.md",
            "inputs": ["docs/notes.md"],
            "dynamic_inputs": [],
            "general_instructions": [".ao/house-rules.md"],
            "outputs": ["out/summary.md"],
            "digests": {
                "specs/instr/summarize.md": SHA_B,
                "docs/notes.md": SHA,
                ".ao/house-rules.md": SHA,
            },
            "repo_heads": {"core": "0123456789abcdef0123456789abcdef01234567"},
            "components": {"agent": "ced58570dab6", "argv": "315cfbdef1cf"},
        },
    }


def mutated(fn: Any) -> dict[str, Any]:
    data = hld_example()
    fn(data)
    return data


# ---------------------------------------------------------------- U-T1: round trip
def test_hld_example_round_trips() -> None:
    entry = CacheEntry.model_validate(hld_example())
    assert entry.key == KEY
    assert entry.outputs[0].kind == c.KIND_FILE == "file"  # the Literal default pins the constant
    dumped = entry.model_dump(mode="json", by_alias=True)
    assert dumped["schema"] == c.ENTRY_SCHEMA and "schema_" not in dumped
    assert CacheEntry.model_validate(dumped) == entry
    assert parse_entry_bytes(entry.to_canonical_bytes(), KEY) == entry


def test_canonical_bytes_parse_back_through_the_boundary() -> None:
    entry = CacheEntry.model_validate(hld_example())
    raw = entry.to_canonical_bytes()
    assert raw.isascii()
    assert parse_entry_bytes(raw, KEY).to_canonical_bytes() == raw


# ---------------------------------------------------------------- U-T2: rejections
REJECTED: dict[str, Any] = {
    "naive_created_at": lambda d: d.__setitem__("created_at", "2026-10-05T10:15:02"),
    "cost_inf": lambda d: d["usage"].__setitem__("cost_usd", float("inf")),
    "cost_nan": lambda d: d["usage"].__setitem__("cost_usd", float("nan")),
    "cost_negative": lambda d: d["usage"].__setitem__("cost_usd", -0.01),
    "cost_too_big": lambda d: d["usage"].__setitem__("cost_usd", c.MAX_ENTRY_COST_USD * 2),
    "duration_inf": lambda d: d["usage"].__setitem__("duration_seconds", float("inf")),
    "mode_over_0777": lambda d: d["outputs"][0].__setitem__("mode", 0o4777),
    "mode_negative": lambda d: d["outputs"][0].__setitem__("mode", -1),
    "size_negative": lambda d: d["outputs"][0].__setitem__("size", -1),
    "outputs_empty": lambda d: d.__setitem__("outputs", []),
    "key_not_hex": lambda d: d.__setitem__("key", "../" + "a" * 61),
    "key_uppercase": lambda d: d.__setitem__("key", KEY.upper()),
    "key_trailing_newline": lambda d: d.__setitem__("key", KEY + "\n"),
    "key_short": lambda d: d.__setitem__("key", KEY[:63]),
    "sha_not_hex": lambda d: d["outputs"][0].__setitem__("sha256", "../escape"),
    "sha_trailing_newline": lambda d: d["outputs"][0].__setitem__("sha256", SHA + "\n"),
    "run_id_300": lambda d: d["source"].__setitem__("run_id", "r" * 300),
    "path_5000_in_inputs": lambda d: d["key_summary"].__setitem__("inputs", ["p" * 5000]),
    "output_path_5000": lambda d: d["outputs"][0].__setitem__("path", "p" * 5000),
    "digest_value_not_hex": lambda d: d["key_summary"]["digests"].__setitem__("x", "nothex"),
    "component_value_300": lambda d: d["key_summary"]["components"].__setitem__("agent", "c" * 300),
    "too_many_outputs": lambda d: d.__setitem__("outputs", d["outputs"] * (c.MAX_LIST_ITEMS + 1)),
    "key_schema_zero": lambda d: d.__setitem__("key_schema", 0),
    "kind_directory": lambda d: d["outputs"][0].__setitem__("kind", "dir"),
    "tokens_too_big": lambda d: d["usage"].__setitem__("input_tokens", c.MAX_ENTRY_TOKENS + 1),
    "missing_source": lambda d: d.pop("source"),
}


@pytest.mark.parametrize("name", sorted(REJECTED))
def test_models_reject(name: str) -> None:
    data = mutated(REJECTED[name])
    with pytest.raises(ValidationError):
        CacheEntry.model_validate(data)


def test_non_finite_floats_rejected_when_constructed_in_python() -> None:
    from agent_orchestrator.cache.types import EntryUsage

    for bad in (float("inf"), float("-inf"), float("nan")):
        with pytest.raises(ValidationError):
            EntryUsage(cost_usd=bad)


def test_aware_non_utc_datetime_accepted() -> None:
    data = mutated(lambda d: d.__setitem__("created_at", "2026-10-05T12:15:02+02:00"))
    assert CacheEntry.model_validate(data).created_at.utcoffset() is not None


# ---------------------------------------------------------------- U-T3/T4: extra, alias, order
def test_unknown_extra_fields_are_ignored_everywhere() -> None:
    data = hld_example()
    data["future_field"] = {"a": 1}
    data["source"]["future"] = 1
    data["usage"]["future"] = 1
    data["outputs"][0]["future"] = 1
    data["key_summary"]["future"] = 1
    entry = CacheEntry.model_validate(data)
    assert "future_field" not in entry.model_dump()
    assert b"future" not in entry.to_canonical_bytes()


def test_dump_by_alias_emits_schema() -> None:
    entry = CacheEntry.model_validate(hld_example())
    assert "schema" in entry.model_dump(by_alias=True)
    assert "schema_" in entry.model_dump()


def test_populate_by_name_accepts_python_field_name() -> None:
    data = hld_example()
    data["schema_"] = data.pop("schema")
    assert CacheEntry.model_validate(data).schema_ == c.ENTRY_SCHEMA


def test_canonical_bytes_identical_for_different_field_orders() -> None:
    a = hld_example()
    b = json.loads(json.dumps(a, sort_keys=True))  # reversed / sorted construction order
    b = {k: b[k] for k in reversed(list(b))}
    b["key_summary"] = {k: b["key_summary"][k] for k in reversed(list(b["key_summary"]))}
    b["usage"] = {k: b["usage"][k] for k in reversed(list(b["usage"]))}
    assert list(a) != list(b)
    assert (
        CacheEntry.model_validate(a).to_canonical_bytes()
        == CacheEntry.model_validate(b).to_canonical_bytes()
    )


# ---------------------------------------------------------------- U-T5 / AC-5: total parse boundary
def _corpus() -> list[dict[str, str]]:
    return json.loads((CORPUS_DIR / "MANIFEST.json").read_text())["entries"]


def test_corpus_manifest_lists_every_file_and_the_required_cases() -> None:
    entries = _corpus()
    on_disk = {p.name for p in CORPUS_DIR.iterdir()} - {"MANIFEST.json"}
    assert {e["file"] for e in entries} == on_disk
    required = {
        "deep_nesting_100000.json",
        "invalid_utf8.json",
        "empty_array.json",
        "schema_only.json",
        "huge_string_field_1mib.json",
        "key_mismatch.json",
    }
    assert required <= on_disk
    assert (CORPUS_DIR / "deep_nesting_100000.json").read_bytes() == b"[" * 100_000
    assert (CORPUS_DIR / "huge_string_field_1mib.json").stat().st_size > 1024 * 1024


@pytest.mark.parametrize("item", _corpus(), ids=lambda e: e["file"])
def test_parse_entry_bytes_raises_only_integrity_error_on_corpus(item: dict[str, str]) -> None:
    raw = (CORPUS_DIR / item["file"]).read_bytes()
    with pytest.raises(CacheIntegrityError) as info:  # and nothing else (RecursionError, ...)
        parse_entry_bytes(raw, item["expected_key"])
    assert info.value.reason == item["reason"]


def test_key_mismatch_is_the_only_non_corrupt_reason_in_the_corpus() -> None:
    reasons = {e["file"]: e["reason"] for e in _corpus()}
    assert reasons["key_mismatch.json"] == c.REASON_KEY_MISMATCH
    assert {r for f, r in reasons.items() if f != "key_mismatch.json"} == {c.REASON_CORRUPT_ENTRY}


def test_parse_entry_bytes_total_over_seeded_garbage() -> None:
    rng = random.Random(20261005)  # fixed seed: deterministic and replayable
    valid = CacheEntry.model_validate(hld_example()).to_canonical_bytes()
    samples: list[bytes] = [
        bytes(rng.randrange(256) for _ in range(rng.randrange(0, 200))) for _ in range(200)
    ]
    for _ in range(200):  # byte-flipped / truncated variants of a valid entry
        mangled = bytearray(valid)
        for _ in range(rng.randrange(1, 5)):
            mangled[rng.randrange(len(mangled))] = rng.randrange(256)
        samples.append(bytes(mangled[: rng.randrange(1, len(mangled) + 1)]))
    for sample in samples:
        try:
            parse_entry_bytes(sample, KEY)
        except CacheIntegrityError:
            pass  # the only permitted failure


def test_parse_entry_bytes_accepts_a_valid_entry_and_checks_the_key() -> None:
    raw = CacheEntry.model_validate(hld_example()).to_canonical_bytes()
    assert parse_entry_bytes(raw, KEY).key == KEY
    with pytest.raises(CacheIntegrityError) as info:
        parse_entry_bytes(raw, "b" * 64)
    assert info.value.reason == c.REASON_KEY_MISMATCH


def test_parse_entry_bytes_error_never_chains_hostile_exception_text() -> None:
    with pytest.raises(CacheIntegrityError) as info:
        parse_entry_bytes(b"[" * 100_000, KEY)
    assert info.value.__cause__ is None and info.value.detail == "RecursionError"


# ---------------------------------------------------------------- AC-6: canonical_json
def test_canonical_json_matches_the_specified_dumps_call() -> None:
    obj = {"b": [1, 2, {"z": "é", "a": None}], "a": 1.5, "c": "x y"}
    expected = json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    assert canonical_json(obj) == expected
    assert canonical_json(obj) == '{"a":1.5,"b":[1,2,{"a":null,"z":"\\u00e9"}],"c":"x y"}'


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_canonical_json_rejects_non_finite(bad: float) -> None:
    with pytest.raises(ValueError):
        canonical_json({"x": bad})


# ---------------------------------------------------------------- AC-7: ABCs and protocol
CACHE_STORE_METHODS = {
    "check",
    "get_entry",
    "put_entry",
    "touch_entry",
    "delete_entry",
    "has_blob",
    "put_blob",
    "read_blob",
    "delete_blob",
    "maybe_enforce_limits",
}
CACHE_ADMIN_METHODS = {"iter_entries", "stats", "prune", "clear", "verify"}


def test_abcs_cannot_be_instantiated_and_have_the_specified_abstract_methods() -> None:
    with pytest.raises(TypeError):
        CacheStore()  # type: ignore[abstract]
    with pytest.raises(TypeError):
        CacheAdmin()  # type: ignore[abstract]
    assert set(CacheStore.__abstractmethods__) == CACHE_STORE_METHODS
    assert set(CacheAdmin.__abstractmethods__) == CACHE_ADMIN_METHODS


def test_cache_admin_verify_has_no_repair_parameter() -> None:
    assert list(inspect.signature(CacheAdmin.verify).parameters) == ["self"]
    assert "repaired" not in {f.name for f in dataclasses.fields(VerifyReport)}


def test_result_cache_hook_is_a_protocol_with_exactly_two_methods() -> None:
    assert getattr(ResultCacheHook, "_is_protocol", False) is True
    members = {n for n, v in vars(ResultCacheHook).items() if callable(v) and not n.startswith("_")}
    assert members == {"lookup", "store_success"}


def test_result_cache_hook_is_satisfied_structurally() -> None:
    class Impl:
        def lookup(self, request: Any, log: Any) -> Any: ...

        def store_success(self, pending: Any, *, ts: Any, now: Any, log: Any) -> Any: ...

    hook: ResultCacheHook = Impl()  # accepted by the type checker without subclassing
    assert hook is not None


# ---------------------------------------------------------------- AC-8: no dependency on models
def test_import_has_no_runtime_dependency_on_models_or_engine() -> None:
    code = textwrap.dedent(
        """
        import sys
        import agent_orchestrator.cache.types as t  # noqa: F401
        banned = [m for m in sys.modules if m in {
            "agent_orchestrator.models", "agent_orchestrator.engine",
            "agent_orchestrator.runstate", "agent_orchestrator.cli",
            "agent_orchestrator.artifacts"} or m.startswith("agent_orchestrator.ui")]
        assert not banned, banned
        """
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr


def test_import_succeeds_when_models_has_no_result_cache_record() -> None:
    code = textwrap.dedent(
        """
        import agent_orchestrator.models as m
        if hasattr(m, "ResultCacheRecord"):
            del m.ResultCacheRecord  # simulate the tree before T-28J9oR lands
        import agent_orchestrator.cache.types as t
        assert hasattr(t, "LookupOutcome")
        """
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr


def test_models_are_only_imported_under_type_checking() -> None:
    tree = ast.parse(Path(types_mod.__file__).read_text())
    guarded: set[int] = set()
    for stmt in tree.body:
        if isinstance(stmt, ast.If) and "TYPE_CHECKING" in ast.unparse(stmt.test):
            guarded.update(id(n) for n in ast.walk(stmt))
    assert tree.body[0].__class__ is ast.Expr  # docstring
    future = [n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "__future__"]
    assert future and future[0].names[0].name == "annotations"
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {
            "agent_orchestrator.models",
            "agent_orchestrator.artifacts",
        }:
            assert id(node) in guarded, f"{node.module} imported outside TYPE_CHECKING"


# ---------------------------------------------------------------- layering (HLD 8.0)
def _imports(module: Any) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_layering_rules() -> None:
    forbidden = ("engine", "runstate", "cli", "ui")
    for mod in (c, safeio, types_mod):
        for name in _imports(mod):
            parts = name.split(".")
            assert not (
                parts[0] == "agent_orchestrator" and len(parts) > 1 and parts[1] in forbidden
            ), (mod.__name__, name)
    assert not any(n.startswith("agent_orchestrator") for n in _imports(c))
    assert {n for n in _imports(safeio) if n.startswith("agent_orchestrator")} == {
        "agent_orchestrator.cache.constants"
    }


# ---------------------------------------------------------------- AC-9: errors
def test_every_error_exposes_reason_and_clips_detail() -> None:
    for cls in (
        CacheError,
        types_mod.CacheIntegrityError,
        CacheUnsafePathError,
        CacheTooLargeError,
        CacheLayoutError,
    ):
        err = cls(c.REASON_CORRUPT_ENTRY, "x" * 1000)
        assert err.reason == c.REASON_CORRUPT_ENTRY
        assert len(err.detail) == c.MAX_TEXT_CHARS
    assert CacheIntegrityError(c.REASON_CORRUPT_ENTRY).detail == ""


def test_blob_missing_error_reason_and_sha() -> None:
    err = CacheBlobMissingError(SHA)
    assert err.reason == c.REASON_BLOB_MISSING and err.detail == SHA
    assert isinstance(err, CacheError)


def test_uncacheable_and_store_skip_expose_reason_and_detail() -> None:
    for cls in (UncacheableError, StoreSkip):
        err = cls(c.REASON_INPUT_MISSING, "docs/x.md")
        assert (err.reason, err.detail) == (c.REASON_INPUT_MISSING, "docs/x.md")
        assert cls(c.REASON_INPUT_MISSING).detail == ""
        assert len(cls(c.REASON_INPUT_MISSING, "d" * 999).detail) == c.MAX_TEXT_CHARS
        assert not isinstance(err, CacheError)


def test_restore_miss_attributes() -> None:
    miss = RestoreMiss(c.REASON_BLOB_CORRUPT, True, SHA, "size mismatch")
    assert (miss.reason, miss.evict, miss.blob, miss.detail) == (
        c.REASON_BLOB_CORRUPT,
        True,
        SHA,
        "size mismatch",
    )
    bare = RestoreMiss(c.REASON_RESTORE_FAILED)
    assert (bare.evict, bare.blob, bare.detail) == (False, None, "")
    kw = RestoreMiss(reason=c.REASON_MANIFEST_MISMATCH, evict=True, blob=None, detail="d")
    assert kw.evict is True


def test_unsafe_path_is_a_cache_error_but_not_an_integrity_error() -> None:
    assert issubclass(CacheUnsafePathError, CacheError)
    assert not issubclass(CacheUnsafePathError, CacheIntegrityError)
    for cls in (CacheIntegrityError, CacheBlobMissingError, CacheTooLargeError, CacheLayoutError):
        assert issubclass(cls, CacheError)


# ---------------------------------------------------------------- dataclass shapes
def test_prune_report_defaults_deferred_and_total_removed() -> None:
    assert PruneReport().total_removed == 0 and PruneReport().deferred is False
    assert PruneReport(deferred=True).deferred is True
    report = PruneReport(removed_entries={c.REASON_EXPIRED: 2, c.REASON_EVICT_LRU: 3})
    assert report.total_removed == 5
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.dry_run = True  # type: ignore[misc]


def test_cache_key_has_components_and_cli_version() -> None:
    names = {f.name for f in dataclasses.fields(CacheKey)}
    assert {"key", "summary", "output_paths", "output_abs", "preseed"} <= names
    assert {"components", "cli_version"} <= names


def test_key_summary_defaults_are_independent_instances() -> None:
    a = KeySummary(key_schema=1, executor="fake", instruction="i.md")
    b = KeySummary(key_schema=1, executor="fake", instruction="i.md")
    a.inputs.append("x")
    assert b.inputs == [] and copy.copy(a.digests) == {}
