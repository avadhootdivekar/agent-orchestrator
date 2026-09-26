"""Engine integration tests for T-pYt478 (emit-settle atomicity, epic fix/emit-settle-atomicity).

Bug fixed: `_settle_completed_task` used to persist a successful emit_tasks task's
"succeeded" status (and evaluate circuit breakers) BEFORE ever reading/injecting its
manifest. A breaker trip (or a crash) landing between that save and the injection block
meant the manifest was read and injected only if nothing halted the run first — a
`stop_file`/`injected_task_count`/any other breaker tripping at that exact boundary
caused the manifest to be silently and permanently lost: `RunStateStore.prepare_resume`
leaves a succeeded, outputs-present task exactly as-is (never re-run), so the manifest is
never re-read on a later resume either.

Fix: injection now happens BEFORE the first `self._runstate.save(state)` and BEFORE
circuit-breaker evaluation, so a successful emitter's "succeeded" status and its injected
child task ids land in the SAME save. `evaluate_breakers` still runs after injection (now
at the SAME boundary rather than one boundary later), so a breaker like
`injected_task_count` can still catch and halt an over-large emission right at the
emitter's own settle — before any injected child is ever dispatched.

Covers acceptance criteria 1-6 of ticket T-pYt478-emit-settle-atomicity.
"""

from __future__ import annotations

from pathlib import Path

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import (
    AgentSpec,
    CircuitBreakerSpec,
    RepoRef,
    RepoSet,
    RunState,
    TaskSpec,
    WorkflowSpec,
)
from agent_orchestrator.monitoring import RuleBasedMonitor
from agent_orchestrator.runstate import RunStateStore

# ---------------------------------------------------------------------------
# Shared helpers (mirrors the style of test_monitoring_breaker_consult.py /
# the architect's repro script this suite is based on)
# ---------------------------------------------------------------------------


def _make_workspace(tmp_path: Path) -> tuple[LocalFsArtifactStore, RunStateStore]:
    store = LocalFsArtifactStore(str(tmp_path))
    rs_store = RunStateStore(str(tmp_path), store)
    return store, rs_store


def _fake_agents() -> dict:
    return {"ag": AgentSpec(executor="fake")}


def _fake_reposets(workspace: str) -> dict:
    return {
        "rs": RepoSet(
            workspace_root=workspace,
            repos=[RepoRef(id="core", path=".", role="primary")],
        )
    }


def _emitter_workflow(
    tmp_path: Path,
    circuit_breakers: list[CircuitBreakerSpec] | None = None,
    manifest_path: str = "out/manifest.json",
) -> WorkflowSpec:
    """A single emit_tasks task, optionally guarded by *circuit_breakers*.

    Shared instruction file ("i.md") for the emitter and every injected child --
    same simplification the architect's repro script used.
    """
    (tmp_path / "i.md").write_text("x")
    emitter = TaskSpec(
        id="emit",
        agent="ag",
        instruction="i.md",
        outputs=[],
        emit_tasks=True,
        task_manifest_path=manifest_path,
    )
    return WorkflowSpec(
        version="1.0",
        id="wf",
        repo_set="rs",
        tasks=[emitter],
        circuit_breakers=circuit_breakers or [],
    )


def _manifest(*child_ids: str) -> dict:
    return {
        "tasks": [
            {
                "id": cid,
                "agent": "ag",
                "instruction": "i.md",
                "depends_on": ["emit"],
                "outputs": [f"out/{cid}.md"],
            }
            for cid in child_ids
        ]
    }


# ---------------------------------------------------------------------------
# AC1: a breaker tripping at the emitter's own settle boundary keeps the
# injection; a plain resume then dispatches it to completion.
# ---------------------------------------------------------------------------


