"""A later run that skips a started game keeps that game's earlier picks card (same day only)."""
import gui


def _res(date, cards, skipped=()):
    return {"date": date, "schedule": [{"game_id": "1"}, {"game_id": "2"}],
            "cards": [{"game_id": g, "matchup": f"M{g}"} for g in cards],
            "skipped": [{"matchup": f"M{g}", "reason": "already tipped off — skipped."} for g in skipped]}


def test_started_game_keeps_its_card(tmp_path):
    f = tmp_path / "cards.json"
    gui.keep_todays_cards(_res("Wed", ["1", "2"]), f)
    r = gui.keep_todays_cards(_res("Wed", ["2"], skipped=["1"]), f)
    assert [c["game_id"] for c in r["cards"]] == ["1", "2"]          # schedule order kept
    assert r["cards"][0]["earlier"] and not r["cards"][1]["earlier"]
    assert r["skipped"] == []                                        # no "skipped" line for a game we have a card for


def test_new_day_starts_fresh(tmp_path):
    f = tmp_path / "cards.json"
    gui.keep_todays_cards(_res("Wed", ["1", "2"]), f)
    r = gui.keep_todays_cards(_res("Thu", ["2"]), f)
    assert [c["game_id"] for c in r["cards"]] == ["2"]
