"""Dashboard hardening regressions (overseer unit w03-03; audit rows C6, D13).

* ``FileBrowser.read_file`` must never ``open()`` a FIFO/socket/device (a FIFO with no writer
  blocks the request thread forever).
* ``DashboardService.start_run`` must refuse a ``workflow_path`` that resolves outside the
  workspace (or the spec search roots), including via a symlink.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from agent_orchestrator.project_config import ProjectConfig
from agent_orchestrator.ui.files import FileBrowser, PathNotFoundError, Root
from agent_orchestrator.ui.service import DashboardError, DashboardService

from .conftest import StubSupervisor, write_workflow


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs POSIX FIFOs")
def test_read_file_refuses_a_fifo_without_blocking(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "pipe")
    browser = FileBrowser(roots=[Root(name="workspace", path=str(tmp_path))])
    outcome: list[BaseException | None] = []

    def attempt() -> None:
        try:
            browser.read_file("workspace", "pipe")
            outcome.append(None)
        except BaseException as exc:  # noqa: BLE001 - recorded and asserted below
            outcome.append(exc)

    worker = threading.Thread(target=attempt, daemon=True)
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive(), "read_file blocked on a FIFO"
    assert isinstance(outcome[0], PathNotFoundError)


def test_read_file_still_reads_regular_files(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hi\n", encoding="utf-8")
    browser = FileBrowser(roots=[Root(name="workspace", path=str(tmp_path))])
    assert browser.read_file("workspace", "a.txt").text == "hi\n"


class TestStartRunWorkspaceRestriction:
    def test_rejects_an_absolute_spec_outside_the_workspace(
        self,
        service: DashboardService,
        tmp_path_factory: pytest.TempPathFactory,
        stub_supervisor: StubSupervisor,
    ) -> None:
        outside = write_workflow(tmp_path_factory.mktemp("elsewhere") / "wf.json")
        with pytest.raises(DashboardError, match="outside the workspace"):
            service.start_run(workflow_path=str(outside))
        assert stub_supervisor.launch_calls == []

    def test_rejects_a_symlink_escaping_the_workspace(
        self,
        service: DashboardService,
        workspace: Path,
        tmp_path_factory: pytest.TempPathFactory,
        stub_supervisor: StubSupervisor,
    ) -> None:
        outside = write_workflow(tmp_path_factory.mktemp("elsewhere") / "wf.json")
        link = workspace / "link.json"
        link.symlink_to(outside)
        with pytest.raises(DashboardError, match="outside the workspace"):
            service.start_run(workflow_path=str(link))
        assert stub_supervisor.launch_calls == []

    def test_still_launches_a_spec_inside_the_workspace(
        self, service: DashboardService, workspace: Path, stub_supervisor: StubSupervisor
    ) -> None:
        wf = write_workflow(workspace / "wf.json")
        service.start_run(workflow_path=str(wf))
        assert len(stub_supervisor.launch_calls) == 1

    def test_still_launches_the_configured_spec_outside_the_workspace(
        self,
        workspace: Path,
        tmp_path_factory: pytest.TempPathFactory,
        stub_supervisor: StubSupervisor,
    ) -> None:
        # list_workflows offers the configured spec even from outside the roots, so it must launch.
        external = write_workflow(tmp_path_factory.mktemp("elsewhere") / "wf.json")
        svc = DashboardService(
            str(workspace),
            supervisor=stub_supervisor,  # type: ignore[arg-type]
            project_config=ProjectConfig(workflow=str(external)),
        )
        svc.start_run()
        assert len(stub_supervisor.launch_calls) == 1
