import sqlite3

import pytest

import ev


# ── The math ─────────────────────────────────────────────────
def test_profit_and_implied_chance():
    assert ev.profit_per_dollar(460) == pytest.approx(4.60)
    assert ev.profit_per_dollar(-150) == pytest.approx(100 / 150)
    assert ev.implied_chance(400) == pytest.approx(0.20)
    assert ev.implied_chance(-200) == pytest.approx(2 / 3)


def test_ev_per_dollar():
    # 20.3% at +460: 0.203 * 4.6 - 0.797 = +0.1368
    assert ev.ev_per_dollar(0.203, 460) == pytest.approx(0.203 * 4.6 - 0.797)
    assert ev.ev_per_dollar(0.20, 400) == pytest.approx(0.0)        # exactly the fair price: break even
    assert ev.ev_per_dollar(0.179, 330) < 0                         # Giannis tonight: FanDuel pays too little
    assert ev.edge(0.25, 400) == pytest.approx(0.05)


def test_priced_players_sorted_by_ev_and_only_priced():
    players = [{"id": "1", "name": "Bam", "team": "MIA", "prob": 0.203},
               {"id": "2", "name": "Giannis", "team": "MIA", "prob": 0.179},
               {"id": "3", "name": "Bench Guy", "team": "MIL", "prob": 0.05}]
    rows = ev.priced_players(players, {"Bam": 460, "Giannis": 330})
    assert [r["name"] for r in rows] == ["Bam", "Giannis"]          # best EV first; no price -> left out
    assert rows[0]["ev"] > 0 > rows[1]["ev"] and rows[0]["american"] == 460


# ── Saving prices and paper bets ─────────────────────────────
def db():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE first_baskets (game_id TEXT PRIMARY KEY, scorer_player_id TEXT, scorer_name TEXT)")
    return con


def snap(*rows):
    return [{"id": pid, "name": name, "team": team, "prob": prob, "american": a, "ev": ev.ev_per_dollar(prob, a)}
            for pid, name, team, prob, a in rows]


def test_log_odds_skips_unchanged_snapshots():
    con = db()
    rows = snap(("1", "Bam", "MIA", 0.2, 460), ("2", "Giannis", "MIA", 0.18, 330))
    assert ev.log_odds(con, "g1", "20261118", "MIL @ MIA", rows, logged_at="2026-11-18T12:00:00")
    assert not ev.log_odds(con, "g1", "20261118", "MIL @ MIA", rows, logged_at="2026-11-18T13:00:00")
    moved = snap(("1", "Bam", "MIA", 0.2, 500), ("2", "Giannis", "MIA", 0.18, 330))     # Bam's price moved
    assert ev.log_odds(con, "g1", "20261118", "MIL @ MIA", moved, logged_at="2026-11-18T14:00:00")
    assert not ev.log_odds(con, "g1", "20261118", "MIL @ MIA", [])
    assert con.execute("SELECT COUNT(*) FROM odds_log").fetchone()[0] == 4


def test_paper_results_uses_last_snapshot_and_only_positive_ev():
    con = db()
    early = snap(("1", "Bam", "MIA", 0.2, 900))                     # earlier, bigger price: must be ignored
    late = snap(("1", "Bam", "MIA", 0.2, 460), ("2", "Giannis", "MIA", 0.18, 330), ("3", "Herro", "MIL", 0.11, 900))
    ev.log_odds(con, "g1", "20261118", "MIL @ MIA", early, logged_at="2026-11-18T12:00:00")
    ev.log_odds(con, "g1", "20261118", "MIL @ MIA", late, logged_at="2026-11-18T18:00:00")
    ev.log_odds(con, "g2", "20261118", "ATL @ OKC", snap(("9", "SGA", "OKC", 0.25, 380)),
                logged_at="2026-11-18T18:00:00")                  # not finished yet: no first_baskets row
    con.execute("INSERT INTO first_baskets VALUES ('g1', '1', 'Bam')")
    r = ev.paper_results(con)
    # +EV at the last snapshot: Bam (0.2*4.6-0.8 = +0.12) and Herro (0.11*9-0.89 = +0.10); Giannis is negative
    assert {b["player"] for b in r["rows"]} == {"Bam", "Herro"}
    assert r["bets"] == 2 and r["wins"] == 1 and r["games"] == 1
    assert r["profit"] == pytest.approx(4.60 - 1.0)                 # Bam won at +460, Herro lost $1
    assert r["roi"] == pytest.approx(3.60 / 2)
    assert r["expected_profit"] == pytest.approx(ev.ev_per_dollar(0.2, 460) + ev.ev_per_dollar(0.11, 900))


