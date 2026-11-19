#!/usr/bin/env python3
"""
Starting-lineup resolution for the NBA first-basket bot.

Sources, in order of trust:
  1. ESPN-confirmed starters  (only when ESPN flags exactly 5 starters per team)
  2. Projected starters from the local `lineups` table (recent actual starts,
     minus players ruled out; "Day-to-Day" players are KEPT but flagged uncertain)

All dates are 'YYYYMMDD' strings, matching games.game_date in the DB.
"""

import ast
import sqlite3
import time
from collections import defaultdict
from typing import Optional

SLEEP = 0.3

# Statuses that mean the player will not play. Day-to-Day / Questionable are
# NOT out: they often start, so they are kept and flagged instead.


def severity(status: str) -> int:
    """0 = out, 1 = doubtful, 2 = questionable, 3 = day-to-day/probable, 9 = unknown/healthy.
    Case-insensitive and tolerant of variants ('Out For Season', 'out', 'Game Time Decision')."""
    k = (status or "").strip().lower()
    if not k:
        return 9
    if k.startswith("out") or k in ("injured reserve", "suspension", "suspended", "inactive", "ir"):
        return 0
    if k.startswith("doubtful"):
        return 1
    if k.startswith("questionable") or "game time" in k or "gtd" in k:
        return 2
    if k.startswith("day") or k.startswith("probable"):
        return 3
    return 9


# ──────────────────────────────────────────────
# DATES
# ──────────────────────────────────────────────
def yyyymmdd(d) -> str:
    """'2026-11-07', '20261107', or a date object -> '20261107'."""
    return str(d).replace("-", "")[:8]


# ──────────────────────────────────────────────
# SCHEMA
# ──────────────────────────────────────────────
def ensure_lineups_table(con):
    con.execute(
        """CREATE TABLE IF NOT EXISTS lineups (
            game_id TEXT, team TEXT, player_id TEXT, player_name TEXT,
            is_starter INTEGER, minutes REAL,
            PRIMARY KEY (game_id, team, player_id))"""
    )
    con.commit()


# ──────────────────────────────────────────────
# WRITE: box score -> lineups
# ──────────────────────────────────────────────
def _minutes(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def fetch_boxscore_lineups(game_id: str) -> list[tuple]:
    """Rows (game_id, team, player_id, name, is_starter, minutes) for players who played."""
    from sportsdataverse.nba import espn_nba_cdn_boxscore

    bs = espn_nba_cdn_boxscore(game_id=int(game_id), return_parsed=True)
    if not bs or "boxscore_player" not in bs:
        return []
    rows = []
    for r in bs["boxscore_player"].iter_rows(named=True):
        pid = str(r.get("athlete_id", "") or "")
        team = r.get("team_abbreviation")
        if not pid or not team:
            continue
        mins = _minutes(r.get("minutes"))
        starter = 1 if r.get("starter") else 0
        if mins == 0 and not starter:
            continue  # DNP
        rows.append((str(game_id), team, pid, r.get("athlete_display_name", ""), starter, mins))
    return rows


def save_lineups(con, rows: list[tuple]):
    con.executemany(
        "INSERT OR REPLACE INTO lineups "
        "(game_id, team, player_id, player_name, is_starter, minutes) VALUES (?,?,?,?,?,?)",
        rows,
    )
    con.commit()


def store_game_lineup(con, game_id: str) -> int:
    """Fetch + store one game's lineup. Never raises (ingest must not break on this)."""
    try:
        ensure_lineups_table(con)
        rows = fetch_boxscore_lineups(game_id)
        if rows:
            save_lineups(con, rows)
        return len(rows)
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️ lineup fetch failed for {game_id}: {e}")
        return 0


def backfill_lineups(con, sleep: float = SLEEP) -> int:
    """Fill lineups for every game that has none. Resumable (skips games already stored)."""
    ensure_lineups_table(con)
    todo = con.execute(
        """SELECT game_id, game_date FROM games
           WHERE game_id NOT IN (SELECT DISTINCT game_id FROM lineups)
           ORDER BY game_date"""
    ).fetchall()
    print(f"Backfilling lineups for {len(todo)} games...")
    done = 0
    for i, (gid, gdate) in enumerate(todo):
        time.sleep(sleep)
        if store_game_lineup(con, gid):
            done += 1
        if i % 50 == 0:
            print(f"  [{i}/{len(todo)}] {gdate} — {done} stored")
    print(f"✅ Lineups stored for {done}/{len(todo)} games")
    return done


# ──────────────────────────────────────────────
# INJURIES
# ──────────────────────────────────────────────


# ──────────────────────────────────────────────
# READ: projected starters from stored lineups
# ──────────────────────────────────────────────


# ──────────────────────────────────────────────
# READ: ESPN-confirmed starters
# ──────────────────────────────────────────────


# ──────────────────────────────────────────────
# RESOLVE
# ──────────────────────────────────────────────


# ──────────────────────────────────────────────
# NBA.COM OFFICIAL STARTERS (primary pre-game source)
# ──────────────────────────────────────────────
# Teams must submit starting lineups ~30 min before tip; NBA.com's live box score
# exposes them as players[].starter == "1". NBA tricodes differ from ESPN's.
ESPN_TO_NBA = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS"}
NBA_SCOREBOARD = "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json"
NBA_BOXSCORE = "https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{gid}.json"
NBA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.nba.com/",
    "Accept": "application/json",
}
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def _nba_json(url: str):
    import requests

    r = requests.get(url, headers=NBA_HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def norm_name(name: str) -> str:
    """'Nikola Jokić' / 'Wendell Carter Jr.' -> comparable lowercase ascii key."""
    import re
    import unicodedata

    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z\s]", " ", s)
    s = " ".join(w for w in s.split() if w not in _SUFFIXES)
    # join runs of single-letter initials: 'p j washington' -> 'pj washington' (matches 'PJ Washington')
    return re.sub(r"\b[a-z](?: [a-z]\b)+", lambda m: m.group().replace(" ", ""), s)


