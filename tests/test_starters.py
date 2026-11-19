import sqlite3
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
    monkeypatch.setattr(st, "fetch_injury_report", lambda game_ids=None: [{"name": "A", "status": "Out", "detail": "ankle"}])
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
    r = st.project_usual_starters(con, "DEN", "20261109", [{"name": "Nikola Jokić", "status": "Out"}])
    assert "Nikola Jokic" not in [p["name"] for p in r["players"]]
    assert "E Five" in [p["name"] for p in r["players"]] and r["complete"]


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


# ── injury substitution (stateless: rebuilt from the CURRENT report every run) ──
def star_db():
    """NY: a,b,c,d start every game. XAVI starts games 1-8, then misses games 9-10 while ZED starts.
    FLO is a bench regular throughout."""
    con = make_db()
    for i in range(10):
        starters = ["a", "b", "c", "d"] + (["Xavi"] if i < 8 else ["Zed"])
        bench = ["Flo"] + (["Zed"] if i < 8 else [])
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
    assert afternoon["returning"] == {"XAVI": 2}        # flagged: missed 2 of the last 3 team games


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
    """STAR missed all of the recent games and is no longer on the (loaded) report: he starts again."""
    con = returning_star_db()
    r = st.project_usual_starters(con, "NY", "20261101", [rep("SOMEONE ELSE", "Out")])
    ids = [p["player_id"] for p in r["players"]]
    assert "Star" in ids and "Fill" not in ids and r["complete"]
    assert r["returning"] == {"STAR": 3}             # missed all of the last 3 (RECENT_GAMES) team games


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


# ── NBA.com official lineups stored for every game day (used to train the tip-off model) ──
FEED_DAY = {"games": [{"homeTeam": {"teamAbbreviation": "NYK", "players": [
    {"playerName": "Karl-Anthony Towns", "position": "C", "lineupStatus": "Confirmed"},
    {"playerName": "Jalen Brunson", "position": "PG", "lineupStatus": "Confirmed"},
    {"playerName": "Bench Guy", "position": "", "lineupStatus": ""}]},
    "awayTeam": {"teamAbbreviation": "TOR", "players": [
        {"playerName": "Jakob Poeltl", "position": "C", "lineupStatus": "Confirmed"}]}}]}


def test_official_lineups_are_stored_with_espn_teams_and_ids():
    con = make_db()
    st.save_lineups(con, [("g0", "NY", "espn_kat", "Karl-Anthony Towns", 1, 30.0)])
    n = st.store_official_lineups(con, "20261117", FEED_DAY)
    assert n == 3                                                    # starters only (bench skipped)
    rows = con.execute("SELECT team, player_id, player_name, position FROM official_lineups ORDER BY player_name").fetchall()
    assert ("NY", "espn_kat", "Karl-Anthony Towns", "C") in rows       # NBA tricode -> ESPN, name -> ESPN id
    assert ("TOR", None, "Jakob Poeltl", "C") in rows                  # unknown player: id left empty
    assert st.listed_center(con, "20261117", "NY") == "espn_kat"
    assert st.listed_center(con, "20261117", "TOR") is None            # no ESPN id -> can't be used
    assert st.listed_center(con, "20261118", "NY") is None
    st.store_official_lineups(con, "20261117", FEED_DAY)               # storing a day twice changes nothing
    assert con.execute("SELECT COUNT(*) FROM official_lineups").fetchone()[0] == 3


def test_days_without_a_file_are_remembered_so_they_are_not_fetched_again():
    con = make_db()
    assert st.store_official_lineups(con, "20260601", None) == 0
    assert "20260601" in st.official_lineup_days(con)


