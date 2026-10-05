"""AC-1 / AC-2: constants.py completeness, vocabularies, regexes and leaf-module property."""

from __future__ import annotations

import ast
import inspect

import pytest

from agent_orchestrator.cache import constants as c


@pytest.fixture(autouse=True)
def _cache_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AO_CACHE", raising=False)


# Copied from the HLD reason tables: 8.3.3 (ineligibility), 8.6.4 (miss and store-skip lists)
# and 15 (evict reasons). A reason missing on either side fails the comparison below.
HLD_REASONS = frozenset(
    {
        # 8.3.3
        "not_opted_in",
        "run_integration_active",
        "unknown_task_field",
        "unknown_agent_field",
        "unknown_workflow_field",
        "emit_tasks",
        "task_manifest_path",
        "output_manifest",
        "pre_hook",
        "post_hook",
        "isolation_worktree",
        "router_task",
        "loop_member",
        "no_outputs",
        "agent_unknown",
        "executor_not_cacheable",
        "command_not_cacheable",
        "model_unresolved",
        "verdict_sidecar_undeclared",
        "breaker_verdict_source",
        "artifact_store_unsupported",
        "path_rejected",
        "path_in_cache_dir",
        "duplicate_output",
        "sensitive_output",
        "control_output",
        "input_missing",
        "input_not_regular",
        "input_unstable",
        "input_too_large",
        "input_unreadable",
        "output_not_regular_file",
        "repo_head_unavailable",
        "executor_fingerprint_unavailable",
        "prompt_render_error",
        "key_encoding",
        # 8.6.4 miss reasons
        "not_found",
        "expired",
        "corrupt_entry",
        "key_mismatch",
        "blob_missing",
        "manifest_mismatch",
        "blob_corrupt",
        "restore_failed",
        "unsafe_path",
        "store_unavailable",
        "store_error",
        # 8.6.4 store-skip reasons
        "repo_head_moved",
        "key_changed_during_run",
        "repo_worktree_changed",
        "repo_worktree_probe_failed",
        "output_missing",
        "entry_too_large",
        "cache_disabled",
        # 15 evict reasons not listed above
        "lru",
        "invalid",
        "deferred",
    }
)

HLD_EVENTS = frozenset(
    {
        "cache.hit",
        "cache.would_hit",
        "cache.miss",
        "cache.skip",
        "cache.store",
        "cache.evict",
        "cache.corrupt",
        "cache.disabled",
    }
)


def _constants(prefix: str) -> dict[str, str]:
    return {n: v for n, v in vars(c).items() if n.startswith(prefix) and isinstance(v, str)}


def test_reason_values_match_hld_tables_both_ways() -> None:
    values = set(_constants("REASON_").values())
    assert values - HLD_REASONS == set(), "REASON_* not in the HLD tables"
    assert HLD_REASONS - values == set(), "HLD reason without a REASON_* constant"


def test_reason_values_are_unique_and_bounded() -> None:
    consts = _constants("REASON_")
    assert len(set(consts.values())) == len(consts)
    assert all(0 < len(v) <= c.MAX_REASON_CHARS for v in consts.values())


def test_mandatory_reasons_present() -> None:
    assert c.REASON_UNKNOWN_AGENT_FIELD == "unknown_agent_field"
    assert c.REASON_UNSAFE_PATH == "unsafe_path"


def test_event_values_equal_the_eight_hld_events() -> None:
    assert set(_constants("EVENT_").values()) == HLD_EVENTS
    assert len(_constants("EVENT_")) == 8


def test_modes_and_no_refresh() -> None:
    assert (c.MODE_OFF, c.MODE_ON, c.MODE_SHADOW) == ("off", "on", "shadow")
    assert not hasattr(c, "MODE_REFRESH")


def test_default_task_cache_policy_is_false() -> None:
    assert c.DEFAULT_TASK_CACHE_POLICY is False


def test_agent_field_sets_are_disjoint_and_complete_names() -> None:
    assert c.AGENT_KEY_FIELDS.isdisjoint(c.AGENT_NON_KEY_FIELDS)
    assert c.AGENT_NON_KEY_FIELDS == {"forbidden_task_models"}


def test_misc_pinned_values() -> None:
    assert c.INLINE_PRUNE_MAX_ENTRIES == 5000
    assert c.INLINE_PRUNE_MAX_ENTRY_FILE_BYTES == 64 * 1024**2
    assert (c.GIT_OPTIONAL_LOCKS_VAR, c.GIT_OPTIONAL_LOCKS_OFF) == ("GIT_OPTIONAL_LOCKS", "0")


def test_module_imports_only_re() -> None:
    tree = ast.parse(inspect.getsource(c))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert imported == ["re"]


@pytest.mark.parametrize("good", ["a" * 64, "0123456789abcdef" * 4])
def test_sha256_regex_accepts(good: str) -> None:
    assert c.SHA256_HEX_RE.fullmatch(good)


@pytest.mark.parametrize(
    "bad",
    ["A" * 64, "a" * 63, "a" * 65, "../x", "a" * 63 + "\x00", "a" * 64 + "\n", "", "g" * 64],
)
def test_sha256_regex_rejects(bad: str) -> None:
    assert c.SHA256_HEX_RE.fullmatch(bad) is None


@pytest.mark.parametrize("n", [4, 5, 32, 63, 64])
def test_key_prefix_accepts_4_to_64(n: int) -> None:
    assert c.KEY_PREFIX_RE.fullmatch("f" * n)


@pytest.mark.parametrize("bad", ["abc", "f" * 65, "ABCD", "abcg", "abcd\n", ""])
def test_key_prefix_rejects(bad: str) -> None:
    assert c.KEY_PREFIX_RE.fullmatch(bad) is None


def test_hex64_token_finds_every_token_in_unparseable_bytes() -> None:
    a, b = "a" * 64, "0123456789abcdef" * 4
    raw = b"\xff\xfe{not json " + a.encode() + b"\x00garbage-" + b.encode() + b"]]]["
    assert [m.group() for m in c.HEX64_TOKEN_RE_BYTES.finditer(raw)] == [a.encode(), b.encode()]
