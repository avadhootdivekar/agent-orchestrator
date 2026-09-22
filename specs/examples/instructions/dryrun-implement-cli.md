# Instruction: implement CLI (`ao run --dry-run` worked example)

Payload referenced by path. Read the design note at the provided input path. Implement the
`--dry-run` flag in the `core` repo (`src/agent_orchestrator/cli.py`'s `run` command and
whatever `engine.py` entry point it needs), sized to a single focused change: this task owns
CLI/engine code only, not documentation (see the sibling `implement-docs` task, which runs in
parallel against a disjoint set of files). Write a short implementation report to the declared
output path.