# ── current rosters (ESPN) override the team learned from box scores: traded players ──
def test_traded_player_is_not_projected_for_his_old_team():
    con = star_db()                          # XAVI started NY's games 1-6; ZED games 7-10
    for i in range(10, 13):                  # ...then XAVI starts three more NY games
        add_game(con, f"h{i}", f"202610{i + 10:02d}", "NY", ["a", "b", "c", "d", "Xavi"], bench=["Flo", "Zed"])
    assert "Xavi" in [p["player_id"] for p in st.project_usual_starters(con, "NY", "20261101", [])["players"]]
    traded = st.project_usual_starters(con, "NY", "20261101", [], rosters={"xavi": "BOS"})
    ids = [p["player_id"] for p in traded["players"]]
    assert "Xavi" not in ids and len(ids) == 5 and traded["complete"]
    assert traded["filled"] == ["ZED"]                     # open spot filled by the next player in line


def test_parse_espn_roster():
    data = {"team": {"abbreviation": "LAL"}, "athletes": [{"displayName": "Luka Dončić", "id": "1"},
                                                          {"displayName": "Austin Reaves", "id": "2"}]}
    assert st.parse_roster(data) == {"luka doncic": "LAL", "austin reaves": "LAL"}


# ── position-aware replacements (centre for centre, guard for guard) ──
def test_position_group():
    assert [st.position_group(p) for p in ("PG", "SG", "G", "SF", "PF", "F", "C", "F-C", "", None)] == \
           ["G", "G", "G", "F", "F", "F", "C", "F", None, None]


def utah_db():
    """UTAH: CEN (a centre) starts every game; WING (a guard) is the bench player with the most minutes;
    BIG (another centre) plays fewer minutes off the bench."""
    con = make_db()
    for i in range(10):
        add_game(con, f"g{i}", f"202610{i + 10:02d}", "UTAH", ["a", "b", "c", "d", "Cen"], bench=["Wing", "Big"])
    con.execute("UPDATE lineups SET minutes = 25 WHERE player_id = 'Wing'")
    con.execute("UPDATE lineups SET minutes = 12 WHERE player_id = 'Big'")
    return con


def test_out_centre_is_replaced_by_a_centre_when_positions_are_known():
    con = utah_db()
    groups = {"cen": "C", "big": "C", "wing": "G", "a": "G", "b": "G", "c": "F", "d": "F"}
    without = st.project_usual_starters(con, "UTAH", "20261101", [rep("CEN", "Out")])
    assert without["replaced"][0]["in"] == "WING"                       # old behaviour: most minutes
    r = st.project_usual_starters(con, "UTAH", "20261101", [rep("CEN", "Out")], groups=groups)
    assert r["replaced"][0]["in"] == "BIG" and "same position" in r["replaced"][0]["evidence"]


def test_position_is_ignored_when_unknown_or_nobody_matches():
    con = utah_db()
    no_centre = {"cen": "C", "wing": "G"}                                 # BIG's position unknown
    r = st.project_usual_starters(con, "UTAH", "20261101", [rep("CEN", "Out")], groups=no_centre)
    assert r["replaced"][0]["in"] == "WING"



def test_injury_report_is_kept_once_per_status():
    import sqlite3
    con = sqlite3.connect(":memory:")
    rep = [{"name": "A Player", "team": "LAL", "status": "Questionable", "detail": "Ankle", "note": "", "return": ""}]
    assert st.save_injury_report(con, "20261119", rep) == 1
    assert st.save_injury_report(con, "20261119", rep) == 0                      # same status again: not repeated
    rep[0]["status"] = "Out"
    assert st.save_injury_report(con, "20261119", rep) == 1                      # a change gets its own row
    assert con.execute("SELECT COUNT(*) FROM injury_log").fetchone()[0] == 2


# ── ESPN fallback when NBA.com posts no lineups for a day (11/02/26) ──
def _espn_summary(team_starters):
    return {"boxscore": {"players": [
        {"team": {"abbreviation": team}, "statistics": [{"athletes": [
            {"starter": True, "athlete": {"id": pid, "displayName": name, "position": {"abbreviation": pos}}}
            for pid, name, pos in starters] + [
            {"starter": False, "athlete": {"id": "b", "displayName": "Bench Guy", "position": {"abbreviation": "C"}}}]}]}
        for team, starters in team_starters.items()]}}


