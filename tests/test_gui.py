import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import numpy as np

import gui


def test_collector_keeps_what_predict_would_print():
    steps = []
    c = gui.Collector(steps.append)
    with c.console.status("Checking injuries and lineups…"):
        pass
    c.header("Wednesday, November 18, 2026", "Data up to date", "")
    c.schedule([{"game_id": "1", "start": "7:00 PM ET", "home_team": "MIA", "away_team": "MIL", "status": 1,
                 "game_date": "20261118"}])
    c.game_card({"matchup": "MIL @ MIA", "picks": [("Bam Adebayo", "MIA", 0.2, 400)]})
    c.skipped("DET @ NO", "lineup not confirmed yet")
    c.notes(["FanDuel odds unavailable"])
    c.legend(True)
    r = c.result
    assert steps == ["Checking injuries and lineups"]
    assert r["date"] == "Wednesday, November 18, 2026" and r["summary"] == ["Data up to date"]
    assert set(r["schedule"][0]) == set(gui.GAME_KEYS) and r["schedule"][0]["home_team"] == "MIA"
    assert r["cards"][0]["matchup"] == "MIL @ MIA"
    assert r["skipped"] == [{"matchup": "DET @ NO", "reason": "lineup not confirmed yet"}]
    assert r["notes"] == ["FanDuel odds unavailable"]


def test_to_json_handles_numpy_numbers():
    out = json.loads(gui.to_json({"p": np.float64(0.25), "n": np.int64(3), "tip": {"MIA": np.float32(0.5)}}))
    assert out == {"p": 0.25, "n": 3, "tip": {"MIA": 0.5}}


def test_job_reports_errors_instead_of_crashing(monkeypatch):
    import nba

    def boom(**kw):
        raise RuntimeError("ESPN down")
    monkeypatch.setattr(nba, "predict_today", boom)
    job = gui.Job()
    assert job.start(set())
    for _ in range(100):
        if not job.state["running"]:
            break
        threading.Event().wait(0.02)
    assert job.state["running"] is False and job.state["error"] == "RuntimeError: ESPN down"


def test_job_runs_one_at_a_time(monkeypatch):
    import nba
    release = threading.Event()
    monkeypatch.setattr(nba, "predict_today", lambda **kw: release.wait(2))
    job = gui.Job()
    assert job.start(set()) is True
    assert job.start(set()) is False             # second click while running is refused
    release.set()


