import sqlite3
import picks


def make_db():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE first_baskets (game_id TEXT, scorer_player_id TEXT, scorer_name TEXT)")
    picks.ensure_table(con)
    return con


def pick(game="g1", label="PROJECTED", model="m1", order=("a", "b", "c", "d", "e", "f", "g", "h", "i", "j"),
         lineup=None, when="2026-11-17T12:00:00"):
    players = [{"id": pid, "name": pid.upper(), "team": "NY", "prob": 0.2 - 0.01 * k} for k, pid in enumerate(order)]
    return {"game_id": game, "game_date": "20261117", "matchup": "NY @ TOR", "lineup_type": label,
            "lineup_source": "x", "model_id": model, "players": players, "tip_home": 0.5,
            "lineup_sig": lineup or ",".join(sorted(order)), "logged_at": when}


def test_every_pick_is_kept_and_nothing_is_overwritten():
    con = make_db()
    assert picks.log_pick(con, pick(label="PROJECTED", when="2026-11-17T10:00:00"))
    assert picks.log_pick(con, pick(label="EXPECTED", when="2026-11-17T14:00:00"))
    rows = con.execute("SELECT lineup_type FROM pick_log ORDER BY id").fetchall()
    assert [r[0] for r in rows] == ["PROJECTED", "EXPECTED"]


def test_same_lineup_and_model_again_is_not_logged_twice():
    con = make_db()
    assert picks.log_pick(con, pick())
    assert not picks.log_pick(con, pick(when="2026-11-17T12:05:00"))
    assert picks.log_pick(con, pick(model="m2"))                       # new model -> new entry
    assert picks.log_pick(con, pick(model="m2", lineup="changed"))     # lineup changed -> new entry
    assert con.execute("SELECT COUNT(*) FROM pick_log").fetchone()[0] == 3


def test_top_pick_is_the_most_likely_player():
    con = make_db()
    picks.log_pick(con, pick(order=("x", "a", "b", "c", "d", "e", "f", "g", "h", "i")))
    assert con.execute("SELECT pick_player_id, ROUND(pick_prob, 2) FROM pick_log").fetchone() == ("x", 0.2)


def test_summary_uses_the_last_pick_before_the_game_and_splits_by_type_and_model():
    con = make_db()
    # game 1: projected pick wrong (a), later expected pick right (b)
    picks.log_pick(con, pick("g1", "PROJECTED", "m1", order=tuple("acdefghijb"), when="2026-11-17T10:00:00"))
    picks.log_pick(con, pick("g1", "EXPECTED", "m1", order=tuple("bacdefghij"), lineup="L2", when="2026-11-17T14:00:00"))
    # game 2: only a confirmed pick, wrong but scorer in its top 3
    picks.log_pick(con, pick("g2", "CONFIRMED", "m2", order=tuple("abzcdefghi"), when="2026-11-17T18:00:00"))
    # game 3: not played yet (no first basket) -> ignored
    picks.log_pick(con, pick("g3", "EXPECTED", "m2"))
    con.executemany("INSERT INTO first_baskets VALUES (?,?,?)", [("g1", "b", "B"), ("g2", "z", "Z")])

    s = picks.summary(con)
    assert s["final"] == {"games": 2, "hits": 1, "top3": 2}
    assert s["by_type"]["PROJECTED"] == {"games": 1, "hits": 0, "top3": 0}   # scorer ranked last
    assert s["by_type"]["EXPECTED"] == {"games": 1, "hits": 1, "top3": 1}
    assert s["by_type"]["CONFIRMED"] == {"games": 1, "hits": 0, "top3": 1}
    assert s["by_model"]["m1"]["hits"] == 1 and s["by_model"]["m2"]["hits"] == 0
    assert [g["game_id"] for g in s["games"]] == ["g2", "g1"]        # newest first


def test_model_id_changes_when_weights_change():
    a = picks.model_id({"w": [0.1, 0.2], "trained_through": "20261116"})
    b = picks.model_id({"w": [0.1, 0.3], "trained_through": "20261116"})
    assert a != b and a.startswith("20261116-")


