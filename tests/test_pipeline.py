"""Safety tests for the engine behind `nba.py predict`: the data sync, the incremental training-matrix build
and the retrain decision. Everything runs in a temporary folder with a small fake database — no internet,
and the real project files are never touched."""
import json
import subprocess
from datetime import date, timedelta

import numpy as np
import pytest

import build_features as bf
import nba
import starters as st


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Temporary project folder with an empty database (same schema as `nba.py init`)."""
    monkeypatch.chdir(tmp_path)
    db = str(tmp_path / "nba_first_basket.db")
    monkeypatch.setattr(nba, "DB", db)
    monkeypatch.setattr(bf, "DB", db)
    monkeypatch.setattr(nba, "CSV_PATH", str(tmp_path / "first_basket_data.csv"))
    monkeypatch.setattr(nba, "PATCH_DIR", tmp_path / "data_patches")     # never the real 2024-25 season file
    nba.init()
    return nba.db()


def add_game(con, gid, day, labelled=True, lineup=True, home="NY", away="BOS"):
    con.execute("""INSERT INTO games (game_id, game_date, home_team, away_team, status, tip_winner_team,
                   tip_winner_player_id, tip_loser_player_id) VALUES (?,?,?,?,3,?,?,?)""",
                (gid, day, home, away, home, f"{home}5", f"{away}5"))
    if labelled:
        con.execute("INSERT INTO first_baskets (game_id, scorer_player_id, scorer_name, scorer_team) VALUES (?,?,?,?)",
                    (gid, f"{home}1", "Scorer", home))
    if lineup:
        st.save_lineups(con, [(gid, t, f"{t}{k}", f"{t} Player {k}", 1, 30.0) for t in (home, away) for k in range(1, 6)])
    con.commit()


def ymd(days_ago):
    return (date.today() - timedelta(days=days_ago)).strftime("%Y%m%d")


# ── sync_data ────────────────────────────────────────────────────────────────
def test_sync_retries_recent_gaps_drops_orphans_and_rewrites_the_csv(project, monkeypatch):
    con = project
    add_game(con, "old_unlabelled", ymd(40), labelled=False)      # too old: a permanent miss, not retried
    add_game(con, "recent_unlabelled", ymd(2), labelled=False)    # retried
    add_game(con, "recent_no_lineup", ymd(3), lineup=False)       # lineup retried
    add_game(con, "ok", ymd(4))
    add_game(con, "recent_no_first_shots", ymd(5))                # labelled, lineup stored, first shots missing
    con.execute(nba.FIRST_SHOTS_SCHEMA)
    con.execute(nba.EARLY_SHOTS_SCHEMA)
    for gid in ("old_unlabelled", "recent_unlabelled", "recent_no_lineup", "ok"):
        con.execute("INSERT INTO first_shots (game_id, team) VALUES (?, 'NY')", (gid,))
        con.execute("INSERT INTO early_shots (game_id, team, seq) VALUES (?, 'NY', 1)", (gid,))
    con.execute("INSERT INTO first_baskets (game_id, scorer_name) VALUES ('preseason', 'X')")   # orphan
    con.commit()

    ingested, lineups = [], []
    monkeypatch.setattr(nba, "catchup_missing_games", lambda: 2)
    monkeypatch.setattr(nba, "ingest_game", lambda gid, c: ingested.append(gid))
    monkeypatch.setattr(st, "store_game_lineup", lambda c, gid: lineups.append(gid))
    monkeypatch.setattr(bf, "build_player_game_logs", lambda: {})
    monkeypatch.setattr(nba, "_retrain_if_needed", lambda c: "model up to date")
    fetched = []
    monkeypatch.setattr(st, "store_official_lineups", lambda c, d: fetched.append(d))
    monkeypatch.setattr(nba, "SLEEP", 0)
    monkeypatch.setattr(nba, "audit_season", lambda *a: 0)          # (whole-season check: network, tested apart)
    monkeypatch.setattr(nba, "apply_data_patches", lambda c: 0)      # (tested apart)
    monkeypatch.setattr(nba, "repair_old_gaps", lambda c: {})

    summary = nba.sync_data()

    assert fetched == sorted({ymd(40), ymd(2), ymd(3), ymd(4), ymd(5)})     # every played day, fetched once

    assert sorted(ingested) == ["recent_no_first_shots", "recent_unlabelled"]
    assert lineups == ["recent_no_lineup"]
    assert nba.db().execute("SELECT COUNT(*) FROM first_baskets WHERE game_id = 'preseason'").fetchone()[0] == 0
    csv_rows = open(nba.CSV_PATH).read().strip().splitlines()
    assert len(csv_rows) == 1 + 3                                  # header + the three labelled games
    assert "(+2 new)" in summary and "model up to date" in summary


# ── _retrain_if_needed ───────────────────────────────────────────────────────
@pytest.fixture
def trained_through_yesterday(project):
    """Training matrices cover one game played 2 days ago."""
    add_game(project, "trained", ymd(2))
    json.dump(["trained"], open("game_ids.json", "w"))
    import pregame_model as pm
    json.dump({"team_labels": "first_field_goal", "early_m": pm.EARLY_M["first_field_goal"]}, open("pregame_model_fg.json", "w"))
    json.dump({"early_m": pm.EARLY_M["first_points"]}, open("pregame_model.json", "w"))   # both models current
    return project


def fake_run(codes, calls):
    def run(cmd, **kw):
        calls.append(cmd[1:])
        return subprocess.CompletedProcess(cmd, codes.get(cmd[1], 0), stdout="⚠️ Kept the previous model: x\n", stderr="")
    return run


def test_retrain_does_nothing_when_training_data_is_current(trained_through_yesterday, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_run({}, calls))
    assert nba._retrain_if_needed(trained_through_yesterday) == "model up to date"
    assert calls == []


def test_retrain_builds_then_trains_incrementally_when_new_games_exist(trained_through_yesterday, monkeypatch):
    add_game(trained_through_yesterday, "new1", ymd(1))
    add_game(trained_through_yesterday, "new2", ymd(1), home="LAL", away="GS")
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_run({}, calls))
    assert nba._retrain_if_needed(trained_through_yesterday) == "model updated with 2 new games"
    assert calls == [["build_features.py", "--incremental"], ["train_pregame.py", "--incremental"]]


def test_retrain_reports_when_the_previous_model_was_kept(trained_through_yesterday, monkeypatch):
    add_game(trained_through_yesterday, "new1", ymd(1))
    monkeypatch.setattr(subprocess, "run", fake_run({"train_pregame.py": 3}, []))
    assert nba._retrain_if_needed(trained_through_yesterday).startswith("kept the existing model")


def test_retrain_stops_if_the_feature_build_fails(trained_through_yesterday, monkeypatch):
    add_game(trained_through_yesterday, "new1", ymd(1))
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_run({"build_features.py": 1}, calls))
    assert nba._retrain_if_needed(trained_through_yesterday) == "model update FAILED"
    assert calls == [["build_features.py", "--incremental"]]          # training is not attempted


# ── build_features --incremental (the bug that appended zero games) ──────────
@pytest.fixture
def offline_build(project, monkeypatch):
    """build_features without the internet: starters come from the lineups table."""
    def starters(game_id, cache, home, away):
        rows = nba.db().execute("SELECT team, player_id, player_name FROM lineups WHERE game_id = ? AND is_starter = 1",
                                (game_id,)).fetchall()
        return {t: [{"player_id": pid, "name": n, "position": "", "jersey": ""} for team, pid, n in rows if team == t]
                for t in (home, away)}
    monkeypatch.setattr(bf, "get_game_starters", starters)
    monkeypatch.setattr(bf, "build_player_game_logs", lambda: {})
    return project


def test_incremental_build_appends_only_the_new_games(offline_build):
    con = offline_build
    for k in range(3):
        add_game(con, f"g{k}", f"2026110{k + 1}")
    bf.build_features(incremental=False)                          # full build: 3 games
    X0 = np.load("X_train.npy")
    assert json.load(open("game_ids.json")) == ["g0", "g1", "g2"] and X0.shape[0] == 30

    add_game(con, "g3", "20261105")
    add_game(con, "g4", "20261106", home="LAL", away="GS")
    bf.build_features(incremental=True)

    ids, X, y, g = json.load(open("game_ids.json")), np.load("X_train.npy"), np.load("y_train.npy"), np.load("groups.npy")
    assert ids == ["g0", "g1", "g2", "g3", "g4"]                  # appended, old games kept, order preserved
    assert X.shape[0] == 50 and np.array_equal(X[:30], X0)
    assert list(g) == [10] * 5 and (y.reshape(5, 10).sum(1) == 1).all()


def test_incremental_build_with_nothing_new_changes_nothing(offline_build):
    for k in range(2):
        add_game(offline_build, f"g{k}", f"2026110{k + 1}")
    bf.build_features(incremental=False)
    before = open("X_train.npy", "rb").read()
    bf.build_features(incremental=True)
    assert open("X_train.npy", "rb").read() == before


def test_a_game_labelled_late_is_still_added_to_training(offline_build):
    """g_late is older than the newest game already in the matrices (its result arrived late)."""
    con = offline_build
    add_game(con, "g1", "20261101")
    add_game(con, "g3", "20261103")
    bf.build_features(incremental=False)
    add_game(con, "g_late", "20261102")
    bf.build_features(incremental=True)
    assert json.load(open("game_ids.json")) == ["g1", "g3", "g_late"]
    assert np.load("X_train.npy").shape[0] == 30


def test_retrain_counts_a_late_labelled_older_game_as_new(trained_through_yesterday, monkeypatch):
    add_game(trained_through_yesterday, "late", ymd(5))       # older than the trained game, not in game_ids.json
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_run({}, calls))
    assert nba._retrain_if_needed(trained_through_yesterday) == "model updated with 1 new game"


def test_team_tip_rate_ignores_games_without_a_recorded_tip(project):
    con = project
    add_game(con, "won", "20261101")                           # NY won the tip
    add_game(con, "unknown", "20261102")
    con.execute("UPDATE games SET tip_winner_team = NULL WHERE game_id = 'unknown'")
    assert bf.compute_team_stats_up_to_date("NY", "20261110", con)["team_tipoff_win_pct"] == 1.0


# ── first-basket label guard ─────────────────────────────────────────────────
def play(kind, team="NY", pid="p1", period=1, scoring=True):
    return {"gameId": "g", "personId": pid, "playerName": pid.upper(), "teamTricode": team,
            "actionType": kind, "shotType": kind, "period": period, "clock": "11:30", "scoringPlay": scoring}


def test_first_basket_counts_made_free_throws():
    """Owner's rule (sportsbook 'first basket'): the first points of the game count, free throws included."""
    plays = [play("jumpball", scoring=False), play("free_throw_-_1_of_2", scoring=False),   # missed FT
             play("free_throw_-_2_of_2", pid="p2"), play("layup_shot", pid="p3")]
    assert nba.first_basket(plays)["personId"] == "p2"


