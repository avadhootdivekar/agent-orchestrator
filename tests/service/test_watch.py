"""T-Fw8Gp4: file-watch digest store, bounded scanner, FileWatchSource, engine integration.
Fixed clocks, temp dirs; the engine is driven through the same fixtures as test_schedule_engine."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_orchestrator.schedules.models import WatchSpec
from agent_orchestrator.service.fire_store import ScheduleState, fire_key
from agent_orchestrator.service.schedule_engine import Candidate
from agent_orchestrator.service.watch import (
    DEFAULT_WATCH_IGNORES,
    WATCH_SCAN_BUDGET_SECONDS,
    FileWatchSource,
    WatchDigestStore,
    _glob_to_regex,
    change_discriminator,
    changed_paths,
    file_watch_factory,
    pattern_under_ignored_tree,
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


class Clock:
    """Fake monotonic clock; poll_seconds gates rescans, so tests bump ``t`` to re-poll."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


CLOCK = Clock()


def make_source(tmp_path: Path, w: WatchSpec, **kw: object) -> tuple[FileWatchSource, Path, list]:
    CLOCK.t = 0.0
    kw.setdefault("monotonic", CLOCK)
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
    assert events == []  # observe is silent (L3); the event is emitted once, on commit
    src.commit(B, cand)
    assert events == ["watch.changed"]
    assert src.observe(B, ST, NOW) is None  # unchanged after commit
    touch(ws / "a.md", EPOCH + 20, content="xx")
    CLOCK.t += 100
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
    # L-f: the reported decision key carries the discriminator too (pins _already_fired)
    digest, changed = scan(env.root, env.binding().watch)[0], ["w/a.md"]  # type: ignore[arg-type]
    disc = change_discriminator({}, digest, changed)
    assert d.fire_key == fire_key(env.root, "s", T0, disc) != fire_key(env.root, "s", T0)


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


# -- review fixes (w13-03): each test below fails when its fix is reverted ------------------------


def _engine_env(tmp_path: Path, w: WatchSpec, **kw: object):  # type: ignore[no-untyped-def]
    """Engine with NO test-registered source: exercises the production file_watch factory."""
    return env_for(
        tmp_path, [binding("s", kind="file_watch", interval_seconds=None, watch=w, **kw)]
    )


def test_production_factory_is_registered_by_default(tmp_path: Path) -> None:  # prod wiring
    env = _engine_env(tmp_path, WatchSpec(paths=["w/*.md"], debounce_seconds=30))
    touch(Path(env.root) / "w/a.md", EPOCH)
    assert len(env.tick(T0 + timedelta(minutes=5))) == 1
    assert callable(file_watch_factory(tmp_path))


def test_h1_modified_only_seeds_baseline_then_fires_on_edit(tmp_path: Path) -> None:
    env = _engine_env(tmp_path, WatchSpec(paths=["w/*.md"], on=["modified"], debounce_seconds=30))
    clock = Clock()
    store = WatchDigestStore(tmp_path / "state")
    env.engine.register_trigger_source(
        "file_watch", lambda r, b: FileWatchSource(r, b.watch, store, monotonic=clock)
    )
    f = touch(Path(env.root) / "w/prompt.md", EPOCH)
    assert env.tick(T0 + timedelta(minutes=5)) == []  # baseline seeded, not an event
    clock.t += 100
    touch(f, EPOCH + 600, content="edited")
    assert len(env.tick(T0 + timedelta(minutes=15))) == 1
    assert env.tick(T0 + timedelta(minutes=16)) == []


def test_h1_modified_only_file_created_later_then_edited(tmp_path: Path) -> None:
    src, ws, _ = make_source(tmp_path, spec("*.md", on=["modified"]))
    touch(ws / "a.md", EPOCH)
    assert src.observe(B, ST, NOW) is None
    CLOCK.t += 100
    touch(ws / "late.md", EPOCH + 10)  # creation is unwanted ...
    assert src.observe(B, ST, NOW) is None
    CLOCK.t += 100
    touch(ws / "late.md", EPOCH + 20, content="more")  # ... but a later edit is a modification
    assert src.observe(B, ST, NOW) is not None