# ── my_predictions.xlsx: Scorecard tab + Games tab (one row per game, our pick vs what happened) ──
import spreadsheet


def add_result_tables(con):
    con.execute("""CREATE TABLE games (game_id TEXT, game_date TEXT, home_team TEXT, away_team TEXT,
                   tip_winner_team TEXT, tip_winner_player_name TEXT)""")
    con.execute("ALTER TABLE first_baskets ADD COLUMN scorer_team TEXT")
    con.execute("ALTER TABLE first_baskets ADD COLUMN shot_type TEXT")


def games_tab(path):
    return spreadsheet.read_xlsx(str(path))["Games"]


def read_row(path, n=0):
    """Data row n of the Games tab, keyed by the code's column names (the file has plain-English headers)."""
    back = {label: key for key, label in picks.LABELS.items()}
    header, *rows = games_tab(path)
    return {back[h]: (rows[n][i] if i < len(rows[n]) else "") for i, h in enumerate(header)}


def test_every_column_has_a_plain_english_header_and_a_width():
    assert set(picks.LABELS) == set(picks.COLUMNS) == set(picks.WIDTHS)
    assert len(set(picks.LABELS.values())) == len(picks.COLUMNS)


def test_predictions_csv_shows_our_final_pick_and_the_result(tmp_path):
    con = make_db(); add_result_tables(con)
    players = [{"id": f"t{k}", "name": f"Tor {k}", "team": "TOR", "prob": 0.08} for k in range(5)] + \
              [{"id": "bru", "name": "Jalen Brunson", "team": "NY", "prob": 0.16},
               {"id": "kat", "name": "Karl-Anthony Towns", "team": "NY", "prob": 0.11}] + \
              [{"id": f"n{k}", "name": f"Ny {k}", "team": "NY", "prob": 0.11} for k in range(3)]
    base = {"game_id": "g1", "game_date": "20261117", "matchup": "NY @ TOR", "lineup_source": "x",
            "model_id": "m", "tip_home": 0.46, "home_jumper": "Collin Murray-Boyles", "away_jumper": "Karl-Anthony Towns"}
    picks.log_pick(con, {**base, "lineup_type": "EXPECTED", "lineup_sig": "a", "players": players, "logged_at": "2026-11-17T14:00:00"})
    picks.log_pick(con, {**base, "lineup_type": "CONFIRMED", "lineup_sig": "b", "players": players, "logged_at": "2026-11-17T19:00:00"})
    con.execute("INSERT INTO games VALUES ('g1','20261117','TOR','NY','NY','Karl-Anthony Towns')")
    con.execute("INSERT INTO first_baskets VALUES ('g1','bru','Jalen Brunson','NY','Pullup Jump Shot')")
    path = tmp_path / "my_predictions.xlsx"
    assert picks.export_predictions(con, str(path)) == 1
    tabs = spreadsheet.read_xlsx(str(path))
    assert list(tabs) == ["Scorecard", "Games", "Lineups"]
    header = tabs["Games"][0]
    assert header[0] == "Date"                                         # the Games tab starts with its header row
    assert "Tip-off call right?" in header and "tip_right" not in header
    assert "Predicted team to score first" in header and "Team call right?" in header
    assert header[-1] == "Running: #1 pick right"
    # each prediction sits right next to its result
    assert header[2:6] == ["Our #1 pick to score first", "#1 chance", "Who actually scored first", "#1 pick right?"]
    assert header.index("Tip-off call right?") == header.index("Actual tip-off winner") + 1
    assert header.index("#1 or #2 right?") == header.index("Our #2 pick to score first") + 2
    row = read_row(path)
    assert row["lineup"] == "Confirmed" and row["game"] == "NY @ TOR" and row["date"] == "11/17/26"
    assert row["pick_made"] == "Live"
    assert row["pick_1"] == "Jalen Brunson (NY)" and row["pick_1_chance"] == "16.0%"
    assert row["pick_2"].startswith(("Karl-Anthony Towns", "Ny ")) and row["pick_2_chance"] == "11.0%"
    assert row["predicted_tip_winner"] == "Karl-Anthony Towns (NY)" and row["tip_chance"] == "54%"
    assert row["actual_first_scorer"] == "Jalen Brunson (NY)" and row["how_scored"] == "Pullup Jump Shot"
    assert row["actual_tip_winner"] == "Karl-Anthony Towns (NY)"
    assert row["team_to_score_first"] == "NY" and row["team_chance"] == "60%"
    assert (row["pick_1_right"], row["top_2_right"], row["tip_right"], row["team_right"]) == ("YES", "YES", "YES", "YES")
    assert row["running_all"] == "3 of 3 (100%)"                       # team to score first is not counted
    assert row["running_pick_1"] == "1 of 1 (100%)"
    assert tabs["Scorecard"][0] == ["SCORECARD — 2026-27 season"]


