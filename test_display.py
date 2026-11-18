from rich.console import Console
import display as ui
import nba


def card(label="PROJECTED", notes=()):
    starters = lambda team, names: [{"name": n, "team": team, "player_id": n} for n in names]
    return {
        "matchup": "NY @ TOR", "start": "7:30 PM ET", "label": label, "updated": False,
        "picks": [("Jalen Brunson", "NY", 0.162, 517), ("RJ Barrett", "TOR", 0.119, 739)],
        "tip": {"TOR": 0.45, "NY": 0.55}, "jumpers": {"NY": "Karl-Anthony Towns", "TOR": None},
        "home": "TOR", "away": "NY",
        "home_starters": starters("TOR", ["RJ Barrett", "Scottie Barnes", "A", "B", "C"]),
        "away_starters": starters("NY", ["Jalen Brunson", "Josh Hart", "D", "E", "F"]),
        "injuries": {"Scottie Barnes": "Day-To-Day"}, "notes": list(notes),
    }


def render(fn, *args):
    ui.console = Console(record=True, width=100, color_system=None)
    fn(*args)
    return ui.console.export_text()


def test_game_card_shows_picks_tip_lineup_and_notes():
    out = render(ui.game_card, card(notes=["Josh Hart is back from injury"]))
    for text in ("NY @ TOR", "7:30 PM ET", "Jalen Brunson", "16.2%", "+517", "PROJECTED",
                 "Scottie Barnes (Day-To-Day)", "Josh Hart is back from injury"):
        assert text in out, text
    assert out.index("Jalen Brunson") < out.index("RJ Barrett")      # best pick first


def test_card_headline_shows_top_two_and_the_tip_winner_by_name():
    out = render(ui.game_card, card())
    head = out[:out.index("Fair odds")]                                 # the headline sits above the table
    assert "Top 2" in head and "1. Jalen Brunson (NY) 16.2%" in head and "2. RJ Barrett (TOR) 11.9%" in head
    assert "Karl-Anthony Towns (NY) 55%  vs  TOR jumper not known yet 45%" in head   # away first; TOR unknown


def test_tip_headline_names_both_jumpers_when_known():
    c = card()
    c["jumpers"] = {"NY": "Karl-Anthony Towns", "TOR": "Jakob Poeltl"}
    c["tip"] = {"TOR": 0.62, "NY": 0.38}
    out = render(ui.game_card, c)
    assert "Karl-Anthony Towns (NY) 38%  vs  Jakob Poeltl (TOR) 62%" in out


def test_tip_line_bolds_the_favourite():
    c = card()
    c["jumpers"] = {"NY": "Karl-Anthony Towns", "TOR": "Jakob Poeltl"}
    c["tip"] = {"TOR": 0.62, "NY": 0.38}
    line = ui.tip_line(c)
    bold = [line.plain[s.start:s.end] for s in line.spans if "bold" in str(s.style)]
    assert "Jakob Poeltl (TOR)" in bold and " 62%" in bold and "Karl-Anthony Towns (NY)" not in bold


def test_every_lineup_label_renders():
    for label in ("CONFIRMED", "EXPECTED", "PROJECTED"):
        assert label in render(ui.game_card, card(label))


def test_legend_explains_projected_lineups_only_when_needed():
    assert "--watch" in render(ui.legend, True)
    assert "--watch" not in render(ui.legend, False)


def test_et_time_from_espn_utc():
    assert nba._et_time("2026-11-18T00:30Z") == "7:30 PM ET"
    assert nba._et_time("") == ""


def test_track_record_shows_totals_by_lineup_and_recent_games():
    s = {"final": {"games": 2, "hits": 1, "top3": 2},
         "by_type": {"EXPECTED": {"games": 2, "hits": 1, "top3": 2}, "PROJECTED": {"games": 1, "hits": 0, "top3": 0}},
         "by_model": {"20261116-abc123": {"games": 2, "hits": 1, "top3": 2}},
         "games": [{"game_id": "g1", "game_date": "20261117", "matchup": "NY @ TOR", "lineup_type": "EXPECTED",
                    "pick": "Jalen Brunson", "prob": 0.162, "actual": "Jalen Brunson", "hit": True}]}
    out = render(ui.track_record, s, "Data up to date")
    for text in ("TRACK RECORD", "1/2 = 50.0%", "2/2 = 100.0%", "EXPECTED", "PROJECTED", "0/1 = 0.0%",
                 "Jalen Brunson", "16.2%", "11/17"):
        assert text in out, text


