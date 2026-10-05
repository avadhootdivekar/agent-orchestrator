"""Cross-run result cache (E-Rc4Hk8, ADR-0019).

Reuses the declared output files of an identical, previously SUCCESSFUL task across runs. This is
NOT Claude prompt caching. The cache is off by default (operator opt-in AND author opt-in).

This package marker deliberately has no imports: a cache-off process must load at most
`agent_orchestrator.cache` and `agent_orchestrator.cache.constants` (HLD 8.0, NFR-1).
"""
