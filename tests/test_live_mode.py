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
