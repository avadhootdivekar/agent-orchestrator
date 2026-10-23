"""Generated-task families (HLD §4.1): seed + difficulty + language -> spec, skeleton, hidden
probes and a hidden reference implementation.

One module per family (``crud_relational``, ``job_queue``); shared pieces: ``base`` (instance,
registry), ``traps`` (catalogue + sampling), ``wordlists``, ``skeleton`` (per-language project
files and run-contract prose), ``dsl`` (probe builders), ``validate`` (probes.json shape check),
``refs`` (reference programs under ``reference/``).  Importing this package registers every family.
"""

from __future__ import annotations

from . import (
    crud_relational as _crud_relational,  # noqa: F401 -- registers "crud-relational"
)
from . import (
)
from .base import FAMILY_REGISTRY, Difficulty, Family, Instance, register_family

__all__ = ["FAMILY_REGISTRY", "Difficulty", "Family", "Instance", "register_family"]
