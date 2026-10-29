"""Reference bot for `game-arena` (grader-owned): a reference-ladder bot served over jsonl.

Used for the self-check (the strongest rung, as an artefact, must rate near its own frozen
rating) and as a stand-in subject in tests. It needs the ``agent_orchestrator`` package: set
``AO_BENCH_SRC`` to the ``src`` directory if the package is not importable. The rung is
``limits.reference_rung`` of ``instance.json`` (default: the strongest).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _load_instance() -> dict:
    path = os.environ.get("INSTANCE_JSON") or str(Path(__file__).resolve().parent / "instance.json")
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main() -> None:
    src = os.environ.get("AO_BENCH_SRC")
    if src:
        sys.path.insert(0, src)
    from agent_orchestrator.bench.games import game_from_wire
    from agent_orchestrator.bench.games.bots import make_bot
    from agent_orchestrator.bench.games.referee import replay

    limits = _load_instance().get("limits", {})
    rung = limits.get("reference_rung")
    game = bot = None
    games_seen = 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            op = req.get("op")
            if op == "ping":
                resp: dict = {"ok": True}
            elif op == "new":
                game = game_from_wire(req["game"])
                specs = game.ladder()
                bot = make_bot(specs[rung if rung is not None else len(specs) - 1])
                games_seen += 1
                bot.new_game(game, int(req["you"]), games_seen)
                resp = {"ok": True}
            elif op == "move" and game is not None and bot is not None:
                history = [game.decode_move(m) for m in req["history"]]
                state = replay(game, history)
                legal = [game.decode_move(m) for m in req["legal"]]
                resp = {"ok": True, "move": game.encode_move(bot.move(game, state, legal))}
            else:
                resp = {"ok": False, "error": f"bad op {op!r}"}
        except Exception as exc:  # noqa: BLE001 -- a protocol server answers, never crashes
            resp = {"ok": False, "error": str(exc)}
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
