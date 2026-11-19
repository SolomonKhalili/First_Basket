"""A later run that skips a started game keeps that game's earlier picks card (same day only)."""
import gui


def _res(date, cards, skipped=()):
    return {"date": date, "schedule": [{"game_id": "1", "away_team": "A1", "home_team": "H1"},
                                       {"game_id": "2", "away_team": "A2", "home_team": "H2"}],
            "cards": [{"matchup": f"A{g} @ H{g}"} for g in cards],          # real cards have no game id
            "skipped": [{"matchup": f"A{g} @ H{g}", "reason": "already tipped off — skipped."} for g in skipped]}


def test_started_game_keeps_its_card(tmp_path):
    f = tmp_path / "cards.json"
    gui.keep_todays_cards(_res("Wed", ["1", "2"]), f)
    r = gui.keep_todays_cards(_res("Wed", ["2"], skipped=["1"]), f)
    assert [c["matchup"] for c in r["cards"]] == ["A1 @ H1", "A2 @ H2"]   # schedule order kept
    assert r["cards"][0]["earlier"] and not r["cards"][1]["earlier"]
    assert r["skipped"] == []                                        # no "skipped" line for a game we have a card for


def test_new_day_starts_fresh(tmp_path):
    f = tmp_path / "cards.json"
    gui.keep_todays_cards(_res("Wed", ["1", "2"]), f)
    r = gui.keep_todays_cards(_res("Thu", ["2"]), f)
    assert [c["matchup"] for c in r["cards"]] == ["A2 @ H2"]


def test_log_card_never_replaces_saved_card(tmp_path):
    f = tmp_path / "cards.json"
    gui.keep_todays_cards(_res("Wed", ["1"]), f)                     # real card, made before tip-off
    later = _res("Wed", [])
    later["cards"] = [{"matchup": "A1 @ H1", "from_log": True}, {"matchup": "A2 @ H2", "from_log": True}]
    r = gui.keep_todays_cards(later, f)
    assert len(r["cards"]) == 2                                      # no duplicates
    by = {c["matchup"]: c for c in r["cards"]}
    assert "from_log" not in by["A1 @ H1"] and by["A1 @ H1"]["earlier"]   # the saved card wins
    assert by["A2 @ H2"]["from_log"] and by["A2 @ H2"]["earlier"]         # rebuilt from the log, tagged
