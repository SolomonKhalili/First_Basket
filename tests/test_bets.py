"""My bets: cleaning what the page sends, encryption the browser can read, the Mac app's /api/bets, settling."""
import json
import os

import pytest

import bets

KEY = os.urandom(32)


def bet(**kw):
    return {"day": "20261119", "game_id": "401910019", "away": "GS", "home": "MEM", "player": "Zach Edey",
            "player_id": "4600663", "team": "MEM", "book": "FanDuel", "american": "+600", "stake": "10", **kw}


def test_clean_keeps_the_shape_and_fixes_types():
    b = bets.clean(bet(id="a1"))
    assert b["american"] == 600 and b["stake"] == 10.0 and b["id"] == "a1" and b["book"] == "FanDuel"
    assert bets.clean(bet(american=-110))["american"] == -110


@pytest.mark.parametrize("bad", [{"american": 50}, {"american": "abc"}, {"stake": 0}, {"stake": -5},
                                 {"player": ""}, {"day": "11/19"}])
def test_clean_refuses_nonsense(bad):
    with pytest.raises(ValueError):
        bets.clean(bet(**bad))


def test_seal_unseal_round_trip_and_wrong_key_fails():
    blob = bets.seal([bets.clean(bet(id="x"))], KEY)
    assert "Zach" not in json.dumps(blob)                          # the relay only sees scrambled text
    assert bets.unseal(blob, KEY)[0]["player"] == "Zach Edey"
    assert bets.unseal(None, KEY) == []
    with pytest.raises(Exception):
        bets.unseal(blob, os.urandom(32))


def test_write_code_differs_per_user_and_key():
    assert bets.write_code(KEY, "solomon") != bets.write_code(KEY, "simon")
    assert bets.write_code(KEY, "solomon") != bets.write_code(os.urandom(32), "solomon")
    assert len(bets.write_code(KEY, "solomon")) == 64


def test_add_and_remove_go_through_the_relay(monkeypatch):
    store = {}
    monkeypatch.setattr(bets, "load", lambda user, key=None: list(store.get(user, [])))
    monkeypatch.setattr(bets, "save", lambda user, b, key=None: store.__setitem__(user, b))
    bets.add("solomon", bet(id="1"))
    bets.add("solomon", bet(id="2", player="Ja Morant"))
    bets.add("simon", bet(id="3"))
    assert [b["id"] for b in store["solomon"]] == ["1", "2"] and len(store["simon"]) == 1   # each person separate
    bets.remove("solomon", "1")
    assert [b["id"] for b in store["solomon"]] == ["2"]


def test_results_maps_games_to_first_scorer():
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE first_baskets (game_id TEXT, scorer_player_id TEXT, scorer_name TEXT)")
    con.execute("INSERT INTO first_baskets VALUES ('401910019', '4600663', 'Zach Edey')")
    assert bets.results(con) == {"401910019": ["4600663", "Zach Edey"]}


def test_every_username_gets_its_own_slot():
    assert bets.slot("solomon") == "solomon"
    odd = ["mike@work", "josé", "a" * 60, "Big!Mike"]
    slots = [bets.slot(u) for u in odd]
    assert all(bets.USER_RE.match(s) for s in slots)               # the relay accepts every one
    assert len(set(slots)) == len(odd)                              # and nobody shares a list


def test_parlay_shape_and_rules():
    leg = lambda g, p: {"day": "20261119", "game_id": g, "away": "GS", "home": "MEM", "player": p, "american": 600}  # noqa: E731
    b = bets.clean({"type": "parlay", "legs": [leg("1", "A"), leg("2", "B")], "american": 4800, "stake": "5", "book": "DraftKings"})
    assert b["type"] == "parlay" and len(b["legs"]) == 2 and b["american"] == 4800 and b["stake"] == 5.0
    for bad in ({"legs": [leg("1", "A")]},                                  # one leg is a straight bet
                {"legs": [leg("1", "A"), leg("1", "B")]},                   # two players from the same game
                {"legs": [leg("1", "A"), leg("2", "B")], "stake": 0}):
        with pytest.raises(ValueError):
            bets.clean({"type": "parlay", "american": 4800, "stake": 5, **bad})
