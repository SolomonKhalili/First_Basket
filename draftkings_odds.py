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


# ──────────────────────────────────────────────
# VIEWERS → RELAY → CLOUD: browsers send the DraftKings lines they read to the Cloudflare relay (GET /dk-log);
# the cloud job pulls them each check: logs every line in dk_odds_log (next to FanDuel's odds_log, for the
# "did we beat Vegas?" checks) and writes SNAPSHOT so nba._draftkings_prices can put them on the cards.
# ──────────────────────────────────────────────
DK_LOG_SCHEMA = """CREATE TABLE IF NOT EXISTS dk_odds_log (
    game_date TEXT, matchup TEXT, logged_at TEXT, player_name TEXT, team TEXT, american INTEGER,
    PRIMARY KEY (game_date, matchup, logged_at, player_name))"""
RECENT_MIN = 45                          # a browser-read line counts as current for this long


def _row(r) -> dict:
    a = int(r["american"])
    dec = 1 + (a / 100 if a > 0 else 100 / -a)
    return {"player": r["player"], "team": r.get("team", ""), "american": a, "decimal": dec, "implied": 1 / dec}


def store_relay_log(data: dict, con, day: str, now: float, path: Path = SNAPSHOT) -> int:
    """Pure-ish (testable): relay log JSON -> rows in dk_odds_log + the snapshot file. Returns new rows logged."""
    import json
    import time as _t
    import fanduel_odds as fd
    con.execute(DK_LOG_SCHEMA)
    before = con.total_changes
    for snap in data.get("snapshots", []):
        at = _t.strftime("%Y-%m-%d %H:%M:%S", _t.localtime(snap["at"] / 1000))
        away, home = snap["key"].split("@")
        for r in snap.get("rows", []):
            con.execute("INSERT OR IGNORE INTO dk_odds_log VALUES (?, ?, ?, ?, ?, ?)",
                        (day, f"{away} @ {home}", at, r["player"], r.get("team", ""), int(r["american"])))
    con.commit()
    games = []
    for key, g in data.get("games", {}).items():
        if now - g["at"] / 1000 <= RECENT_MIN * 60:
            away, home = key.split("@")
            rows = sorted((_row(r) for r in g["rows"]), key=lambda r: r["decimal"])
            games.append({"home_code": home, "away_code": away, "players": rows, "margin": fd.margin(rows)})
    if games:
        path.write_text(json.dumps({"at": now, "games": games}))
    return con.total_changes - before


def pull_relay_log(relay_url: str, con, day: str) -> int:
    """The cloud: fetch today's DraftKings log from the relay and store it (see store_relay_log)."""
    import time as _t
    r = requests.get(f"{relay_url.rstrip('/')}/dk-log", params={"day": day}, timeout=20)
    r.raise_for_status()
    return store_relay_log(r.json(), con, day, _t.time())


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


def push_to_relay(relay_url: str, games: list) -> int:
    """Send DraftKings lines (parse_first_points output) to the relay's shared log, exactly like a viewer's browser
    does (POST /dk-log) — the Mac app does this every few minutes so DraftKings is logged all day, not only while
    someone has the page open. Returns how many games the relay saved as changed."""
    body = {"games": [{"away": g["away_code"], "home": g["home_code"],
                       "rows": [{"player": r["player"], "team": r["team"], "american": r["american"]} for r in g["players"]]}
                      for g in games if g.get("home_code") and g.get("away_code") and g.get("players")]}
    if not body["games"]:
        return 0
    r = requests.post(f"{relay_url.rstrip('/')}/dk-log", json=body, timeout=20,
                      headers={"Origin": "http://127.0.0.1:8765", "User-Agent": "first-basket-mac"})
    r.raise_for_status()
    return int(r.json().get("saved", 0))


# ──────────────────────────────────────────────
# DRAFTKINGS VIA ESPN: ESPN's public odds feed (the same feed the cloud already reads for games) republishes
# DraftKings' "First Basket" prices — current AND opening — for every game. DraftKings refuses cloud servers, ESPN
# doesn't, so the cloud gets DraftKings all day without a Mac or a browser. ESPN refreshes them every few hours
# (checked 2026-11-19: identical to DraftKings when fresh, a few hours behind otherwise); the Mac and viewers'
# browsers still add DraftKings' live prices in between.
# ──────────────────────────────────────────────
ESPN_PROPS = ("https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events/{g}/competitions/{g}"
              "/odds/100/propBets?lang=en&region=us&limit=1000&page={p}")        # provider 100 = DraftKings


