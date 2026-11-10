"""T-Fw8Gp4: file-watch digest store, bounded scanner, FileWatchSource, engine integration.
Fixed clocks, temp dirs; the engine is driven through the same fixtures as test_schedule_engine."""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from pathlib import Path

import pytest

from agent_orchestrator.schedules.models import WatchSpec
from agent_orchestrator.service.fire_store import ScheduleState
from agent_orchestrator.service.schedule_engine import Candidate
from agent_orchestrator.service.watch import (
    DEFAULT_WATCH_IGNORES,
    WATCH_SCAN_BUDGET_SECONDS,
    FileWatchSource,
    WatchDigestStore,
    changed_paths,
    scan,
)

from .test_schedule_engine import T0, binding, env_for

EPOCH = int(T0.timestamp())


def touch(path: Path, mtime: int = EPOCH, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    os.utime(path, (mtime, mtime))
    return path


def spec(*paths: str, debounce: int = 30, on: list[str] | None = None) -> WatchSpec:
    kw = {"on": on} if on else {}
    return WatchSpec(paths=list(paths), debounce_seconds=debounce, **kw)  # type: ignore[arg-type]


def mono_stub(steps: list[float]) -> object:
    it = iter(steps)
    last = [0.0]

    def f() -> float:
        last[0] = next(it, last[0])
        return last[0]

    return f


# -- store ---------------------------------------------------------------------------------------


def test_digest_store_roundtrip_and_layout(tmp_path: Path) -> None:
    st = WatchDigestStore(tmp_path)
    assert st.load("slug", "s") == {}
    st.save("slug", "s", {"a.md": [1, 2]})
    assert (tmp_path / "schedules/watch/slug/s.json").is_file()
    assert st.load("slug", "s") == {"a.md": [1, 2]}


def test_digest_store_corrupt_starts_clean_and_rejects_unsafe_ids(tmp_path: Path) -> None:
    st = WatchDigestStore(tmp_path)
    p = st.path_for("slug", "s")
    p.parent.mkdir(parents=True)
    p.write_text("{nope")
    assert st.load("slug", "s") == {}
    for bad in ("../x", "a/b", "", ".."):
        with pytest.raises(ValueError):
            st.path_for("slug", bad)


# -- scan ----------------------------------------------------------------------------------------


def test_scan_globs_and_ignores(tmp_path: Path) -> None:
    touch(tmp_path / "epics/a/prompt.md")
    touch(tmp_path / "epics/b/prompt.md")
    touch(tmp_path / "epics/b/other.txt")
    touch(tmp_path / "specs/x/y/z.json")
    touch(tmp_path / "node_modules/p/prompt.md")
    touch(tmp_path / "epics/.git/prompt.md")
    digest, trunc = scan(tmp_path, spec("epics/*/prompt.md", "specs/**/*.json", "**/prompt.md"))
    assert not trunc
    assert set(digest) == {"epics/a/prompt.md", "epics/b/prompt.md", "specs/x/y/z.json"}
    assert digest["specs/x/y/z.json"] == [EPOCH * 10**9, 1]
    assert {".git", "node_modules", ".venv", "__pycache__", ".orchestrator"} <= (
        DEFAULT_WATCH_IGNORES
    )


def test_scan_never_reaches_run_output_tree(tmp_path: Path) -> None:
    touch(tmp_path / ".orchestrator/runs/r1/out.md")
    touch(tmp_path / "keep.md")
    digest, _ = scan(tmp_path, spec("**/*.md", ".orchestrator/**/*.md"))
    assert set(digest) == {"keep.md"}


def test_scan_max_files_truncates(tmp_path: Path) -> None:
    for i in range(5):
        touch(tmp_path / f"f{i}.md")
    digest, trunc = scan(tmp_path, spec("*.md"), max_files=3)
    assert trunc and len(digest) == 3
    assert scan(tmp_path, spec("*.md"), max_files=5)[1] is False


def test_scan_budget_truncates_with_jumping_clock(tmp_path: Path) -> None:
    for i in range(3):
        touch(tmp_path / f"f{i}.md")
    clock = mono_stub([0.0, 0.0, WATCH_SCAN_BUDGET_SECONDS + 1])
    _, trunc = scan(tmp_path, spec("*.md"), monotonic=clock)  # type: ignore[arg-type]
    assert trunc


def test_scan_rejects_escaping_patterns(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    ws = tmp_path / "ws"
    touch(ws / "in.md")
    touch(tmp_path / "secret.md")
    w = WatchSpec.model_construct(paths=["../*.md", "/etc/*", "sub/../../*.md"], on=["created"])
    with caplog.at_level(logging.WARNING):
        digest, trunc = scan(ws, w)
    assert digest == {} and not trunc
    assert caplog.text.count("escapes the workspace root") == 3


def test_scan_skips_symlinks_outside_root(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    ws = tmp_path / "ws"
    outside = touch(tmp_path / "outside.md")
    outdir = tmp_path / "outdir"
    touch(outdir / "deep.md")
    touch(ws / "real.md")
    (ws / "link.md").symlink_to(outside)
    (ws / "dirlink").symlink_to(outdir, target_is_directory=True)
    (ws / "inlink.md").symlink_to(ws / "real.md")
    (ws / "dangling.md").symlink_to(tmp_path / "nope")
    with caplog.at_level(logging.WARNING):
        digest, _ = scan(ws, spec("**/*.md"))
    assert set(digest) == {"real.md", "inlink.md"}  # in-root file symlink resolves; dir links never
    assert "link.md" in caplog.text and "dirlink" in caplog.text


def test_scan_symlink_into_ignored_dir_skipped(tmp_path: Path) -> None:
    touch(tmp_path / ".orchestrator/o.md")
    (tmp_path / "sneaky.md").symlink_to(tmp_path / ".orchestrator/o.md")
    assert scan(tmp_path, spec("*.md"))[0] == {}


def test_changed_paths_on_filter() -> None:
    prev = {"a": [1, 1], "b": [1, 1]}
    cur = {"a": [2, 1], "b": [1, 1], "c": [1, 1]}
    assert changed_paths(prev, cur, ["created", "modified"]) == ["a", "c"]
    assert changed_paths(prev, cur, ["created"]) == ["c"]
    assert changed_paths(prev, {"a": [1, 2]}, ["modified"]) == ["a"]  # size-only change


# -- FileWatchSource -----------------------------------------------------------------------------


def make_source(tmp_path: Path, w: WatchSpec, **kw: object) -> tuple[FileWatchSource, Path, list]:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    events: list = []
    src = FileWatchSource(
        ws,
        w,
        WatchDigestStore(tmp_path / "state"),
        emit=lambda n, p: events.append(n),
        **kw,  # type: ignore[arg-type]
    )
    return src, ws, events


NOW = T0 + timedelta(hours=1)
B = binding("w", kind="file_watch", interval_seconds=None, watch=WatchSpec(paths=["a.md"]))
ST = ScheduleState()


def test_observe_created_then_modified_and_scheduled_for_is_newest_mtime(tmp_path: Path) -> None:
    src, ws, events = make_source(tmp_path, spec("*.md"))
    touch(ws / "a.md", EPOCH + 5)
    touch(ws / "b.md", EPOCH + 9)
    cand = src.observe(B, ST, NOW)
    assert cand is not None
    assert cand.scheduled_for == T0 + timedelta(seconds=9) and cand.scheduled_for != NOW
    assert events == ["watch.changed"]
    src.commit(B, cand)
    assert src.observe(B, ST, NOW) is None  # unchanged after commit
    touch(ws / "a.md", EPOCH + 20, content="xx")
    cand2 = src.observe(B, ST, NOW)
    assert cand2 is not None and cand2.scheduled_for == T0 + timedelta(seconds=20)


def test_observe_is_read_only_and_repeatable(tmp_path: Path) -> None:
    src, ws, _ = make_source(tmp_path, spec("*.md"))
    touch(ws / "a.md")
    a, b = src.observe(B, ST, NOW), src.observe(B, ST, NOW)
    assert a == b and a is not None
    assert not (tmp_path / "state").exists()  # never wrote a digest


def test_on_created_only_ignores_modification(tmp_path: Path) -> None:
    src, ws, _ = make_source(tmp_path, spec("*.md", on=["created"]))
    touch(ws / "a.md")
    c = src.observe(B, ST, NOW)
    assert c is not None
    src.commit(B, c)
    touch(ws / "a.md", EPOCH + 50, content="changed")
    assert src.observe(B, ST, NOW) is None


def test_debounce_defers_then_fires_once_after_window(tmp_path: Path) -> None:
    src, ws, _ = make_source(tmp_path, spec("*.md", debounce=30))
    touch(ws / "a.md", EPOCH)
    assert src.observe(B, ST, T0 + timedelta(seconds=10)) is None
    touch(ws / "a.md", EPOCH + 15, content="again")  # touched twice inside the window
    assert src.observe(B, ST, T0 + timedelta(seconds=40)) is None  # 25s after newest
    c = src.observe(B, ST, T0 + timedelta(seconds=46))
    assert c is not None and c.scheduled_for == T0 + timedelta(seconds=15)
    src.commit(B, c)
    assert src.observe(B, ST, T0 + timedelta(seconds=90)) is None  # fired once


def test_truncated_scan_never_fires_and_emits(tmp_path: Path) -> None:
    src, ws, events = make_source(tmp_path, spec("*.md"), max_files=1)
    touch(ws / "a.md")
    touch(ws / "b.md")
    assert src.observe(B, ST, NOW) is None
    assert events == ["schedule.watch_budget_exceeded"]
    src2, _, ev2 = make_source(tmp_path, spec("*.md"), monotonic=mono_stub([0, 0, 99]))  # type: ignore[arg-type]
    assert src2.observe(B, ST, NOW) is None and ev2 == ["schedule.watch_budget_exceeded"]


def test_failing_event_sink_does_not_break_observe(tmp_path: Path) -> None:
    def boom(n: str, p: dict) -> None:
        raise RuntimeError("sink")

    ws = tmp_path / "ws"
    touch(ws / "a.md")
    src = FileWatchSource(ws, spec("*.md"), WatchDigestStore(tmp_path / "s"), emit=boom)
    assert src.observe(B, ST, NOW) is not None


# -- engine integration (F6 / idempotency / AC12 equivalent) --------------------------------------


def _watch_env(tmp_path: Path, **kw: object):  # type: ignore[no-untyped-def]
    env = env_for(
        tmp_path,
        [binding("s", kind="file_watch", interval_seconds=None,
                 watch=WatchSpec(paths=["w/*.md"], debounce_seconds=30), **kw)],
    )  # fmt: skip
    store = WatchDigestStore(tmp_path / "state")
    commits: list[Candidate] = []

    def factory(root: str, b):  # type: ignore[no-untyped-def]
        src = FileWatchSource(root, b.watch, store)
        orig = src.commit

        def counting(bb, c):  # type: ignore[no-untyped-def]
            commits.append(c)
            orig(bb, c)

        src.commit = counting  # type: ignore[method-assign]
        return src

    env.engine.register_trigger_source("file_watch", factory)
    return env, store, commits


def test_engine_fires_once_then_not_again(tmp_path: Path) -> None:
    env, _, commits = _watch_env(tmp_path)
    touch(Path(env.root) / "w/a.md", EPOCH)
    fired = env.tick(T0 + timedelta(minutes=5))
    assert len(fired) == 1 and fired[0].scheduled_for == T0 and len(commits) == 1
    assert env.tick(T0 + timedelta(minutes=6)) == []  # no change -> no fire
    assert len(commits) == 1


def test_engine_debounce_then_single_fire(tmp_path: Path) -> None:
    env, _, _ = _watch_env(tmp_path)
    touch(Path(env.root) / "w/a.md", EPOCH + 100)
    assert env.tick(T0 + timedelta(seconds=110)) == []
    assert len(env.tick(T0 + timedelta(seconds=140))) == 1


def test_crash_between_fire_and_digest_save_does_not_double_fire(tmp_path: Path) -> None:
    env, store, commits = _watch_env(tmp_path)
    touch(Path(env.root) / "w/a.md", EPOCH)
    assert len(env.tick(T0 + timedelta(minutes=5))) == 1
    # simulate the crash: the fire key is burned but the digest save was lost
    from agent_orchestrator.service.supervisor import _slug_for_root

    store.path_for(_slug_for_root(env.root), "s").unlink()
    d = env.evaluate(now=T0 + timedelta(minutes=6))
    assert not d.fire and d.reason == "already_fired"  # same mtime -> same fire_key, burned


def test_observe_never_commits_via_dry_evaluation(tmp_path: Path) -> None:
    env, store, commits = _watch_env(tmp_path)
    touch(Path(env.root) / "w/a.md", EPOCH)
    src = FileWatchSource(env.root, env.binding().watch, store)  # type: ignore[arg-type]
    for _ in range(2):
        assert src.observe(env.binding(), ScheduleState(), T0 + timedelta(minutes=5))
    assert commits == [] and not (tmp_path / "state/schedules/watch").exists()


def test_concurrency_skip_keeps_change_for_next_tick(tmp_path: Path) -> None:
    env, _, commits = _watch_env(tmp_path, overlap="allow", max_concurrent=1)
    env.make_active()
    touch(Path(env.root) / "w/a.md", EPOCH)
    assert env.evaluate(now=T0 + timedelta(minutes=5)).reason == "concurrency"
    assert commits == []
    env.alive.clear()
    fired = env.tick(T0 + timedelta(minutes=6))
    assert len(fired) == 1 and len(commits) == 1


def test_run_output_tree_writes_never_fire(tmp_path: Path) -> None:
    env, _, _ = _watch_env(tmp_path)
    env.engine.register_trigger_source(
        "file_watch",
        lambda root, b: FileWatchSource(
            root, WatchSpec(paths=["**/*.md"]), WatchDigestStore(tmp_path / "state")
        ),
    )
    touch(Path(env.root) / ".orchestrator/runs/r/out.md", EPOCH)
    assert env.tick(T0 + timedelta(minutes=5)) == []
