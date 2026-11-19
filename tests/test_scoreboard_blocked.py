"""NBA.com refusing the scoreboard (403) gives ONE note per run, not one per game, and the daily feed's game id is used."""
import starters as st


class _Resp:
    status_code = 403


class _Forbidden(Exception):
    response = _Resp()


def test_blocked_scoreboard_reported_once(monkeypatch, capsys):
    calls = []

    def fake_json(url):
        calls.append(url)
        raise _Forbidden("403 Client Error: Forbidden")

    monkeypatch.setattr(st, "_nba_json", fake_json)
    monkeypatch.setattr("time.sleep", lambda s: None)
    st._FEED_CACHE.clear(); st._SB_CACHE.clear()
    for _ in range(6):
        assert st.nba_confirmed_starters("LAL", "UTAH", None) is None
    out = capsys.readouterr().out
    assert out.count("scoreboard unavailable") == 1
    assert "(403)" in out
    assert len(calls) == 2            # first try + one retry, then cached for the run


def test_daily_feed_game_id_skips_scoreboard(monkeypatch):
    seen = []

    def fake_json(url):
        seen.append(url)
        raise _Forbidden("403")                    # box score not posted yet

    monkeypatch.setattr(st, "_nba_json", fake_json)
    st._SB_CACHE.clear(); st._FEED_CACHE.clear()
    st._FEED_CACHE["20261118"] = {"games": [{"gameId": "0022600999",
                                             "homeTeam": {"teamAbbreviation": "LAL"},
                                             "awayTeam": {"teamAbbreviation": "UTA"}}]}
    assert st.nba_confirmed_starters("LAL", "UTAH", None) is None
    assert seen == [st.NBA_BOXSCORE.format(gid="0022600999")]      # straight to the box score
    st._FEED_CACHE.clear()
