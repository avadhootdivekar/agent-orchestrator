# Task: build the game-playing bot described in `SPEC.md`

This directory is an empty project skeleton. Read `SPEC.md` completely, then implement what it
describes.

- `SPEC.md` is the only specification. Your bot is rated by playing reference bots of increasing
  strength; a stronger bot earns a higher rating, with no upper limit. An illegal, late or
  malformed move loses that game.
- The bot is started with `./run.sh`; keep `run.sh` working (you may edit it, but it must still
  start your bot).
- Use only the dependencies allowed in `SPEC.md` (the project manifest already lists them). No
  network access or external service will be available when the bot is run.
- Test your work before you finish: write your own game loop, play your bot against simple
  opponents and against itself, and check legality, speed and strength.
- Do not modify `SPEC.md`. There is nothing to commit or hand in; leave the finished project in
  this directory.

The service is checked through its standard input and output, one JSON object per line.