def test_h2_template_instance_tree_is_ignored(tmp_path: Path) -> None:
    touch(tmp_path / "workflows/epic-runner/runs/r-abc/prompt.md")
    touch(tmp_path / "workflows/epic-runner/runs/r-abc/outputs/report.md")
    touch(tmp_path / "workflows/epic-runner/instructions/prompt.md")
    digest, _ = scan(tmp_path, spec("**/prompt.md", "**/*.md"))
    assert set(digest) == {"workflows/epic-runner/instructions/prompt.md"}


def test_h2_instance_scaffold_does_not_self_trigger(tmp_path: Path) -> None:
    env = _engine_env(tmp_path, WatchSpec(paths=["**/prompt.md"], debounce_seconds=0))
    touch(Path(env.root) / "workflows/t/runs/r1/prompt.md", EPOCH)
    assert env.tick(T0 + timedelta(minutes=5)) == []


def test_m1_old_mtime_file_still_fires(tmp_path: Path) -> None:
    env = _engine_env(tmp_path, WatchSpec(paths=["w/*.md"], debounce_seconds=30))
    touch(Path(env.root) / "w/a.md", EPOCH - 2 * 86400)  # cp -p / tar x: mtime two days old
    assert len(env.tick(T0 + timedelta(minutes=5))) == 1


def test_m1_catch_up_skip_with_long_debounce_still_fires(tmp_path: Path) -> None:
    env = _engine_env(tmp_path, WatchSpec(paths=["w/*.md"], debounce_seconds=90), catch_up="skip")
    touch(Path(env.root) / "w/a.md", EPOCH)
    assert len(env.tick(T0 + timedelta(minutes=5))) == 1


def test_m2_same_second_different_state_gets_different_key(tmp_path: Path) -> None:
    src, ws, _ = make_source(tmp_path, spec("*.md"))
    f = touch(ws / "a.md", EPOCH, content="1")
    c1 = src.observe(B, ST, NOW)
    assert c1 is not None
    src.commit(B, c1)
    f.write_text("22")
    os.utime(f, (EPOCH, EPOCH))  # same whole second, different size
    c2 = src.observe(B, ST, NOW)
    assert c2 is not None and c2.scheduled_for == c1.scheduled_for
    assert c2.discriminator != c1.discriminator


def test_m2_revert_with_preserved_mtime_fires_again_in_engine(tmp_path: Path) -> None:
    env = _engine_env(tmp_path, WatchSpec(paths=["w/*.md"], debounce_seconds=30))
    f = touch(Path(env.root) / "w/a.md", EPOCH, content="A")
    assert len(env.tick(T0 + timedelta(minutes=5))) == 1
    touch(f, EPOCH + 100, content="BB")
    env.tick(T0 + timedelta(minutes=6))  # B observed (fire or overlap_skip) and committed
    touch(f, EPOCH, content="A")  # ``cp -p`` revert: same (mtime, size) as the first fire
    env.end_run("run-1")  # w21-05 review: free the overlap slot so the only valid outcome is a fire
    d = env.evaluate(now=T0 + timedelta(minutes=30))
    assert d.fire and d.reason == "due"


def test_m2_discriminator_is_deterministic_for_replay() -> None:
    prev: dict = {"a": [1, 2]}
    cur: dict = {"a": [3, 4]}
    assert change_discriminator(prev, cur, ["a"]) == change_discriminator(prev, cur, ["a"])
    assert change_discriminator({}, cur, ["a"]) != change_discriminator(prev, cur, ["a"])


def test_m3_directory_only_tree_respects_budget(tmp_path: Path) -> None:
    for i in range(50):
        (tmp_path / f"d{i}").mkdir()
    clock = mono_stub([0.0, 0.0, WATCH_SCAN_BUDGET_SECONDS + 1])
    _, trunc = scan(tmp_path, spec("**/*.md"), monotonic=clock)  # type: ignore[arg-type]
    assert trunc


