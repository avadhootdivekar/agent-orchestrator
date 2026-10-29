"""Games plugin (T-Ga4Rna): rules, wire format, reference bots, referee and the Elo maths."""

from __future__ import annotations

import random

import pytest

from agent_orchestrator.bench.games import GAME_REGISTRY, GameError, game_from_wire
from agent_orchestrator.bench.games.base import DRAW, LOSS, WIN, Game
from agent_orchestrator.bench.games.bots import make_bot
from agent_orchestrator.bench.games.breakthrough import Breakthrough
from agent_orchestrator.bench.games.connectk import ConnectK
from agent_orchestrator.bench.games.ladder import ladder_version, load_ladder, write_ladder
from agent_orchestrator.bench.games.othello import Othello
from agent_orchestrator.bench.games.rating import expected, fit_round_robin, rate_against
from agent_orchestrator.bench.games.referee import (
    BotPlayer,
    Forfeit,
    make_opening,
    play_game,
    play_pair,
    replay,
    score_for_a,
)
from agent_orchestrator.bench.games.roundrobin import RoundRobin

SAMPLE_GAMES = [
    {"id": "othello", "size": 8},
    {"id": "othello", "size": 6},
    {"id": "connectk", "width": 7, "height": 6, "k": 4, "gravity": 1},
    {"id": "connectk", "width": 6, "height": 6, "k": 4, "gravity": 0},
    {"id": "breakthrough", "size": 8},
    {"id": "breakthrough", "size": 6},
]
OTHELLO_PERFT = [4, 12, 56, 244, 1396, 8200]  # published counts for the 8x8 start position


def perft(g: Game, s: object, depth: int) -> int:
    moves = g.legal(s)
    if depth == 0 or not moves:
        return 1
    return sum(perft(g, g.apply(s, m), depth - 1) for m in moves)


# -- registry and parameters --------------------------------------------------------------------


def test_registry_has_the_shipped_games() -> None:
    assert {"othello", "connectk", "breakthrough"} <= set(GAME_REGISTRY)


@pytest.mark.parametrize("wire", SAMPLE_GAMES)
def test_wire_round_trip(wire: dict) -> None:
    g = game_from_wire(wire)
    assert g.wire() == wire
    assert game_from_wire(g.wire()).key == g.key


@pytest.mark.parametrize(
    "wire",
    [
        {"id": "nope"},
        {"id": "othello", "size": 7},
        {"id": "othello", "size": 8, "extra": 1},
        {"id": "othello", "size": True},
        {"id": "connectk", "width": 7, "height": 6, "k": 9, "gravity": 1},
        {"id": "connectk", "width": 7, "height": 6, "k": 4},
        {"id": "breakthrough", "size": 3},
    ],
)
def test_bad_parameters_are_rejected(wire: dict) -> None:
    with pytest.raises(GameError):
        game_from_wire(wire)


# -- generic contract, on every game ------------------------------------------------------------


@pytest.mark.parametrize("wire", SAMPLE_GAMES)
def test_random_playouts_keep_the_contract(wire: dict) -> None:
    g = game_from_wire(wire)
    rng = random.Random(7)
    for _ in range(25):
        s = g.initial()
        assert not g.terminal(s) and g.legal(s)
        while not g.terminal(s):
            moves = g.legal(s)
            assert moves, "a non-terminal state always has a legal move"
            assert len(set(moves)) == len(moves)
            m = rng.choice(moves)
            assert g.decode_move(g.encode_move(m)) == m
            assert g.to_move(s) in (0, 1)
            s = g.apply(s, m)
            assert g.plies(s) <= g.max_plies()
        assert g.legal(s) == [] and g.to_move(s) == -1
        assert g.result(s) in (WIN, DRAW, LOSS)


