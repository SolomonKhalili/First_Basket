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
    assert r["schedule"][0] == {"game_id": "1", "start": "7:00 PM ET", "home_team": "MIA", "away_team": "MIL",
                                "status": 1}
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
