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
    assert capsys.readouterr().out == ""        # 404 is normal: quiet (nba.py reports status per game)
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


def test_team_injury_lines_shows_whole_report_starters_first():
    """Every listed player appears; starters (incl. a usual starter dropped from the projected
    five, and one who isn't in it) come first and are marked."""
    con = make_db()
    for i, d in enumerate(["20261102", "20261104", "20261106"]):
        add_game(con, f"g{i}", d, "CHA", ["a", "b", "c", "d", "e"], bench=["f"])
    report = [
        {"name": "f", "team": "CHA", "status": "Out", "detail": "G League"},
        {"name": "A", "team": "CHA", "status": "Out", "detail": "Wrist"},          # usual starter, dropped
        {"name": "b", "team": "CHA", "status": "Questionable", "detail": "Ankle"},  # usual starter, kept
        {"name": "zz", "team": "NY", "status": "Out", "detail": "elsewhere"},       # other team: excluded
    ]
    lines = st.team_injury_lines(con, "CHA", "2026-11-09", report,
                                 projected_names=["B", "C", "D", "E", "G"], dropped_names=["A"])
    assert len(lines) == 3 and not any("elsewhere" in l for l in lines)
    assert "removed from projected five" in lines[0] and lines[0].lstrip().startswith("★")
    assert "STARTER" in lines[1] and "Questionable" in lines[1]
    assert "★" not in lines[2] and "G League" in lines[2]                              # non-starter last


def test_long_injured_regular_starter_is_still_marked_starter():
    """A starter who hasn't played for 2+ weeks isn't in the last-5 window, but 6 starts in
    the last 25 games still marks him a STARTER (and not in the projected five)."""
    con = make_db()
    for i in range(6):
        add_game(con, f"old{i}", f"2026100{i + 1}", "DET", ["x", "b", "c", "d", "e"])
    for i, d in enumerate(["20261102", "20261104", "20261106", "20261108", "20261109"][:4]):
        add_game(con, f"new{i}", d, "DET", ["r", "b", "c", "d", "e"])       # 'r' replaced 'x'
    report = [{"name": "X", "team": "DET", "status": "Day-To-Day", "detail": "Hamstring"}]
    lines = st.team_injury_lines(con, "DET", "2026-11-10", report,
                                 projected_names=["R", "B", "C", "D", "E"])
    assert len(lines) == 1 and "STARTER" in lines[0] and "NOT in projected five" in lines[0]


# ── injury hardening (2026-11-09) ────────────────────────────
def test_severity_tolerates_variants():
    assert st.severity("Out For Season") == 0 and st.severity("out") == 0
    assert st.severity("Injured Reserve") == 0 and st.severity("Doubtful") == 1
    assert st.severity("Questionable") == 2 and st.severity("Day-To-Day") == 3
    assert st.severity("") == 9 and st.severity("Healthy") == 9


def test_out_player_dropped_despite_accent_mismatch():
    """ESPN lists 'Nikola Jokić'; box scores store 'Nikola Jokic'. Must still be removed."""
    con = sqlite3.connect(":memory:")
    st.ensure_lineups_table(con)
    con.execute("CREATE TABLE games (game_id TEXT, game_date TEXT)")
    names = ["Nikola Jokic", "A One", "B Two", "C Three", "D Four", "E Five"]
    for i in range(3):
        con.execute("INSERT INTO games VALUES (?,?)", (f"g{i}", f"2026110{i+1}"))
        for j, n in enumerate(names):
            con.execute("INSERT INTO lineups VALUES (?,?,?,?,?,?)", (f"g{i}", "DEN", f"p{j}", n, 1 if j < 5 else 0, 30))
    r = st.recent_starters(con, "DEN", "20261109", out_names={"Nikola Jokić"})
    assert "Nikola Jokic" not in [p["name"] for p in r["players"]]
    assert "E Five" in [p["name"] for p in r["players"]]


