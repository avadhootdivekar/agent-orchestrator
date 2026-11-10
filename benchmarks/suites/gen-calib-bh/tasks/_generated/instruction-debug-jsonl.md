# Task: find and fix the bugs in the service described in `SPEC.md`

This directory already contains a complete implementation of `SPEC.md` (see `run.sh` and the
`app/` package), but it has several bugs: in places it does not behave as `SPEC.md` requires.

- `SPEC.md` is the only specification. Every statement in it is a requirement and is checked:
  the normal cases, error responses, ordering and behaviour as the amount of data grows.
- Find and fix every deviation from `SPEC.md`. Fix the existing code rather than rewriting it
  from scratch; keep `./run.sh` starting the service and keep the run contract.
- Use only the dependencies allowed in `SPEC.md`. No network access or external service will be
  available when the service is run.
- Test your work before you finish: start the service through `./run.sh` and exercise it.
- Do not modify `SPEC.md`. There is nothing to commit or hand in; leave the fixed project in this
  directory.

The service is checked through its standard input and output, one JSON object per line.