def test_breaker_trip_at_emitter_keeps_injection(tmp_path: Path) -> None:
    """Inverted assertions of the architect's repro script (same shape workflow):
    a `stop_file` breaker whose flag exists when the emitter settles now halts the run
    WITHOUT losing the manifest injection -- the child is persisted (pending) rather than
    silently dropped -- and a plain resume (flag removed) completes it."""
    store, rs_store = _make_workspace(tmp_path)
    flag = tmp_path / "control" / "halt.flag"
    flag.parent.mkdir(parents=True)
    flag.write_text("")  # present when the emitter settles -> trips the breaker

    wf = _emitter_workflow(
        tmp_path,
        circuit_breakers=[
            CircuitBreakerSpec(
                id="kill", condition="stop_file", action="stop", path="control/halt.flag"
            )
        ],
    )
    manifest = _manifest("child")
    orch = Orchestrator(FakeExecutor(emit_payloads={"emit": manifest}), store, rs_store)

    state1 = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

    # The run halted (breaker tripped) but the injection was NOT lost.
    assert state1.status == "failed"
    assert [tb.id for tb in state1.tripped_breakers] == ["kill"]
    assert "child" in [t.id for t in state1.injected_tasks]
    assert state1.tasks["child"].status == "pending"
    # The emitter itself genuinely succeeded -- only the RUN halted, via the breaker.
    assert state1.tasks["emit"].status == "succeeded"

    # Resume: remove the flag, prepare_resume + run -- the child now dispatches and the
    # run completes. Before the fix this could never happen: the manifest was never
    # persisted in the first place, so `state1.injected_tasks` would have been empty and
    # "child" would not exist at all.
    flag.unlink()
    loaded = rs_store.load(state1.run_id)
    wf2 = _emitter_workflow(tmp_path, circuit_breakers=wf.circuit_breakers)
    loaded = rs_store.prepare_resume(loaded, wf2)
    orch2 = Orchestrator(FakeExecutor(emit_payloads={"emit": manifest}), store, rs_store)

    state2 = orch2.run(wf2, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=loaded)

    assert state2.tasks["child"].status == "succeeded"
    assert state2.status == "succeeded"


# ---------------------------------------------------------------------------
# AC2: the FIRST save after the emitter's executor returns already contains both
# the "succeeded" status AND the injected ids -- no half-persisted state.
# ---------------------------------------------------------------------------


def test_single_save_contains_success_and_injection(tmp_path: Path, monkeypatch) -> None:
    """A spy on `RunStateStore.save` proves the atomicity guarantee directly: the FIRST
    persisted snapshot in which the emitter shows "succeeded" already carries the
    injected child in `injected_tasks` (as `pending`) -- there is no earlier save where
    "succeeded" is visible without the injection."""
    store, rs_store = _make_workspace(tmp_path)
    wf = _emitter_workflow(tmp_path)  # no circuit breakers -- isolates the save ordering
    manifest = _manifest("child")

    saves: list[RunState] = []
    original_save = RunStateStore.save

    def _recording_save(self: RunStateStore, state: RunState) -> None:
        saves.append(state.model_copy(deep=True))
        original_save(self, state)

    monkeypatch.setattr(RunStateStore, "save", _recording_save)

    orch = Orchestrator(FakeExecutor(emit_payloads={"emit": manifest}), store, rs_store)
    state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

    assert state.status == "succeeded"
    assert state.tasks["child"].status == "succeeded"

    first_succeeded_save = next(
        s
        for s in saves
        if s.tasks.get("emit") is not None and s.tasks["emit"].status == "succeeded"
    )
    injected_ids = [t.id for t in first_succeeded_save.injected_tasks]
    assert "child" in injected_ids
    assert first_succeeded_save.tasks["child"].status == "pending"


# ---------------------------------------------------------------------------
# AC3: injected_task_count now trips AT the emitting boundary itself (not one
# boundary later) -- injected tasks are persisted but never dispatched.
# ---------------------------------------------------------------------------


def test_injected_task_count_trips_at_emitting_boundary(tmp_path: Path) -> None:
    """threshold=1 with a 2-task manifest trips as soon as injection happens -- i.e. at
    the EMITTER's own settle, before any child ever gets a chance to run. Before the fix,
    breaker evaluation ran before injection at this boundary, so `injected_tasks` was
    still empty here and the trip could only fire one boundary later (at a child's own
    settle)."""
    store, rs_store = _make_workspace(tmp_path)
    wf = _emitter_workflow(
        tmp_path,
        circuit_breakers=[
            CircuitBreakerSpec(
                id="cap", condition="injected_task_count", action="stop", threshold=1
            )
        ],
    )
    manifest = _manifest("child1", "child2")
    orch = Orchestrator(FakeExecutor(emit_payloads={"emit": manifest}), store, rs_store)

    state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

    assert state.status == "failed"
    assert len(state.tripped_breakers) == 1
    assert state.tripped_breakers[0].id == "cap"
    assert state.tripped_breakers[0].condition == "injected_task_count"
    # The emitter succeeded, injection happened (both children persisted)...
    assert state.tasks["emit"].status == "succeeded"
    assert {t.id for t in state.injected_tasks} == {"child1", "child2"}
    # ...but neither child was ever dispatched: the halt happened at emit's own boundary.
    assert state.tasks["child1"].status == "pending"
    assert state.tasks["child2"].status == "pending"