def test_server_serves_page_and_status():
    server = ThreadingHTTPServer(("127.0.0.1", 0), gui.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page = urllib.request.urlopen(base + "/").read().decode()
        assert "<title>First Basket Picks</title>" in page
        status = json.loads(urllib.request.urlopen(base + "/api/status").read())
        assert set(status) >= {"running", "step", "error", "result"}
        try:
            urllib.request.urlopen(base + "/nope")
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        server.shutdown()


def test_result_of_first_basket_vs_our_pick():
    pick = {"id": "1", "name": "Bam Adebayo", "top3": ["Bam Adebayo", "Giannis Antetokounmpo", "Tyler Herro"]}
    assert gui.result_of({"id": "1", "name": "Bam Adebayo"}, pick) == "hit"
    assert gui.result_of({"id": "9", "name": "Giannis Antetokounmpo"}, pick) == "top3"
    assert gui.result_of({"id": "8", "name": "Kyle Kuzma"}, pick) == "miss"
    assert gui.result_of(None, pick) is None and gui.result_of({"id": "1", "name": "X"}, None) is None


def test_live_board_marks_results(monkeypatch):
    import sqlite3

    import nba
    import picks
    con = sqlite3.connect(":memory:")
    picks.log_pick(con, {"game_id": "g1", "game_date": "20261118", "matchup": "MIL @ MIA", "lineup_type": "CONFIRMED",
                         "lineup_sig": "x", "model_id": "m", "tip_home": 0.85, "home_jumper": "Bam Adebayo",
                         "away_jumper": "Myles Turner",
                         "players": [{"id": "1", "name": "Bam Adebayo", "team": "MIA", "prob": 0.2},
                                     {"id": "2", "name": "Tyler Herro", "team": "MIL", "prob": 0.1}]})
    games = [{"game_id": "g1", "start": "7:00 PM ET", "start_utc": "2026-11-19T00:00Z", "home_team": "MIA",
              "away_team": "MIL", "status": 2, "home_score": 2, "away_score": 0, "detail": "Q1 11:30"},
             {"game_id": "g2", "start": "9:00 PM ET", "start_utc": "2026-11-19T02:00Z", "home_team": "DEN",
              "away_team": "BOS", "status": 1, "home_score": 0, "away_score": 0, "detail": ""}]
    calls = []
    monkeypatch.setattr(nba, "get_todays_games", lambda: games)
    monkeypatch.setattr(nba, "db", lambda: con)
    monkeypatch.setattr(nba, "season_records_by_name",
                        lambda con, names, d: {"Bam Adebayo": {"fb": (4, 14), "tips": (10, 14)}})
    monkeypatch.setattr(nba, "get_pbp", lambda gid: calls.append(gid) or [
        {"scoringPlay": True, "period": 1, "personId": "1", "playerName": "Bam Adebayo", "teamTricode": "MIA"}])
    b = gui.LiveBoard()
    snap = b.snapshot()
    live, upcoming = snap["games"]
    assert live["first_basket"]["name"] == "Bam Adebayo" and live["result"] == "hit"
    assert live["our_pick"]["name"] == "Bam Adebayo"
    assert (live["our_pick"]["away_jumper"], live["our_pick"]["home_jumper"]) == ("Myles Turner", "Bam Adebayo")
    assert live["our_pick"]["tip_home"] == 0.85
    assert live["records"]["name"] == {"fb": (4, 14), "tips": (10, 14)}         # Bam: pick and home jumper
    assert live["records"]["home_jumper"]["tips"] == (10, 14) and live["records"]["away_jumper"] is None
    assert upcoming["first_basket"] is None and upcoming["result"] is None   # pre-game: no play-by-play call
    b.refresh()
    assert calls == ["g1"]                     # first basket found once, then remembered


def test_data_tabs_match_the_spreadsheet_and_csv():
    import nba
    import picks
    con = nba.db()
    picks_tab = gui.data_tab("picks")
    assert [c["label"] for c in picks_tab["columns"]] == [picks.LABELS[k] for k in picks.COLUMNS]
    assert len(picks_tab["rows"]) == len(picks.prediction_rows(con))
    assert any(c["yesno"] for c in picks_tab["columns"])
    games = gui.data_tab("games")
    assert len(games["rows"]) == len(nba.csv_rows(con))                  # every game in the CSV
    assert games["columns"][0]["label"] == "Date"
    assert len(gui.data_tab("lineups")["columns"]) == 8
    assert gui.data_tab("scorecard")["lines"][0][0].startswith("SCORECARD")
    try:
        gui.data_tab("nope")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_server_data_endpoint():
    server = ThreadingHTTPServer(("127.0.0.1", 0), gui.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        d = json.loads(urllib.request.urlopen(base + "/api/data?tab=games").read())
        assert d["tab"] == "games" and len(d["rows"]) > 100
        try:
            urllib.request.urlopen(base + "/api/data?tab=bogus")
            raise AssertionError("expected 400")
        except urllib.error.HTTPError as e:
            assert e.code == 400
    finally:
        server.shutdown()


def test_data_version_changes_when_a_file_is_updated(tmp_path, monkeypatch):
    import os
    for f in gui.DATA_FILES:
        (tmp_path / f).write_text("x")
    monkeypatch.setattr(gui, "__file__", str(tmp_path / "gui.py"))
    v1 = gui.data_version()
    later = os.stat(tmp_path / "my_predictions.xlsx").st_mtime + 60
    os.utime(tmp_path / "my_predictions.xlsx", (later, later))           # the spreadsheet gets re-saved
    assert gui.data_version() != v1