def test_predictions_csv_marks_unplayed_games_pending(tmp_path):
    con = make_db(); add_result_tables(con)
    picks.log_pick(con, pick(game="g9"))
    path = tmp_path / "p.xlsx"
    picks.export_predictions(con, str(path))
    row = read_row(path)
    assert row["actual_first_scorer"] == "" and row["pick_1_right"] == "pending"


def card_text(lines):
    """Scorecard summary lines (column C) and table {label: [correct, out of, percent]}."""
    text = [line[0] for line in lines if len(line) == 1]
    table = {line[0]: line[1:] for line in lines if len(line) == 4 and line[0]}
    return text, table


def test_scorecard_counts_only_finished_games():
    rows = [{"date": "11/16/26", "pick_1_right": "YES", "top_2_right": "YES", "tip_right": "YES", "team_right": "YES"},
            {"date": "11/15/26", "pick_1_right": "NO", "top_2_right": "YES", "tip_right": "", "team_right": "NO"},
            {"date": "11/17/26", "pick_1_right": "pending", "top_2_right": "pending", "tip_right": "pending"}]
    lines = picks.scorecard(rows, today="11/17/26")
    text, table = card_text(lines)
    assert text == ["SCORECARD — 2026-27 season", "2 finished games since 11/15", "1 game still to play today"]
    assert card_text(picks.scorecard(rows, today="11/18/26"))[0][-1] == "1 game still to play"   # not all today
    assert table["Our #1 pick scored first"] == [1, 2, "50%"]
    assert table["Our #1 or #2 pick scored first"] == [2, 2, "100%"]
    assert table["We called the tip-off winner"] == [1, 1, "100%"]          # unknown tip winner not counted
    assert table["ALL OUR PREDICTIONS COMBINED"] == [4, 5, "80%"]           # 3 + 2 answers
    assert len(table) == 4 and not any("team" in k.lower() for k in table)  # no counter for team to score first
    assert card_text(picks.scorecard([]))[1]["Our #1 pick scored first"] == [0, 0, "-"]


def test_scorecard_start_date_works_across_new_year():
    rows = [{"date": d, "pick_1_right": "NO", "top_2_right": "NO", "tip_right": "NO"}
            for d in ("01/05/27", "12/20/26")]
    assert "2 finished games since 12/20" in card_text(picks.scorecard(rows))[0]


def test_running_counts_add_up_oldest_first_and_skip_unplayed_games():
    def g(date, p1, top2, tip):
        return {"date": date, "pick_1_right": p1, "top_2_right": top2, "tip_right": tip}
    rows = [g("11/18/26", "pending", "pending", "pending"),     # newest first, as in the file
            g("11/17/26", "YES", "YES", "NO"),
            g("11/16/26", "NO", "NO", "")]                          # tip winner unknown: 2 answers
    picks.add_running_counts(rows)
    assert rows[2]["running_all"] == "0 of 2 (0%)" and rows[2]["running_pick_1"] == "0 of 1 (0%)"
    assert rows[1]["running_all"] == "2 of 5 (40%)" and rows[1]["running_pick_1"] == "1 of 2 (50%)"
    assert rows[0]["running_all"] == "" and rows[0]["running_pick_1"] == ""