def test_first_basket_ignores_overtime():
    plays = [play("jump_shot", pid="p2", period=5), play("layup_shot", pid="p3", period=6)]
    assert nba.first_basket(plays) is None


def test_first_basket_is_left_empty_when_the_scorer_or_team_is_missing():
    """Better no label (retried later) than a wrong one."""
    assert nba.first_basket([play("jump_shot", team=""), play("layup_shot", pid="p3")]) is None
    assert nba.first_basket([play("jump_shot", pid=""), play("layup_shot", pid="p3")]) is None


def test_tip_winner_team_is_unknown_not_guessed_when_missing():
    jb = play("jumpball", team="", scoring=False)
    jb.update({"tip_loser_player_id": "p9", "tip_loser_player_name": "P9"})
    assert nba.tip_jumpball([jb])["tip_winner_team"] is None


def test_player_log_flags_follow_the_current_label(project):
    con = project
    add_game(con, "g1", "20261101")                                   # scorer NY1
    logs = {"NY1": [{"game_id": "g1", "first_basket": 1}], "NY2": [{"game_id": "g1", "first_basket": 0}]}
    con.execute("UPDATE first_baskets SET scorer_player_id = 'NY2' WHERE game_id = 'g1'")   # label changed
    assert bf.sync_first_basket_flags(logs, con) == 2
    assert logs["NY1"][0]["first_basket"] == 0 and logs["NY2"][0]["first_basket"] == 1


