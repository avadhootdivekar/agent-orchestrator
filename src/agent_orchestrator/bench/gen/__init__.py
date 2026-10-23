"""Generated-task families (HLD §4.1) -- STUB package, owner: generators track.

One module per family (`crud_relational.py`, `job_queue.py`, ...), a shared trap
catalogue, word lists and per-language skeletons. Each family: seed + difficulty +
language -> Instance (spec, skeleton, probes.json, reference implementation).
"""

from __future__ import annotations

FAMILY_REGISTRY: dict[str, type] = {}