def test_scorecard_says_how_many_picks_were_re_created():
    rows = [{"date": "11/16/26", "pick_made": "Live", "pick_1_right": "YES", "top_2_right": "YES", "tip_right": "NO"},
            {"date": "10/21/26", "pick_made": "Re-created", "pick_1_right": "NO", "top_2_right": "NO",
             "tip_right": "YES"}]
    assert "1 re-created week by week, 1 live pick" in card_text(picks.scorecard(rows))[0]


def test_spreadsheet_is_this_season_only_and_labels_re_created_picks(tmp_path):
    con = make_db(); add_result_tables(con)
    old = pick(game="old"); old["game_date"] = "20260301"                 # last season: left out
    bt = pick(game="bt", label="BACKTEST"); bt["game_date"] = "20261021"
    picks.log_pick(con, old); picks.log_pick(con, bt)
    path = tmp_path / "p.xlsx"
    assert picks.export_predictions(con, str(path)) == 1
    row = read_row(path)
    assert row["pick_made"] == "Re-created" and row["lineup"] == "Confirmed"     # real starters, picked afterwards


def test_spreadsheet_lists_newest_games_first(tmp_path):
    con = make_db(); add_result_tables(con)
    for gid, d in (("late", "20261116"), ("early", "20261021")):
        p_ = pick(game=gid); p_["game_date"] = d
        picks.log_pick(con, p_)
    path = tmp_path / "p.xlsx"
    picks.export_predictions(con, str(path))
    assert [r[0] for r in games_tab(path)[1:]] == ["11/16/26", "10/21/26"]


def test_excel_file_has_three_tabs_frozen_header_and_coloured_yes_no(tmp_path):
    import zipfile
    con = make_db(); add_result_tables(con)
    picks.log_pick(con, pick(game="g1"))
    con.execute("INSERT INTO games VALUES ('g1','20261117','TOR','NY','NY','X')")
    con.execute("INSERT INTO first_baskets VALUES ('g1','a','A','NY','Jump Shot')")
    path = tmp_path / "p.xlsx"
    picks.export_predictions(con, str(path))
    with zipfile.ZipFile(path) as z:
        games = z.read("xl/worksheets/sheet2.xml").decode()
        assert 'state="frozen"' in games and 'xSplit="2"' in games and "<autoFilter" in games and "<cols>" in games
        assert z.read("xl/workbook.xml").decode().count("<sheet ") == 3
    card = spreadsheet.read_xlsx(str(path))["Scorecard"]
    assert ["Our #1 pick scored first", 1, 1, "100%"] in card


def test_xlsx_writer_round_trips_text_numbers_and_symbols(tmp_path):
    path = tmp_path / "t.xlsx"
    spreadsheet.write_xlsx(str(path), [{"name": "A & B", "rows": [["x <y> & 'z'", 3, 2.5], [], ["Dončić"]]}])
    assert spreadsheet.read_xlsx(str(path)) == {"A & B": [["x <y> & 'z'", 3, 2.5], [], ["Dončić"]]}
    assert spreadsheet.col_letter(0) == "A" and spreadsheet.col_letter(26) == "AA"


