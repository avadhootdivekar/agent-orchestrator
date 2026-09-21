# Instruction: implement docs (`ao run --dry-run` worked example)

Payload referenced by path. Read the design note at the provided input path. Document the new
`--dry-run` flag in the `docs` repo (`docs-md/`): CLI reference, one example invocation and its
output shape. Sized to documentation only — no source changes (see the sibling
`implement-cli` task, which runs in parallel against a disjoint set of files, so the two tasks
never write to the same path). Write a short implementation report to the declared output path.