def test_m3_walk_is_pruned_to_the_static_prefix(tmp_path: Path) -> None:
    for i in range(120):
        touch(tmp_path / f"zz/f{i}.txt")
    touch(tmp_path / "epics/a/prompt.md")
    # visit cap is 5 * 20 = 100: the unrelated 120 files would truncate an unpruned walk
    digest, trunc = scan(tmp_path, spec("epics/*/prompt.md"), max_files=5)
    assert not trunc and set(digest) == {"epics/a/prompt.md"}


def test_m3_non_matching_dirs_are_not_descended(tmp_path: Path) -> None:
    for i in range(120):
        touch(tmp_path / f"epics/x/deep/f{i}.txt")  # below ``epics/*/``: cannot match
    touch(tmp_path / "epics/a/prompt.md")
    digest, trunc = scan(tmp_path, spec("epics/*/prompt.md"), max_files=5)
    assert not trunc and set(digest) == {"epics/a/prompt.md"}


def test_visit_cap_truncates(tmp_path: Path) -> None:
    for i in range(30):
        touch(tmp_path / f"f{i}.txt")
    _, trunc = scan(tmp_path, spec("*.md"), max_files=1)  # cap = 20 visits < 30 entries
    assert trunc
    assert scan(tmp_path, spec("*.md"), max_files=2)[1] is False  # cap 40 >= 30


def test_m4_poll_seconds_gates_rescans(tmp_path: Path) -> None:
    now = [100.0]
    w = WatchSpec(paths=["*.md"], debounce_seconds=0, poll_seconds=600)
    src, ws, _ = make_source(tmp_path, w, monotonic=lambda: now[0])
    assert src.observe(B, ST, NOW) is None  # empty tree, scan #1 arms the gate
    touch(ws / "a.md", EPOCH)
    now[0] += 599
    assert src.observe(B, ST, NOW) is None  # gated: the new file is not seen yet
    now[0] += 2
    assert src.observe(B, ST, NOW) is not None  # gate open


def test_l1_per_binding_max_files_is_configurable(tmp_path: Path) -> None:
    assert WatchSpec(paths=["*.md"], max_files=3).max_files == 3
    src, ws, events = make_source(tmp_path, WatchSpec(paths=["*.md"], max_files=1))
    touch(ws / "a.md")
    touch(ws / "b.md")
    assert src.observe(B, ST, NOW) is None and events == ["schedule.watch_budget_exceeded"]


def test_l2_whole_second_truncation_never_rounds_up(tmp_path: Path) -> None:
    src, ws, _ = make_source(tmp_path, spec("*.md"))
    f = touch(ws / "a.md")
    ns = (EPOCH + 5) * 10**9 + 999_999_800  # .9999998 would round up through a float
    os.utime(f, ns=(ns, ns))
    c = src.observe(B, ST, NOW)
    assert c is not None and c.scheduled_for == datetime.fromtimestamp(EPOCH + 5, tz=UTC)
    assert c.scheduled_for.microsecond == 0


def test_debounce_boundary_is_inclusive(tmp_path: Path) -> None:
    src, ws, _ = make_source(tmp_path, spec("*.md", debounce=30))
    touch(ws / "a.md", EPOCH)
    assert src.observe(B, ST, T0 + timedelta(seconds=29)) is None
    assert src.observe(B, ST, T0 + timedelta(seconds=30)) is not None  # exactly debounce: fires


def test_l3_budget_event_emitted_once_per_episode(tmp_path: Path) -> None:
    now = [0.0]
    w = WatchSpec(paths=["*.md"], poll_seconds=1, max_files=1)
    src, ws, events = make_source(tmp_path, w, monotonic=lambda: now[0])
    touch(ws / "a.md")
    touch(ws / "b.md")
    for _ in range(3):
        now[0] += 10
        assert src.observe(B, ST, NOW) is None
    assert events == ["schedule.watch_budget_exceeded"]


def test_l5_negated_class_cannot_cross_directories() -> None:
    rx = _glob_to_regex("a[!x]b.md")
    assert rx.fullmatch("a/b.md") is None  # a negated class must never match ``/``
    assert rx.fullmatch("ayb.md") is not None and rx.fullmatch("axb.md") is None