# ── first shot of each team (feature: who usually takes it) ─────────────────
def shot(team, pid, kind="jump_shot", made=False, period=1):
    a = play(kind, team=team, pid=pid, period=period, scoring=made)
    a["shootingPlay"] = True
    return a


def test_first_shots_are_each_teams_first_field_goal_attempt_made_or_missed():
    plays = [play("jumpball", scoring=False), shot("NY", "ft_guy", kind="free_throw_-_1_of_2", made=True),
             shot("NY", "p1"), shot("BOS", "p7", made=True), shot("NY", "p2", made=True), shot("BOS", "p8")]
    fs = nba.first_shots(plays)
    assert {t: (a["personId"], a["scoringPlay"]) for t, a in fs.items()} == {"NY": ("p1", False), "BOS": ("p7", True)}


def test_first_shots_skip_plays_without_team_or_player():
    plays = [shot("", "p1"), shot("NY", ""), shot("NY", "p3")]
    assert nba.first_shots(plays)["NY"]["personId"] == "p3"


def test_ingest_stores_first_shots(project, monkeypatch):
    con = project
    add_game(con, "g1", "20261101", labelled=False)
    plays = [shot("NY", "NY1"), shot("BOS", "BOS2", made=True), shot("NY", "NY3", made=True)]
    monkeypatch.setattr(nba, "get_pbp", lambda gid: plays)
    nba.ingest_game("g1", con)
    rows = con.execute("SELECT team, player_id, made FROM first_shots WHERE game_id = 'g1' ORDER BY team").fetchall()
    assert [tuple(r) for r in rows] == [("BOS", "BOS2", 1), ("NY", "NY1", 0)]


