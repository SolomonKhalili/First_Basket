"""Game-night live mode: publishes when something changes (throttled), stops once every game has tipped off."""
import sys
from datetime import datetime, timedelta, timezone

import publish


def _result(price, status=1):
    soon = (datetime.now(timezone.utc) + timedelta(minutes=40)).isoformat()
    return {"cards": [{"matchup": "A @ B", "picks": [["X", "B", 0.2, 400]], "fanduel": {"X": price}}],
            "skipped": [], "schedule": [{"status": status, "start_utc": soon}]}


def _run(monkeypatch, tmp_path, results, filed=()):
    import nba
    seq, files = iter(results), iter(filed)
    published, saved, clock = [], [], [1000.0]
    monkeypatch.setattr(publish, "HERE", tmp_path)
    monkeypatch.setattr(publish, "RELAY_FILE", tmp_path / "relay_url.txt")    # no relay: never touch the real database
    monkeypatch.setattr(publish, "load_site_users", lambda: {"u": {}})
    monkeypatch.setattr(publish, "run_update", lambda: next(seq))
    monkeypatch.setattr(publish, "publish_site", lambda out, r: published.append(r["cards"][0]["fanduel"]["X"]))
    monkeypatch.setattr(publish, "save_state", lambda: saved.append(1))
    monkeypatch.setattr(publish.time, "time", lambda: clock[0])
    monkeypatch.setattr(publish.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(nba, "file_finished_games", lambda day: next(files, 0))
    out, err = sys.stdout, sys.stderr
    try:
        assert publish.live(tmp_path / "site") == 0
    finally:
        sys.stdout, sys.stderr = out, err
    return published, saved


def test_fingerprint_sees_odds_changes_only():
    assert publish.fingerprint(_result(450)) == publish.fingerprint(_result(450))
    assert publish.fingerprint(_result(450)) != publish.fingerprint(_result(500))


def test_live_loop(monkeypatch, tmp_path):
    published, saved = _run(monkeypatch, tmp_path, [_result(450), _result(450), _result(500), _result(500, status=3)])
    # first check publishes; same odds = no publish; new odds 3 min later still publishes (6-min gap reached at
    # check 3); the last check (every game over) ends the run and saves
    assert published == [450, 500]
    assert saved == [1]


def test_files_results_after_last_tip(monkeypatch, tmp_path):
    # check 1: game on (tipped, not final) -> nothing to file; check 2: a game finished -> filed + published;
    # check 3: all final -> done
    published, saved = _run(monkeypatch, tmp_path, [_result(450, status=2), _result(450, status=2),
                                                    _result(450, status=3)], filed=[0, 1, 0])
    assert published == [450, 450]          # the first publish + the one after filing a result
    assert saved == [1]


def test_should_run_until_results_are_filed(monkeypatch):
    import requests

    class R:
        def __init__(self, states): self.states = states
        def json(self): return {"events": [{"id": str(i), "status": {"type": {"state": s}}} for i, s in enumerate(self.states)]}

    def run(states, filed):
        monkeypatch.setattr(requests, "get", lambda *a, **k: R(states))
        monkeypatch.setattr(publish, "filed_games", lambda ids: set(filed))
        return publish.should_run("hourly")

    assert run(["pre", "post"], []) is True               # a game still to come
    assert run(["in", "post"], ["1"]) is True             # a game in progress: file it when it ends
    assert run(["post", "post"], ["0"]) is True           # one finished game not filed yet
    assert run(["post", "post"], ["0", "1"]) is False     # everything filed: nothing to do
    assert run([], []) is False                           # no games today


def test_live_job_restarts_only_for_code_changes():
    from publish import code_changed
    assert code_changed(["gui/index.html"]) and code_changed(["nba.py"]) and code_changed(["requirements.txt"])
    assert not code_changed(["site_users.json"]) and not code_changed(["website_live/relay_url.txt"])
    assert not code_changed(["BLUEPRINT.md", "README.md"]) and not code_changed(["tests/test_bets.py"])
    assert not code_changed([])


def test_nap_without_github_just_waits(monkeypatch):
    import publish
    slept = []
    monkeypatch.delenv("STATE_REMOTE", raising=False)
    clock = iter([0, 0, 60, 60, 120, 120, 180, 180, 999, 999])
    monkeypatch.setattr(publish.time, "time", lambda: next(clock))
    monkeypatch.setattr(publish.time, "sleep", lambda s: slept.append(s))
    assert publish.nap(150) is False and slept and all(s <= 60 for s in slept)


def test_quick_odds_logs_prices_and_the_close(monkeypatch):
    import sqlite3
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    import ev
    import fanduel_odds as fd
    import publish
    con = sqlite3.connect(":memory:")
    tip = (datetime.now(ZoneInfo("UTC")) + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%MZ")
    far = (datetime.now(ZoneInfo("UTC")) + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%MZ")
    result = {"schedule": [{"game_id": "g1", "away_team": "GS", "home_team": "MEM", "status": 1, "start_utc": tip},
                           {"game_id": "g2", "away_team": "NY", "home_team": "MIN", "status": 1, "start_utc": far}],
              "cards": [{"matchup": "GS @ MEM", "fd_market": {"id": "m1", "runners": {"7": "Zach Edey"}},
                         "probs": [["Zach Edey", "MEM", 0.15]], "home_starters": [{"name": "Zach Edey", "player_id": "1"}]},
                        {"matchup": "NY @ MIN", "fd_market": {"id": "m2", "runners": {}}, "probs": []}]}
    asked = []
    def prices(ids):
        asked.append(list(ids))
        return [{"marketId": "m1", "marketStatus": "OPEN", "runnerDetails": [
            {"selectionId": 7, "winRunnerOdds": {"americanDisplayOdds": {"americanOddsInt": 650}}}]}]
    monkeypatch.setattr(fd, "fetch_prices", prices)
    assert publish.quick_odds(result, con) == 1 and asked == [["m1"]]          # only the game tipping soon
    assert con.execute("SELECT player_name, american FROM odds_log").fetchall() == [("Zach Edey", 650)]
    monkeypatch.setattr(fd, "fetch_prices", lambda ids: [{"marketId": "m1", "marketStatus": "SUSPENDED"}])
    publish.quick_odds(result, con)
    assert con.execute("SELECT closed_at IS NOT NULL FROM market_times WHERE book='FanDuel'").fetchone()[0] == 1


def test_republish_page_uses_the_sites_current_data(tmp_path, monkeypatch):
    import io
    import json
    import urllib.request
    import publish
    data = {"v": 1, "iv": "IV", "ct": "CT", "users": {}}
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=0: io.BytesIO(json.dumps(data).encode()))
    monkeypatch.setattr(publish, "load_site_users", lambda: {"u": {"x": 1}})
    monkeypatch.delenv("SITE_REMOTE", raising=False)
    assert publish.republish_page(tmp_path) is True
    d = json.loads((tmp_path / "data.json").read_text())
    assert d["iv"] == "IV" and d["ct"] == "CT" and "u" in d["users"] and (tmp_path / "index.html").exists()