def test_parse_summary_injuries_and_merge_keeps_more_severe():
    data = {"injuries": [{"team": {"abbreviation": "DEN"}, "injuries": [
        {"status": "Out", "athlete": {"displayName": "Nikola Jokić"},
         "details": {"type": "Knee", "returnDate": "2026-11-20"}}]}]}
    g = st.parse_summary_injuries(data)
    assert g[0]["team"] == "DEN" and g[0]["status"] == "Out" and "Knee" in g[0]["detail"]
    league = [{"name": "Nikola Jokic", "team": "DEN", "status": "Day-To-Day", "detail": "", "note": "", "return": ""}]
    m = st.merge_injury_reports(league, g)
    assert len(m) == 1 and m[0]["status"] == "Out" and m[0]["conflict"] is True


def test_missing_injury_data_is_loud():
    assert "NO INJURY DATA" in st.injury_coverage_note([])
    assert st.parse_summary_injuries({}) == [] and st.parse_summary_injuries(None) == []


def test_game_card_is_organized(monkeypatch):
    import sys, types
    for m in ("sportsdataverse", "sportsdataverse.nba", "dotenv"):
        sys.modules.setdefault(m, types.ModuleType(m))
    sys.modules["dotenv"].load_dotenv = lambda *a, **k: None
    for k in ("espn_nba_scoreboard", "espn_nba_pbp", "espn_nba_cdn_boxscore", "espn_nba_injuries"):
        setattr(sys.modules["sportsdataverse.nba"], k, lambda *a, **k: None)
    import nba
    rep = [{"name": "Luka Doncic", "team": "LAL", "status": "Out", "detail": "Left Toe", "return": "2026-11-09"},
           {"name": "Quentin Grimes", "team": "LAL", "status": "Day-To-Day", "detail": "Finger", "return": "2026-11-09"}]
    mk = lambda ns: [{"name": n} for n in ns]
    out = nba.render_game_card("LAL", "ATL", "PROJECTED", mk(list("ABCD") + ["Quentin Grimes"]),
                               mk(list("VWXYZ")), rep, dropped=["Luka Doncic"],
                               picks=[("V", "ATL", .136, 634, "")], today_iso="2026-11-09")
    assert "PROJECTED LINEUP" in out and "STARTERS" in out and "INJURY WATCH" in out and "TOP PICKS" in out
    assert "Luka Doncic (LAL)" in out and "REMOVED from lineup" in out and "may actually play" in out
    assert out.count("may actually play") == 1          # only for the removed player, not the in-lineup one
    assert "🟡 Quentin Grimes" in out


# ── injury substitution (stateless: rebuilt from the CURRENT report every run) ──
def star_db():
    """NY: a,b,c,d start every game. XAVI starts games 1-6, then misses games 7-10 while ZED starts.
    FLO is a bench regular throughout."""
    con = make_db()
    for i in range(10):
        starters = ["a", "b", "c", "d"] + (["Xavi"] if i < 6 else ["Zed"])
        bench = ["Flo"] + (["Zed"] if i < 6 else [])
        add_game(con, f"g{i}", f"202610{i + 10:02d}", "NY", starters, bench)
    return con


def rep(name, status, detail=""):
    return {"name": name, "status": status, "detail": detail}


def test_out_starter_is_replaced_by_who_started_while_he_was_out():
    con = star_db()
    r = st.project_usual_starters(con, "NY", "20261101", [rep("XAVI", "Out")])
    ids = [p["player_id"] for p in r["players"]]
    assert "Zed" in ids and "Xavi" not in ids and r["complete"]
    assert r["replaced"][0]["out"] == "XAVI" and r["replaced"][0]["in"] == "ZED"
    assert r["replaced"][0]["how"] == "history"
    assert next(p for p in r["players"] if p["player_id"] == "Zed")["replaces"] == "XAVI"


def test_cleared_player_goes_straight_back_into_the_lineup():
    """Same data, later run: he is no longer on the report, so he starts and the fill-in sits."""
    con = star_db()
    morning = st.project_usual_starters(con, "NY", "20261101", [rep("XAVI", "Out")])
    afternoon = st.project_usual_starters(con, "NY", "20261101", [])
    assert "Xavi" not in [p["player_id"] for p in morning["players"]]
    ids = [p["player_id"] for p in afternoon["players"]]
    assert "Xavi" in ids and "Zed" not in ids
    assert afternoon["replaced"] == [] and afternoon["dropped"] == []
    assert afternoon["returning"] == {"XAVI": 4}        # flagged: missed 4 of the last 5 team games