# ---------------------------------------------------------------------------
# AC4: the manifest-error path is byte-identical to before the reorder.
# ---------------------------------------------------------------------------


def test_manifest_error_path_unchanged(tmp_path: Path) -> None:
    """A malformed/missing manifest still fails the emitter and the run, injecting
    nothing -- moving the block earlier changes WHEN it runs, never its own internal
    error handling."""
    store, rs_store = _make_workspace(tmp_path)
    wf = _emitter_workflow(tmp_path, manifest_path="out/does-not-exist.json")
    # No emit_payloads configured -- the executor never writes the manifest file, so
    # read_task_manifest() raises ValueError("...not found...").
    orch = Orchestrator(FakeExecutor(), store, rs_store)

    state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

    assert state.status == "failed"
    assert state.tasks["emit"].status == "failed"
    assert state.tasks["emit"].outputs_present is False
    assert state.injected_tasks == []


# ---------------------------------------------------------------------------
# AC5: a plain resume (no --extend-breaker) now dispatches the persisted injected
# tasks -- documented as intentional, narrow, and NOT a general re-arm.
# ---------------------------------------------------------------------------


def test_plain_resume_after_trip_dispatches_injected(tmp_path: Path) -> None:
    """Documents latch semantics on resume -- NOT a pre/post-fix discriminator for THIS
    breaker type (reviewer finding on T-pYt478's review: for `injected_task_count`
    specifically, the manifest was never actually lost pre-fix either, only detected one
    boundary late -- see the note below). This test passes identically before and after
    this fix; it is kept because the latch behavior it documents is real and worth
    pinning, but it must not be read as evidence for the "manifest silently lost forever"
    bug this ticket fixes. That claim IS genuinely pre/post discriminating, and is covered
    by `test_breaker_trip_at_emitter_keeps_injection` (a `stop_file` condition, which does
    not depend on `injected_tasks` count and so really can trip at the emitter's own
    settle before any injection -- pre-fix that trip permanently loses the manifest, since
    there is no later boundary at which the count could still cross a threshold) and by
    `test_injected_task_count_trips_at_emitting_boundary` (asserts the injected children
    are `pending`, never dispatched, immediately after the trip).

    What this test actually shows: `evaluate_breakers` latches by breaker id
    (`state.tripped_breakers`), and only `apply_breaker_extension` (the explicit
    `--extend-breaker` path) ever removes a latch. Here the SAME numeric condition that
    tripped ("cap" requires count < 1, but 2 injected tasks exist) is still true after
    resume -- the run nonetheless completes because "cap"'s `TrippedBreaker` record is
    still present, so `evaluate_breakers`'s already-tripped filter skips re-evaluating it
    for this id. `prepare_resume` resets `state.status` back to "running" (independent of
    the breaker latch), which is what lets the run loop resume dispatching in the first
    place. Re-arming tripped breakers globally on every resume is explicitly out of scope
    for this fix (and would defeat the entire point of a breaker).

    Why `injected_task_count` can't discriminate here: `InjectedTaskCountBreaker.evaluate`
    counts `len(state.injected_tasks)`. Pre-fix, breaker evaluation ran BEFORE injection,
    so at the emitter's own boundary the count was always 0 -- this breaker type could
    structurally never trip exactly at the emitter's boundary in the old code; it always
    tripped one boundary later, at the first injected child's own settle, by which point
    the old code's (now-removed) second save had already persisted the injection. So for
    this breaker type specifically, pre-fix code just detected the overrun one task late,
    not "never" -- the end state after a subsequent resume is identical either way.
    """
    store, rs_store = _make_workspace(tmp_path)
    wf = _emitter_workflow(
        tmp_path,
        circuit_breakers=[
            CircuitBreakerSpec(
                id="cap", condition="injected_task_count", action="stop", threshold=1
            )
        ],
    )
    manifest = _manifest("child1", "child2")
    orch = Orchestrator(FakeExecutor(emit_payloads={"emit": manifest}), store, rs_store)
    state1 = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())
    assert state1.status == "failed"
    assert state1.tripped_breakers[0].id == "cap"

    loaded = rs_store.load(state1.run_id)
    wf2 = _emitter_workflow(tmp_path, circuit_breakers=wf.circuit_breakers)
    loaded = rs_store.prepare_resume(loaded, wf2)
    assert loaded.status == "running"  # prepare_resume resets this
    # The latch survives resume verbatim -- never cleared by a plain resume.
    assert [tb.id for tb in loaded.tripped_breakers] == ["cap"]
    assert loaded.breaker_overrides == {}  # never extended

    orch2 = Orchestrator(FakeExecutor(emit_payloads={"emit": manifest}), store, rs_store)
    state2 = orch2.run(wf2, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=loaded)

    assert state2.status == "succeeded"
    assert state2.tasks["child1"].status == "succeeded"
    assert state2.tasks["child2"].status == "succeeded"
    # The trip record for "cap" is still there -- it was never un-latched, just no longer
    # consulted for THIS id since it never newly trips again after resume.
    assert [tb.id for tb in state2.tripped_breakers] == ["cap"]


