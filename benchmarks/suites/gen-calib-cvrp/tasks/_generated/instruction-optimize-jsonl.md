# Task: build the optimisation solver described in `SPEC.md`

This directory is an empty project skeleton. Read `SPEC.md` completely, then implement what it
describes.

- `SPEC.md` is the only specification. Any feasible answer is accepted; answers are scored by
  quality (the objective in `SPEC.md`) against reference solvers of increasing strength, with no
  upper limit. Late, infeasible or malformed answers score 0.
- The solver is started with `./run.sh`; keep `run.sh` working (you may edit it, but it must still
  start your solver).
- Use only the dependencies allowed in `SPEC.md` (the project manifest already lists them). No
  network access or external service will be available when the solver is run.
- Test your work before you finish: start the solver through `./run.sh`, feed it instances of the
  stated sizes and check feasibility, objective and time.
- Do not modify `SPEC.md`. There is nothing to commit or hand in; leave the finished project in
  this directory.

The service is checked through its standard input and output, one JSON object per line.
