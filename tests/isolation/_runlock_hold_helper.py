"""Helper process for `test_runlock.py::TestSecondHolder`'s real-second-process tests
(E-Wk9Tz3 T-Wl2Bq7, AC-5). Not a test module itself -- never collected by pytest (no
``test_*`` name).

Usage: ``python _runlock_hold_helper.py <workspace_root> <run_id>``. Acquires the
workspace lock, prints ``ACQUIRED`` (flushed) so the parent test knows the flock is
actually held, then sleeps until killed -- the parent's own `proc.kill()` is what
releases it (verifying the OS auto-release-on-death property this module's stale-
reclamation logic depends on).
"""

from __future__ import annotations

import sys
import time

from agent_orchestrator.isolation.runlock import WorkspaceRunLock

if __name__ == "__main__":
    workspace_root, run_id = sys.argv[1], sys.argv[2]
    lock = WorkspaceRunLock(workspace_root, run_id)
    claim = lock.acquire()
    print("ACQUIRED" if claim.granted else "DENIED", flush=True)
    time.sleep(60)