CLE = [("allen", "Jarrett Allen", "C"), ("mobley", "Evan Mobley", "C"), ("harden", "James Harden", "G"),
       ("mitchell", "Donovan Mitchell", "G"), ("thomas", "Meleek Thomas", "G")]
OKC = [("jw", "Jaylin Williams", "F"), ("hart", "Isaiah Hartenstein", "C"), ("kw", "Kenrich Williams", "G"),
       ("sga", "Shai Gilgeous-Alexander", "G"), ("am", "Ajay Mitchell", "G")]


def test_espn_starters_are_parsed_without_the_bench():
    got = st.parse_espn_starters(_espn_summary({"CLE": CLE, "OKC": OKC}))
    assert got["CLE"] == CLE and got["OKC"] == OKC
    assert st.parse_espn_starters(_espn_summary({"CLE": CLE[:4]})) == {}       # not 5 starters: unusable


def test_day_without_nba_lineups_is_filled_from_espn_with_one_centre_per_team():
    con = make_db()
    st.ensure_official_tables(con)
    con.execute("INSERT INTO games VALUES ('g1', '20261102', 'CLE', 'OKC')")
    con.execute("INSERT INTO official_lineups VALUES ('20261020', 'CLE', 'allen', 'Jarrett Allen', 'C', 'Confirmed')")
    con.execute("INSERT INTO official_lineups VALUES ('20261020', 'CLE', 'mobley', 'Evan Mobley', 'PF', 'Confirmed')")
    calls = []
    fetch = lambda gid: calls.append(gid) or st.parse_espn_starters(_espn_summary({"CLE": CLE, "OKC": OKC}))
    empty_feed = {"games": [{"homeTeam": {"teamAbbreviation": "CLE", "players": []},
                             "awayTeam": {"teamAbbreviation": "OKC", "players": []}}]}
    assert st.store_official_lineups(con, "20261102", empty_feed, espn_fetch=fetch) == 10
    assert calls == ["g1"]
    rows = dict(((t, n), (p, s)) for t, n, p, s in con.execute(
        "SELECT team, player_name, position, status FROM official_lineups WHERE game_date = '20261102'"))
    assert rows[("CLE", "Jarrett Allen")] == ("C", "ESPN")      # NBA.com's usual C keeps it
    assert rows[("CLE", "Evan Mobley")] == ("F", "ESPN")        # the second ESPN "C" becomes F
    assert st.listed_center(con, "20261102", "CLE") == "allen"
    assert st.listed_center(con, "20261102", "OKC") == "hart"


def test_espn_is_not_asked_when_nba_listed_both_teams():
    con = make_db()
    con.execute("INSERT INTO games VALUES ('g0', '20261117', 'NY', 'TOR')")
    asked = []
    st.store_official_lineups(con, "20261117", FEED_DAY, espn_fetch=lambda gid: asked.append(gid) or {})
    assert asked == []


def test_espn_forward_who_nba_listed_at_centre_becomes_the_centre():
    con = make_db()
    st.ensure_official_tables(con)
    con.execute("INSERT INTO games VALUES ('g2', '20261102', 'SA', 'TOR')")
    con.execute("INSERT INTO official_lineups VALUES ('20261025', 'SA', 'wemby', 'Victor Wembanyama', 'C', 'Confirmed')")
    sa = [("fox", "De'Aaron Fox", "G"), ("wemby", "Victor Wembanyama", "F"), ("castle", "Stephon Castle", "G"),
          ("v", "Devin Vassell", "G"), ("h", "Dylan Harper", "G")]
    tor = [("b", "Scottie Barnes", "F"), ("q", "Immanuel Quickley", "G"), ("w", "Ja'Kobe Walter", "G"),
           ("r", "RJ Barrett", "F"), ("m", "Collin Murray-Boyles", "F")]
    st.store_official_lineups(con, "20261102", None,
                              espn_fetch=lambda gid: st.parse_espn_starters(_espn_summary({"SA": sa, "TOR": tor})))
    assert st.listed_center(con, "20261102", "SA") == "wemby"
    assert st.listed_center(con, "20261102", "TOR") is None      # no history, no ESPN C: no centre (model's backup)
