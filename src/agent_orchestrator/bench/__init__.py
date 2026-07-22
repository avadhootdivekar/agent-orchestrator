"""Benchmark harness for agent-orchestrator (design doc: docs-md/benchmarking-framework-hld.md).

Compares subjects (an `ao` workflow vs bare `claude -p` at a given model vs, later,
other CLIs/APIs) on suites of dev tasks, and writes machine-readable + human-readable
results. A separate module that shells out to `ao`/`claude` -- it is NEVER imported by
core (safety invariant SI-1: `ao run` works whether or not `bench/` exists).

Kept import-light: this module does not eagerly import `spec`/`cli` (which pull in
`jsonschema`/`typer`) so `import agent_orchestrator.bench` alone stays cheap.
"""

from __future__ import annotations