def test_questionable_stays_in_with_an_if_he_sits_replacement():
    con = star_db()
    report = [rep("XAVI", "Questionable")]
    r = st.project_usual_starters(con, "NY", "20261101", report)
    xavi = next(p for p in r["players"] if p["player_id"] == "Xavi")
    assert xavi["uncertain"] is True and r["replaced"] == []
    c = st.contingencies(con, "NY", r["players"], report, "20261101")
    assert c["xavi"]["in"] == "ZED"


def test_replace_max_severity_can_also_replace_day_to_day():
    con = star_db()
    base = st.healthy_base_five(con, "NY", "20261101")["players"]
    r = st.substitute_injured(con, "NY", base, [rep("XAVI", "Day-To-Day")], "20261101", replace_max_severity=3)
    assert [x["in"] for x in r["replaced"]] == ["ZED"]


def test_expected_lineup_from_nba_com_gets_the_same_substitution_in_the_same_slot():
    con = star_db()
    feed = [{"player_id": p, "name": p.upper(), "team": "NY"} for p in ["Xavi", "a", "b", "c", "d"]]
    r = st.substitute_injured(con, "NY", feed, [rep("XAVI", "Out")], "20261101")
    assert [p["name"] for p in r["players"]] == ["ZED", "A", "B", "C", "D"]
    cleared = st.substitute_injured(con, "NY", feed, [], "20261101")
    assert [p["name"] for p in cleared["players"]] == ["XAVI", "A", "B", "C", "D"]


def test_no_replacement_available_leaves_him_in_and_reports_it():
    con = make_db()
    for i in range(3):
        add_game(con, f"g{i}", f"202610{i + 10:02d}", "NY", ["Anna", "Bob", "Cy", "Dee", "Eve"])
    r = st.project_usual_starters(con, "NY", "20261101", [rep("ANNA", "Out")])
    assert r["unresolved"] == ["ANNA"] and "Anna" in [p["player_id"] for p in r["players"]]


def test_benched_player_is_not_a_usual_starter():
    """OLD started games 1-5 then came off the bench for the last five: coach demoted him."""
    con = make_db()
    for i in range(10):
        starters = ["a", "b", "c", "d"] + (["Old"] if i < 5 else ["New"])
        add_game(con, f"g{i}", f"202610{i + 10:02d}", "NY", starters, bench=["Old"] if i >= 5 else ["New"])
    ids = [p["player_id"] for p in st.healthy_base_five(con, "NY", "20261101")["players"]]
    assert "New" in ids and "Old" not in ids


def test_player_gone_all_window_not_assumed_back_unless_still_on_the_report():
    con = make_db()
    for i in range(10):
        add_game(con, f"g{i}", f"202610{i + 10:02d}", "NY", ["a", "b", "c", "d"] + (["Gone"] if i < 5 else ["New"]),
                 bench=["Flo"])
    assert "Gone" not in [p["player_id"] for p in st.healthy_base_five(con, "NY", "20261101")["players"]]
    v = st.healthy_base_five(con, "NY", "20261101", verified_names={"gone"})
    assert "Gone" in [p["player_id"] for p in v["players"]]


def test_one_game_of_history_is_not_enough_to_pick_a_replacement():
    """Fill-in must have started >= 2 of the games missed; otherwise fall back to recent starts/minutes."""
    con = make_db()
    for i in range(8):
        add_game(con, f"g{i}", f"202610{i + 10:02d}", "NY", ["a", "b", "c", "d", "Xavi"], bench=["Flo", "Zed"])
    add_game(con, "g8", "20261018", "NY", ["a", "b", "c", "d", "Zed"], bench=["Flo"])   # XAVI misses ONE game
    for i in range(9, 11):
        add_game(con, f"g{i}", f"202610{i + 10:02d}", "NY", ["a", "b", "c", "d", "Xavi"], bench=["Flo", "Zed"])
    r = st.substitute_injured(con, "NY", st.healthy_base_five(con, "NY", "20261101")["players"],
                              [rep("XAVI", "Out")], "20261101")
    assert r["replaced"][0]["how"] == "recent"


