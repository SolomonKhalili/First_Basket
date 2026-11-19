"""DraftKings 1st Points Scorer parsing, and picking the better price across books."""
import draftkings_odds as dk
import nba


def _data():
    team = lambda nick, short, role: {"name": f"{short} {nick}", "venueRole": role, "type": "Team",  # noqa: E731
                                       "metadata": {"rosettaTeamName": nick, "shortName": short}}
    sel = lambda name, role, am, dec: {"marketId": "m1", "label": name, "trueOdds": dec,  # noqa: E731
                                       "displayOdds": {"american": am}, "participants": [{"venueRole": role}]}
    return {"events": [{"id": "e1", "participants": [team("Kings", "SAC", "Home"), team("Suns", "PHO", "Away")]}],
            "markets": [{"id": "m1", "eventId": "e1", "name": "1st Points Scorer"}],
            "selections": [sel("Domantas Sabonis", "HomePlayer", "+600", 7.0),
                           sel("Devin Booker", "AwayPlayer", "+454", 5.54),
                           sel("Bad Row", "AwayPlayer", "n/a", 0)]}


def test_parse_first_points():
    (g,) = dk.parse_first_points(_data())
    assert (g["home_code"], g["away_code"]) == ("SAC", "PHX")
    assert [(r["player"], r["team"], r["american"]) for r in g["players"]] == [
        ("Devin Booker", "PHX", 454), ("Domantas Sabonis", "SAC", 600)]      # shortest price first, bad row skipped


def test_unicode_minus():
    assert dk._american("−166") == -166 and dk._american("+454") == 454


def test_best_price_across_books():
    best, book = nba._best_prices({"FanDuel": {"A": 500, "B": 700}, "DraftKings": {"A": 600, "B": 650, "C": 900}})
    assert best == {"A": 600, "B": 700, "C": 900}
    assert book == {"A": "DraftKings", "B": "FanDuel", "C": "DraftKings"}


def test_card_uses_better_book_for_value():
    fd_games = [{"home_code": "SAC", "away_code": "PHX", "players": [
        {"player": "Devin Booker", "team": "PHX", "american": 400, "market_id": "1", "selection_id": "9"}]}]
    dk_games = [{"home_code": "SAC", "away_code": "PHX", "players": [
        {"player": "Devin Booker", "team": "PHX", "american": 600}]}]
    everyone = [{"name": "Devin Booker", "team": "PHX", "prob": 0.2}]
    card = nba._fanduel_card(fd_games, "SAC", "PHX", [("Devin Booker", "PHX", 0.2, 400)], everyone, dk_games)
    assert card["fanduel"] == {"Devin Booker": 400} and card["draftkings"] == {"Devin Booker": 600}
    assert card["best_book"]["Devin Booker"] == "DraftKings"
    assert abs(card["ev"]["Devin Booker"] - (0.2 * 6 - 0.8)) < 1e-9      # EV at the better (DraftKings) price
    assert card["best_value"]["book"] == "DraftKings"


def test_snapshot_used_only_when_fresh(tmp_path):
    import json
    import time
    f = tmp_path / "dk.json"
    f.write_text(json.dumps({"at": time.time() - 120, "games": [{"home_code": "SAC"}]}))
    games, age = dk.load_snapshot(f)
    assert games == [{"home_code": "SAC"}] and 1.9 < age < 2.1          # 2 minutes old: used
    f.write_text(json.dumps({"at": time.time() - 15 * 60, "games": [{"home_code": "SAC"}]}))
    assert dk.load_snapshot(f) == (None, None)                          # 15 minutes old: ignored
    assert dk.load_snapshot(tmp_path / "missing.json") == (None, None)