def test_first_shot_share_uses_only_earlier_starts_for_that_player(project):
    con = project
    con.execute(nba.FIRST_SHOTS_SCHEMA)
    for k, (day, shooter) in enumerate([("20261101", "NY1"), ("20261102", "NY2"), ("20261103", "NY1"), ("20261104", "NY1")]):
        add_game(con, f"g{k}", day)
        con.execute("INSERT INTO first_shots VALUES (?, 'NY', ?, '', 0, '')", (f"g{k}", shooter))
    idx = bf.build_first_shot_index(con)
    assert bf.first_shot_share(idx, "NY1", "20261104") == (2 / 3, 3)    # g0..g2 only, not the game itself
    assert bf.first_shot_share(idx, "NY2", "20261105") == (1 / 4, 4)
    assert bf.first_shot_share(idx, "NY1", "20261101") == (0.0, 0)
    assert bf.first_shot_share(idx, "nobody", "20261105") == (0.0, 0)


def test_first_shot_share_ignores_games_without_first_shot_data(project):
    con = project
    con.execute(nba.FIRST_SHOTS_SCHEMA)
    add_game(con, "with_data", "20261101")
    add_game(con, "no_data", "20261102")
    con.execute("INSERT INTO first_shots VALUES ('with_data', 'NY', 'NY1', '', 0, '')")
    assert bf.first_shot_share(bf.build_first_shot_index(con), "NY1", "20261110") == (1.0, 1)


def test_early_shots_are_each_teams_first_five_attempts_in_order():
    plays = [shot("NY", f"n{i}", made=i % 2 == 0) for i in range(7)] + [shot("BOS", "b1"), shot("NY", "x", period=2)]
    es = nba.early_shots(plays, n=5)
    assert [a["personId"] for a in es["NY"]] == ["n0", "n1", "n2", "n3", "n4"]
    assert [a["personId"] for a in es["BOS"]] == ["b1"]


def test_ingest_stores_early_shots(project, monkeypatch):
    con = project
    add_game(con, "g1", "20261101", labelled=False)
    plays = [shot("NY", "NY1"), shot("NY", "NY2", made=True), shot("BOS", "BOS1")]
    monkeypatch.setattr(nba, "get_pbp", lambda gid: plays)
    nba.ingest_game("g1", con)
    rows = con.execute("SELECT team, seq, player_id, made FROM early_shots WHERE game_id='g1' ORDER BY team, seq").fetchall()
    assert [tuple(r) for r in rows] == [("BOS", 1, "BOS1", 0), ("NY", 1, "NY1", 0), ("NY", 2, "NY2", 1)]


def test_early_shot_share_counts_his_share_of_the_teams_first_shots_before_the_game(project):
    con = project
    con.execute(nba.EARLY_SHOTS_SCHEMA)
    plan = {"g0": ("20261101", ["NY1", "NY1", "NY2", "NY3", "NY1"]), "g1": ("20261102", ["NY2", "NY1", "NY2"]),
            "g2": ("20261103", ["NY1", "NY1", "NY1", "NY1", "NY1"])}
    for gid, (day, shooters) in plan.items():
        add_game(con, gid, day)
        for seq, pid in enumerate(shooters, 1):
            con.execute("INSERT INTO early_shots VALUES (?, 'NY', ?, ?, '', 0)", (gid, seq, pid))
    idx = bf.build_early_shot_index(con)
    assert bf.early_shot_share(idx, "NY1", "20261103") == (4 / 8, 8)      # g0 + g1 only
    assert bf.early_shot_share(idx, "NY2", "20261110") == (3 / 13, 13)
    assert bf.early_shot_share(idx, "NY1", "20261101") == (0.0, 0)


