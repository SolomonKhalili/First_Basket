"""
FanDuel "First Basket" odds for today's NBA games.

Reads FanDuel's public sportsbook feed (the same JSON its website loads), with a plain request:
  1. the NBA page        -> today's games and their FanDuel event ids
  2. each game's page    -> the "First Basket" market: player, team, price

Nothing is logged in and no bets are placed; this only reads prices.

    python3 fanduel_odds.py                 # every game today
    python3 fanduel_odds.py heat bucks      # only games with these teams
    python3 fanduel_odds.py --json          # machine-readable output

Team codes are converted to the ESPN codes used everywhere else in the project (GS, NY, UTAH, ...).
"""

import json
import os
import sys
import time

import requests

STATE = os.environ.get("FANDUEL_STATE", "ny")      # FanDuel serves each state from its own host
API = f"https://sbapi.{STATE}.sportsbook.fanduel.com/api"
APP_KEY = "FhMFpcPWXMeyZxOx"                         # public key the FanDuel website sends with every request
HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
# FanDuel's small live-price feed (what its own site polls): POST {"marketIds": [...]} -> each runner's current price.
# ~7 KB per game instead of a 680 KB game page; players are identified by selection id, not name.
PRICES_URL = f"https://smp.{STATE}.sportsbook.fanduel.com/api/sports/fixedodds/readonly/v1/getMarketPrices"
SLEEP = 0.5                                          # pause between game pages

# FanDuel abbreviation -> ESPN code (only the ones that differ)
FD_TO_ESPN = {"GSW": "GS", "NYK": "NY", "SAS": "SA", "UTA": "UTAH", "NOP": "NO", "WAS": "WSH",
              "BRK": "BKN", "PHO": "PHX"}


def espn_code(fd_abbr: str) -> str:
    a = (fd_abbr or "").upper()
    return FD_TO_ESPN.get(a, a)


