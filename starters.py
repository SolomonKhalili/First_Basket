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
OUT_STATUSES = {"Out", "Injured Reserve", "Suspension"}
UNCERTAIN_STATUSES = {"Day-To-Day", "Day-to-Day", "Questionable", "Doubtful"}


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
def fetch_injuries() -> dict:
    """{player display name: status}. Empty dict on any failure."""
    try:
        from sportsdataverse.nba import espn_nba_injuries

        time.sleep(SLEEP)
        inj = espn_nba_injuries()
        if inj.height == 0:
            return {}
        out = {}
        for row in inj.iter_rows(named=True):
            lst = row["injuries"]
            lst = ast.literal_eval(lst) if isinstance(lst, str) else lst
            for p in lst:
                out[p["athlete"]["displayName"]] = p.get("status", "")
        return out
    except Exception:  # noqa: BLE001
        return {}


def split_injuries(status_by_name: dict) -> tuple[set, set]:
    """-> (definitely_out_names, uncertain_names)"""
    out = {n for n, s in status_by_name.items() if s in OUT_STATUSES}
    unc = {n for n, s in status_by_name.items() if s in UNCERTAIN_STATUSES}
    return out, unc


# ──────────────────────────────────────────────
# READ: projected starters from stored lineups
# ──────────────────────────────────────────────
def recent_starters(
    con,
    team: str,
    before_date: str,
    n_games: int = 5,
    out_names: Optional[set] = None,
    uncertain_names: Optional[set] = None,
) -> dict:
    """
    Projected starting 5 from the team's last `n_games` completed games BEFORE
    `before_date` (YYYYMMDD, string-compared against YYYYMMDD in the DB).

    Rank: starts in window (desc), then total minutes (desc), then most recent
    start. Players ruled out are skipped, so the next-ranked player (usually the
    top bench rotation player) fills in.

    Returns {'players': [{player_id, name, position, jersey, team, uncertain}],
             'games_used': int, 'complete': bool}
    """
    out_names = out_names or set()
    uncertain_names = uncertain_names or set()
    before = yyyymmdd(before_date)

    games = con.execute(
        """SELECT DISTINCT g.game_id, g.game_date
           FROM games g JOIN lineups l ON l.game_id = g.game_id AND l.team = ?
           WHERE g.game_date < ?
           ORDER BY g.game_date DESC, g.game_id DESC
           LIMIT ?""",
        (team, before, n_games),
    ).fetchall()
    if not games:
        return {"players": [], "games_used": 0, "complete": False}

    stats = defaultdict(lambda: {"name": "", "starts": 0, "minutes": 0.0, "last": ""})
    for gid, gdate in games:
        for pid, name, st, mins in con.execute(
            "SELECT player_id, player_name, is_starter, minutes FROM lineups "
            "WHERE game_id = ? AND team = ?",
            (gid, team),
        ):
            s = stats[pid]
            s["name"] = name
            s["minutes"] += mins or 0.0
            if st:
                s["starts"] += 1
                s["last"] = max(s["last"], gdate)

    ranked = sorted(
        (
            (pid, s)
            for pid, s in stats.items()
            if s["name"] not in out_names
        ),
        key=lambda kv: (-kv[1]["starts"], -kv[1]["minutes"], kv[1]["last"] == ""),
    )
    top = ranked[:5]
    players = [
        {
            "player_id": pid,
            "name": s["name"],
            "position": "",
            "jersey": "",
            "team": team,
            "uncertain": s["name"] in uncertain_names,
        }
        for pid, s in top
    ]
    return {"players": players, "games_used": len(games), "complete": len(players) == 5}


# ──────────────────────────────────────────────
# READ: ESPN-confirmed starters
# ──────────────────────────────────────────────
def espn_confirmed_starters(game_id: str) -> Optional[dict]:
    """
    {team_abbrev: [5 starters]} ONLY if ESPN flags exactly 5 starters for both
    teams (i.e. lineups are confirmed). Otherwise None.
    """
    try:
        from sportsdataverse.nba import espn_nba_pbp

        time.sleep(SLEEP)
        pbp = espn_nba_pbp(game_id=int(game_id))
        players = pbp.get("boxscore", {}).get("players", [])
        if len(players) != 2:
            return None
        res = {}
        for team_data in players:
            abbrev = team_data.get("team", {}).get("abbreviation", "")
            flagged = []
            for stat in team_data.get("statistics", []):
                for a in stat.get("athletes", []):
                    if not a.get("starter", False):
                        continue
                    ath = a.get("athlete", {})
                    pid, name = str(ath.get("id", "")), ath.get("displayName", "")
                    if pid and name:
                        flagged.append(
                            {
                                "player_id": pid,
                                "name": name,
                                "position": ath.get("position", {}).get("abbreviation", ""),
                                "jersey": str(ath.get("jersey", "") or ""),
                                "team": abbrev,
                                "uncertain": False,
                            }
                        )
            if len(flagged) != 5:
                return None  # not confirmed yet
            res[abbrev] = flagged
        return res if len(res) == 2 else None
    except Exception as e:  # noqa: BLE001
        print(f"  Error getting confirmed starters for {game_id}: {e}")
        return None


