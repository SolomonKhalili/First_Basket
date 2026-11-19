"""
DraftKings first-basket odds for today's NBA games (the owner chose to add DraftKings, 2026-11-18).

Reads the same public JSON DraftKings' own website loads (found by recording the site, the reverse-api-engineer
way), with ordinary browser-style request headers — no login, no cookies, nothing bet. DraftKings can change or
block this at any time; the app then simply shows FanDuel only.

DraftKings calls the market "1st Points Scorer" (subcategory 16305): the game's first points, free throws
included — the same rule as FanDuel's "First Basket" and our model, so the prices compare like for like.

    python3 draftkings_odds.py               # today's prices
"""
from urllib.parse import quote

import requests

SITE = "US-NY-SB"
NBA = "42648"
FIRST_POINTS = "16305"
LEAGUE = f"https://sportsbook-nash.draftkings.com/sites/{SITE}/api/sportscontent/controldata/league/leagueSubcategory/v1/markets"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
           "Accept": "application/json", "Referer": "https://sportsbook.draftkings.com/"}


def league_markets(subcategory: str) -> dict:
    """One market subcategory for every NBA game (events, markets, selections) — one request."""
    events = quote(f"$filter=leagueId eq '{NBA}' AND clientMetadata/Subcategories/any(s: s/Id eq '{subcategory}')")
    markets = quote(f"$filter=clientMetadata/subCategoryId eq '{subcategory}' AND tags/all(t: t ne 'SportcastBetBuilder')")
    url = (f"{LEAGUE}?isBatchable=false&templateVars={NBA}&eventsQuery={events}&marketsQuery={markets}"
           f"&include=Events&entity=events")
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()


def _american(text) -> int:
    """'+454' / '−166' (DraftKings uses a real minus sign) -> 454 / -166."""
    return int(str(text).replace("−", "-").replace("+", "").strip())


def team_code(participant: dict) -> str:
    """A DraftKings team -> ESPN code, by nickname ('Nuggets' -> 'DEN'); '' if not recognised."""
    from nba import TEAM_NAMES
    nick = ((participant.get("metadata") or {}).get("rosettaTeamName") or participant.get("name") or "").lower()
    return next((c for name, c in TEAM_NAMES.items() if nick.endswith(name)), "")


def parse_first_points(data: dict) -> list:
    """Pure (testable): DraftKings' league response for FIRST_POINTS -> one entry per game, the same shape as
    fanduel_odds.fetch_first_basket_odds: {event_id, home_code, away_code, players: [{player, team, american,
    decimal, implied}], margin}."""
    import fanduel_odds as fd
    teams = {}
    for e in data.get("events", []):
        sides = {p.get("venueRole"): team_code(p) for p in e.get("participants", []) if p.get("type") == "Team"}
        teams[str(e.get("id"))] = (sides.get("Home", ""), sides.get("Away", ""))
    by_market = {}
    for s in data.get("selections", []):
        by_market.setdefault(str(s.get("marketId")), []).append(s)
    out = []
    for m in data.get("markets", []):
        home, away = teams.get(str(m.get("eventId")), ("", ""))
        rows = []
        for s in by_market.get(str(m.get("id")), []):
            try:
                american = _american((s.get("displayOdds") or {}).get("american"))
                decimal = float(s.get("trueOdds") or 0)
            except (TypeError, ValueError):
                continue
            if decimal <= 1:
                continue
            role = ((s.get("participants") or [{}])[0]).get("venueRole", "")
            rows.append({"player": str(s.get("label", "")).strip(), "team": home if role == "HomePlayer" else away,
                         "american": american, "decimal": decimal, "implied": 1 / decimal})
        rows.sort(key=lambda r: r["decimal"])
        out.append({"event_id": str(m.get("eventId")), "home_code": home, "away_code": away,
                    "players": rows, "margin": fd.margin(rows)})
    return out


def fetch_first_basket_odds() -> list:
    """Today's DraftKings first-points prices for every NBA game that still has the market open."""
    return parse_first_points(league_markets(FIRST_POINTS))


if __name__ == "__main__":
    for g in fetch_first_basket_odds():
        print(f"{g['away_code']} @ {g['home_code']}  ({len(g['players'])} players)")
        for r in g["players"]:
            print(f"   {r['american']:+6d}  {r['player']} ({r['team']})")
