"""Game-night live mode: publishes when something changes (throttled), stops once every game has tipped off."""
import sys
from datetime import datetime, timedelta, timezone

import publish


def _result(price, tipped=False):
    soon = (datetime.now(timezone.utc) + timedelta(minutes=40)).isoformat()
    return {"cards": [{"matchup": "A @ B", "picks": [["X", "B", 0.2, 400]], "fanduel": {"X": price}}],
            "skipped": [], "schedule": [{"status": 2 if tipped else 1, "start_utc": soon}]}


def test_fingerprint_sees_odds_changes_only():
    assert publish.fingerprint(_result(450)) == publish.fingerprint(_result(450))
    assert publish.fingerprint(_result(450)) != publish.fingerprint(_result(500))


def test_live_loop(monkeypatch, tmp_path):
    seq = iter([_result(450), _result(450), _result(500), _result(500, tipped=True)])
    published, saved, clock = [], [], [1000.0]
    monkeypatch.setattr(publish, "HERE", tmp_path)
    monkeypatch.setattr(publish, "load_site_users", lambda: {"u": {}})
    monkeypatch.setattr(publish, "run_update", lambda: next(seq))
    monkeypatch.setattr(publish, "publish_site", lambda out, r: published.append(r["cards"][0]["fanduel"]["X"]))
    monkeypatch.setattr(publish, "save_state", lambda: saved.append(1))
    monkeypatch.setattr(publish.time, "time", lambda: clock[0])
    monkeypatch.setattr(publish.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    out, err = sys.stdout, sys.stderr
    try:
        assert publish.live(tmp_path / "site") == 0
    finally:
        sys.stdout, sys.stderr = out, err
    # first check publishes; same odds = no publish; new odds 3 min after = wait for the 6-min gap; then the
    # last check (everything tipped) publishes the pending change and saves
    assert published == [450, 500]
    assert saved == [1]
