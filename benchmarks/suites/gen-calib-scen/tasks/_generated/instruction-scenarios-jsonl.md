# Task: build the move-choosing game bot described in `SPEC.md`

This directory is an empty project skeleton. Read `SPEC.md` completely, then implement what it
describes.

- `SPEC.md` is the only specification. Your bot is asked for one move in each of many positions
  and every answer is scored by how good the move is; a better search earns a higher score, with
  no upper limit. An illegal, late or malformed move scores 0 for that position.
- The bot is started with `./run.sh`; keep `run.sh` working (you may edit it, but it must still
  start your bot).
- Use only the dependencies allowed in `SPEC.md` (the project manifest already lists them). No
  network access or external service will be available when the bot is run.
- Test your work before you finish: generate positions yourself (random play from the start),
  check legality and speed, and check strength by playing your bot against simple opponents.
- Do not modify `SPEC.md`. There is nothing to commit or hand in; leave the finished project in
  this directory.

The service is checked through its standard input and output, one JSON object per line.