def parse_espn_props(items: list, home: str, away: str, who: dict, teams: dict | None = None) -> dict:
    """Pure (testable). ESPN propBets items -> one game in parse_first_points' shape, plus each player's
    "open" price and "updated" time. who: ESPN athlete id -> (name, team)."""
    rows, team_first, team_open = [], {}, {}
    for i in items:
        kind = (i.get("type") or {}).get("name")
        if kind == "First Team to Score":                  # team market: {team code: price}, with the opening price
            tid = str((i.get("team") or {}).get("$ref", "")).split("/teams/")[-1].split("?")[0]
            code = (teams or {}).get(tid)
            am = (i.get("odds") or {}).get("american") or {}
            try:
                if code:
                    team_first[code] = int(str(am.get("value", "")).replace("+", ""))
                    team_open[code] = int(str(am.get("open", am.get("value", ""))).replace("+", ""))
            except ValueError:
                pass
            continue
        if kind != "First Basket":
            continue
        aid = str((i.get("athlete") or {}).get("$ref", "")).split("/athletes/")[-1].split("?")[0]
        name, team = who.get(aid, ("", ""))
        am = (i.get("odds") or {}).get("american") or {}
        try:
            american = int(str(am.get("value", "")).replace("+", ""))
        except ValueError:
            continue
        if not name or not (american >= 100 or american <= -100):
            continue
        try:
            opened = int(str(am.get("open", "")).replace("+", ""))
        except ValueError:
            opened = american
        decimal = 1 + (american / 100 if american > 0 else 100 / -american)
        rows.append({"player": name, "team": team, "american": american, "decimal": decimal, "implied": 1 / decimal,
                     "open": opened, "updated": i.get("lastUpdated", "")})
    import fanduel_odds as fd
    rows.sort(key=lambda r: r["decimal"])
    return {"home_code": home, "away_code": away, "players": rows, "margin": fd.margin(rows) if rows else None,
            "source": "ESPN", "team_first": team_first, "team_first_open": team_open}


def fetch_via_espn(games: list, con) -> list:
    """DraftKings' first-basket prices for these games (each {game_id, home_team, away_team}) from ESPN's feed."""
    who = {}
    for pid, name, team in con.execute("SELECT player_id, player_name, team FROM lineups ORDER BY rowid"):
        who[str(pid)] = (name, team)                        # latest team wins (trades)
    try:                                                    # ESPN team id -> code (e.g. "9" -> "GS")
        sports = requests.get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams", timeout=20).json()
        teams = {str(x["team"]["id"]): x["team"]["abbreviation"]
                 for x in sports["sports"][0]["leagues"][0]["teams"]}
    except Exception:  # noqa: BLE001
        teams = {}
    out = []
    for g in games:
        items, page = [], 1
        while page <= 5:
            r = requests.get(ESPN_PROPS.format(g=g["game_id"], p=page), timeout=30)
            if not r.ok:
                break
            data = r.json()
            items += data.get("items", [])
            if page >= int(data.get("pageCount") or 1):
                break
            page += 1
        game = parse_espn_props(items, g["home_team"], g["away_team"], who, teams)
        if game["players"] or game["team_first"]:
            out.append(game)
    return out


