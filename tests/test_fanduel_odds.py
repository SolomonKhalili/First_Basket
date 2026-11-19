import pytest

import fanduel_odds as fd


def runner(name, team, american, decimal, status="ACTIVE"):
    return {"runnerName": name, "teamAbbr": team, "runnerStatus": status,
            "winRunnerOdds": {"americanDisplayOdds": {"americanOddsInt": american},
                              "trueOdds": {"decimalOdds": {"decimalOdds": decimal}}}}


def event_page(runners, status="OPEN", name="First Basket", mtype="FIRST_BASKET_(SAME_GAME_MULTIS)"):
    return {"attachments": {"markets": {
        "1": {"marketName": "Money Line", "marketType": "MONEY_LINE", "runners": [runner("X", "MIA", -150, 1.67)]},
        "2": {"marketName": name, "marketType": mtype, "marketStatus": status, "runners": runners},
    }}}


def test_parse_games_keeps_only_matchups_sorted_by_start():
    page = {"attachments": {"events": {
        "1": {"name": "NBA Futures", "openDate": "2026-06-10T00:00:00Z"},
        "3": {"name": "Golden State Warriors @ Dallas Mavericks", "openDate": "2026-11-19T01:10:00Z"},
        "2": {"name": "Milwaukee Bucks @ Miami Heat", "openDate": "2026-11-19T00:10:00Z"},
    }}}
    games = fd.parse_games(page)
    assert [g["event_id"] for g in games] == ["2", "3"]
    assert games[1]["away"] == "Golden State Warriors" and games[1]["home"] == "Dallas Mavericks"


def full_market():
    """5 Heat + 5 Warriors, plus one scratched player."""
    rs = [runner(f"Heat {i}", "MIA", 400 + 100 * i, 5.0 + i) for i in range(5)]
    rs += [runner(f"Dubs {i}", "GSW", 450 + 100 * i, 5.5 + i) for i in range(5)]
    return rs + [runner("Scratched Guy", "MIA", 300, 4.0, status="REMOVED")]


def test_parse_first_basket_prices_and_no_vig():
    rows = fd.parse_first_basket(event_page(full_market()))
    assert len(rows) == 10 and "Scratched Guy" not in {r["player"] for r in rows}
    assert rows[0]["player"] == "Heat 0" and rows[1]["player"] == "Dubs 0"   # shortest price first
    assert rows[1]["team"] == "GS"                                            # FanDuel code -> ESPN code
    assert rows[0]["american"] == 400 and rows[0]["implied"] == pytest.approx(1 / 5.0)
    assert sum(r["no_vig"] for r in rows) == pytest.approx(1.0)
    assert fd.margin(rows) == pytest.approx(sum(r["implied"] for r in rows) - 1)


def test_partial_list_has_no_margin_or_no_vig():
    # FanDuel sometimes lists only one team's starters (the other lineup not posted yet).
    rows = fd.parse_first_basket(event_page([runner(f"Pels {i}", "NOP", 500, 6.0) for i in range(5)]))
    assert len(rows) == 5 and rows[0]["team"] == "NO"
    assert not fd.is_complete(rows)
    assert fd.margin(rows) is None and all(r["no_vig"] is None for r in rows)


def test_parse_first_basket_missing_or_closed_market():
    assert fd.parse_first_basket({"attachments": {"markets": {}}}) == []
    assert fd.parse_first_basket(event_page([runner("A", "MIA", 300, 4.0)], status="SUSPENDED")) == []
    assert fd.margin([]) is None


def test_parse_first_basket_found_by_type_when_renamed():
    rows = fd.parse_first_basket(event_page([runner("A", "NYK", 300, 4.0)], name="1st Basket Scorer"))
    assert rows[0]["team"] == "NY"


def test_team_code_from_full_name():
    assert fd.team_code("Golden State Warriors") == "GS"
    assert fd.team_code("Portland Trail Blazers") == "POR"
    assert fd.team_code("Philadelphia 76ers") == "PHI"
    assert fd.team_code("Nowhere Team") == ""


def test_match_prices_by_normalized_name_then_last_name():
    fd_players = [{"player": "P.J. Washington", "team": "DAL", "american": 600},
                  {"player": "Herbert Jones", "team": "NO", "american": 1200},
                  {"player": "Nikola Jokic", "team": "DEN", "american": 300}]
    picks = [("PJ Washington", "DAL", 0.1, 800), ("Herb Jones", "NO", 0.08, 1100),
             ("Nikola Jokić", "DEN", 0.2, 400), ("Jalen Jones", "DAL", 0.05, 1900)]
    got = fd.match_prices(fd_players, picks)
    assert {k: v["american"] for k, v in got.items()} == \
        {"PJ Washington": 600, "Herb Jones": 1200, "Nikola Jokić": 300}  # Jalen Jones: DAL has no Jones listed


def test_game_for():
    games = [{"home_code": "MIA", "away_code": "MIL"}, {"home_code": "OKC", "away_code": "ATL"}]
    assert fd.game_for(games, "OKC", "ATL") is games[1]
    assert fd.game_for(games, "ATL", "OKC") is None


def test_team_to_score_first_prices():
    import fanduel_odds as fd
    page = {"attachments": {"markets": {"1": {"marketName": "Team to Score First", "marketStatus": "OPEN", "runners": [
        {"runnerName": "Golden State Warriors", "winRunnerOdds": {"americanDisplayOdds": {"americanOddsInt": 124}}},
        {"runnerName": "Memphis Grizzlies", "winRunnerOdds": {"americanDisplayOdds": {"americanOddsInt": -158}}}]}}}}
    assert fd.parse_team_first(page) == {"GS": 124, "MEM": -158}
    page["attachments"]["markets"]["1"]["marketStatus"] = "SUSPENDED"
    assert fd.parse_team_first(page) == {}