def test_l5_caret_after_bracket_is_negation(tmp_path: Path) -> None:
    touch(tmp_path / "x.md")
    touch(tmp_path / "y.md")
    assert set(scan(tmp_path, spec("[^x].md"))[0]) == {"y.md"}


# -- w15 re-review fixes (N1-N3, L-c, L-d) -------------------------------------------------------


def test_n1_symlinked_prefix_to_outside_root_is_not_followed(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    touch(tmp_path / "outside/secret.md")
    ws.mkdir()
    (ws / "docs").symlink_to(tmp_path / "outside")
    assert scan(ws, spec("docs/*.md")) == ({}, False)


def test_n1_symlinked_prefix_into_ignored_tree_is_not_followed(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    touch(ws / ".orchestrator/runs/r/out.md")
    (ws / "logs").symlink_to(ws / ".orchestrator/runs")
    assert scan(ws, spec("logs/*/*.md")) == ({}, False)


def test_n1_symlink_in_middle_of_prefix_is_not_followed(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    touch(tmp_path / "outside/b/f.md")
    ws.mkdir()
    (ws / "a").symlink_to(tmp_path / "outside")
    touch(ws / "real/b/f.md")
    assert scan(ws, spec("a/b/*.md", "real/b/*.md"))[0].keys() == {"real/b/f.md"}


def test_n2_queued_file_watch_fire_survives_restart(tmp_path: Path) -> None:
    env = _engine_env(tmp_path, WatchSpec(paths=["w/*.md"], debounce_seconds=30), overlap="queue")
    touch(Path(env.root) / "w/a.md", EPOCH)
    env.make_active()
    assert env.tick(T0 + timedelta(minutes=5)) == []  # queued behind the active run
    st = env.engine.state_store.get(env.root, "s")
    assert st is not None and st.queued_discriminator
    env.end_run()
    env.engine = env.make_engine()  # restart: only the state files survive
    fired = env.tick(T0 + timedelta(minutes=6))
    assert [d.reason for d in fired] == ["queue_drain"]
    assert not env.events("schedule.missed")


def test_n3_future_mtime_file_does_not_starve_real_change(tmp_path: Path) -> None:
    src, ws, events = make_source(tmp_path, spec("w/*.md"))
    touch(ws / "w/future.md", EPOCH + 10 * 86400)
    touch(ws / "w/real.md", EPOCH)
    cand = src.observe(B, ST, T0 + timedelta(minutes=5))
    assert cand is not None and cand.scheduled_for == T0  # keyed on the real change, not "now"
    again = src.observe(B, ST, T0 + timedelta(minutes=9))
    assert again is not None and again.scheduled_for == cand.scheduled_for  # replay-deterministic
    assert events.count("schedule.watch_future_mtime") == 1  # once per episode


def test_n3_only_future_changes_wait_until_their_mtime(tmp_path: Path) -> None:
    src, ws, _ = make_source(tmp_path, spec("w/*.md"))
    touch(ws / "w/future.md", EPOCH + 3600)
    assert src.observe(B, ST, T0 + timedelta(minutes=5)) is None
    assert src.observe(B, ST, T0 + timedelta(minutes=95)) is not None


def test_lc_production_factory_uses_engine_monotonic(tmp_path: Path) -> None:
    env = _engine_env(tmp_path, WatchSpec(paths=["w/*.md"], debounce_seconds=30))
    src = env.engine._source_for(env.root, env.binding())
    assert src is not None and src._monotonic is env.mono  # type: ignore[attr-defined]


def test_ld_pattern_under_ignored_tree_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    assert pattern_under_ignored_tree("workflows/*/runs/*/outputs/f.md")
    assert pattern_under_ignored_tree(".orchestrator/runs/*/x.md")
    assert not pattern_under_ignored_tree("workflows/*/instructions/*.md")
    with caplog.at_level(logging.WARNING):
        make_source(tmp_path, spec("node_modules/**/*.md"))
    assert "under an ignored tree" in caplog.text
