"""Generated-task families (HLD §4.1): seed + difficulty + language -> spec, skeleton, hidden
probes and a hidden reference implementation.

One module per family (``crud_relational``, ``job_queue``); shared pieces: ``base`` (instance,
registry), ``traps`` (catalogue + sampling), ``wordlists``, ``skeleton`` (per-language project
files and run-contract prose), ``dsl`` (probe builders), ``validate`` (probes.json shape check),
``refs`` (reference programs under ``reference/``).  Importing this package registers every family.
"""

from __future__ import annotations

from . import (
    algo_hard as _algo_hard,  # noqa: F401 -- registers "algo-hard" (T-Pf4Mix)
)
from . import (
    algo_puzzle as _algo_puzzle,  # noqa: F401 -- registers "algo-puzzle" (T-Pf4Mix)
)
from . import (
    algo_staircase as _algo_staircase,  # noqa: F401 -- registers "algo-staircase" (T-Os5Lad)
)
from . import (
    bug_hunt as _bug_hunt,  # noqa: F401 -- registers "bug-hunt" (T-Pf4Mix)
)
from . import (
    crud_relational as _crud_relational,  # noqa: F401 -- registers "crud-relational"
)
from . import (
    game_arena as _game_arena,  # noqa: F401 -- registers "game-arena" (T-Ga4Rna)
)
from . import (
    job_queue as _job_queue,  # noqa: F401 -- registers "job-queue"
)
from . import (
    opt_ladder as _opt_ladder,  # noqa: F401 -- registers "opt-ladder" (T-Os5Lad)
)
from . import (
    order_book as _order_book,  # noqa: F401 -- registers "order-book" (T-Pf4Mix)
)
from . import (
    rules_engine as _rules_engine,  # noqa: F401 -- registers "rules-engine" (T-Pf4Mix)
)
from .base import FAMILY_REGISTRY, Difficulty, Family, Instance, register_family

__all__ = ["FAMILY_REGISTRY", "Difficulty", "Family", "Instance", "register_family"]