def log_espn(con, day: str, games: list) -> int:
    """Save ESPN's DraftKings prices in dk_odds_log (same table as the browsers' log): the official opening price
    (stamped 00:00:00 = "opening") and each price at the time ESPN says it changed. Returns rows added."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    con.execute(DK_LOG_SCHEMA)
    before = con.total_changes
    d0 = f"{day[:4]}-{day[4:6]}-{day[6:]} 00:00:00"
    for g in games:
        m = f"{g['away_code']} @ {g['home_code']}"
        for r in g["players"]:
            con.execute("INSERT OR IGNORE INTO dk_odds_log VALUES (?, ?, ?, ?, ?, ?)",
                        (day, m, d0, r["player"], r["team"], int(r["open"])))
            now = datetime.now(ZoneInfo("America/New_York"))
            try:                               # ESPN's "updated" time — but never later than now (ESPN sometimes
                at = min(datetime.fromisoformat(r["updated"].replace("Z", "+00:00")).astimezone(now.tzinfo), now)
            except ValueError:                 # stamps a time hours ahead), and now if it gives none
                at = now
            at = at.strftime("%Y-%m-%d %H:%M:%S")
            last = con.execute("SELECT american, logged_at FROM dk_odds_log WHERE game_date = ? AND matchup = ? AND player_name = ? "
                               "ORDER BY logged_at DESC LIMIT 1", (day, m, r["player"])).fetchone()
            # ESPN is a few minutes behind DraftKings: if a newer live price (Mac / browsers) is already logged, ESPN's
            # older one is not a change — logging it made prices look like they flipped back and forth
            if not last or (last[0] != r["american"] and at > str(last[1])):
                con.execute("INSERT OR IGNORE INTO dk_odds_log VALUES (?, ?, ?, ?, ?, ?)",
                            (day, m, at, r["player"], r["team"], r["american"]))
        for team, a in (g.get("team_first") or {}).items():             # team to score first, same idea
            log_team_first(con, day, m, "DraftKings", team, a, opened=(g.get("team_first_open") or {}).get(team), at=None)
    con.commit()
    return con.total_changes - before


TEAM_FIRST_SCHEMA = """CREATE TABLE IF NOT EXISTS team_first_log (
    game_date TEXT, matchup TEXT, logged_at TEXT, book TEXT, team TEXT, american INTEGER,
    PRIMARY KEY (game_date, matchup, logged_at, book, team))"""


def log_team_first(con, day: str, matchup: str, book: str, team: str, american: int, opened=None, at=None) -> None:
    """Team to score first price log (both books): the opening price (stamped 00:00:00, if the book gives it) and
    every change after that. at: New York "YYYY-MM-DD HH:MM:SS" (default now)."""
    import time as _t
    con.execute(TEAM_FIRST_SCHEMA)
    if opened is not None:
        con.execute("INSERT OR IGNORE INTO team_first_log VALUES (?, ?, ?, ?, ?, ?)",
                    (day, matchup, f"{day[:4]}-{day[4:6]}-{day[6:]} 00:00:00", book, team, int(opened)))
    last = con.execute("SELECT american FROM team_first_log WHERE game_date = ? AND matchup = ? AND book = ? AND team = ? "
                       "ORDER BY logged_at DESC LIMIT 1", (day, matchup, book, team)).fetchone()
    if not last or last[0] != int(american):
        con.execute("INSERT OR IGNORE INTO team_first_log VALUES (?, ?, ?, ?, ?, ?)",
                    (day, matchup, at or _t.strftime("%Y-%m-%d %H:%M:%S"), book, team, int(american)))


def backfill_history(con, limit: int = 5000) -> dict:
    """DraftKings' opening and closing first-basket (and team) prices for finished games, from ESPN's feed — it keeps
    them for every game this season (and some of last season). Stamped "00:00:00" (= opening) and "23:59:59"
    (= closing, the last price before tip-off). Each game is asked once (table dk_history); `limit` games per run so
    the live job isn't held up. -> {"games": done this run, "with_odds": ..., "left": ...}"""
    con.execute(DK_LOG_SCHEMA)
    con.execute("CREATE TABLE IF NOT EXISTS dk_history (game_id TEXT PRIMARY KEY, players INTEGER)")
    todo = con.execute("""SELECT g.game_id, g.game_date, g.home_team, g.away_team FROM games g
                          JOIN first_baskets f USING(game_id)
                          WHERE g.game_id NOT IN (SELECT game_id FROM dk_history) ORDER BY g.game_date DESC""").fetchall()
    done = found = 0
    for gid, day, home, away in todo[:limit]:
        try:
            got = fetch_via_espn([{"game_id": gid, "home_team": home, "away_team": away}], con)
        except Exception:  # noqa: BLE001 — ESPN busy: try this game next run
            continue
        g = got[0] if got else {"players": [], "team_first": {}}
        m, d0, d1 = f"{away} @ {home}", f"{day[:4]}-{day[4:6]}-{day[6:]} 00:00:00", f"{day[:4]}-{day[4:6]}-{day[6:]} 23:59:59"
        for r in g["players"]:
            con.execute("INSERT OR IGNORE INTO dk_odds_log VALUES (?, ?, ?, ?, ?, ?)", (day, m, d0, r["player"], r["team"], int(r["open"])))
            con.execute("INSERT OR IGNORE INTO dk_odds_log VALUES (?, ?, ?, ?, ?, ?)", (day, m, d1, r["player"], r["team"], int(r["american"])))
        for team, a in (g.get("team_first") or {}).items():
            con.execute(TEAM_FIRST_SCHEMA)
            opened = (g.get("team_first_open") or {}).get(team, a)
            con.execute("INSERT OR IGNORE INTO team_first_log VALUES (?, ?, ?, ?, ?, ?)", (day, m, d0, "DraftKings", team, int(opened)))
            con.execute("INSERT OR IGNORE INTO team_first_log VALUES (?, ?, ?, ?, ?, ?)", (day, m, d1, "DraftKings", team, int(a)))
        con.execute("INSERT OR REPLACE INTO dk_history VALUES (?, ?)", (gid, len(g["players"])))
        con.commit()
        done += 1
        found += bool(g["players"])
    return {"games": done, "with_odds": found, "left": max(0, len(todo) - done)}