# ── python3 nba.py LAL  -> only the Lakers' game ──
def test_team_arguments_are_understood_in_common_spellings():
    assert nba.parse_teams(["LAL"]) == {"LAL"}
    assert nba.parse_teams(["lal", "nyk"]) == {"LAL", "NY"}                  # lower case, NBA.com codes
    assert nba.parse_teams(["GSW", "SAS", "UTA", "NOP", "WAS", "BRK"]) == {"GS", "SA", "UTAH", "NO", "WSH", "BKN"}
    assert nba.parse_teams(["predict", "--watch"]) == set()                  # not teams
    assert nba.parse_teams(["predict", "--no-odds"]) == set()                # 'no' in a flag is not the Pelicans
    assert nba.parse_teams(["no"]) == {"NO"}                                 # ...but typed on its own it is
    assert nba.parse_teams(["XYZ"]) == set()


def test_only_the_requested_teams_games_are_kept():
    games = [{"home_team": "TOR", "away_team": "NY"}, {"home_team": "LAL", "away_team": "CHA"}]
    assert nba.filter_games(games, {"LAL"}) == [games[1]]
    assert nba.filter_games(games, set()) == games


def test_team_names_and_nicknames_work_in_any_case():
    assert nba.parse_teams(["Knicks"]) == {"NY"}
    assert nba.parse_teams(["lakers", "SPURS"]) == {"LAL", "SA"}
    assert nba.parse_teams(["Hornets", "Thunder", "rockets", "Wizards"]) == {"CHA", "OKC", "HOU", "WSH"}
    assert nba.parse_teams(["trail", "blazers"]) == {"POR"} and nba.parse_teams(["Blazers"]) == {"POR"}
    assert nba.parse_teams(["sixers", "76ers", "cavs", "mavs", "wolves"]) == {"PHI", "CLE", "DAL", "MIN"}
    assert nba.parse_teams(["knicks,", "lakers"]) == {"NY", "LAL"}             # punctuation ignored
    assert len(set(nba.TEAM_NAMES.values())) == 30                                # every team has a name


def test_plain_run_asks_which_team_and_enter_means_all(monkeypatch, capsys):
    games = [{"home_team": "TOR", "away_team": "NY", "start": "7:30 PM ET", "status": 1},
             {"home_team": "LAL", "away_team": "CHA", "start": "11:00 PM ET", "status": 1}]
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    assert nba.ask_for_teams(games) == set()                                     # Enter = all games
    monkeypatch.setattr("builtins.input", lambda prompt="": "Lakers")
    assert nba.ask_for_teams(games) == {"LAL"}
    answers = iter(["Celtics", "knicks"])                                         # no game today -> asks again
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert nba.ask_for_teams(games) == {"NY"}
    assert "Knicks @ Raptors" in capsys.readouterr().out


def test_ctrl_d_at_the_question_means_all_games(monkeypatch):
    def eof(prompt=""):
        raise EOFError
    monkeypatch.setattr("builtins.input", eof)
    assert nba.ask_for_teams([{"home_team": "TOR", "away_team": "NY", "status": 1}]) == set()


def test_question_lists_numbered_games_with_names_and_accepts_a_number(monkeypatch, capsys):
    games = [{"home_team": "TOR", "away_team": "NY", "start": "7:30 PM ET", "status": 1},
             {"home_team": "LAL", "away_team": "CHA", "start": "11:00 PM ET", "status": 1}]
    answers = iter(["9", "pizza", "2"])                            # bad number, nonsense, then game 2
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert nba.ask_for_teams(games) == {"LAL", "CHA"}
    out = capsys.readouterr().out
    assert "1.  Knicks @ Raptors" in out and "2.  Hornets @ Lakers" in out
    assert "1 = Knicks @ Raptors" in out and "press Enter" in out and "pick a number from 1 to 2" in out and "Didn't recognise" in out
    assert set(nba.TEAM_LABEL) == set(nba.TEAM_NAMES.values())          # every team has a display name


