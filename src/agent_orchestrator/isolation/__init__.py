"""Per-task git isolation (E-Wk9Tz3).

Package home for the isolation epic's modules. Only `git.py` (`T-Gt4Pw8-git-porcelain`)
exists so far; `paths.py`/`worktrees.py`/`integrator.py`/etc. land with their own tasks
(HLD §11 M2-M11, `docs-md/task-isolation-hld.md`). This file intentionally stays free of
imports so that landing a sibling module never risks a partial/circular import here.
"""

from __future__ import annotations