def test_lineups_tab_keeps_expected_confirmed_and_actual_starters_apart(monkeypatch):
    import starters as st
    con = make_db(); add_result_tables(con)
    con.execute("CREATE TABLE lineups (game_id TEXT, team TEXT, player_id TEXT, player_name TEXT, is_starter INTEGER)")
    st.ensure_official_tables(con)
    monkeypatch.setattr(st, "espn_ids_by_name", lambda con: {})             # no id matching needed here
    con.execute("INSERT INTO games VALUES ('g1','20261116','TOR','NY','NY','X')")
    def feed(status, ny_center):
        five = [("Jalen Brunson", "PG"), ("Mikal Bridges", "SG"), ("Josh Hart", "SF"), ("OG Anunoby", "PF"),
                (ny_center, "C")]
        tor = [(f"Tor {k}", pos) for k, pos in enumerate(("PG", "SG", "SF", "PF", "C"))]
        side = lambda abbr, ps: {"teamAbbreviation": abbr, "players": [
            {"playerName": n, "position": pos, "lineupStatus": status} for n, pos in ps] + [
            {"playerName": "Bench Guy", "position": "", "lineupStatus": status}]}
        return {"games": [{"homeTeam": side("TOR", tor), "awayTeam": side("NYK", five)}]}
    st.store_lineup_snapshot(con, "20261116", feed("Expected", "Karl-Anthony Towns"))     # morning
    st.store_lineup_snapshot(con, "20261116", feed("Confirmed", "Mitchell Robinson"))     # Towns ruled out
    for n in ("Jalen Brunson", "Mikal Bridges", "Josh Hart", "OG Anunoby", "Mitchell Robinson"):
        con.execute("INSERT INTO lineups VALUES ('g1','NY',?,?,1)", (n.lower(), n))
    ny = next(r for r in picks.lineup_rows(con) if r[2] == "NY")
    assert ny[0] == "11/16/26" and ny[1] == "NY @ TOR"
    assert ny[3].endswith("Karl-Anthony Towns") and ny[4].endswith("Mitchell Robinson")   # both kept, PG first, C last
    assert ny[3].startswith("Jalen Brunson") and "Bench Guy" not in ny[3]
    assert (ny[6], ny[7]) == ("NO", "YES")                       # the expected five changed; confirmed was right
    tor = next(r for r in picks.lineup_rows(con) if r[2] == "TOR")
    assert tor[5] == "Box score not available" or tor[5] == "Game not played yet"
    assert all(cell for row in picks.lineup_rows(con) for cell in row)       # no empty cells anywhere


def test_lineups_tab_explains_missing_lineups_in_words(monkeypatch):
    import starters as st
    con = make_db(); add_result_tables(con)
    st.ensure_official_tables(con)
    con.execute("INSERT INTO games VALUES ('old','20261101','TOR','NY','NY','X')")          # played, before tracking
    con.execute("INSERT INTO first_baskets VALUES ('old','a','A','NY','Jump Shot')")
    picks.log_pick(con, {**pick(game="today"), "game_date": "20261117"})                    # today, nothing posted
    rows = {r[0]: r for r in picks.lineup_rows(con)}
    assert rows["11/01/26"][3].startswith("Not available: NBA.com overwrites its early lineup")
    assert rows["11/01/26"][6] == "Not available: no early lineup saved before 11/17/26"
    assert rows["11/01/26"][7] == "Missing: the final lineup wasn't saved"
    assert rows["11/17/26"][4].startswith("Not confirmed yet") and rows["11/17/26"][5] == "Game not played yet"
    assert rows["11/17/26"][7] == "Waiting for the final lineup"


def test_lineups_tab_says_when_nba_com_published_no_lineups():
    import starters as st
    con = make_db(); add_result_tables(con)
    st.ensure_official_tables(con)
    con.execute("INSERT INTO games VALUES ('g','20261102','LAL','MIA','LAL','X')")
    con.execute("INSERT INTO first_baskets VALUES ('g','a','A','LAL','Jump Shot')")
    con.execute("INSERT INTO official_lineup_days VALUES ('20261102', 0)")          # NBA.com's file had no players
    row = picks.lineup_rows(con)[0]
    assert row[4].startswith("Not available: NBA.com never posted starting lineups this day")