def test_season_first_baskets_counts_games_played_before_today():
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE games (game_id TEXT, game_date TEXT)")
    con.execute("CREATE TABLE lineups (game_id TEXT, team TEXT, player_id TEXT, player_name TEXT, is_starter INTEGER,"
                " minutes REAL)")
    con.execute("CREATE TABLE first_baskets (game_id TEXT, scorer_player_id TEXT)")
    games = [("old", "20260415"), ("g1", "20261101"), ("g2", "20261105"), ("g3", "20261110"), ("today", "20261118")]
    con.executemany("INSERT INTO games VALUES (?,?)", games)
    for gid, _ in games:
        con.execute("INSERT INTO lineups VALUES (?, 'MIA', 'bam', 'Bam', 1, 30)", (gid,))
    con.execute("UPDATE lineups SET minutes = 0 WHERE game_id = 'g3'")             # DNP: not a game played
    con.executemany("INSERT INTO first_baskets VALUES (?,?)",
                    [("old", "bam"), ("g1", "bam"), ("g2", "other"), ("g3", "bam"), ("today", "bam")])
    out = nba.season_first_baskets(con, ["bam", "nobody"], "20261118", season_start="20260801")
    # counts g1 and g2 only: 'old' is last season, g3 he didn't play, 'today' hasn't happened yet
    assert out == {"bam": (1, 2)}
    assert nba.season_first_baskets(con, [], "20261118") == {}


def test_season_tip_records_and_lookup_by_name():
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE games (game_id TEXT, game_date TEXT, tip_winner_player_id TEXT, tip_loser_player_id TEXT)")
    con.execute("CREATE TABLE lineups (game_id TEXT, team TEXT, player_id TEXT, player_name TEXT, is_starter INTEGER,"
                " minutes REAL)")
    con.execute("CREATE TABLE first_baskets (game_id TEXT, scorer_player_id TEXT)")
    con.executemany("INSERT INTO games VALUES (?,?,?,?)", [
        ("old", "20260410", "allen", "edey"),          # last season: ignored
        ("g1", "20261101", "allen", "edey"),
        ("g2", "20261103", "edey", "allen"),
        ("g3", "20261105", "allen", "zubac"),
        ("today", "20261118", "edey", "allen")])        # not played yet: ignored
    con.executemany("INSERT INTO lineups VALUES (?, 'CLE', 'allen', 'Jarrett Allen', 1, 30)",
                    [("g1",), ("g2",), ("g3",)])
    con.execute("INSERT INTO first_baskets VALUES ('g3', 'allen')")
    tips = nba.season_tip_records(con, ["allen", "edey", "nobody"], "20261118", season_start="20260801")
    assert tips == {"allen": (2, 3), "edey": (1, 2)}
    by_name = nba.season_records_by_name(con, ["Jarrett Allen", "Unknown Guy", None], "20261118",
                                         season_start="20260801")
    assert by_name == {"Jarrett Allen": {"fb": (1, 3), "tips": (2, 3)}}
    assert nba.season_records_by_name(con, [], "20261118") == {}


def test_season_records_by_name_ignores_accents():
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE games (game_id TEXT, game_date TEXT, tip_winner_player_id TEXT, tip_loser_player_id TEXT)")
    con.execute("CREATE TABLE lineups (game_id TEXT, team TEXT, player_id TEXT, player_name TEXT, is_starter INTEGER,"
                " minutes REAL)")
    con.execute("CREATE TABLE first_baskets (game_id TEXT, scorer_player_id TEXT)")
    con.execute("INSERT INTO games VALUES ('g1', '20261101', 'luka', 'x')")
    con.execute("INSERT INTO lineups VALUES ('g1', 'LAL', 'luka', 'Luka Doncic', 1, 35)")   # box score: no accents
    con.execute("INSERT INTO first_baskets VALUES ('g1', 'luka')")
    out = nba.season_records_by_name(con, ["Luka Dončić"], "20261118", season_start="20260801")
    assert out == {"Luka Dončić": {"fb": (1, 1), "tips": (1, 1)}}


def test_tip_jumpball_skips_an_empty_entry_before_the_real_one():
    # ESPN, MIA @ DAL 12/03/25: an empty jump ball (no player, no team) listed before the real one
    empty = play("jumpball", team="", pid="", scoring=False)
    real = play("jumpball", team="MIA", pid="bam", scoring=False)
    real.update({"playerName": "Bam Adebayo", "tip_loser_player_id": "ad", "tip_loser_player_name": "Anthony Davis"})
    tip = nba.tip_jumpball([empty, real])
    assert (tip["tip_winner_team"], tip["tip_winner_player_name"], tip["tip_loser_player_name"]) == \
        ("MIA", "Bam Adebayo", "Anthony Davis")
    assert nba.tip_jumpball([empty]) is None                       # only an empty one: no tip recorded