@pytest.mark.parametrize("wire", SAMPLE_GAMES)
def test_wire_state_and_rules_text(wire: dict) -> None:
    g = game_from_wire(wire)
    st = g.encode_state(g.initial())
    assert st["to_move"] == 0 and st["plies"] == 0 and isinstance(st["board"], list)
    assert "State on the wire" in g.rules_md()
    assert g.heuristic(g.initial(), 0) == pytest.approx(-g.heuristic(g.initial(), 1))


def test_decode_move_is_strict() -> None:
    g = Othello(8)
    for bad in (True, 1.0, "3", None, [], [1.5], {"a": 1}):
        with pytest.raises(GameError):
            g.decode_move(bad)
    assert g.decode_move(19) == 19
    assert Breakthrough(8).decode_move([8, 16]) == (8, 16)


# -- Othello ------------------------------------------------------------------------------------


def test_othello_perft_matches_published_counts() -> None:
    g = Othello(8)
    assert [perft(g, g.initial(), d) for d in range(1, 7)] == OTHELLO_PERFT


def test_othello_opening_moves_and_flips() -> None:
    g = Othello(8)
    assert g.legal(g.initial()) == [19, 26, 37, 44]  # d3, c4, f5, e6
    s = g.apply(g.initial(), 19)
    assert s[0].bit_count() == 4 and s[1].bit_count() == 1 and s[2] == 1


def test_othello_pass_is_handled_inside_apply() -> None:
    g = Othello(6)
    rng = random.Random(3)
    seen_pass = False
    for _ in range(300):
        s = g.initial()
        while not g.terminal(s):
            mover = g.to_move(s)
            s = g.apply(s, rng.choice(g.legal(s)))
            if not g.terminal(s) and g.to_move(s) == mover:
                seen_pass = True  # the opponent had no move and was skipped
        if seen_pass:
            break
    assert seen_pass


def test_othello_result_counts_discs() -> None:
    g = Othello(4)
    black_wins = (0b1111, 0b0, -1, 10)
    assert g.result(black_wins) == WIN
    assert g.result((0b0, 0b1, -1, 10)) == LOSS
    assert g.result((0b1, 0b10, -1, 10)) == DRAW


# -- Connect-K ----------------------------------------------------------------------------------


def _play(g: Game, moves: list) -> object:
    s = g.initial()
    for m in moves:
        s = g.apply(s, m)
    return s


def test_connectk_gravity_drops_to_the_lowest_cell() -> None:
    g = ConnectK(7, 6, 4, 1)
    s = _play(g, [3, 3])
    assert s[0][5 * 7 + 3] == 1 and s[0][4 * 7 + 3] == 2
    assert g.legal(g.initial()) == [3, 2, 4, 1, 5, 0, 6]  # centre-out
    full_column = _play(g, [0, 0, 0, 0, 0, 0])
    assert 0 not in g.legal(full_column)


def test_connectk_detects_every_line_direction() -> None:
    g = ConnectK(7, 6, 4, 1)
    assert g.result(_play(g, [0, 0, 1, 1, 2, 2, 3])) == WIN  # horizontal, player 0
    assert g.result(_play(g, [0, 1, 0, 1, 0, 1, 0])) == WIN  # vertical
    # Player 0 builds the rising diagonal (0,5) (1,4) (2,3) (3,2).
    diag = _play(g, [0, 1, 1, 2, 3, 2, 2, 3, 3, 6, 3])
    assert g.terminal(diag) and g.result(diag) == WIN
    assert g.result(_play(g, [0, 1, 0, 1, 0, 1, 2, 1])) == LOSS  # player 1 wins vertically


def test_connectk_free_placement_and_draw() -> None:
    g = ConnectK(3, 3, 3, 0)
    assert sorted(g.legal(g.initial())) == list(range(9))
    # X O X / X O O / O X X : full board, no line of three
    drawn = _play(g, [0, 1, 2, 4, 3, 5, 7, 6, 8])
    assert g.terminal(drawn) and g.result(drawn) == DRAW
    row = _play(g, [0, 3, 1, 4, 2])  # X completes the top row
    assert g.terminal(row) and g.result(row) == WIN