# ── lineup gaps found in the 2026-11-17 review ────────────────
def test_unresolved_out_player_makes_the_lineup_incomplete():
    """Nobody can replace ANNA, so the five still contains an Out player: not a usable lineup."""
    con = make_db()
    for i in range(3):
        add_game(con, f"g{i}", f"202610{i + 10:02d}", "NY", ["Anna", "Bob", "Cy", "Dee", "Eve"])
    r = st.project_usual_starters(con, "NY", "20261101", [rep("ANNA", "Out")])
    assert r["unresolved"] == ["ANNA"] and r["complete"] is False


def returning_star_db():
    """NY: Star starts games 0-9, then misses the last 5 (10-14) while Fill starts."""
    con = make_db()
    for i in range(15):
        starters = ["a", "b", "c", "d"] + (["Star"] if i < 10 else ["Fill"])
        add_game(con, f"g{i}", f"202610{i + 10:02d}", "NY", starters, bench=[] if i >= 10 else ["Fill"])
    return con


def test_cleared_starter_who_missed_the_whole_recent_window_is_back():
    """STAR missed all of the last 5 games and is no longer on the (loaded) report: he starts again."""
    con = returning_star_db()
    r = st.project_usual_starters(con, "NY", "20261101", [rep("SOMEONE ELSE", "Out")])
    ids = [p["player_id"] for p in r["players"]]
    assert "Star" in ids and "Fill" not in ids and r["complete"]
    assert r["returning"] == {"STAR": 5}


def test_missing_starter_not_assumed_back_when_report_is_empty_or_he_is_out():
    con = returning_star_db()
    for report in ([], [rep("STAR", "Out")]):      # empty = injury feed failed; Out = still hurt
        ids = [p["player_id"] for p in st.project_usual_starters(con, "NY", "20261101", report)["players"]]
        assert "Star" not in ids and "Fill" in ids


def test_replacement_comes_from_the_current_absence_not_an_old_one():
    """X missed 3 games in October (OLD started), came back, and has now missed 2 more (NEW started)."""
    con = make_db()
    plan = (["X"] * 3) + (["Old"] * 3) + (["X"] * 4) + (["New"] * 2)
    for i, fifth in enumerate(plan):
        bench = [p for p in ("Old", "New") if p != fifth]
        add_game(con, f"g{i}", f"202610{i + 10:02d}", "NY", ["a", "b", "c", "d", fifth], bench)
    rep_ = st.pick_replacement(con, "NY", "X", {"a", "b", "c", "d"}, "20261101")
    assert rep_["name"] == "NEW" and rep_["how"] == "history"


def test_two_questionable_starters_get_different_if_he_sits_players():
    con = star_db()
    report = [rep("XAVI", "Questionable"), rep("A", "Questionable")]
    r = st.project_usual_starters(con, "NY", "20261101", report)
    c = st.contingencies(con, "NY", r["players"], report, "20261101")
    assert set(c) == {"xavi", "a"} and c["xavi"]["in"] != c["a"]["in"]


def test_dotted_initials_match_undotted():
    assert st.norm_name("P.J. Washington") == st.norm_name("PJ Washington")
    assert st.norm_name("C. J. McCollum") == st.norm_name("CJ McCollum")
    assert st.norm_name("A.J. Green Jr.") == st.norm_name("AJ Green")
    assert st.norm_name("Karl-Anthony Towns") == "karl anthony towns"


def test_returning_by_handles_return_dates_with_a_time():
    report = [{"name": "Luka Doncic", "status": "Out", "return": "2026-11-09T00:00Z"}]
    assert st.returning_by(report, ["Luka Doncic"], "2026-11-09") == ["Luka Doncic"]