def espn_ids_by_name(con) -> dict:
    """normalized player name -> ESPN athlete id, from the stored lineups."""
    try:
        return {norm_name(n): pid for pid, n in
                con.execute("SELECT player_id, player_name FROM lineups ORDER BY rowid")}
    except sqlite3.Error:
        return {}


def parse_nba_starters(box: dict, home: str, away: str, id_map: dict) -> Optional[dict]:
    """Pure function (testable): NBA boxscore 'game' dict -> {abbr: [5 starters]} or None."""
    out = {}
    for side, abbr in (("homeTeam", home), ("awayTeam", away)):
        flagged = [p for p in box.get(side, {}).get("players", []) if str(p.get("starter")) == "1"]
        if len(flagged) != 5:
            return None  # lineup not posted yet
        players = []
        for p in flagged:
            name = p.get("name") or f"{p.get('firstName','')} {p.get('familyName','')}".strip()
            pid = id_map.get(norm_name(name))
            players.append({
                "player_id": pid or f"nba{p.get('personId')}",
                "name": name, "position": p.get("position", ""), "jersey": str(p.get("jerseyNum", "") or ""),
                "team": abbr, "uncertain": False, "id_matched": pid is not None,
            })
        out[abbr] = players
    return out


_SB_CACHE: dict = {}    # "games" -> today's scoreboard games, or None if NBA.com refused (per run; cleared with _FEED_CACHE)


def _scoreboard_games():
    """Today's NBA.com scoreboard, fetched once per run (one retry). A failure is reported ONCE, not once per game."""
    if "games" not in _SB_CACHE:
        import time
        games, err = None, None
        for attempt in range(2):
            try:
                games = _nba_json(NBA_SCOREBOARD)["scoreboard"]["games"]
                break
            except Exception as e:  # noqa: BLE001
                err = e
                if attempt == 0:
                    time.sleep(2)
        if games is None:
            code = getattr(getattr(err, "response", None), "status_code", None)
            print(f"  (NBA.com scoreboard unavailable right now{f' ({code})' if code else ''} — "
                  "using NBA.com's daily lineups and our projection instead)")
        _SB_CACHE["games"] = games
    return _SB_CACHE["games"]


def _daily_game_id(h: str, a: str) -> Optional[str]:
    """The game's NBA.com id from the daily lineups feed already downloaded this run (no extra request)."""
    for feed in _FEED_CACHE.values():
        for g in (feed or {}).get("games", []):
            if (g.get("homeTeam", {}).get("teamAbbreviation") == h
                    and g.get("awayTeam", {}).get("teamAbbreviation") == a and g.get("gameId")):
                return g["gameId"]
    return None


def nba_confirmed_starters(home: str, away: str, con) -> Optional[dict]:
    """{home: [5], away: [5]} from NBA.com once lineups are posted, else None."""
    try:
        h, a = ESPN_TO_NBA.get(home, home), ESPN_TO_NBA.get(away, away)
        gid = _daily_game_id(h, a)            # usually known already; the scoreboard is only the backup
        if not gid:
            games = _scoreboard_games() or []
            gid = next((g["gameId"] for g in games
                        if g["homeTeam"]["teamTricode"] == h and g["awayTeam"]["teamTricode"] == a), None)
        if not gid:
            return None
        try:
            box = _nba_json(NBA_BOXSCORE.format(gid=gid))["game"]
        except Exception as e:  # noqa: BLE001
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (403, 404):  # file not created yet = lineup not submitted
                return None
            raise
        return parse_nba_starters(box, home, away, espn_ids_by_name(con))
    except Exception as e:  # noqa: BLE001
        print(f"  (NBA.com lineup fetch failed: {e})")
        return None


# ──────────────────────────────────────────────
# NBA.COM DAILY LINEUPS FEED (primary source; has Expected vs Confirmed status)
# ──────────────────────────────────────────────
NBA_DAILY = "https://stats.nba.com/js/data/leaders/00_daily_lineups_{d}.json"


def parse_daily_game(game: dict, home: str, away: str, id_map: dict) -> Optional[dict]:
    """
    Pure function (testable). `game` is one entry of the feed's 'games' list.
    -> {'teams': {abbr: [5 players]}, 'confirmed': bool, 'statuses': [..]} or None
    Starters are the players with a position tag (bench players have none).
    """
    teams, statuses = {}, set()
    for side, abbr in (("homeTeam", home), ("awayTeam", away)):
        players = [p for p in game.get(side, {}).get("players", [])
                   if p.get("position") and p.get("rosterStatus", "Active") == "Active"]
        if len(players) != 5:
            return None
        out = []
        for p in players:
            statuses.add(str(p.get("lineupStatus", "")))
            name = p.get("playerName") or f"{p.get('firstName','')} {p.get('lastName','')}".strip()
            pid = id_map.get(norm_name(name))
            out.append({
                "player_id": pid or f"nba{p.get('personId')}", "name": name,
                "position": p.get("position", ""), "jersey": "", "team": abbr,
                "uncertain": False, "id_matched": pid is not None,
                "status": str(p.get("lineupStatus", "")),
            })
        teams[abbr] = out
    confirmed = bool(statuses) and all(s.strip().lower() == "confirmed" for s in statuses)
    return {"teams": teams, "confirmed": confirmed, "statuses": sorted(statuses)}


_FEED_CACHE: dict = {}   # date -> feed dict, or None if not published yet (per run)


def _daily_feed(d: str):
    """Fetch the daily lineups feed once per date per run. None = not published / unavailable."""
    if d in _FEED_CACHE:
        return _FEED_CACHE[d]
    feed = None
    try:
        feed = _nba_json(NBA_DAILY.format(d=d))
    except Exception as e:  # noqa: BLE001
        code = getattr(getattr(e, "response", None), "status_code", None)
        if code == 404:
            pass   # not published yet: normal; each game reports its own lineup status
        else:
            print(f"  (NBA.com daily lineups fetch failed for {d}: {e})")
    _FEED_CACHE[d] = feed
    return feed


OFFICIAL_SCHEMA = """CREATE TABLE IF NOT EXISTS official_lineups (
    game_date TEXT, team TEXT, player_id TEXT, player_name TEXT, position TEXT, status TEXT,
    PRIMARY KEY (game_date, team, player_name))"""
