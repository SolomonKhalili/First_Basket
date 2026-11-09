import sqlite3
import pytest
import starters as st


def make_db():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE games (game_id TEXT, game_date TEXT, home_team TEXT, away_team TEXT)")
    st.ensure_lineups_table(con)
    return con


def add_game(con, gid, date, team, starters, bench=()):
    con.execute("INSERT INTO games VALUES (?,?,?,?)", (gid, date, team, "XXX"))
    rows = [(gid, team, p, p.upper(), 1, 30.0) for p in starters]
    rows += [(gid, team, p, p.upper(), 0, 15.0) for p in bench]
    st.save_lineups(con, rows)


OLD = ["a", "b", "c", "d", "e"]
NEW = ["a", "b", "c", "d", "z"]  # 'e' traded away, 'z' arrived


def test_2026_games_are_used_not_stale_2025():
    """Regression: old SQL compared 'YYYYMMDD' to date('now') ('YYYY-MM-DD'),
    which silently dropped every 2026 game and returned December-2025 lineups."""
    con = make_db()
    add_game(con, "g1", "20251230", "NY", OLD)
    for i, d in enumerate(["20261030", "20261102", "20261104"]):
        add_game(con, f"n{i}", d, "NY", NEW)
    res = st.recent_starters(con, "NY", "2026-11-07", n_games=3)
    ids = {p["player_id"] for p in res["players"]}
    assert ids == set(NEW) and "e" not in ids
    assert res["games_used"] == 3


def test_before_date_excludes_that_day_and_later():
    con = make_db()
    add_game(con, "g1", "20261104", "NY", OLD)
    add_game(con, "g2", "20261107", "NY", NEW)  # today's game: must not leak in
    res = st.recent_starters(con, "NY", "20261107")
    assert {p["player_id"] for p in res["players"]} == set(OLD)


def test_out_player_replaced_by_top_bench():
    con = make_db()
    for i, d in enumerate(["20261101", "20261103", "20261105"]):
        add_game(con, f"g{i}", d, "NY", OLD, bench=["x", "y"])
    res = st.recent_starters(con, "NY", "20261107", out_names={"A"})
    ids = [p["player_id"] for p in res["players"]]
    assert "a" not in ids and len(ids) == 5 and res["complete"]
    assert ids[-1] in ("x", "y")


def test_day_to_day_kept_and_flagged():
    con = make_db()
    add_game(con, "g1", "20261105", "NY", OLD)
    out, unc = st.split_injuries({"A": "Day-To-Day", "B": "Out"})
    assert out == {"B"} and unc == {"A"}
    res = st.recent_starters(con, "NY", "20261107", out_names=out, uncertain_names=unc)
    a = next(p for p in res["players"] if p["player_id"] == "a")
    assert a["uncertain"] is True
    assert "b" not in [p["player_id"] for p in res["players"]]


def test_incomplete_when_no_history():
    con = make_db()
    assert st.recent_starters(con, "NY", "20261107")["complete"] is False


def test_starts_beat_minutes():
    con = make_db()
    for i, d in enumerate(["20261101", "20261103", "20261105"]):
        add_game(con, f"g{i}", d, "NY", OLD)
    # a sixth player who played huge minutes off the bench must not displace a starter
    con.execute("UPDATE lineups SET minutes=40 WHERE player_id='a'")
    con.execute("INSERT INTO lineups VALUES ('g2','NY','big','BIG',0,45.0)")
    ids = {p["player_id"] for p in st.recent_starters(con, "NY", "20261107")["players"]}
    assert ids == set(OLD)


def test_yyyymmdd():
    assert st.yyyymmdd("2026-11-07") == "20261107" == st.yyyymmdd("20261107")


# ── NBA.com official lineup parsing ──────────────────────────
def _box(home_starters=5, away_starters=5):
    def team(n, prefix):
        return {"players": [{"personId": i, "name": f"{prefix} Player{i}", "starter": "1" if i < n else "0"}
                            for i in range(8)]}
    return {"homeTeam": team(home_starters, "H"), "awayTeam": team(away_starters, "A")}


def test_parse_returns_five_each():
    r = st.parse_nba_starters(_box(), "NY", "CLE", {})
    assert len(r["NY"]) == 5 and len(r["CLE"]) == 5


def test_parse_none_until_lineup_posted():
    assert st.parse_nba_starters(_box(home_starters=0), "NY", "CLE", {}) is None
    assert st.parse_nba_starters(_box(away_starters=4), "NY", "CLE", {}) is None


