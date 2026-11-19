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


# ──────────────────────────────────────────────
# MAC → CLOUD: DraftKings answers home connections but refuses cloud servers (GitHub's job gets an error).
# While the Mac is on, it reads the prices every 3 minutes (launcher/com.firstbasket.dk.plist) and puts them on the
# private repo's "dk-prices" branch; the cloud job uses them when they're under 10 minutes old.
# ──────────────────────────────────────────────
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent
SNAPSHOT = HERE / "dk_prices.json"
BRANCH = "dk-prices"
MAX_AGE_MIN = 10


def load_snapshot(path: Path = SNAPSHOT, max_age_min: float = MAX_AGE_MIN):
    """(games, minutes old) from the Mac's latest prices, or (None, None) if missing or too old."""
    import json
    import time
    try:
        snap = json.loads(path.read_text())
        age = (time.time() - float(snap["at"])) / 60
    except (OSError, ValueError, KeyError, TypeError):
        return None, None
    return (snap.get("games"), age) if 0 <= age <= max_age_min else (None, None)


def push_snapshot() -> str:
    """Mac only: read DraftKings now and replace the "dk-prices" branch with the result (one file, no history)."""
    import json
    import shutil
    import subprocess
    import tempfile
    import time
    games = fetch_first_basket_odds()
    folder = Path(tempfile.mkdtemp(prefix="dk-"))
    try:
        (folder / SNAPSHOT.name).write_text(json.dumps({"at": time.time(), "games": games}))
        remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=HERE, capture_output=True, text=True,
                                check=True).stdout.strip()
        who = ["-c", "user.name=First Basket bot", "-c", "user.email=bot@users.noreply.github.com"]
        for args in (["init", "-q", "-b", BRANCH], [*who, "add", "-A"], [*who, "commit", "-q", "-m", "DraftKings prices"],
                     ["push", "-q", "--force", remote, f"HEAD:{BRANCH}"]):
            subprocess.run(["git", *args], cwd=folder, capture_output=True, check=True, timeout=120)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return f"{sum(len(g['players']) for g in games)} prices for {len(games)} games"


if __name__ == "__main__":
    import sys
    if "--push" in sys.argv:
        import time
        try:
            print(time.strftime("%Y-%m-%d %H:%M:%S"), "pushed", push_snapshot(), flush=True)
        except Exception as e:  # noqa: BLE001 — offline or DraftKings down: try again in 3 minutes
            print(time.strftime("%Y-%m-%d %H:%M:%S"), "couldn't push:", type(e).__name__, str(e)[:200], flush=True)
        sys.exit(0)
    for g in fetch_first_basket_odds():
        print(f"{g['away_code']} @ {g['home_code']}  ({len(g['players'])} players)")
        for r in g["players"]:
            print(f"   {r['american']:+6d}  {r['player']} ({r['team']})")
