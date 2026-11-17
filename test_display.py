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
                 "NY 55%", "TOR 45%", "Scottie Barnes (Day-To-Day)", "Josh Hart is back from injury"):
        assert text in out, text
    assert out.index("Jalen Brunson") < out.index("RJ Barrett")      # best pick first


def test_every_lineup_label_renders():
    for label in ("CONFIRMED", "EXPECTED", "PROJECTED"):
        assert label in render(ui.game_card, card(label))


def test_legend_explains_projected_lineups_only_when_needed():
    assert "--watch" in render(ui.legend, True)
    assert "--watch" not in render(ui.legend, False)


def test_et_time_from_espn_utc():
    assert nba._et_time("2026-11-18T00:30Z") == "7:30 PM ET"
    assert nba._et_time("") == ""