def test_names_map_to_espn_ids_despite_accents_and_suffixes():
    ids = {st.norm_name("Nikola Jokic"): "3112335", st.norm_name("Wendell Carter Jr."): "4278039"}
    assert st.norm_name("Nikola Jokić") == st.norm_name("Nikola Jokic")
    assert st.norm_name("Wendell Carter Jr") == st.norm_name("Wendell Carter Jr.")
    box = {"homeTeam": {"players": [{"personId": 1, "name": "Nikola Jokić", "starter": "1"}] +
                        [{"personId": i, "name": f"X{i}", "starter": "1"} for i in range(2, 6)]},
           "awayTeam": {"players": [{"personId": i, "name": f"Y{i}", "starter": "1"} for i in range(5)]}}
    r = st.parse_nba_starters(box, "DEN", "IND", ids)
    jok = r["DEN"][0]
    assert jok["player_id"] == "3112335" and jok["id_matched"] is True
    assert r["DEN"][1]["id_matched"] is False and r["DEN"][1]["player_id"].startswith("nba")


def test_tricode_mapping():
    assert st.ESPN_TO_NBA["NY"] == "NYK" and st.ESPN_TO_NBA["GS"] == "GSW" and st.ESPN_TO_NBA["UTAH"] == "UTA"


# ── NBA.com daily lineups feed ───────────────────────────────
def _feed_game(status="Expected", n_home=5, bench=False):
    def team(prefix, n):
        pl = [{"personId": i, "playerName": f"{prefix} P{i}", "lineupStatus": status,
               "position": "PG", "rosterStatus": "Active"} for i in range(n)]
        if bench:  # bench players carry no position
            pl += [{"personId": 90 + i, "playerName": f"{prefix} B{i}", "lineupStatus": status,
                    "position": "", "rosterStatus": "Active"} for i in range(8)]
        return {"players": pl}
    return {"homeTeam": team("H", n_home), "awayTeam": team("A", 5)}


def test_daily_expected_is_not_confirmed():
    r = st.parse_daily_game(_feed_game("Expected"), "ATL", "BKN", {})
    assert r["confirmed"] is False and r["statuses"] == ["Expected"]
    assert len(r["teams"]["ATL"]) == 5 and len(r["teams"]["BKN"]) == 5


def test_daily_confirmed_detected_case_insensitive():
    assert st.parse_daily_game(_feed_game("Confirmed"), "ATL", "BKN", {})["confirmed"] is True
    assert st.parse_daily_game(_feed_game("confirmed"), "ATL", "BKN", {})["confirmed"] is True


def test_daily_mixed_status_not_confirmed():
    g = _feed_game("Confirmed")
    g["awayTeam"]["players"][0]["lineupStatus"] = "Expected"
    assert st.parse_daily_game(g, "ATL", "BKN", {})["confirmed"] is False


def test_daily_bench_ignored_and_bad_counts_rejected():
    assert len(st.parse_daily_game(_feed_game(bench=True), "ATL", "BKN", {})["teams"]["ATL"]) == 5
    assert st.parse_daily_game(_feed_game(n_home=4), "ATL", "BKN", {}) is None


# ── injury report ────────────────────────────────────────────
REPORT = [{"name": "Nikola Jokić", "status": "Out", "detail": "knee"},
          {"name": "Jalen Brunson", "status": "Day-To-Day", "detail": ""},
          {"name": "Healthy Guy", "status": "", "detail": ""}]


def test_injury_flags_match_across_accents_and_skip_healthy():
    f = st.injury_flags(["Nikola Jokic", "Jalen Brunson", "Healthy Guy", "Nobody"], REPORT)
    assert [(n, s) for n, s, _ in f] == [("Nikola Jokic", "Out"), ("Jalen Brunson", "Day-To-Day")]


def test_team_injuries_sorted_by_severity_and_mapped_by_team():
    team_of = {st.norm_name("Nikola Jokic"): "DEN", st.norm_name("Jalen Brunson"): "DEN"}
    rows = st.team_injuries("DEN", REPORT, team_of)
    assert [r["name"] for r in rows] == ["Nikola Jokić", "Jalen Brunson"]
    assert st.team_injuries("NY", REPORT, team_of) == []


def test_nba_to_espn_roundtrip():
    assert st.NBA_TO_ESPN["NYK"] == "NY" and st.NBA_TO_ESPN["GSW"] == "GS"