def _get(path: str, **params) -> dict:
    r = requests.get(f"{API}/{path}", params={"_ak": APP_KEY, **params}, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


# ──────────────────────────────────────────────
# PARSING (pure functions, tested in test_fanduel_odds.py)
# ──────────────────────────────────────────────
def parse_games(nba_page: dict) -> list[dict]:
    """FanDuel NBA page -> [{event_id, away, home, start_utc}] for real games (not futures), by start time."""
    games = []
    for event_id, ev in nba_page.get("attachments", {}).get("events", {}).items():
        name = ev.get("name", "")
        if " @ " not in name:
            continue
        away, home = (s.strip() for s in name.split(" @ ", 1))
        games.append({"event_id": str(event_id), "away": away, "home": home, "start_utc": ev.get("openDate", "")})
    return sorted(games, key=lambda g: (g["start_utc"], g["event_id"]))


def _is_first_basket(m: dict) -> bool:
    return m.get("marketName", "").strip().lower() == "first basket" or \
        m.get("marketType", "").upper().startswith("FIRST_BASKET")


def parse_first_basket(event_page: dict) -> list[dict]:
    """A game's page -> one row per player offered in the First Basket market, shortest price first.

    `implied` is 1 / decimal odds (includes FanDuel's margin); `no_vig` rescales those so the listed
    players add up to 100%. [] when the market is not offered (yet) or is closed."""
    markets = [m for m in event_page.get("attachments", {}).get("markets", {}).values() if _is_first_basket(m)]
    if not markets or markets[0].get("marketStatus", "OPEN") != "OPEN":
        return []
    rows = []
    for r in markets[0].get("runners", []):
        if r.get("runnerStatus", "ACTIVE") != "ACTIVE":
            continue
        odds = r.get("winRunnerOdds", {})
        american = odds.get("americanDisplayOdds", {}).get("americanOddsInt")
        decimal = odds.get("trueOdds", {}).get("decimalOdds", {}).get("decimalOdds")
        if not decimal or american is None:
            continue
        rows.append({"player": r.get("runnerName", "").strip(), "team": espn_code(r.get("teamAbbr")),
                     "american": int(american), "decimal": float(decimal), "implied": 1 / float(decimal),
                     # FanDuel's ids: the page re-reads just the prices by these (PRICES_URL), every ~30 s
                     "market_id": str(markets[0].get("marketId", "")), "selection_id": str(r.get("selectionId", ""))})
    total = sum(r["implied"] for r in rows)
    full = is_complete(rows)
    for r in rows:
        r["no_vig"] = r["implied"] / total if full else None
    return sorted(rows, key=lambda r: r["decimal"])


def is_complete(rows: list[dict]) -> bool:
    """True when FanDuel lists 5+ players for both teams. Until then (e.g. one side's lineup not posted)
    the list is partial and rescaling it to 100% would overstate everyone on it."""
    per_team = {}
    for r in rows:
        per_team[r["team"]] = per_team.get(r["team"], 0) + 1
    return len(per_team) == 2 and min(per_team.values()) >= 5


def margin(rows: list[dict]):
    """FanDuel's margin on the listed players: sum of implied chances minus 100% (0.15 = 15%).
    None when the list is partial (see is_complete)."""
    return sum(r["implied"] for r in rows) - 1 if is_complete(rows) else None


# ──────────────────────────────────────────────
# FETCHING
# ──────────────────────────────────────────────
def fetch_games() -> list[dict]:
    return parse_games(_get("content-managed-page", page="CUSTOM", customPageId="nba",
                            pbHorizontal="false", timezone="America/New_York"))


def fetch_first_basket(event_id: str) -> list[dict]:
    # The First Basket market sits on the game's "Popular" tab.
    return parse_first_basket(_get("event-page", eventId=event_id, tab="popular"))


def fetch_first_basket_odds(teams=frozenset()) -> list[dict]:
    """Today's games with their First Basket prices. `teams`: ESPN codes to keep (empty = every game)."""
    out = []
    for g in fetch_games():
        g["home_code"], g["away_code"] = team_code(g["home"]), team_code(g["away"])
        if teams and not ({g["home_code"], g["away_code"]} & set(teams)):
            continue
        players = fetch_first_basket(g["event_id"])
        out.append({**g, "players": players, "margin": margin(players)})
        time.sleep(SLEEP)
    return out


def team_code(full_name: str) -> str:
    """'Golden State Warriors' -> 'GS', using the nickname map in nba.py. '' if not recognised."""
    from nba import TEAM_NAMES
    low = full_name.lower()
    return next((c for name, c in TEAM_NAMES.items() if low.endswith(name)), "")


def game_for(fd_games: list[dict], home: str, away: str):
    """The FanDuel game between these two ESPN team codes, or None."""
    return next((g for g in fd_games if g.get("home_code") == home and g.get("away_code") == away), None)


def match_prices(fd_players: list[dict], picks: list[tuple]) -> dict:
    """Model picks [(name, team, ...)] -> {name: FanDuel row} for the players FanDuel prices.

    Names are compared with starters.norm_name ('P.J. Washington' == 'PJ Washington', accents ignored);
    if that fails, a unique last-name match on the same team is accepted ('Herb Jones' / 'Herbert Jones')."""
    from starters import norm_name
    by_name = {norm_name(p["player"]): p for p in fd_players}
    out = {}
    for name, team, *_ in picks:
        row = by_name.get(norm_name(name))
        if row is None:
            last = norm_name(name).split()[-1:] or [""]
            same = [p for p in fd_players if p["team"] == team and norm_name(p["player"]).split()[-1:] == last]
            row = same[0] if len(same) == 1 else None
        if row is not None:
            out[name] = row
    return out


# ──────────────────────────────────────────────
# OUTPUT
# ──────────────────────────────────────────────
def _fmt_american(a: int) -> str:
    return f"+{a}" if a > 0 else str(a)


def print_games(games: list[dict]) -> None:
    if not games:
        print("No NBA games on FanDuel right now.")
        return
    for g in games:
        print(f"\n{g['away']} @ {g['home']}   (FanDuel event {g['event_id']}, {g['start_utc']})")
        if not g["players"]:
            print("  First Basket market not offered yet.")
            continue
        print(f"  {'Player':<26}{'Team':<6}{'Odds':>7}{'Implied':>9}{'No-vig':>8}")
        for p in g["players"]:
            no_vig = f"{p['no_vig']:.1%}" if p["no_vig"] is not None else "–"
            print(f"  {p['player']:<26}{p['team']:<6}{_fmt_american(p['american']):>7}"
                  f"{p['implied']:>9.1%}{no_vig:>8}")
        if g["margin"] is None:
            print(f"  Partial list ({len(g['players'])} players): FanDuel hasn't posted both lineups yet.")
        else:
            print(f"  FanDuel margin on these {len(g['players'])} players: {g['margin']:.1%}")


def main(argv) -> int:
    as_json = "--json" in argv
    args = [a for a in argv if a != "--json"]
    teams = set()
    if args:
        from nba import parse_teams
        teams = parse_teams(args)
        if not teams:
            print(f"Didn't recognise a team in: {' '.join(args)}")
            return 1
    try:
        games = fetch_first_basket_odds(teams)
    except requests.RequestException as e:
        print(f"FanDuel request failed: {e}")
        return 1
    if as_json:
        print(json.dumps(games, indent=2))
    else:
        print_games(games)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


def fetch_prices(market_ids) -> list:
    """Current prices for these markets (FanDuel's live-price feed). [] for no ids."""
    ids = [str(m) for m in market_ids if m][:20]
    if not ids:
        return []
    r = requests.post(PRICES_URL, json={"marketIds": ids}, headers={**HEADERS, "Content-Type": "application/json"},
                      timeout=15)
    r.raise_for_status()
    return r.json()