DAYS_SCHEMA = "CREATE TABLE IF NOT EXISTS official_lineup_days (game_date TEXT PRIMARY KEY, players INTEGER)"
# Every lineup NBA.com showed us, kept per status: the Expected list (posted earlier in the day) and the Confirmed
# list (posted near tip-off) are stored separately, so a later Confirmed list never erases the Expected one.
SNAPSHOT_SCHEMA = """CREATE TABLE IF NOT EXISTS lineup_snapshots (
    game_date TEXT, team TEXT, status TEXT, player_id TEXT, player_name TEXT, position TEXT, seen_at TEXT,
    PRIMARY KEY (game_date, team, status, player_name))"""


def ensure_official_tables(con):
    con.execute(OFFICIAL_SCHEMA)
    con.execute(DAYS_SCHEMA)
    con.execute(SNAPSHOT_SCHEMA)


def store_lineup_snapshot(con, d: str, feed) -> int:
    """Keep the starters NBA.com lists for day `d` (YYYYMMDD), separately for each status (Expected / Confirmed).
    For each team and status the latest list replaces the previous one (e.g. an injury swap in the Expected
    list), but Expected and Confirmed never overwrite each other. Returns starters stored."""
    from datetime import datetime
    ensure_official_tables(con)
    if not feed:
        return 0
    id_map = espn_ids_by_name(con)
    now = datetime.now().isoformat(timespec="seconds")
    groups: dict = {}
    for g in feed.get("games", []):
        for side in ("homeTeam", "awayTeam"):
            team = NBA_TO_ESPN.get(g[side].get("teamAbbreviation"), g[side].get("teamAbbreviation"))
            for p in g[side].get("players", []):
                if not p.get("position"):
                    continue                      # bench players carry no position
                status = str(p.get("lineupStatus", "")).strip().title() or "Unknown"
                name = p.get("playerName") or f"{p.get('firstName', '')} {p.get('lastName', '')}".strip()
                groups.setdefault((team, status), []).append(
                    (d, team, status, id_map.get(norm_name(name)), name, p["position"], now))
    for (team, status), rows in groups.items():
        con.execute("DELETE FROM lineup_snapshots WHERE game_date = ? AND team = ? AND status = ?", (d, team, status))
        con.executemany("INSERT OR REPLACE INTO lineup_snapshots VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()
    return sum(len(r) for r in groups.values())


def save_seen_lineups(con) -> int:
    """Store every NBA.com daily-lineups feed fetched during this run (see store_lineup_snapshot)."""
    return sum(store_lineup_snapshot(con, d, feed) for d, feed in list(_FEED_CACHE.items()) if feed)


def store_official_lineups(con, d: str, feed=None) -> int:
    """Store NBA.com's starting lineups for day `d` (YYYYMMDD): team (ESPN code), ESPN player id (by name),
    position, status. `feed` = the day's daily-lineups JSON (fetched if not given); None = no file that day.
    The day is recorded in official_lineup_days either way, so it is not fetched again. Returns starters stored."""
    ensure_official_tables(con)
    if feed is None:
        feed = _daily_feed(d) or None
    id_map = espn_ids_by_name(con)
    rows = []
    for g in (feed or {}).get("games", []):
        for side in ("homeTeam", "awayTeam"):
            team = NBA_TO_ESPN.get(g[side].get("teamAbbreviation"), g[side].get("teamAbbreviation"))
            for p in g[side].get("players", []):
                if not p.get("position"):
                    continue                      # bench players carry no position
                name = p.get("playerName") or f"{p.get('firstName', '')} {p.get('lastName', '')}".strip()
                rows.append((d, team, id_map.get(norm_name(name)), name, p["position"], str(p.get("lineupStatus", ""))))
    con.executemany("INSERT OR REPLACE INTO official_lineups VALUES (?,?,?,?,?,?)", rows)
    con.execute("INSERT OR REPLACE INTO official_lineup_days VALUES (?, ?)", (d, len(rows)))
    con.commit()
    store_lineup_snapshot(con, d, feed)          # the day after, this is normally the Confirmed list
    return len(rows)


def official_lineup_days(con) -> set:
    ensure_official_tables(con)
    return {r[0] for r in con.execute("SELECT game_date FROM official_lineup_days")}


def listed_center(con, d: str, team: str) -> Optional[str]:
    """ESPN id of the starter NBA.com listed at C for `team` on day `d`, or None."""
    ensure_official_tables(con)
    row = con.execute("""SELECT player_id FROM official_lineups WHERE game_date = ? AND team = ? AND position = 'C'
                         AND player_id IS NOT NULL""", (yyyymmdd(d), team)).fetchone()
    return row[0] if row else None


def nba_daily_lineup(home: str, away: str, con, dates) -> Optional[dict]:
    """Look the game up in the daily feed. `dates`: YYYYMMDD candidates, ET date first."""
    h, a = ESPN_TO_NBA.get(home, home), ESPN_TO_NBA.get(away, away)
    id_map = espn_ids_by_name(con)
    for d in dict.fromkeys(yyyymmdd(x) for x in dates):
        feed = _daily_feed(d)
        if not feed:
            continue
        for g in feed.get("games", []):
            if (g.get("homeTeam", {}).get("teamAbbreviation") == h
                    and g.get("awayTeam", {}).get("teamAbbreviation") == a):
                return parse_daily_game(g, home, away, id_map)
    return None


# ──────────────────────────────────────────────
# INJURY REPORT + DAILY REPORT
# ──────────────────────────────────────────────
NBA_TO_ESPN = {v: k for k, v in ESPN_TO_NBA.items()}


def _detail(p: dict) -> str:
    """'Left Toe, est. return 2026-11-13' from ESPN's details block; falls back to the short comment."""
    d = p.get("details") or {}
    if isinstance(d, dict) and (d.get("type") or d.get("returnDate")):
        what = " ".join(x for x in (d.get("side"), d.get("type")) if x)
        ret = d.get("returnDate")
        return (what + (f", est. return {ret}" if ret else "")).strip(", ")
    return str(p.get("shortComment") or p.get("longComment") or "")[:90]


def parse_injury_rows(rows) -> list[dict]:
    """Pure function. rows: ESPN injuries rows ({display_name, injuries: list | repr-string}).
    -> [{name, team (ESPN abbrev), status, detail, note}]"""
    out = []
    for row in rows:
        lst = row.get("injuries")
        lst = ast.literal_eval(lst) if isinstance(lst, str) else (lst or [])
        for p in lst:
            ath = p.get("athlete", {})
            out.append({
                "name": ath.get("displayName", ""),
                "team": ath.get("team", {}).get("abbreviation", ""),
                "status": p.get("status", ""),
                "detail": _detail(p),
                "note": p.get("shortComment", ""),
                "return": (p.get("details") or {}).get("returnDate", "") if isinstance(p.get("details"), dict) else "",
            })
    return out


SUMMARY_URL = ("https://site.web.api.espn.com/apis/site/v2/sports/basketball/nba/summary"
               "?region=us&lang=en&contentorigin=espn&event={event}")


def parse_summary_injuries(data: dict) -> list[dict]:
    """Pure function. ESPN game-summary JSON -> same row shape as parse_injury_rows.
    Tolerant: summary['injuries'] = [{team: {...}, injuries: [{status, athlete, details, ...}]}]."""
    out = []
    for blk in (data or {}).get("injuries") or []:
        team = blk.get("team") or {}
        abbr = team.get("abbreviation", "")
        for p in blk.get("injuries") or []:
            ath = p.get("athlete") or {}
            name = ath.get("displayName") or ath.get("fullName") or ""
            if not name:
                continue
            det = p.get("details") if isinstance(p.get("details"), dict) else {}
            out.append({
                "name": name,
                "team": abbr or (ath.get("team") or {}).get("abbreviation", ""),
                "status": p.get("status", "") or (p.get("type") or {}).get("description", ""),
                "detail": _detail(p),
                "note": p.get("shortComment", ""),
                "return": det.get("returnDate", ""),
            })
    return out


def fetch_game_injuries(event_id: str) -> list[dict]:
    """Injuries ESPN attaches to one game (summary endpoint). [] on failure."""
    try:
        import requests
        time.sleep(SLEEP)
        r = requests.get(SUMMARY_URL.format(event=event_id), timeout=15,
                         headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        return parse_summary_injuries(r.json())
    except Exception as e:  # noqa: BLE001
        print(f"  (game {event_id} injury fetch failed: {e})")
        return []


def merge_injury_reports(*reports) -> list[dict]:
    """Union of reports by player. If sources disagree, the MORE SEVERE status wins (a missed
    injury is worse than a false alarm) and the row is marked conflict with all statuses seen."""
    by = {}
    for rep in reports:
        for r in rep or []:
            k = norm_name(r["name"])
            if not k:
                continue
            cur = by.get(k)
            if cur is None:
                by[k] = dict(r, seen=[r["status"]])
                continue
            seen = cur["seen"] + ([r["status"]] if r["status"] not in cur["seen"] else [])
            if severity(r["status"]) < severity(cur["status"]):
                cur = dict(r, team=r.get("team") or cur.get("team", ""))
            else:
                cur = dict(cur, team=cur.get("team") or r.get("team", ""),
                           detail=cur.get("detail") or r.get("detail", ""),
                           **{"return": cur.get("return") or r.get("return", "")})
            cur["seen"] = seen
            cur["conflict"] = len({severity(x) for x in seen}) > 1
            by[k] = cur
    return list(by.values())


def fetch_injury_report(game_ids=None, verbose: bool = True) -> list[dict]:
    """ESPN league-wide injury feed, plus (when `game_ids` given) each game's own summary
    injuries, merged. Never fails silently: prints a warning if nothing could be loaded."""
    league = []
    try:
        from sportsdataverse.nba import espn_nba_injuries

        time.sleep(SLEEP)
        inj = espn_nba_injuries()
        if inj.height:
            league = parse_injury_rows(inj.iter_rows(named=True))
    except Exception as e:  # noqa: BLE001
        print(f"  (league injury feed failed: {e})")
    per_game = []
    for gid in game_ids or []:
        per_game.extend(fetch_game_injuries(gid))
    rep = merge_injury_reports(league, per_game)
    if verbose:
        print("  " + injury_coverage_note(rep, len(league), len(per_game), bool(game_ids)))
    return rep


def injury_coverage_note(rep: list[dict], n_league: int = 0, n_game: int = 0, used_games: bool = False) -> str:
    if not rep:
        return ("🚨 NO INJURY DATA LOADED — injuries are NOT accounted for. Do not trust projected "
                "lineups; check ESPN/NBA.com injury reports by hand.")
    outs = sum(severity(r["status"]) == 0 for r in rep)
    conf = [r["name"] for r in rep if r.get("conflict")]
    note = (f"Injury report: {len(rep)} players ({outs} out) from league feed={n_league}"
            + (f", game summaries={n_game}" if used_games else " (game summaries not used)"))
    if used_games and n_game == 0:
        note += " ⚠️ game-summary source returned nothing"
    if conf:
        note += f" · sources disagreed (kept the more severe): {', '.join(conf[:6])}"
    return note


def team_by_player(con) -> dict:
    """normalized player name -> ESPN team abbrev (most recent game wins)."""
    ensure_lineups_table(con)
    rows = con.execute(
        """SELECT l.player_name, l.team FROM lineups l JOIN games g USING(game_id)
           ORDER BY g.game_date""").fetchall()
    return {norm_name(n): t for n, t in rows}


def injury_flags(names, report: list[dict]) -> list[tuple]:
    """[(name, status, detail)] for any of `names` on the report (not Healthy)."""
    by = {norm_name(r["name"]): r for r in report}
    out = []
    for n in names:
        r = by.get(norm_name(n))
        if r and r["status"]:
            out.append((n, r["status"], r["detail"]))
    return out


ROSTER_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{team}/roster"


def parse_roster(data: dict) -> dict:
    """ESPN roster JSON -> {normalized player name: team code}."""
    team = data.get("team", {}).get("abbreviation", "")
    return {norm_name(a.get("displayName", "")): team for a in data.get("athletes", []) if a.get("displayName")}


def position_group(pos) -> Optional[str]:
    """'PG'/'SG'/'G' -> 'G', 'SF'/'PF'/'F' -> 'F', 'C' -> 'C'; combos like 'F-C' use the first part."""
    p = str(pos or "").upper().split("-")[0].strip()
    if p in ("PG", "SG", "G"):
        return "G"
    if p in ("SF", "PF", "F"):
        return "F"
    return "C" if p == "C" else None


def player_groups(con, before, roster_positions: Optional[dict] = None, prefer: str = "official") -> dict:
    """{normalized name: 'G'|'F'|'C'}. prefer="official": the group NBA.com listed him at most often as a starter
    before `before`, and the ESPN roster position only for players NBA.com never listed (bench players).
    prefer="roster": ESPN roster first. ESPN calls many centres 'F' (e.g. Sandro Mamukelashvili: ESPN F, NBA.com C
    in 12 of 13 starts); the two sources disagree for ~21% of players."""
    from collections import Counter

    ensure_official_tables(con)
    seen = {}
    for name, pos in con.execute("SELECT player_name, position FROM official_lineups WHERE game_date < ?",
                                 (yyyymmdd(before),)):
        g = position_group(pos)
        if g:
            seen.setdefault(norm_name(name), Counter())[g] += 1
    official = {k: c.most_common(1)[0][0] for k, c in seen.items()}
    roster = {k: g for k, g in (roster_positions or {}).items() if g}
    return {**roster, **official} if prefer == "official" else {**official, **roster}


def fetch_rosters(teams, positions: Optional[dict] = None) -> dict:
    """Current rosters of `teams` (ESPN codes) -> {normalized player name: team}. A team that fails is
    skipped with a warning (its players keep the team learned from box scores). If `positions` is a dict,
    it is filled with {normalized name: 'G'|'F'|'C'} from the same rosters."""
    import requests

    out = {}
    for team in sorted(set(teams)):
        try:
            time.sleep(SLEEP)
            r = requests.get(ROSTER_URL.format(team=team.lower()), timeout=15)
            r.raise_for_status()
            data = r.json()
            out.update(parse_roster(data))
            if positions is not None:
                positions.update({norm_name(a.get("displayName", "")): position_group(a.get("position", {}).get("abbreviation"))
                                  for a in data.get("athletes", []) if a.get("displayName")})
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠️ roster fetch failed for {team}: {e}")
    return out


def team_injuries(team: str, report: list[dict], team_of: dict) -> list[dict]:
    rows = [r for r in report if (r.get("team") or team_of.get(norm_name(r["name"]))) == team]
    return sorted(rows, key=lambda r: (severity(r["status"]), r["name"]))


def daily_report(con, date_str: str, game_ids=None) -> str:
    """Human-readable report: every game on `date_str` with lineups + injuries.
    `game_ids`: ESPN ids of the day's games, so each game's own injury report (Doubtful/Questionable) is included."""
    feed = _daily_feed(yyyymmdd(date_str))
    if not feed:
        return (f"NBA.com's daily lineups for {yyyymmdd(date_str)} aren't published yet. "
                f"They usually appear during the day; try again later.")
    report = fetch_injury_report(game_ids)
    team_of = team_by_player(con)
    lines = [f"NBA DAILY REPORT — {yyyymmdd(date_str)}", "=" * 60]
    games = feed.get("games", [])
    if not games:
        return lines[0] + "\nNo games in the feed yet."
    for g in games:
        h, a = g["homeTeam"], g["awayTeam"]
        ha, aa = h.get("teamAbbreviation", "?"), a.get("teamAbbreviation", "?")
        state = {1: "", 2: " [LIVE]", 3: " [FINAL]"}.get(g.get("gameStatus"), "")
        lines.append(f"\n{aa} @ {ha} — {g.get('gameStatusText','')}{state}")
        for side, abbr in ((a, aa), (h, ha)):
            starters = [p for p in side.get("players", []) if p.get("position")]
            stat = sorted({str(p.get("lineupStatus", "")) for p in starters}) or ["—"]
            lines.append(f"  {abbr} starters [{', '.join(stat)}]")
            flags = {norm_name(n): (s, d) for n, s, d in
                     injury_flags([p.get("playerName", "") for p in starters], report)}
            for p in starters:
                nm = p.get("playerName", "")
                f = flags.get(norm_name(nm))
                tag = f"   ⚠️ {f[0]}" + (f" ({f[1]})" if f[1] else "") if f else ""
                lines.append(f"    {p.get('position',''):<3} {nm}{tag}")
            inj = team_injuries(NBA_TO_ESPN.get(abbr, abbr), report, team_of)
            if inj:
                lines.append(f"  {abbr} injury report:")
                for r in inj:
                    lines.append(f"    {r['status']:<14} {r['name']}" + (f" — {r['detail']}" if r['detail'] else ""))
    if not report:
        lines.append("\n(No injury data loaded — ESPN injury feed returned nothing.)")
    return "\n".join(lines)


# ── injury sets for projection ───────────────────────────────


# ──────────────────────────────────────────────
# INJURY SUBSTITUTION
# Stateless on purpose: the lineup is rebuilt from the CURRENT injury report on every
# run. Nothing is remembered between runs, so a player who is cleared (no longer on the
# report) goes straight back into the lineup, and one who is newly ruled out is replaced.
# ──────────────────────────────────────────────
# Statuses that get replaced: 0 = Out, 1 = Doubtful. 2 = Questionable / 3 = Day-to-Day are
# KEPT in the lineup (most of them play) and shown with an "if out -> X" note instead.
# Set to 3 to replace Questionable / Day-to-Day players too.
REPLACE_MAX_SEVERITY = 1
MIN_MISSED_GAMES = 2    # need >= this many games the player missed before trusting "who started instead"
# Position-aware replacements (backtest 2026-11-17 on 1,641 team-games with a missing regular starter, vs NBA.com's
# confirmed starters): forcing the same position for everyone made replacements WORSE (39.5% -> 33.9% right on
# Jan-Apr 2026, 55.2% -> 42.8% on 2026-27). Only centres, and only after the coach's own history, helped a little
# (39.5 -> 39.6%, 55.2 -> 55.7%) and fixes cases like a centre replaced by a wing. So:
POSITION_MODE = "after_history"   # history (any position) -> same-position next in line -> anyone
POSITION_GROUPS = {"C"}           # position matching only when the missing starter is a centre
MIN_FILL_STARTS = 2     # ...and the replacement must have started at least this many of them (1 game is noise)


def season_start(before) -> str:
    """First day (YYYYMMDD) of the NBA season that `before` falls in (seasons start in autumn)."""
    b = yyyymmdd(before)
    y, m = int(b[:4]), int(b[4:6])
    return f"{y if m >= 9 else y - 1}0901"


def _team_games(con, team: str, before: str, since: Optional[str] = None, limit: Optional[int] = None) -> list:
    """[(game_id, game_date)] newest first: the team's stored games before `before`."""
    q = ("SELECT DISTINCT g.game_id, g.game_date FROM games g "
         "JOIN lineups l ON l.game_id = g.game_id AND l.team = ? WHERE g.game_date < ?")
    params = [team, yyyymmdd(before)]
    if since:
        q += " AND g.game_date >= ?"
        params.append(since)
    q += " ORDER BY g.game_date DESC, g.game_id DESC"
    if limit:
        q += " LIMIT ?"
        params.append(limit)
    return con.execute(q, params).fetchall()


def _rows_for_games(con, team: str, game_ids: list) -> dict:
    """game_id -> [(player_id, name, is_starter, minutes)]"""
    out = {g: [] for g in game_ids}
    for i in range(0, len(game_ids), 500):
        chunk = game_ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        for gid, pid, name, st_, mins in con.execute(
                f"SELECT game_id, player_id, player_name, is_starter, minutes FROM lineups "
                f"WHERE team = ? AND game_id IN ({q})", (team, *chunk)):
            out[gid].append((pid, name, st_, mins))
    return out


# Projection settings, tuned on Jan–Apr 2026 (1,466 team-games) against NBA.com's confirmed starters and
# confirmed on 2026-27 (366 team-games): 80.4% of starters right vs 77.2% with the old 5 / 2x / 30 settings.
RECENT_GAMES = 3      # 'recent' window
RECENT_WEIGHT = 4     # recent games count this many times
LONG_GAMES = 15       # overall window


def healthy_base_five(con, team: str, game_date, n_recent: int = RECENT_GAMES, long_games: int = LONG_GAMES,
                      team_of: Optional[dict] = None, verified_names: Optional[set] = None,
                      listed_names: Optional[set] = None) -> dict:
    """The team's usual starting five assuming EVERYONE is available (injuries are applied afterwards).

    Ranks players by how often they START WHEN THEY PLAY (recent games count RECENT_WEIGHT times, shrunk toward a low
    prior so one-game samples can't win), over this season's last `long_games` (early in the season it
    borrows last season's). Because it is a rate, a regular starter who missed a few games is still
    recognised once he is healthy again, while the bench player who filled in does not displace him.
    Exceptions:
      * played in the recent games but never started -> benched by the coach, not a starter.
      * missed the ENTIRE recent window -> back in only if he is a regular starter AND either in
        `verified_names` (still on the report as Questionable/Day-to-Day, possibly back) or cleared:
        `listed_names` (everyone on a successfully loaded report) is non-empty and he is not on it.
        Otherwise (still Out, or no report to check against) he is not assumed back.
      * most recent team is not `team` (traded) -> ignored.
    -> {'players': [...5], 'complete': bool, 'games_used': int, 'missed_recent': {norm_name: games missed}}"""
    before = yyyymmdd(game_date)
    team_of = team_of if team_of is not None else team_by_player(con)
    verified_names = verified_names or set()
    listed_names = listed_names or set()
    games = _team_games(con, team, before, since=season_start(before), limit=long_games)
    if len(games) < n_recent:
        games = _team_games(con, team, before, limit=long_games)
    if not games:
        return {"players": [], "complete": False, "games_used": 0, "missed_recent": {}}
    rows = _rows_for_games(con, team, [g for g, _ in games])
    recent = {g for g, _ in games[:n_recent]}
    s = defaultdict(lambda: {"name": "", "st_recent": 0, "st_long": 0, "pl_recent": 0, "pl_long": 0, "mins": 0.0})
    for gid, _ in games:
        for pid, name, st_, mins in rows[gid]:
            e = s[pid]
            e["name"] = name
            e["mins"] += mins or 0.0
            played = (mins or 0) > 0
            e["st_long"] += 1 if st_ else 0
            e["pl_long"] += 1 if played else 0
            if gid in recent:
                e["st_recent"] += 1 if st_ else 0
                e["pl_recent"] += 1 if played else 0

    PRIOR_RATE, PRIOR_WEIGHT = 0.2, 3.0

    def score(e):
        if e["pl_recent"] > 0 and e["st_recent"] == 0:
            return 0.0                                  # healthy and playing, but not starting: benched
        if e["pl_recent"] == 0:
            # Missed the whole recent window. A regular (>= 3 starts and >= 60% of the games he played)
            # is still a starter if he is Questionable/Day-to-Day on the report (the fill-in is shown as
            # his "if he sits" replacement) or has been cleared (report loaded, he's no longer on it).
            # Anyone else who has vanished (still Out, waived, traded, benched) is not assumed back.
            k = norm_name(e["name"])
            cleared = bool(listed_names) and k not in listed_names
            if ((k in verified_names or cleared) and e["st_long"] >= 3
                    and e["st_long"] >= 0.6 * e["pl_long"]):
                return 1.0
            return 0.0
        num = RECENT_WEIGHT * e["st_recent"] + e["st_long"]
        den = RECENT_WEIGHT * e["pl_recent"] + e["pl_long"]
        return (num + PRIOR_RATE * PRIOR_WEIGHT) / (den + PRIOR_WEIGHT) if den else 0.0

    cand = [(pid, e) for pid, e in s.items() if team_of.get(norm_name(e["name"]), team) == team]
    cand.sort(key=lambda kv: (-score(kv[1]), -kv[1]["st_long"], -kv[1]["mins"], kv[1]["name"]))
    top = [(pid, e) for pid, e in cand[:5] if score(e) > 0]
    players = [{"player_id": pid, "name": e["name"], "position": "", "jersey": "", "team": team,
                "uncertain": False, "id_matched": True} for pid, e in top]
    n_rec = len(recent)
    missed = {norm_name(e["name"]): n_rec - e["pl_recent"] for _, e in top if e["pl_recent"] < n_rec}
    return {"players": players, "complete": len(players) == 5, "games_used": len(games), "missed_recent": missed}


def pick_replacement(con, team: str, absent_name: str, exclude: set, game_date,
                     team_of: Optional[dict] = None, groups: Optional[dict] = None) -> Optional[dict]:
    """Who starts when `absent_name` is out? `exclude` = normalized names that can't be chosen
    (the other starters, other players ruled out).

    1. HISTORY: games (this season; both seasons if that is too thin) the player missed while on the
       team -> the non-excluded player who started most of them (needs >= MIN_MISSED_GAMES missed
       games and the replacement to have started >= MIN_FILL_STARTS of them).
    2. FALLBACK: the non-excluded player with the most starts in the last 5 games, then the most
       minutes over the last 10 (the same ranking the lineup projection has always used).
    Only players who played in those last 10 games (the current rotation) can be chosen.
    POSITIONS: with `groups` ({normalized name: 'G'|'F'|'C'}, see player_groups) and a missing CENTRE
    (POSITION_GROUPS), the coach's own history still decides first; failing that, the next-in-line player of
    the same position is preferred before anyone else. Unknown positions never block anyone.
    -> player dict plus 'how' ('history'|'recent') and 'evidence', or None if nobody is available."""
    before = yyyymmdd(game_date)
    ax = norm_name(absent_name)
    team_of = team_of if team_of is not None else team_by_player(con)

    rot_games = (_team_games(con, team, before, since=season_start(before), limit=10)
                 or _team_games(con, team, before, limit=10))
    rot_rows = _rows_for_games(con, team, [g for g, _ in rot_games])
    in_rotation = {norm_name(n) for g, _ in rot_games for _, n, _, m in rot_rows[g] if (m or 0) > 0}

    def ok(name):
        k = norm_name(name)
        return k != ax and k not in exclude and k in in_rotation and team_of.get(k, team) == team

    def make(pid, name, how, evidence):
        return {"player_id": pid, "name": name, "position": "", "jersey": "", "team": team,
                "uncertain": False, "id_matched": True, "how": how, "evidence": evidence}

    def history_pick(allowed):
        if team_of.get(ax, team) != team:
            return None
        for since in (season_start(before), None):
            games = _team_games(con, team, before, since=since)
            if not games:
                continue
            rows = _rows_for_games(con, team, [g for g, _ in games])
            played = [any(norm_name(n) == ax and (m or 0) > 0 for _, n, _, m in rows[g]) for g, _ in games]
            if not any(played):
                continue
            oldest = max(i for i, p in enumerate(played) if p)
            current = list(range(played.index(True)))                  # his ongoing absence (newest games)
            missed = [i for i in range(oldest + 1) if not played[i]]   # every game missed, incl. the current run
            # The current absence decides first (an old absence may have had a different fill-in);
            # if it's too short to tell, fall back to every game he missed.
            for window, label in ((current, f"the last {len(current)} games {absent_name} missed"),
                                  (missed, f"the {len(missed)} games {absent_name} missed")):
                if len(window) < MIN_MISSED_GAMES:
                    continue
                counts = {}
                for i in window:
                    for pid, n, st_, m in rows[games[i][0]]:
                        if st_ and ok(n) and allowed(n):
                            c = counts.setdefault(pid, [n, 0, i])
                            c[1] += 1
                            c[2] = min(c[2], i)
                if counts:
                    pid, (n, c, _) = max(counts.items(), key=lambda kv: (kv[1][1], -kv[1][2]))
                    if c >= MIN_FILL_STARTS:
                        return pid, n, f"started {c} of {label}"
        return None

    recent5 = {g for g, _ in rot_games[:5]}

    def recent_pick(allowed):
        info = {}
        for g, _ in rot_games:
            for pid, n, st_, m in rot_rows[g]:
                if ok(n) and allowed(n):
                    e = info.setdefault(pid, [n, 0, 0.0])      # name, starts in last 5, minutes in last 10
                    e[1] += 1 if (st_ and g in recent5) else 0
                    e[2] += m or 0.0
        cand = [(pid, n, ss, mm) for pid, (n, ss, mm) in info.items() if mm > 0]
        if not cand:
            return None
        pid, n, _, _ = max(cand, key=lambda x: (x[2], x[3], x[1]))
        return pid, n, "next in line: most recent starts, then minutes"

    anyone = lambda n: True
    group = (groups or {}).get(ax)
    steps = [(history_pick, anyone, ""), (recent_pick, anyone, "")]
    if group and (group in POSITION_GROUPS):
        same = lambda n: (groups or {}).get(norm_name(n)) == group
        if POSITION_MODE == "first":
            steps = [(history_pick, same, " (same position)"), (recent_pick, same, " (same position)")] + steps
        else:   # "after_history": the coach's own history first, then same position, then anyone
            steps = [(history_pick, anyone, ""), (recent_pick, same, " (same position)"), (recent_pick, anyone, "")]
    for fn, allowed, note in steps:
        hit = fn(allowed)
        if hit:
            pid, n, evidence = hit
            return make(pid, n, "history" if fn is history_pick else "recent", evidence + note)
    return None


def _status_maps(report: list[dict]) -> dict:
    return {norm_name(r["name"]): r for r in report if r.get("status")}


def substitute_injured(con, team: str, starters: list, report: list[dict], game_date,
                       replace_max_severity: Optional[int] = None,
                       team_of: Optional[dict] = None, groups: Optional[dict] = None) -> dict:
    """Swap starters who are ruled out (severity <= replace_max_severity) for their expected replacement,
    in the same slot. Works on any five-man list (feed lineup or healthy_base_five).
    A starter nobody can replace stays in the list and is reported in 'unresolved'.
    -> {'players': [...], 'replaced': [{'out','in','how','evidence','status','detail','return'}], 'unresolved': [names]}"""
    rms = REPLACE_MAX_SEVERITY if replace_max_severity is None else replace_max_severity
    team_of = team_of if team_of is not None else team_by_player(con)
    by = _status_maps(report)

    def sev(p):
        return severity(by.get(norm_name(p["name"]), {}).get("status", ""))

    unavailable = {k for k, r in by.items() if severity(r["status"]) <= rms}
    exclude = {norm_name(p["name"]) for p in starters} | unavailable
    lineup, replaced, unresolved = list(starters), [], []
    for p in starters:
        if sev(p) > rms:
            continue
        rep = pick_replacement(con, team, p["name"], exclude, game_date, team_of, groups)
        r = by.get(norm_name(p["name"]), {})
        if rep is None:
            unresolved.append(p["name"])
            continue
        exclude.add(norm_name(rep["name"]))
        rep["uncertain"] = severity(by.get(norm_name(rep["name"]), {}).get("status", "")) in (2, 3)
        rep["replaces"] = p["name"]
        lineup[lineup.index(p)] = rep
        replaced.append({"out": p["name"], "in": rep["name"], "how": rep["how"], "evidence": rep["evidence"],
                         "status": r.get("status", ""), "detail": r.get("detail", ""), "return": r.get("return", "")})
    return {"players": lineup, "replaced": replaced, "unresolved": unresolved}


def contingencies(con, team: str, starters: list, report: list[dict], game_date,
                  replace_max_severity: Optional[int] = None, team_of: Optional[dict] = None,
                  groups: Optional[dict] = None) -> dict:
    """For starters KEPT in the lineup but listed Questionable / Day-to-Day: who would start if he sits?
    -> {normalized starter name: {'in': name, 'how': .., 'evidence': ..}}"""
    rms = REPLACE_MAX_SEVERITY if replace_max_severity is None else replace_max_severity
    team_of = team_of if team_of is not None else team_by_player(con)
    by = _status_maps(report)
    unavailable = {k for k, r in by.items() if severity(r["status"]) <= rms}
    base_exclude = {norm_name(p["name"]) for p in starters} | unavailable
    out = {}
    for p in starters:
        k = norm_name(p["name"])
        if not (rms < severity(by.get(k, {}).get("status", "")) <= 3):
            continue
        rep = pick_replacement(con, team, p["name"], base_exclude, game_date, team_of, groups)
        if rep:
            base_exclude.add(norm_name(rep["name"]))      # two players sitting need two different fill-ins
            out[k] = {"in": rep["name"], "how": rep["how"], "evidence": rep["evidence"]}
    return out


def project_usual_starters(con, team: str, game_date, report: list[dict], n_games: int = RECENT_GAMES,
                           rosters: Optional[dict] = None, groups: Optional[dict] = None) -> dict:
    """Usual starting five with everyone healthy, then ruled-out players swapped for their expected
    replacement using the CURRENT report (a player cleared since the last run is back in).
    `rosters`: {normalized name: team} from current rosters (fetch_rosters); it overrides the team learned
    from box scores, so a traded player stops counting for his old team before he plays for the new one.
    -> {'players', 'complete', 'dropped': [names removed], 'replaced': [...], 'unresolved': [...],
        'returning': {name: recent team games missed},  # in the five, no longer on the report
        'filled': [names added to fill an open spot]}"""
    team_of = {**team_by_player(con), **(rosters or {})}
    by = _status_maps(report)
    verified = {k for k, r in by.items() if severity(r["status"]) in (2, 3)}
    base = healthy_base_five(con, team, game_date, n_recent=n_games, team_of=team_of, verified_names=verified,
                             listed_names=set(by))
    if not base["players"]:
        return {"players": [], "complete": False, "dropped": [], "replaced": [], "unresolved": [], "returning": {},
                "filled": []}
    # Fewer than five usual starters (e.g. one was traded away and nobody has started in his place yet):
    # fill each open spot with the next player in line — most recent starts, then minutes.
    filled = []
    unavailable = {k for k, r in by.items() if severity(r["status"]) <= REPLACE_MAX_SEVERITY}
    while len(base["players"]) < 5:
        exclude = {norm_name(p["name"]) for p in base["players"]} | unavailable
        nxt = pick_replacement(con, team, "", exclude, game_date, team_of)
        if not nxt:
            break
        base["players"].append(nxt)
        filled.append(nxt["name"])
    for p in base["players"]:
        p["uncertain"] = severity(by.get(norm_name(p["name"]), {}).get("status", "")) in (2, 3)
    sub = substitute_injured(con, team, base["players"], report, game_date, team_of=team_of, groups=groups)
    returning = {p["name"]: base["missed_recent"][norm_name(p["name"])]
                 for p in sub["players"]
                 if norm_name(p["name"]) in base["missed_recent"] and norm_name(p["name"]) not in by
                 and base["missed_recent"][norm_name(p["name"])] >= 2}
    return {"players": sub["players"],
            "complete": len(sub["players"]) == 5 and not sub["unresolved"],
            "dropped": [r["out"] for r in sub["replaced"]], "replaced": sub["replaced"],
            "unresolved": sub["unresolved"], "returning": returning, "filled": filled}


def returning_by(report: list[dict], names, day_iso: str) -> list[str]:
    """Names (from `names`) that are ruled out but whose estimated return date is `day_iso` or earlier.
    ESPN's status may be stale for these: they could actually play."""
    by = {norm_name(r["name"]): r for r in report if severity(r["status"]) <= 1}
    out = []
    for n in names:
        r = by.get(norm_name(n))
        if r and r.get("return") and r["return"][:10] <= day_iso:
            out.append(n)
    return out
