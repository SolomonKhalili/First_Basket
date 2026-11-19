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


def test_if_the_usual_jumper_sits_the_most_experienced_starter_jumps():
    con = make_db()
    for i in range(10):                      # NY's usual jumper C1 took all of the last 10 tips (not starting tonight)
        tip(con, f"g{i}", f"202610{i + 10:02d}", "NY", "BOS", "NY", "c1", "x")
    tip(con, "h1", "20261001", "LAL", "NY", "LAL", "big", "sub")   # SUB (starting tonight) jumped once, for NY
    tip(con, "h2", "20261002", "LAL", "MIA", "MIA", "y", "big")
    assert tm.team_jumper(con, "NY", "20261101", starter_ids=["sub", "a", "b", "d", "e"]) == "sub"
    # nobody starting has ever jumped -> unknown
    assert tm.team_jumper(con, "NY", "20261101", starter_ids=["a", "b", "d", "e", "f"]) is None
    # only tips BEFORE the date count
    assert tm.team_jumper(con, "NY", "20261001", starter_ids=["sub", "a", "b", "d", "e"]) is None


def test_nba_com_listed_center_jumps_unless_another_starter_is_the_regular_jumper():
    con = make_db()
    for i in range(10):                       # PF "ad" took 7 of NY's last 10 tips, C "sarr" took 3
        tip(con, f"g{i}", f"202610{i + 10:02d}", "NY", "BOS", "NY", "ad" if i < 7 else "sarr", "x")
    starters = ["ad", "sarr", "a", "b", "c"]
    assert tm.team_jumper(con, "NY", "20261101", starters, listed_center="sarr") == "ad"   # clear regular jumper
    assert tm.team_jumper(con, "NY", "20261101", starters) == "ad"                         # no position info: as before
    assert tm.team_jumper(con, "NY", "20261101", ["new_c", "a", "b", "c", "d"], listed_center="new_c") == "new_c"
    con.execute("UPDATE games SET tip_winner_player_id = 'sarr' WHERE game_id IN ('g0','g1')")  # now 5 vs 5
    assert tm.team_jumper(con, "NY", "20261101", starters, listed_center="sarr") == "sarr"  # no clear regular -> listed C


def test_build_row_uses_the_given_team_tip_probability():
    p = {"first_basket_rate": 0.1, "L10_first_basket_rate": 0.1, "games_played": 10}
    assert pm.build_row(p, {}, 1, tip_win_prob=0.7)[pm.TIP] == 0.7
    assert pm.build_row(p, {}, 0)[pm.TIP] == 0.5


def test_extended_history_adds_2024_25_tips_only_when_asked():
    con = make_db()
    con.execute("""CREATE TABLE old_tips (game_id TEXT, game_date TEXT, home_team TEXT, away_team TEXT,
                   tip_winner_team TEXT, tip_winner_player_id TEXT, tip_winner_player_name TEXT,
                   tip_loser_player_id TEXT, tip_loser_player_name TEXT)""")
    for i in range(20):                           # last season BIG beat SMALL 20 times
        con.execute("INSERT INTO old_tips VALUES (?,?,?,?,?,?,?,?,?)",
                    (f"o{i}", f"202412{i + 1:02d}", "NY", "BOS", "NY", "big", "Big", "small", "Small"))
    tip(con, "g0", "20261025", "NY", "BOS", "BOS", "small", "big")     # this season SMALL won once
    normal = tm.home_win_prob(con, "NY", "BOS", "20261101")
    extended = tm.home_win_prob(con, "NY", "BOS", "20261101", extended=True)
    assert normal < 0.5 < extended                 # only the extended call knows BIG's record
    assert tm.jumper_rate(con, "big", "20261101") < 0.5 < tm.jumper_rate(con, "big", "20261101", extended=True)
    # no old_tips table at all -> extended quietly behaves like normal
    # no history: just the home edge (the extended call blends in the rating's own small home edge)
    assert tm.home_win_prob(make_db(), "NY", "BOS", "20261101", extended=True) == pytest.approx(tm.DEFAULT_HCA, abs=0.005)