def test_old_gap_decisions():
    """A stored game with no first basket: never played -> remove; played -> fill in; undecided -> wait."""
    from nba import gap_action
    assert gap_action(False, "", False) == "fill"                    # ESPN didn't answer: NOT proof it wasn't played
    assert gap_action(True, "STATUS_POSTPONED", False) == "drop"
    assert gap_action(True, "STATUS_CANCELED", False) == "drop"
    assert gap_action(True, "STATUS_FINAL", True) == "fill"
    assert gap_action(True, "STATUS_SCHEDULED", False) == "wait"


def test_postponed_games_are_not_counted_as_played():
    import pandas as pd
    from nba import _played
    df = pd.DataFrame({"status_type_state": ["post", "post", "in", "post"], "season_type": [2, 2, 2, 3],
                       "status_type_completed": [True, False, False, True]})
    assert list(_played(df)) == [True, False, False, True]           # final / postponed / live / playoff final


def test_summary_plays_read_like_the_library():
    from nba import flatten_summary
    s = {"header": {"competitions": [{"competitors": [
            {"homeAway": "home", "team": {"id": "4", "abbreviation": "CHI"}},
            {"homeAway": "away", "team": {"id": "12", "abbreviation": "LAC"}}]}]},
         "plays": [{"type": {"text": "Driving Finger Roll Layup"}, "period": {"number": 1}, "clock": {"displayValue": "11:32"},
                    "scoringPlay": True, "shootingPlay": True, "team": {"id": "12"}, "participants": [{"athlete": {"id": "3992"}}]}]}
    p = flatten_summary(s)["plays"][0]
    assert p["team.id"] == "12" and p["awayTeamAbbrev"] == "LAC" and p["participants.0.athlete.id"] == "3992"
    assert p["clock.displayValue"] == "11:32" and p["period.number"] == 1 and p["scoringPlay"] is True


def test_nba_com_plays_translate_like_espn():
    """NBA.com backup (ESPN has no plays for a few 2026 playoff games): same shape nba.get_pbp makes."""
    import nba
    import nba_com
    who = {1: {"id": "e1", "name": "Jalen Duren", "team": "DET"}, 2: {"id": "e2", "name": "Wendell Carter Jr.", "team": "ORL"},
           3: {"id": "e3", "name": "Cade Cunningham", "team": "DET"}}
    acts = nba_com.to_actions("401", [
        {"actionType": "jumpball", "period": 1, "clock": "PT11M58.00S", "teamTricode": "DET", "personId": 9,
         "jumpBallWonPersonId": 1, "jumpBallLostPersonId": 2},
        {"actionType": "2pt", "subType": "Layup", "descriptor": "driving", "period": 1, "clock": "PT11M41.00S",
         "teamTricode": "DET", "personId": 3, "shotResult": "Missed"},
        {"actionType": "freethrow", "subType": "1 of 2", "period": 1, "clock": "PT11M20.00S", "teamTricode": "NYK",
         "personId": 3, "shotResult": "Made"}], who)
    tip = nba.tip_jumpball(acts)
    assert (tip["tip_winner_player_id"], tip["tip_loser_player_id"], tip["tip_winner_team"]) == ("e1", "e2", "DET")
    assert nba.first_shots(acts)["DET"]["personId"] == "e3"               # missed layup = first shot
    fb = nba.first_basket(acts)
    assert fb["clock"] == "11:20" and fb["teamTricode"] == "NY" and fb["actionType"] == "free_throw"


def test_retrain_trains_the_field_goal_model_once_if_missing(trained_through_yesterday, monkeypatch):
    import os
    os.remove("pregame_model_fg.json")
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_run({}, calls))
    nba._retrain_if_needed(trained_through_yesterday)
    assert ["train_pregame.py", "--incremental"] in calls


