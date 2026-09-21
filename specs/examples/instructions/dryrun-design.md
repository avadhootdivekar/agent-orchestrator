# Instruction: design (`ao run --dry-run` worked example)

Payload referenced by path — the orchestrator never reads this file's content, only the
assigned agent does.

Task: design a `--dry-run` flag for `ao run` that resolves the DAG, prints the tasks that
*would* dispatch (in topological order, honoring `skip_if_outputs_exist`) and their resolved
`agent`/`model`/`effort`/`isolation` settings, but dispatches no agent process and writes no
task artifacts. Write a short design note to the declared output path covering: the flag's
CLI surface, what "would dispatch" means for an `emit_tasks` task (can't know its dynamic
children without running it — decide and state the limitation), and the console/JSON output
shape. This is the shared input every downstream task in this workflow reads from.