# ──────────────────────────────────────────────
# RESOLVE
# ──────────────────────────────────────────────
def resolve_starters(game_id: str, home: str, away: str, con, game_date, injuries: Optional[dict] = None):
    """
    -> {'home': [...5], 'away': [...5], 'source': 'espn_confirmed'|'projected_lineups',
        'confirmed': bool, 'uncertain': [names]}  or None if neither source works.
    """
    conf = espn_confirmed_starters(game_id)
    if conf and home in conf and away in conf:
        return {"home": conf[home], "away": conf[away], "source": "espn_confirmed",
                "confirmed": True, "uncertain": []}

    inj = injuries if injuries is not None else fetch_injuries()
    out, unc = split_injuries(inj)
    h = recent_starters(con, home, game_date, out_names=out, uncertain_names=unc)
    a = recent_starters(con, away, game_date, out_names=out, uncertain_names=unc)
    if not (h["complete"] and a["complete"]):
        return None
    flagged = [p["name"] for p in h["players"] + a["players"] if p["uncertain"]]
    return {"home": h["players"], "away": a["players"], "source": "projected_lineups",
            "confirmed": False, "uncertain": flagged}


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
    return " ".join(w for w in s.split() if w not in _SUFFIXES)


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


def nba_confirmed_starters(home: str, away: str, con) -> Optional[dict]:
    """{home: [5], away: [5]} from NBA.com once lineups are posted, else None."""
    try:
        h, a = ESPN_TO_NBA.get(home, home), ESPN_TO_NBA.get(away, away)
        games = _nba_json(NBA_SCOREBOARD)["scoreboard"]["games"]
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
            print(f"  (NBA.com has not published the daily lineups file for {d} yet — it appears later in the day)")
        else:
            print(f"  (NBA.com daily lineups fetch failed for {d}: {e})")
    _FEED_CACHE[d] = feed
    return feed


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
_SEVERITY = {"Out": 0, "Injured Reserve": 0, "Suspension": 0, "Doubtful": 1,
             "Questionable": 2, "Day-To-Day": 3, "Day-to-Day": 3}


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


def fetch_injury_report() -> list[dict]:
    """ESPN injury report. [] on failure."""
    try:
        from sportsdataverse.nba import espn_nba_injuries

        time.sleep(SLEEP)
        inj = espn_nba_injuries()
        if inj.height == 0:
            return []
        return parse_injury_rows(inj.iter_rows(named=True))
    except Exception as e:  # noqa: BLE001
        print(f"  (injury report fetch failed: {e})")
        return []


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


def team_injuries(team: str, report: list[dict], team_of: dict) -> list[dict]:
    rows = [r for r in report if (r.get("team") or team_of.get(norm_name(r["name"]))) == team]
    return sorted(rows, key=lambda r: (_SEVERITY.get(r["status"], 9), r["name"]))


def daily_report(con, date_str: str) -> str:
    """Human-readable report: every game on `date_str` with lineups + injuries."""
    feed = _daily_feed(yyyymmdd(date_str))
    if not feed:
        return (f"NBA.com's daily lineups for {yyyymmdd(date_str)} aren't published yet. "
                f"They usually appear during the day; try again later.")
    report = fetch_injury_report()
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
PROJECTION_OUT = OUT_STATUSES | {"Doubtful"}   # treated as not starting when projecting


def out_names_from_report(report: list[dict]) -> set:
    return {r["name"] for r in report if r["status"] in PROJECTION_OUT}


def uncertain_names_from_report(report: list[dict]) -> set:
    return {r["name"] for r in report if r["status"] in UNCERTAIN_STATUSES - {"Doubtful"}}


def project_usual_starters(con, team: str, game_date, report: list[dict], n_games: int = 5) -> dict:
    """Usual starters (last n games) minus players ruled out; next-in-line fills in.
    -> {'players': [...], 'complete': bool, 'dropped': [names of usual starters removed]}"""
    out, unc = out_names_from_report(report), uncertain_names_from_report(report)
    base = recent_starters(con, team, game_date, n_games=n_games)
    adj = recent_starters(con, team, game_date, n_games=n_games, out_names=out, uncertain_names=unc)
    kept = {p["player_id"] for p in adj["players"]}
    dropped = [p["name"] for p in base["players"] if p["player_id"] not in kept]
    return {"players": adj["players"], "complete": adj["complete"], "dropped": dropped}


def returning_by(report: list[dict], names, day_iso: str) -> list[str]:
    """Names (from `names`) that are ruled out but whose estimated return date is `day_iso` or earlier.
    ESPN's status may be stale for these: they could actually play."""
    by = {norm_name(r["name"]): r for r in report if r["status"] in PROJECTION_OUT}
    out = []
    for n in names:
        r = by.get(norm_name(n))
        if r and r.get("return") and r["return"] <= day_iso:
            out.append(n)
    return out