def test_track_record_explains_when_nothing_finished_yet():
    empty = {"final": {"games": 0, "hits": 0, "top3": 0}, "by_type": {}, "by_model": {}, "games": []}
    assert "No finished games with a logged pick yet" in render(ui.track_record, empty)


def test_report_puts_each_game_in_its_own_box_and_shows_a_readable_date():
    body = ("NBA DAILY REPORT — 20261117\n" + "=" * 60 + "\n\nNYK @ TOR — 7:30 pm ET\n  NYK starters [Expected]\n"
            "    PG  Jalen Brunson\n  TOR starters [Expected]\n    PF  Scottie Barnes   ⚠️ Day-To-Day (Illness)\n"
            "\nPOR @ MIN — 8:00 pm ET\n  POR starters [Expected]\n    PG  Damian Lillard\n")
    out = render(ui.report, body, "Data up to date")
    assert "Tuesday, November 17, 2026" in out and "20261117" not in out
    assert out.count("╭─") == 2                     # one box per game (the header uses a different box style)
    for text in ("NYK @ TOR — 7:30 pm ET", "Jalen Brunson", "Day-To-Day", "POR @ MIN", "Damian Lillard"):
        assert text in out, text


def test_card_shows_which_team_is_expected_to_score_first():
    c = card()
    c["first_team"] = {"NY": 0.53, "TOR": 0.47}
    out = render(ui.game_card, c)
    head = out[:out.index("Fair odds")]
    assert "Scores first" in head and "NY 53%  vs  TOR 47%" in head
    c["first_team"] = {"NY": 0.40, "TOR": 0.60}                        # home favoured: still away first
    assert "NY 40%  vs  TOR 60%" in render(ui.game_card, c)
    assert "Scores first" not in render(ui.game_card, card())          # older cards without it still render


# ── FanDuel column ───────────────────────────────────────────
def test_game_card_fanduel_column_and_note():
    c = card()
    c["fanduel"] = {"Jalen Brunson": 600}          # pays more than fair +517; Barrett not offered
    c["fanduel_note"] = "– = FanDuel doesn't offer that player"
    out = render(ui.game_card, c)
    assert "FanDuel" in out and "+600" in out
    assert "doesn't offer that player" in out
    row = next(line for line in out.splitlines() if "RJ Barrett" in line and "+739" in line)   # table row
    assert row.rstrip(" │").endswith("–")


def test_game_card_without_odds_has_no_fanduel_column():
    assert "FanDuel" not in render(ui.game_card, card())       # --no-odds / FanDuel unreachable


def test_fanduel_cell_green_only_when_paying_more_than_fair():
    assert ui._fanduel_cell(600, 517).style == "bold green"
    assert ui._fanduel_cell(450, 517).style == "dim"
    assert ui._fanduel_cell(-110, -150).style == "bold green"  # -110 pays more than -150
    assert ui._fanduel_cell(None, 517).plain == "–"


def test_fanduel_card_fields():
    fd_games = [{"home_code": "TOR", "away_code": "NY", "players":
                 [{"player": n, "team": t, "american": a, "decimal": 1 + a / 100, "implied": 100 / (100 + a)}
                  for n, t, a in [("Jalen Brunson", "NY", 600), ("Josh Hart", "NY", 900), ("A1", "NY", 1000),
                                  ("A2", "NY", 1000), ("A3", "NY", 1000), ("Scottie Barnes", "TOR", 700),
                                  ("B1", "TOR", 900), ("B2", "TOR", 900), ("B3", "TOR", 900), ("B4", "TOR", 900)]]}]
    top = [("Jalen Brunson", "NY", 0.16, 517), ("RJ Barrett", "TOR", 0.12, 739)]
    out = nba._fanduel_card(fd_games, "TOR", "NY", top)
    assert out["fanduel"] == {"Jalen Brunson": 600}
    assert "doesn't offer" in out["fanduel_note"]
    assert nba._fanduel_card(fd_games, "LAL", "BOS", top)["fanduel"] == {}     # game not on FanDuel yet
    assert nba._fanduel_card(None, "TOR", "NY", top) == {}                     # odds off: no column