# -- Breakthrough -------------------------------------------------------------------------------


def test_breakthrough_start_moves_and_capture_rules() -> None:
    g = Breakthrough(8)
    assert len(g.legal(g.initial())) == 22  # row 1: 8 straight + 14 diagonal; row 0 is blocked
    # Player 0 on square 9 (row 1, col 1); enemies on 17 (straight ahead) and 18 (diagonal).
    s = (1 << 9, (1 << 17) | (1 << 18), 0, 0, -1)
    assert sorted(g.legal(s)) == [(9, 16), (9, 18)]  # no straight move into 17
    after = g.apply(s, (9, 18))
    assert after[1] == 1 << 17  # the diagonal capture removed the piece on 18


def test_breakthrough_win_by_reaching_the_far_row_or_capturing_the_last_piece() -> None:
    g = Breakthrough(5)
    near = (1 << 15, 1 << 24, 0, 10, -1)  # player 0 on row 3, player 1 far away on row 4
    reached = g.apply(near, (15, 20))
    assert g.terminal(reached) and g.result(reached) == WIN
    last = g.apply((1 << 11, 1 << 17, 0, 4, -1), (11, 17))  # captures the only enemy piece
    assert g.terminal(last) and g.result(last) == WIN
    blocked = (1 << 5 | 1 << 10, 1 << 15 | 1 << 9 | 1 << 11, 0, 0, -1)
    assert not g.terminal(g.apply(blocked, (5, 6)))  # the enemy still has moves


# -- reference bots -----------------------------------------------------------------------------


def _bot_game(g: Game, a: tuple, b: tuple, seed: int) -> float:
    op = make_opening(g, seed, 2)
    return play_game(g, (BotPlayer(make_bot(a)), BotPlayer(make_bot(b))), op, 1000, seed).result


def test_bots_are_deterministic_from_the_seed() -> None:
    g = Othello(6)
    runs = []
    for _ in range(2):
        op = make_opening(g, 5, 4)
        rec = play_game(
            g, (BotPlayer(make_bot(("ab", 2))), BotPlayer(make_bot(("random",)))), op, 1000, 5
        )
        runs.append(rec.moves)
    assert runs[0] == runs[1]


def test_alpha_beta_takes_a_win_and_greedy_sees_it() -> None:
    g = ConnectK(7, 6, 4, 1)
    s = _play(g, [0, 6, 1, 6, 2, 5])  # player 0 has three in a row on the bottom: col 3 wins
    for spec in (("greedy",), ("ab", 2), ("mcts", 200)):
        bot = make_bot(spec)
        bot.new_game(g, 0, 1)
        assert bot.move(g, s, g.legal(s)) == 3, spec


def test_alpha_beta_blocks_when_it_sees_two_plies() -> None:
    g = ConnectK(7, 6, 4, 1)
    s = _play(g, [6, 0, 6, 1, 5, 2])  # player 1 threatens 0-1-2 -> column 3
    bot = make_bot(("ab", 2))
    bot.new_game(g, 0, 1)
    assert bot.move(g, s, g.legal(s)) == 3


@pytest.mark.parametrize("wire", SAMPLE_GAMES[:1] + SAMPLE_GAMES[2:3] + SAMPLE_GAMES[4:5])
def test_deeper_search_beats_random(wire: dict) -> None:
    g = game_from_wire(wire)
    score = 0.0
    for seed in range(4):
        op = make_opening(g, seed, 2)
        a, b = play_pair(
            g, BotPlayer(make_bot(("ab", 2))), BotPlayer(make_bot(("random",))), op, 1000, seed
        )
        score += score_for_a(a, b)[0]
    assert score >= 7  # at least 7 of 8 points


# -- referee ------------------------------------------------------------------------------------