def test_data_patches_load_once(tmp_path, monkeypatch):
    import json
    import sqlite3
    import nba
    (tmp_path / "p.json").write_text(json.dumps({"games": [{"game_id": "9", "game_date": "20260430", "home": "MIN", "away": "DEN",
        "home_score": 110, "away_score": 98, "season_type": 3, "tip": {},
        "first_basket": {"personId": "1", "playerName": "A", "teamTricode": "DEN", "shotType": "Layup", "period": 1, "clock": "11:17"},
        "first_fg": {"personId": "1", "playerName": "A", "teamTricode": "DEN", "shotType": "Layup"},
        "first_shots": {}, "early_shots": {}, "lineups": [["9", "DEN", "1", "A", 1, 30.0]]}]}))
    monkeypatch.setattr(nba, "PATCH_DIR", tmp_path)
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE games (game_id TEXT PRIMARY KEY, game_date TEXT, home_team TEXT, away_team TEXT, home_score INT, away_score INT, status INT, tip_winner_team TEXT, tip_winner_player_id TEXT, tip_winner_player_name TEXT, tip_loser_player_id TEXT, tip_loser_player_name TEXT)")
    con.execute("CREATE TABLE first_baskets (game_id TEXT PRIMARY KEY, scorer_player_id TEXT, scorer_name TEXT, scorer_team TEXT, shot_type TEXT, period INT, clock TEXT, x REAL, y REAL)")
    con.execute("CREATE TABLE lineups (game_id TEXT, team TEXT, player_id TEXT, player_name TEXT, is_starter INT, minutes REAL, PRIMARY KEY (game_id, team, player_id))")
    assert nba.apply_data_patches(con) == 1 and nba.apply_data_patches(con) == 0
    assert con.execute("SELECT scorer_name FROM first_baskets").fetchone() == ("A",)


def test_book_status_tells_not_offered_from_not_posted_from_unavailable():
    import nba
    games = [{"home_code": "MEM", "away_code": "GS", "players": [{"player": "A"}]}, {"home_code": "MIL", "away_code": "POR", "players": []}]
    assert nba.book_status(games, "MEM", "GS") == "ok"                 # read fine: a missing player = not offered
    assert nba.book_status(games, "MIL", "POR") == "not_posted"
    assert nba.book_status(games, "SA", "LAC") == "not_posted"
    assert nba.book_status(None, "MEM", "GS") == "unavailable"


def test_retrain_redoes_an_old_kind_field_goal_model(trained_through_yesterday, monkeypatch):
    json.dump({}, open("pregame_model_fg.json", "w"))           # old file: its team part learned from first points
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_run({}, calls))
    nba._retrain_if_needed(trained_through_yesterday)
    assert any("train_pregame.py" in " ".join(c) for c in calls)


# ── 2024-25 history season (data_patches/history_2024_25.json.gz) ─────────────
def test_history_patch_loads_once(project, tmp_path, monkeypatch):
    import gzip
    pd = tmp_path / "patches"; pd.mkdir()
    monkeypatch.setattr(nba, "PATCH_DIR", pd)
    cols = [r[1] for r in project.execute("PRAGMA table_info(games)")]
    row = [None] * len(cols)
    row[cols.index("game_id")], row[cols.index("game_date")] = "h1", "20250301"
    row[cols.index("home_team")], row[cols.index("away_team")], row[cols.index("status")] = "NY", "BOS", 3
    fb_cols = [r[1] for r in project.execute("PRAGMA table_info(first_baskets)")]
    fb = [None] * len(fb_cols); fb[fb_cols.index("game_id")], fb[fb_cols.index("scorer_player_id")] = "h1", "7"
    data = {"tables": {"games": {"columns": cols, "rows": [row]}, "first_baskets": {"columns": fb_cols, "rows": [fb]}}, "logs": {}}
    with gzip.open(pd / nba.HISTORY_PATCH, "wt") as f:
        json.dump(data, f)
    assert nba.apply_history_patch(project) == 1
    assert nba.apply_history_patch(project) == 0                      # already in: nothing added
    assert project.execute("SELECT season_type FROM game_types WHERE game_id = 'h1'").fetchone()[0] == 2


def test_history_patch_missing_file_does_nothing(project, tmp_path, monkeypatch):
    monkeypatch.setattr(nba, "PATCH_DIR", tmp_path / "nothing_here")
    assert nba.apply_history_patch(project) == 0


def test_retrain_when_model_used_another_early_shot_setting(trained_through_yesterday, monkeypatch):
    json.dump({"early_m": 25}, open("pregame_model.json", "w"))              # trained before the 150 setting
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_run({}, calls))
    nba._retrain_if_needed(trained_through_yesterday)
    assert any("train_pregame.py" in " ".join(c) for c in calls)
