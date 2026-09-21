"""Multi-workspace service (E-GIytcL): one supervisor daemon serving every registered
workspace's dashboard, with auto-resume after reboot
(HLD: `docs-md/multi-workspace-service-hld.md`).

This package is kept fully separate from the engine core (`models.py`, `executors/`,
`spec.py`, `validate.py`) -- it manages `ui/` instances rather than being one.

Module layout (HLD §4):

``paths``    XDG config/state dir resolution + env override constants
``registry`` `WorkspaceEntry`/`ServiceRegistryFile` (pydantic), atomic + file-locked
             load/save/mutate -- the registered-workspace source of truth
``ports``    P1 (workspace config) > P2 (registry pin) > P3 (random free port)
             resolution, tier-first conflict detection, persistence

Later tasks in this epic add ``boot_resume``, ``supervisor``, ``hub``, ``systemd``, and
``cli`` on top of these two foundation modules.
"""

from __future__ import annotations