class _Scripted:
    def __init__(self, move: object = None, exc: Exception | None = None) -> None:
        self.move, self.exc = move, exc

    def start(self, game: Game, you: int, ms: int, seed: int) -> None: ...
    def finish(self) -> None: ...

    def choose(self, game: Game, state: object, legal: list, history: list) -> object:
        if self.exc:
            raise self.exc
        return self.move


def test_illegal_move_and_forfeit_end_the_game() -> None:
    g = Othello(8)
    random_bot = BotPlayer(make_bot(("random",)))
    rec = play_game(g, (_Scripted(0), random_bot), [], 1000, 1)
    assert rec.forfeit == 0 and rec.result == LOSS and "illegal" in rec.reason
    rec = play_game(g, (random_bot, _Scripted(exc=Forfeit("late"))), [], 1000, 1)
    assert rec.forfeit == 1 and rec.result == WIN and rec.reason == "late"


def test_opening_is_never_terminal_and_replays() -> None:
    for wire in SAMPLE_GAMES:
        g = game_from_wire(wire)
        op = make_opening(g, 11, 6)
        assert len(op) == 6 and not g.terminal(replay(g, op))
        assert make_opening(g, 11, 6) == op


def test_pair_plays_both_colours() -> None:
    g = ConnectK(7, 6, 4, 1)
    op = make_opening(g, 2, 2)
    a, b = play_pair(
        g, BotPlayer(make_bot(("ab", 4))), BotPlayer(make_bot(("random",))), op, 1000, 2
    )
    assert score_for_a(a, b)[1] == 2.0 and score_for_a(a, b)[0] >= 1.0


# -- rating -------------------------------------------------------------------------------------


def test_expected_score_is_the_logistic() -> None:
    assert expected(0, 0) == pytest.approx(0.5)
    assert expected(400, 0) == pytest.approx(10 / 11)


def test_rate_against_recovers_the_rating_from_expected_results() -> None:
    opps = [0.0, 200.0, 400.0]
    results = [(o, 20 * expected(300.0, o), 20.0) for o in opps]
    assert rate_against(results).rating == pytest.approx(300.0, abs=15.0)


def test_perfect_and_empty_results_stay_finite() -> None:
    top = rate_against([(1000.0, 10.0, 10.0)])
    assert 1000.0 < top.rating < 2500.0 and top.se > 0
    low = rate_against([(0.0, 0.0, 10.0)])
    assert -2000.0 <= low.rating < 0.0
    assert rate_against([]).rating == 0.0
    lo, hi = rate_against([(0.0, 5.0, 10.0)]).ci95
    assert lo < 0.0 < hi


def test_fit_round_robin_recovers_ordering_and_scale() -> None:
    truth = [0.0, 150.0, 400.0, 700.0]
    pairs = {
        (i, j): (50 * expected(truth[i], truth[j]), 50.0) for i in range(4) for j in range(i + 1, 4)
    }
    got = fit_round_robin(4, pairs)
    assert got[0] == 0.0
    assert got == sorted(got)
    for g, t in zip(got, truth):  # the one-draw prior shrinks the extremes a little
        assert g == pytest.approx(t, abs=60.0)


# -- ladder versions and frozen ratings ---------------------------------------------------------


def test_ladder_version_tracks_rules_and_ladder(tmp_path) -> None:
    g = Othello(8)
    v = ladder_version(g)
    assert v == ladder_version(Othello(8)) and v != ladder_version(Othello(6))
    rr = RoundRobin([0.0] * len(g.ladder()), {}, 0.5, 0.0, 10)
    path = write_ladder(g, rr, root=tmp_path)
    assert load_ladder(g, root=tmp_path).version == v
    with pytest.raises(GameError, match="no frozen ratings"):
        load_ladder(Othello(6), root=tmp_path)
    path.write_text(path.read_text().replace(v, "0" * 12))
    with pytest.raises(GameError, match="stale"):
        load_ladder(g, root=tmp_path)