def test_record_splits_this_season_from_overall():
    """The page shows this season's #1-pick record and an overall one (last season's re-created picks included)."""
    import json, sqlite3
    import picks
    con = sqlite3.connect(":memory:")
    picks.ensure_table(con)
    con.execute("CREATE TABLE first_baskets (game_id TEXT, scorer_player_id TEXT, scorer_name TEXT)")
    for gid, day, scorer in (("a", "20260210", "1"), ("b", "20260211", "9"), ("c", "20261105", "1")):
        picks.log_pick(con, {"game_id": gid, "game_date": day, "matchup": "X @ Y", "lineup_type": "BACKTEST",
                             "lineup_source": "t", "lineup_sig": gid, "model_id": "m",
                             "players": [{"id": "1", "name": "P", "team": "Y", "prob": 0.2}],
                             "logged_at": f"{day[:4]}-{day[4:6]}-{day[6:]}T00:00:00"})
        con.execute("INSERT INTO first_baskets VALUES (?, ?, 'S')", (gid, scorer))
    s = picks.summary(con)
    assert s["final"]["games"] == 1 and s["final"]["hits"] == 1             # this season: game c only
    assert s["overall"]["games"] == 3 and s["overall"]["hits"] == 2 and s["overall"]["since"] == "20260210"
    assert [g["game_id"] for g in s["games"]] == ["c"]                       # recent list stays this season
    assert s["last_season"]["games"] == 2 and s["last_season"]["hits"] == 1   # games a + b


def test_grading_uses_the_live_models_final_step():
    """Re-created (graded) picks must apply the same simple-rule mix as pregame_model.probabilities."""
    import numpy as np
    import pregame_model as pm
    import season_backtest as sb
    M = np.zeros((1, 10, len(pm.FEATURES)))
    M[0, :, pm.FEATURES.index("fb_rate_shrunk")] = [0.3] + [0.1] * 9
    p = np.full(10, 0.1)
    got = sb.with_simple_mix([p], M, [0])[0]
    fb = M[0, :, pm.FEATURES.index("fb_rate_shrunk")]
    k, mix = pm.SETTINGS["first_points"]
    assert np.allclose(got, pm.stretch_teams((1 - mix) * p + mix * fb / fb.sum(), M[0, :, pm.FEATURES.index("is_home")] > 0.5, k))


def test_team_stretch_keeps_shares_and_totals():
    import numpy as np
    import pregame_model as pm
    p = np.array([0.2, 0.15, 0.1, 0.1, 0.05] + [0.08] * 5)         # home 60%, away 40%
    home = np.array([True] * 5 + [False] * 5)
    q = pm.stretch_teams(p, home, 1.4)
    assert abs(q.sum() - 1) < 1e-12 and q[home].sum() > 0.6        # the favourite gets more
    assert np.allclose(q[home] / q[home].sum(), p[home] / p[home].sum())   # same shares inside the team
    assert np.allclose(pm.stretch_teams(p, home, 1.0), p)


def test_dropped_from_top5_warns_for_ruled_out_and_benched():
    import sqlite3, picks
    con = sqlite3.connect(":memory:")
    players = [{"id": str(i), "name": n, "team": "POR", "prob": p}
               for i, (n, p) in enumerate([("A Star", .2), ("Deni Avdija", .15), ("C Guy", .12), ("D Guy", .1),
                                           ("E Guy", .09), ("F Guy", .05)])]
    picks.log_pick(con, {"game_id": "1", "game_date": "20261119", "lineup_type": "EXPECTED", "lineup_sig": "a",
                         "model_id": "m", "players": players, "logged_at": "2026-11-19T18:00:00"})
    now = ["A Star", "C Guy", "D Guy", "E Guy", "F Guy", "New Guy"]
    out = picks.dropped_from_top5(con, "1", "20261119", now, {"Deni Avdija": "Out"}, False)
    assert [(d["name"], d["rank"], d["why"]) for d in out] == [("Deni Avdija", 2, "ruled out")]
    out = picks.dropped_from_top5(con, "1", "20261119", now, {}, True)
    assert out[0]["why"] == "not in the confirmed starting lineup"
    out = picks.dropped_from_top5(con, "1", "20261119", now, {"Deni Avdija": "Available"}, True)
    assert out[0]["why"] == "not in the confirmed starting lineup"
    # nothing dropped when he's still starting; picks from an earlier day don't count
    assert picks.dropped_from_top5(con, "1", "20261119", now + ["Deni Avdija"], {}, True) == []
    assert picks.dropped_from_top5(con, "1", "20261120", now, {}, True) == []
