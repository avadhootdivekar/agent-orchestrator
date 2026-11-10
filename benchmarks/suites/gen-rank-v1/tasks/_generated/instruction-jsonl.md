# Task: build the service described in `SPEC.md`

This directory is an empty project skeleton. Read `SPEC.md` completely, then implement what it
describes.

- `SPEC.md` is the only specification. Every statement in it is a requirement and is checked:
  the normal cases, error responses, ordering, behaviour under concurrent use, and behaviour as
  the amount of data grows.
- The service is started with `./run.sh`; the run contract in `SPEC.md` says what it must do once
  started. Keep `run.sh` working (you may edit it, but it must still start your service).
- Use only the dependencies allowed in `SPEC.md` (the project manifest already lists them). No
  network access or external service will be available when the service is run.
- Test your work before you finish: start the service through `./run.sh` and exercise it.
- Do not modify `SPEC.md`. There is nothing to commit or hand in; leave the finished project in
  this directory.

The service is checked through its standard input and output, one JSON object per line.
