import sqlite3
import pytest
import tip_model as tm
import pregame_model as pm


def make_db():
    con = sqlite3.connect(":memory:")
    con.execute("""CREATE TABLE games (game_id TEXT, game_date TEXT, home_team TEXT, away_team TEXT,
                   tip_winner_team TEXT, tip_winner_player_id TEXT, tip_loser_player_id TEXT)""")
    return con


def tip(con, gid, date, home, away, winner_team, winner_id, loser_id):
    con.execute("INSERT INTO games VALUES (?,?,?,?,?,?,?)", (gid, date, home, away, winner_team, winner_id, loser_id))


def strong_vs_weak_db():
    """BIG (NY) wins all 8 of his tips against SMALL (BOS), home and away."""
    con = make_db()
    for i in range(8):
        home, away = ("NY", "BOS") if i % 2 == 0 else ("BOS", "NY")
        tip(con, f"g{i}", f"202610{i + 10:02d}", home, away, "NY", "big", "small")
    return con


def test_stronger_jumper_is_favoured_and_both_sides_add_to_one():
    con = strong_vs_weak_db()
    p_home = tm.home_win_prob(con, "NY", "BOS", "20261101")
    assert p_home > 0.6
    assert tm.team_win_probs(con, "NY", "BOS", "20261101") == pytest.approx((p_home, 1 - p_home))
    # same matchup with BOS at home: BOS is still the underdog
    assert tm.home_win_prob(con, "BOS", "NY", "20261101") < 0.4


def test_only_games_before_the_date_count():
    con = strong_vs_weak_db()
    before = tm.home_win_prob(con, "NY", "BOS", "20261101")
    tip(con, "today", "20261101", "NY", "BOS", "BOS", "small", "big")     # today's own tip
    tip(con, "later", "20261105", "NY", "BOS", "BOS", "small", "big")
    assert tm.home_win_prob(con, "NY", "BOS", "20261101") == before


def test_no_history_means_home_advantage_only():
    con = make_db()
    p = tm.home_win_prob(con, "NY", "BOS", "20261101")
    assert p == pytest.approx(tm.DEFAULT_HCA)


def test_team_jumper_is_who_actually_jumped_and_must_be_starting():
    con = make_db()
    for i in range(6):                          # C1 jumps 4 times, C2 twice (most recent)
        jumper = "c2" if i >= 4 else "c1"
        tip(con, f"g{i}", f"202610{i + 10:02d}", "NY", "BOS", "NY", jumper, "x")
    assert tm.team_jumper(con, "NY", "20261101") == "c1"
    assert tm.team_jumper(con, "NY", "20261101", starter_ids=["c2", "a", "b", "d", "e"]) == "c2"
    assert tm.team_jumper(con, "NY", "20261101", starter_ids=["a", "b", "d", "e", "f"]) is None
    assert tm.team_jumper(con, "BOS", "20261101") == "x"


def test_build_row_uses_the_given_team_tip_probability():
    p = {"first_basket_rate": 0.1, "L10_first_basket_rate": 0.1, "games_played": 10}
    assert pm.build_row(p, {}, 1, tip_win_prob=0.7)[-1] == 0.7
    assert pm.build_row(p, {}, 0)[-1] == 0.5