def test_paper_results_empty():
    r = ev.paper_results(db())
    assert r["bets"] == 0 and r["profit"] == 0 and r["roi"] == 0.0 and r["rows"] == []


def test_value_threshold_is_one_cent():
    assert not ev.is_value(0.004) and ev.is_value(0.005) and ev.is_value(0.12) and not ev.is_value(None)
    assert not ev.is_value(-0.1)


def test_closing_line_value():
    """A good-deal pick whose price shortens before tip-off has positive CLV; drift of everyone is the yardstick."""
    import sqlite3
    import ev
    con = sqlite3.connect(":memory:")
    ev.ensure_table(con)
    con.execute("CREATE TABLE first_baskets (game_id TEXT, scorer_player_id TEXT, scorer_name TEXT)")
    con.execute("INSERT INTO first_baskets VALUES ('g', '9', 'X')")
    for at, a1, e1, a2, e2 in (("t1", 1000, 0.10, 500, -0.2), ("t2", 700, -0.05, 500, -0.2)):
        con.execute("INSERT INTO odds_log (game_id, game_date, logged_at, player_id, player_name, american, ev) VALUES ('g','d',?,'1','A',?,?)", (at, a1, e1))
        con.execute("INSERT INTO odds_log (game_id, game_date, logged_at, player_id, player_name, american, ev) VALUES ('g','d',?,'2','B',?,?)", (at, a2, e2))
    r = ev.clv_results(con)
    assert r["picks"] == 1 and r["shorter"] == 1 and abs(r["avg_clv"] - (ev.implied(700) / ev.implied(1000) - 1)) < 1e-9
    assert r["all_players"]["picks"] == 2


def test_who_guesses_better():
    import sqlite3
    import ev
    con = sqlite3.connect(":memory:")
    ev.ensure_table(con)
    con.execute("CREATE TABLE first_baskets (game_id TEXT, scorer_player_id TEXT, scorer_name TEXT)")
    con.execute("INSERT INTO first_baskets VALUES ('g', '1', 'A')")
    for pid, prob, american in (("1", 0.30, 400), ("2", 0.70, 100)):   # FanDuel: A 20/(20+50)=28.6% < our 30%
        con.execute("INSERT INTO odds_log (game_id, game_date, logged_at, player_id, prob, american) VALUES ('g','d','t',?,?,?)",
                    (pid, prob, american))
    r = ev.market_results(con)
    assert r["games"] == 1 and r["us_closer"] == 1 and r["fanduel_closer"] == 0


def test_opening_prices_both_books():
    import sqlite3
    import ev
    con = sqlite3.connect(":memory:")
    ev.ensure_table(con)
    con.execute("CREATE TABLE dk_odds_log (game_date TEXT, matchup TEXT, logged_at TEXT, player_name TEXT, team TEXT, american INTEGER)")
    for at, a in (("t1", 700), ("t2", 600)):
        con.execute("INSERT INTO odds_log (game_id, game_date, logged_at, matchup, player_name, american) VALUES ('g','d',?,'A @ B','P',?)", (at, a))
        con.execute("INSERT INTO dk_odds_log VALUES ('d','A @ B',?,'P','B',?)", (at, a + 50))
    assert ev.opening_prices(con, "d", "A @ B") == {"FanDuel": {"P": 700}, "DraftKings": {"P": 750}}


def test_odds_moves_log_both_books():
    import sqlite3
    import ev
    con = sqlite3.connect(":memory:")
    ev.ensure_table(con)
    con.execute("CREATE TABLE dk_odds_log (game_date TEXT, matchup TEXT, logged_at TEXT, player_name TEXT, team TEXT, american INTEGER)")
    for at, a in (("t1", 700), ("t2", 650), ("t3", 600)):
        con.execute("INSERT INTO odds_log (game_id, game_date, logged_at, matchup, player_name, american) VALUES ('g','20261119',?,'A @ B','P',?)", (at, a))
    con.execute("INSERT INTO dk_odds_log VALUES ('20261119','A @ B','t1','P','B',800)")
    rows = {r[3]: r for r in ev.odds_moves(con)}
    assert rows["FanDuel"][4:8] == ["+700", "+600", "↓ payout dropped", 2]
    assert rows["FanDuel"][8].count("→") == 2                        # every change listed
    assert rows["DraftKings"][4:8] == ["+800", "+800", "same", 0]
