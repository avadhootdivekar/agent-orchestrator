# Bench operating instructions (shared by every subject)

You are completing one benchmark task. These instructions are identical for every subject
under test, so follow them exactly and do not look for extra context elsewhere.

1. Read `repo/INSTRUCTION.md` (and `repo/SPEC.md` when present) in full before changing anything.
2. Work only inside the `repo/` directory. Do not modify `INSTRUCTION.md`, `SPEC.md`, or any
   file the instruction marks as fixed.
3. If the task has tests or a run contract, make them pass / satisfy it before you finish.
   Run the repo's own tests (`uv run pytest -q`, `npm test`, `go test ./...`) when they exist.
4. Prefer small, correct changes. Do not refactor unrelated code.
5. When done, write a short summary of what you changed and how you verified it.