def test_daily_report_renders(monkeypatch):
    con = make_db()
    add_game(con, "g1", "20261105", "BKN", ["a", "b", "c", "d", "e"])
    feed = {"games": [{"gameStatus": 1, "gameStatusText": "7:30 pm ET",
        "homeTeam": {"teamAbbreviation": "ATL", "players": [
            {"playerName": f"H{i}", "position": "PG", "lineupStatus": "Expected"} for i in range(5)]},
        "awayTeam": {"teamAbbreviation": "BKN", "players": [
            {"playerName": "A", "position": "PG", "lineupStatus": "Expected"}] +
            [{"playerName": f"X{i}", "position": "SF", "lineupStatus": "Expected"} for i in range(4)]}}]}
    monkeypatch.setattr(st, "_nba_json", lambda u: feed)
    monkeypatch.setattr(st, "fetch_injury_report", lambda: [{"name": "A", "status": "Out", "detail": "ankle"}])
    out = st.daily_report(con, "20261109")
    assert "BKN @ ATL" in out and "[Expected]" in out and "Out" in out and "ankle" in out


def test_daily_feed_cached_and_404_means_not_published(monkeypatch, capsys):
    calls = []

    class R404(Exception):
        response = type("R", (), {"status_code": 404})()

    def boom(u):
        calls.append(u)
        raise R404()

    st._FEED_CACHE.clear()
    monkeypatch.setattr(st, "_nba_json", boom)
    con = make_db()
    for _ in range(3):
        assert st.nba_daily_lineup("ATL", "LAL", con, ["20261109"]) is None
    assert len(calls) == 1                      # fetched once, not per game
    assert "not published" in capsys.readouterr().out
    st._FEED_CACHE.clear()


def test_projection_swaps_out_injured_usual_starter_and_flags_questionable():
    con = make_db()
    for i, d in enumerate(["20261101", "20261103", "20261105"]):
        add_game(con, f"g{i}", d, "NY", ["Anna", "Bob", "Cy", "Dee", "Eve"], bench=["Flo", "Gus"])
    report = [{"name": "ANNA", "status": "Out", "detail": ""},
              {"name": "BOB", "status": "Questionable", "detail": ""},
              {"name": "CY", "status": "Doubtful", "detail": ""}]
    r = st.project_usual_starters(con, "NY", "20261107", report)
    ids = [p["player_id"] for p in r["players"]]
    assert r["complete"] and len(ids) == 5
    assert "Anna" not in ids and "Cy" not in ids      # Out and Doubtful removed
    assert "Bob" in ids                                # Questionable stays
    assert next(p for p in r["players"] if p["player_id"] == "Bob")["uncertain"] is True
    assert set(r["dropped"]) == {"ANNA", "CY"}


# ── real ESPN injuries row format (from a live capture) ──────
ESPN_ROW = {"id": 1, "display_name": "Atlanta Hawks", "injuries": str([
    {"id": "538188", "shortComment": "Newell (toe) has been ruled out for Monday's game against the Lakers.",
     "status": "Out", "athlete": {"displayName": "Asa Newell", "team": {"abbreviation": "ATL"}},
     "details": {"type": "Toe", "location": "Leg", "side": "Left", "returnDate": "2026-11-13"}},
    {"id": "9", "shortComment": "Day-to-day with soreness.", "status": "Day-To-Day",
     "athlete": {"displayName": "Dyson Daniels", "team": {"abbreviation": "ATL"}}},
])}


def test_parse_real_espn_row():
    r = st.parse_injury_rows([ESPN_ROW])
    assert r[0] == {"name": "Asa Newell", "team": "ATL", "status": "Out",
                    "detail": "Left Toe, est. return 2026-11-13",
                    "note": "Newell (toe) has been ruled out for Monday's game against the Lakers.",
                    "return": "2026-11-13"}
    assert r[1]["team"] == "ATL" and r[1]["detail"].startswith("Day-to-day")


def test_team_comes_from_espn_not_name_matching():
    r = st.parse_injury_rows([ESPN_ROW])
    assert [x["name"] for x in st.team_injuries("ATL", r, {})] == ["Asa Newell", "Dyson Daniels"]
    assert st.team_injuries("BKN", r, {}) == []
    assert st.out_names_from_report(r) == {"Asa Newell"}


def test_returning_by_flags_out_players_expected_back_today():
    rep = st.parse_injury_rows([{"injuries": [
        {"status": "Out", "athlete": {"displayName": "Luka Dončić", "team": {"abbreviation": "LAL"}},
         "details": {"type": "Back", "returnDate": "2026-11-09"}},
        {"status": "Out", "athlete": {"displayName": "Long Term", "team": {"abbreviation": "LAL"}},
         "details": {"type": "Knee", "returnDate": "2027-01-02"}},
        {"status": "Day-To-Day", "athlete": {"displayName": "Dtd Guy", "team": {"abbreviation": "LAL"}},
         "details": {"type": "Ankle", "returnDate": "2026-11-09"}}]}])
    got = st.returning_by(rep, ["Luka Doncic", "Long Term", "Dtd Guy"], "2026-11-09")
    assert got == ["Luka Doncic"]