# ---------------------------------------------------------------------------
# AC6: monitor "extend" consult path at an emitter boundary.
# ---------------------------------------------------------------------------


def test_monitor_extend_at_emitter_boundary_lets_injected_tasks_run(tmp_path: Path) -> None:
    """A `mode: "recommend"` breaker consulted at an emitter's settle, with a monitor that
    answers "extend" (RuleBasedMonitor's documented "extend once" policy, same fixture
    pattern as test_monitoring_breaker_consult.py), un-latches the trip and lets the run
    continue -- the injected task then dispatches and succeeds. This exercises the real,
    correctly-implemented fall-through from the consult "extend" outcome into the (now
    relocated) `if injected:` reshape branch.

    Caveat (reviewer finding on T-pYt478's review, same root cause as
    `test_plain_resume_after_trip_dispatches_injected`): with this single-child fixture,
    `injected_task_count`'s count-at-evaluation-time means the trip+consult would, pre-fix,
    have fired one boundary later (at `child1`'s own settle, not the emitter's) with an
    identical final outcome -- so this specific test does not discriminate pre/post-fix
    either. What genuinely moved earlier (the trip point itself, provably, via a hard
    `stop` breaker whose halt is directly observable rather than consulted-and-extended
    away) is covered by `test_injected_task_count_trips_at_emitting_boundary`. This test's
    job is narrower and still real: proving the monitor-consult "extend" path is wired
    correctly on the far side of the reorder, not proving where the trip point moved.

    A single-child manifest is used deliberately: `apply_breaker_extension` bumps the
    threshold by the ORIGINAL threshold amount (1 -> 2), and `injected_task_count`'s count
    never grows again after this one emission, so `count(1) < new_threshold(2)` holds at
    every later boundary and the extension actually lets the run finish. (A 2-child
    manifest would re-trip at the very next boundary since `count(2) >= new_threshold(2)`
    -- that is RuleBasedMonitor's own documented "extend once then halt" policy correctly
    refusing a second extension, not a bug in this fix.)
    """
    store, rs_store = _make_workspace(tmp_path)
    wf = _emitter_workflow(
        tmp_path,
        circuit_breakers=[
            CircuitBreakerSpec(
                id="cap",
                condition="injected_task_count",
                action="stop",
                threshold=1,
                mode="recommend",
            )
        ],
    )
    manifest = _manifest("child1")
    orch = Orchestrator(
        FakeExecutor(emit_payloads={"emit": manifest}),
        store,
        rs_store,
        monitor=RuleBasedMonitor(),
    )

    state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

    assert state.status == "succeeded"
    assert state.tasks["child1"].status == "succeeded"
    # Un-latched by the extend -- no lingering trip record for "cap".
    assert state.tripped_breakers == []
    assert len(state.monitor_decisions) == 1
    assert state.monitor_decisions[0].consult_point == "breaker_trip"
