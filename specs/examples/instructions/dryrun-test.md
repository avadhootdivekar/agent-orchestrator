# Instruction: test (`ao run --dry-run` worked example)

Payload referenced by path. Read both implementation reports at the provided input paths (the
CLI change and the docs change — this task depends on both parallel implement tasks, so it only
starts once each has settled, `join: "all"` by default). Run/author tests for the `--dry-run`
flag (a real run resolves the DAG without dispatching, a `--dry-run` run against a spec with an
`emit_tasks` task documents the stated limitation from the design note). Write a test report to
the declared output path.
