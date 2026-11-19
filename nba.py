#!/usr/bin/env python3
"""
NBA First Basket Scorer Predictor — 100% Free
Data: ESPN (via sportsdataverse) | DB: SQLite | ML: scikit-learn
"""

from sportsdataverse.nba import espn_nba_scoreboard, espn_nba_pbp
import sqlite3
import time
import json
import os
import csv
import sys
import numpy as np
import traceback
from datetime import date, timedelta
from typing import Optional
import warnings
import starters as st
import pregame_model as pm
import build_features as bf
import tip_model as tm
import picks
import ev
warnings.filterwarnings("ignore")

DB = "nba_first_basket.db"
SLEEP = 0.2  # faster; bump to 0.3 if you hit 429

NBA_TEAMS = {'ATL','BKN','BOS','CHA','CHI','CLE','DAL','DEN','DET','GS',
             'HOU','IND','LAC','LAL','MEM','MIA','MIL','MIN','NO','NY',
             'OKC','ORL','PHI','PHX','POR','SA','SAC','TOR','UTAH','WSH'}

# ──────────────────────────────────────────────
# DATABASE
# ──────────────────────────────────────────────
def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

# Player and team stats come from build_features (bf.compute_*), the same code used for training,
# so live predictions always see exactly the numbers the model was trained on.


def init():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS games (
        game_id TEXT PRIMARY KEY,
        game_date TEXT,
        home_team TEXT,
        away_team TEXT,
        home_score INTEGER,
        away_score INTEGER,
        status INTEGER,
        tip_winner_team TEXT,
        tip_winner_player_id TEXT,
        tip_winner_player_name TEXT,
        tip_loser_player_id TEXT,
        tip_loser_player_name TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS first_baskets (
        game_id TEXT PRIMARY KEY,
        scorer_player_id TEXT,
        scorer_name TEXT,
        scorer_team TEXT,
        shot_type TEXT,
        period INTEGER,
        clock TEXT,
        x REAL,
        y REAL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS predictions (
        game_id TEXT PRIMARY KEY,
        predicted_scorer_id TEXT,
        predicted_scorer_name TEXT,
        confidence REAL,
        model_version TEXT,
        features_json TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)
    con.commit()
    st.ensure_lineups_table(con)
    picks.ensure_table(con)
    st.ensure_official_tables(con)
    con.execute(FIRST_SHOTS_SCHEMA)
    con.execute(EARLY_SHOTS_SCHEMA)
    print("✅ Database ready")

# ──────────────────────────────────────────────
# ESPN DATA
# ──────────────────────────────────────────────
def _et_time(iso_utc: str) -> str:
    """'2026-11-18T00:30Z' -> '7:30 PM ET'"""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    try:
        t = datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))
        return t.strftime("%-I:%M %p ET")
    except ValueError:
        return ""


def get_todays_games() -> list[dict]:
    """Today's games from ESPN."""
    today = date.today().strftime("%Y%m%d")
    df = espn_nba_scoreboard(dates=[today], return_as_pandas=True)
    if df is None or len(df) == 0:
        return []
    games = []
    for _, r in df.iterrows():
        state = r.get('status_type_state', 'pre')
        status = 1 if state == 'pre' else (3 if state == 'post' else 2)
        games.append({
            'game_id': str(r['game_id']),
            'game_date': today,                    # ESPN's 'date' is UTC (a 7:30 PM ET game = tomorrow)
            'start': _et_time(str(r['date'])),
            'home_team': str(r['home_abbreviation']),
            'away_team': str(r['away_abbreviation']),
            'status': status,
            # for the GUI's countdowns and live scoreboard
            'start_utc': str(r['date']),
            'home_score': int(r.get('home_score') or 0),
            'away_score': int(r.get('away_score') or 0),
            'detail': str(r.get('status_type_short_detail') or ''),     # 'Q2 5:31', 'Halftime', 'Final'
        })
    return games

def flatten_summary(summary: dict) -> dict:
    """Pure (testable). ESPN's game summary -> the shape espn_nba_pbp returns (flat "a.b" keys on each play), so
    get_pbp reads both the same way."""
    comps = ((summary.get("header") or {}).get("competitions") or [{}])[0].get("competitors") or []
    side = {c.get("homeAway"): (str(c["team"]["id"]), c["team"].get("abbreviation", "")) for c in comps if c.get("team")}
    home, away = side.get("home", ("", "")), side.get("away", ("", ""))
    plays = []
    for q in summary.get("plays") or []:
        parts = q.get("participants") or []
        flat = {"team.id": str((q.get("team") or {}).get("id", "") or ""), "homeTeamId": home[0], "awayTeamId": away[0],
                "homeTeamAbbrev": home[1], "awayTeamAbbrev": away[1],
                "type.text": (q.get("type") or {}).get("text", ""), "period.number": (q.get("period") or {}).get("number", 0),
                "clock.displayValue": (q.get("clock") or {}).get("displayValue", ""),
                "scoringPlay": bool(q.get("scoringPlay")), "shootingPlay": bool(q.get("shootingPlay")),
                "coordinate.x": (q.get("coordinate") or {}).get("x"), "coordinate.y": (q.get("coordinate") or {}).get("y")}
        for i, a in enumerate(parts[:2]):
            flat[f"participants.{i}.athlete.id"] = str((a.get("athlete") or {}).get("id", "") or "")
        plays.append(flat)
    return {"plays": plays, "boxscore": summary.get("boxscore") or {}}


def _summary_pbp(game_id: str) -> dict:
    try:
        import requests
        r = requests.get(f"https://site.web.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={game_id}",
                         timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        return flatten_summary(r.json()) if r.ok else {}
    except Exception:  # noqa: BLE001
        return {}


def get_pbp(game_id: str) -> list[dict]:
    """Play-by-play + player names from ESPN."""
    time.sleep(SLEEP)
    try:
        pbp = espn_nba_pbp(game_id=int(game_id))
    except Exception as e:
        print(f"  ESPN PBP error for {game_id}: {e}")
        return []
    plays = pbp.get('plays', [])
    if not plays:                                # the library's feed can come back empty: ESPN's summary has them
        pbp = _summary_pbp(game_id) or pbp
        plays = pbp.get('plays', [])

    # Map player ID -> name from boxscore
    names = {}
    for team in pbp.get('boxscore', {}).get('players', []):
        for stat in team.get('statistics', []):
            for a in stat.get('athletes', []):
                athlete = a.get('athlete', {})
                pid = str(athlete.get('id', ''))
                name = athlete.get('displayName', '')
                if pid and name:
                    names[pid] = name

    actions = []
    for p in plays:
        tid = str(p.get('team.id', '') or '')
        if tid and tid == str(p.get('homeTeamId', '')):
            team = p.get('homeTeamAbbrev', '')
        elif tid and tid == str(p.get('awayTeamId', '')):
            team = p.get('awayTeamAbbrev', '')
        else:
            team = ''                     # unknown: never guess the team (see first_basket)
        pid = str(p.get('participants.0.athlete.id', '') or '')
        action = {
            'gameId': game_id,
            'personId': pid,
            'playerName': names.get(pid, ''),
            'teamTricode': team,
            'actionType': p.get('type.text', '').lower().replace(' ', '_'),
            'shotType': p.get('type.text', ''),
            'period': int(p.get('period.number', 0) or 0),
            'clock': str(p.get('clock.displayValue', '')),
            'scoringPlay': p.get('scoringPlay', False),
            'shootingPlay': bool(p.get('shootingPlay', False)),
        }
        # For jumpball, also store loser participant
        if p.get('type.text', '').lower() == 'jumpball':
            loser_id = str(p.get('participants.1.athlete.id', '') or '')
            action['tip_loser_player_id'] = loser_id
            action['tip_loser_player_name'] = names.get(loser_id, '')
        actions.append(action)
    return actions

def first_basket(actions: list[dict]) -> Optional[dict]:
    """First points of the game in Q1-Q4 — a made field goal OR a made free throw (sportsbook "first basket"
    rule, owner's decision 2026-11-17). If that play has no scorer or no team, return None:
    better no label (the game is retried by the next sync) than a wrong one."""
    for a in actions:
        if a['scoringPlay'] and 1 <= a['period'] <= 4:
            return a if a.get('personId') and a.get('teamTricode') else None
    return None

def first_shots(actions: list[dict]) -> dict:
    """Each team's first field-goal attempt of the game (made or missed; free throws excluded), Q1 only.
    -> {team code: action}. Plays without a team or a player are skipped."""
    out = {}
    for a in actions:
        if (a.get('shootingPlay') and 'free_throw' not in a['actionType'] and a['period'] == 1
                and a.get('teamTricode') and a.get('personId') and a['teamTricode'] not in out):
            out[a['teamTricode']] = a
    return out


def early_shots(actions: list[dict], n: int = 5) -> dict:
    """Each team's first `n` field-goal attempts of the game, in order (Q1; free throws excluded).
    -> {team code: [action, ...]}. Plays without a team or a player are skipped."""
    out = {}
    for a in actions:
        if (a.get('shootingPlay') and 'free_throw' not in a['actionType'] and a['period'] == 1
                and a.get('teamTricode') and a.get('personId')):
            lst = out.setdefault(a['teamTricode'], [])
            if len(lst) < n:
                lst.append(a)
    return out


EARLY_SHOTS_SCHEMA = """CREATE TABLE IF NOT EXISTS early_shots (
    game_id TEXT, team TEXT, seq INTEGER, player_id TEXT, player_name TEXT, made INTEGER,
    PRIMARY KEY (game_id, team, seq))"""


def store_early_shots(con, game_id, actions):
    con.execute(EARLY_SHOTS_SCHEMA)
    for team, lst in early_shots(actions).items():
        for seq, a in enumerate(lst, 1):
            con.execute("INSERT OR REPLACE INTO early_shots VALUES (?,?,?,?,?,?)",
                        (str(game_id), team, seq, a['personId'], a['playerName'], int(bool(a['scoringPlay']))))


FIRST_SHOTS_SCHEMA = """CREATE TABLE IF NOT EXISTS first_shots (
    game_id TEXT, team TEXT, player_id TEXT, player_name TEXT, made INTEGER, shot_type TEXT,
    PRIMARY KEY (game_id, team))"""


def tip_jumpball(actions: list[dict]) -> Optional[dict]:
    """Extract both jumpball participants: winner + loser from transformed PBP.
    ESPN sometimes lists an empty jump-ball entry first (no player, no team; e.g. MIA @ DAL 12/03/25) followed by the
    real one: entries without a player are skipped."""
    for a in actions:
        if a.get('actionType') == 'jumpball' and (a.get('personId') or a.get('playerName')):
            winner_id = a.get('personId', '')
            winner_name = a.get('playerName', '')
            loser_id = a.get('tip_loser_player_id', '')
            loser_name = a.get('tip_loser_player_name', '')
            team = a.get('teamTricode') or None      # unknown team -> stored as NULL, never guessed
            return {
                'tip_winner_team': team,
                'tip_winner_player_id': winner_id,
                'tip_winner_player_name': winner_name,
                'tip_loser_player_id': loser_id,
                'tip_loser_player_name': loser_name,
            }
    return None

# ──────────────────────────────────────────────
# INGESTION
# ──────────────────────────────────────────────
def nba_com_backup(game_id: str, con) -> list:
    """ESPN has no plays for this game: if it's a play-in/playoff game, read it from NBA.com instead (nba_com.py),
    storing its starters too. Returns the actions ([] if not possible)."""
    if str(game_id) not in postseason_ids(con):
        return []
    row = con.execute("SELECT game_date, home_team, away_team FROM games WHERE game_id = ?", (str(game_id),)).fetchone()
    if not row:
        return []
    import nba_com
    d, h, a = row
    nid = nba_com.playoff_game_id(int(d[:4]) - 1, d, h, a)
    g = nba_com.game(game_id, nid, con) if nid else None
    if not g or g["unmatched"]:
        return []                                  # a player we can't match to ESPN: better no label than a wrong one
    st.save_lineups(con, g["lineups"])
    con.execute("UPDATE games SET home_score = ?, away_score = ? WHERE game_id = ?",
                (g["home_score"], g["away_score"], str(game_id)))
    print(f"  🔁 {a} @ {h} {d}: read from NBA.com (ESPN has no play-by-play)")
    return g["actions"]


def ingest_game(game_id: str, con):
    """Fetch PBP, extract first basket + tip, store."""
    actions = get_pbp(game_id) or nba_com_backup(game_id, con)
    if not actions:
        return

    fb = first_basket(actions)
    tip = tip_jumpball(actions)
    con.execute(FIRST_SHOTS_SCHEMA)
    for team, a in first_shots(actions).items():
        con.execute("INSERT OR REPLACE INTO first_shots VALUES (?,?,?,?,?,?)",
                    (str(game_id), team, a['personId'], a['playerName'], int(bool(a['scoringPlay'])), a['shotType']))
    store_early_shots(con, game_id, actions)

    if fb:
        con.execute("""
            INSERT OR REPLACE INTO first_baskets
            (game_id, scorer_player_id, scorer_name, scorer_team, shot_type, period, clock, x, y)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (fb['gameId'], fb['personId'], fb['playerName'],
              fb['teamTricode'], fb['shotType'], fb['period'], fb['clock'],
              fb.get('x'), fb.get('y')))

    if tip:
        con.execute("""UPDATE games SET
            tip_winner_team=?, tip_winner_player_id=?, tip_winner_player_name=?,
            tip_loser_player_id=?, tip_loser_player_name=?
            WHERE game_id=?""",
                    (tip["tip_winner_team"], tip["tip_winner_player_id"], tip["tip_winner_player_name"],
                     tip["tip_loser_player_id"], tip["tip_loser_player_name"], game_id))

def backfill(season_year: int = 2024, max_days: int = None):
    """Backfill a season (regular season only, no preseason). Also writes CSV."""
    con = db()
    # Regular season start dates (preseason excluded)
    regular_season_starts = {
        2024: date(2024, 10, 22),  # 2024-25
        2025: date(2025, 10, 21),  # 2025-26
        2026: date(2026, 10, 20),  # 2026-27
    }
    start = regular_season_starts.get(season_year, date(season_year, 10, 21))
    end = date(season_year + 1, 6, 30)
    days = []
    d = start
    while d <= end:
        days.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    if max_days:
        days = days[:max_days]

    # CSV setup (append mode, write header if new)
    csv_path = "first_basket_data.csv"
    csv_exists = os.path.exists(csv_path)
    csv_file = open(csv_path, "a", newline="")
    csv_writer = csv.writer(csv_file)
    if not csv_exists:
        csv_writer.writerow(["game_start", "game_id", "home_team", "away_team", "tip_winner", "tip_winner_player", "tip_winner_player_name", "tip_loser_player", "tip_loser_player_name", "first_basket_scorer",
                             "scorer_team", "shot_type", "clock"])

    print(f"Backfilling {len(days)} days (regular season only)...")
    total = 0
    for i, day in enumerate(days):
        try:
            df = espn_nba_scoreboard(dates=[day], return_as_pandas=True)
            if df is None or len(df) == 0:
                continue
            # Only regular season games (season_type == 2), completed
            games_df = df[_played(df)]

            for _, r in games_df.iterrows():
                gid = str(r['game_id'])
                home = str(r['home_abbreviation'])
                away = str(r['away_abbreviation'])
                # Skip non-NBA games (All-Star, Rising Stars, etc.)
                if home not in NBA_TEAMS or away not in NBA_TEAMS:
                    continue
                print(f"  {day[:10]}: {away} @ {home} ({gid})")
                # Store game info (retry on lock)
                for attempt in range(3):
                    try:
                        con.execute("""
                            INSERT OR IGNORE INTO games (game_id, game_date, home_team, away_team, status)
                            VALUES (?, ?, ?, ?, 3)
                        """, (gid, day[:10], home, away))
                        ingest_game(gid, con)
                        # Write to CSV
                        fb = con.execute("SELECT * FROM first_baskets WHERE game_id=?", (gid,)).fetchone()
                        tip = con.execute("SELECT tip_winner_team, tip_winner_player_id, tip_winner_player_name, tip_loser_player_id, tip_loser_player_name FROM games WHERE game_id=?", (gid,)).fetchone()
                        if fb:
                            # Convert YYYYMMDD -> MM/DD/YY
                            d = day[:10]
                            game_start = f"{d[4:6]}/{d[6:8]}/{d[2:4]}"
                            csv_writer.writerow([
                                game_start,
                                gid,                          # game_id
                                home,                         # home_team
                                away,                         # away_team
                                tip[0] if tip else '',        # tip_winner (team)
                                tip[1] if tip else '',        # tip_winner_player (id)
                                tip[2] if tip else '',        # tip_winner_player_name
                                tip[3] if tip else '',        # tip_loser_player (id)
                                tip[4] if tip else '',        # tip_loser_player_name
                                fb[2],                        # first_basket_scorer (scorer_name)
                                fb[3],                        # scorer_team
                                fb[4],                        # shot_type
                                fb[6],                        # clock
                            ])
                            csv_file.flush()
                        total += 1
                        con.commit()
                        break
                    except sqlite3.OperationalError as e:
                        if "locked" in str(e) and attempt < 2:
                            time.sleep(0.5 * (attempt + 1))
                            continue
                        raise
        except Exception as e:
            print(f"  Error on {day}: {e}")
        if i % 10 == 0:
            print(f"  Day {i+1}/{len(days)} — {total} games done")
    csv_file.close()
    print(f"✅ Done: {total} regular season games (also saved to first_basket_data.csv)")

# ──────────────────────────────────────────────
# AUTO-CATCHUP
# ──────────────────────────────────────────────
def _played(df):
    """Rows of an ESPN scoreboard that are regular-season games played to a final score. ESPN also marks postponed
    and cancelled games as "post" (over), so "completed" must be true too (2026-11-19: 4 postponed games had been
    stored as finished)."""
    completed = df['status_type_completed'].fillna(False).astype(bool) if 'status_type_completed' in df else True
    return (df['status_type_state'] == 'post') & df['season_type'].isin(PLAYED_TYPES) & completed


PLAYED_TYPES = (2, 3, 5)               # ESPN season types: 2 regular season, 3 playoffs (+ Finals), 5 play-in
GAME_TYPES_SCHEMA = "CREATE TABLE IF NOT EXISTS game_types (game_id TEXT PRIMARY KEY, season_type INTEGER)"


def note_type(con, gid, season_type) -> None:
    """Remember whether a game was regular season (2), play-in (5) or playoffs (3): training leaves the last two out."""
    con.execute(GAME_TYPES_SCHEMA)
    con.execute("INSERT OR REPLACE INTO game_types VALUES (?, ?)", (str(gid), int(season_type)))


def postseason_ids(con) -> set:
    con.execute(GAME_TYPES_SCHEMA)
    return {g for (g,) in con.execute("SELECT game_id FROM game_types WHERE season_type IN (3, 5)")}


GAP_DROP = {"STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_CANCELLED", "STATUS_FORFEIT", "STATUS_SUSPENDED"}


def gap_action(espn_found: bool, status_name: str, completed: bool) -> str:
    """Pure (testable). A stored game with no first basket: what does ESPN say now?
    -> "drop" (ESPN says postponed / cancelled — its makeup game has its own id),
       "fill" (played: fetch its play-by-play again — from NBA.com if ESPN has none) or "wait".
    ESPN not answering about a game is NOT proof it wasn't played (2026-11-19: four real 2026 playoff games, which
    ESPN's summary answers "not found" for, were wrongly removed and then restored) — so that means "fill"."""
    if status_name in GAP_DROP:
        return "drop"
    return "fill" if completed or not espn_found else "wait"


def repair_old_gaps(con, recheck_days: int = 7) -> dict:
    """Games older than 14 days still without a first basket (the recent ones are retried in sync_data): ask ESPN
    again. Played -> fill in first basket, tip and lineups; never played -> remove the row. Each game is asked at
    most once a week (table gap_checks), so a game ESPN truly has no play-by-play for costs one request a week."""
    import requests
    con.execute("CREATE TABLE IF NOT EXISTS gap_checks (game_id TEXT PRIMARY KEY, checked TEXT)")
    old = (date.today() - timedelta(days=14)).strftime("%Y%m%d")
    since = (date.today() - timedelta(days=recheck_days)).isoformat()
    gaps = [g for (g,) in con.execute(
        """SELECT game_id FROM games WHERE game_date < ? AND game_id NOT IN (SELECT game_id FROM first_baskets)
           AND game_id NOT IN (SELECT game_id FROM gap_checks WHERE checked >= ?)""", (old, since))]
    done = {"filled": 0, "dropped": 0, "still_missing": 0}
    for gid in gaps:
        try:
            r = requests.get(f"https://site.web.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={gid}",
                             timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code not in (200, 404):
                continue                                         # ESPN busy: try next time
            comp = ((r.json().get("header") or {}).get("competitions") or [{}])[0] if r.status_code == 200 else {}
        except Exception:  # noqa: BLE001 — offline: try next time
            continue
        status = (comp.get("status") or {}).get("type") or {}
        action = gap_action(bool(comp), status.get("name", ""), bool(status.get("completed")))
        if action == "drop":
            for table in ("lineups", "first_shots", "early_shots", "games"):
                con.execute(f"DELETE FROM {table} WHERE game_id = ?", (gid,))
            done["dropped"] += 1
        elif action == "fill":
            ingest_game(gid, con)
            st.store_game_lineup(con, gid)
            got = con.execute("SELECT 1 FROM first_baskets WHERE game_id = ?", (gid,)).fetchone()
            done["filled" if got else "still_missing"] += 1
        con.execute("INSERT OR REPLACE INTO gap_checks VALUES (?, ?)", (gid, date.today().isoformat()))
        con.commit()
        time.sleep(SLEEP)
    if done["filled"] or done["dropped"]:
        print(f"  🩹 Old gaps: {done['filled']} filled in, {done['dropped']} never-played games removed")
    return done


SEASONS_TO_AUDIT = {"2025-26": ("20251021", "20260613")}    # first day .. Finals: checked once, every day


def audit_season(con, key: str, start: str, end: str) -> int:
    """Once per season: compare every day's ESPN schedule with the database and file any finished game that's missing
    (regular season, play-in, playoffs, Finals). Recorded in table audits. Returns how many games were added."""
    con.execute("CREATE TABLE IF NOT EXISTS audits (key TEXT PRIMARY KEY, done TEXT)")
    if con.execute("SELECT 1 FROM audits WHERE key = ?", (key,)).fetchone():
        return 0
    have = {g for (g,) in con.execute("SELECT game_id FROM games")}
    d, last = date.fromisoformat(f"{start[:4]}-{start[4:6]}-{start[6:]}"), f"{end[:4]}-{end[4:6]}-{end[6:]}"
    added, failed = 0, 0
    while d.isoformat() <= last:
        day = d.strftime("%Y%m%d")
        try:
            df = espn_nba_scoreboard(dates=[day], return_as_pandas=True)
        except Exception:  # noqa: BLE001
            df, failed = None, failed + 1
        if df is not None and len(df):
            for _, r in df[_played(df)].iterrows():
                gid, home, away = str(r['game_id']), str(r['home_abbreviation']), str(r['away_abbreviation'])
                if home not in NBA_TEAMS or away not in NBA_TEAMS:
                    continue
                note_type(con, gid, r['season_type'])
                if gid in have:
                    continue
                con.execute("INSERT OR IGNORE INTO games (game_id, game_date, home_team, away_team, status) "
                            "VALUES (?, ?, ?, ?, 3)", (gid, day, home, away))
                ingest_game(gid, con)
                if not con.execute("SELECT 1 FROM lineups WHERE game_id = ?", (gid,)).fetchone():
                    st.store_game_lineup(con, gid)
                added += 1
            con.commit()
        d += timedelta(days=1)
        time.sleep(SLEEP)
    if not failed:                                  # a day ESPN didn't answer: check the whole season again next time
        con.execute("INSERT OR REPLACE INTO audits VALUES (?, ?)", (key, date.today().isoformat()))
    con.commit()
    if added:
        print(f"  🩹 {key}: {added} missing game{'s' * (added != 1)} filed")
    return added


def catchup_missing_games():
    """Fetch games from the last stored DB date → yesterday. The last stored date is re-checked
    (games still in progress when it was stored); games already stored with a label are skipped."""
    con = db()
    row = con.execute("SELECT MAX(game_date) FROM games").fetchone()
    if not row or not row[0]:
        return 0
    last_date = date.fromisoformat(row[0].replace('/', '-')[:4] + '-' + row[0][4:6] + '-' + row[0][6:8])
    yesterday = date.today() - timedelta(days=1)
    if last_date > yesterday:
        return 0
    have = {r[0] for r in con.execute("SELECT game_id FROM first_baskets")}

    print(f"🔄 Catching up {last_date} → {yesterday}")
    days = []
    d = last_date
    while d <= yesterday:
        days.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)

    total = 0
    for day in days:
        try:
            df = espn_nba_scoreboard(dates=[day], return_as_pandas=True)
            if df is None or len(df) == 0:
                continue
            games_df = df[_played(df)]
            for _, r in games_df.iterrows():
                gid = str(r['game_id'])
                home = str(r['home_abbreviation'])
                away = str(r['away_abbreviation'])
                if home not in NBA_TEAMS or away not in NBA_TEAMS or gid in have:
                    continue
                con.execute("INSERT OR IGNORE INTO games (game_id, game_date, home_team, away_team, status) VALUES (?, ?, ?, ?, 3)", (gid, day[:10], home, away))
                note_type(con, gid, r['season_type'])
                ingest_game(gid, con)
                st.store_game_lineup(con, gid)
                total += 1
            con.commit()
        except Exception as e:
            print(f"  Error on {day}: {e}")
            traceback.print_exc()
    if total:
        print(f"  ✅ Caught up {total} games")
    return total


def file_finished_games(day: str) -> int:
    """File the results of `day`'s games that are over (first basket, tip, lineups) and refresh the CSV and
    my_predictions.xlsx, so the Data tabs show tonight's results the same night. Returns how many were added.
    Called only after the day's last tip-off (the model retrains on new results; picks must not change mid-evening)."""
    con = db()
    have = {r[0] for r in con.execute("SELECT game_id FROM first_baskets")}
    df = espn_nba_scoreboard(dates=[day], return_as_pandas=True)
    added = 0
    if df is not None and len(df):
        done = df[_played(df)]
        for _, r in done.iterrows():
            gid, home, away = str(r['game_id']), str(r['home_abbreviation']), str(r['away_abbreviation'])
            if home not in NBA_TEAMS or away not in NBA_TEAMS or gid in have:
                continue
            con.execute("INSERT OR IGNORE INTO games (game_id, game_date, home_team, away_team, status) "
                        "VALUES (?, ?, ?, ?, 3)", (gid, day, home, away))
            con.execute("UPDATE games SET status = 3 WHERE game_id = ?", (gid,))
            note_type(con, gid, r['season_type'])
            ingest_game(gid, con)
            st.store_game_lineup(con, gid)
            added += 1
        con.commit()
    if added:
        export_csv(con)
        picks.export_predictions(con)
    return added


CSV_PATH = "first_basket_data.csv"

CSV_HEADER = ["game_start", "game_id", "home_team", "away_team", "tip_winner", "tip_winner_player",
              "tip_winner_player_name", "tip_loser_player", "tip_loser_player_name", "first_basket_scorer",
              "scorer_team", "shot_type", "clock"]


def csv_rows(con) -> list:
    """Every finished game with its tip-off and first basket (oldest first), as the rows of first_basket_data.csv
    (columns: CSV_HEADER). Also shown in the GUI's Data view ("All games" tab)."""
    rows = con.execute("""
        SELECT g.game_date, g.game_id, g.home_team, g.away_team,
               g.tip_winner_team, g.tip_winner_player_id, g.tip_winner_player_name,
               g.tip_loser_player_id, g.tip_loser_player_name,
               fb.scorer_name, fb.scorer_team, fb.shot_type, fb.clock
        FROM games g
        JOIN first_baskets fb ON g.game_id = fb.game_id
        ORDER BY g.game_date
    """).fetchall()
    out = []
    for (game_date, game_id, home_team, away_team, tip_team, tip_wid, tip_wname, tip_lid, tip_lname,
         scorer_name, scorer_team, shot_type, clock) in rows:
        d = game_date.replace('-', '')
        out.append([f"{d[4:6]}/{d[6:8]}/{d[2:4]}", game_id, home_team, away_team, tip_team or '', tip_wid or '',
                    tip_wname or '', tip_lid or '', tip_lname or '', scorer_name, scorer_team, shot_type, clock])
    return out


def export_csv(con):
    """Rewrite the CSV from the database with all current data including tip winner player name."""
    rows = csv_rows(con)
    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        writer.writerows(rows)
    return len(rows)


def backfill_tip_players():
    """Targeted backfill: only fetch PBP for games that have a first_basket but are missing tip_winner_player_id.
    Much faster than full season backfill — no re-ingest, just the missing column.
    Also fills tip_winner_player_name where ID exists but name is NULL.
    Also fills tip_loser_player_id and tip_loser_player_name."""
    con = db()
    # Find games with first_basket but NULL tip_winner_player_id
    rows = con.execute("""
        SELECT g.game_id, g.game_date, g.home_team, g.away_team
        FROM games g
        JOIN first_baskets fb ON g.game_id = fb.game_id
        WHERE g.tip_winner_player_id IS NULL
        ORDER BY g.game_date
    """).fetchall()

    # Also find games that have tip_winner_player_id but NULL tip_winner_player_name
    name_rows = con.execute("""
        SELECT g.game_id, g.game_date, g.home_team, g.away_team
        FROM games g
        JOIN first_baskets fb ON g.game_id = fb.game_id
        WHERE g.tip_winner_player_id IS NOT NULL
          AND g.tip_winner_player_name IS NULL
        ORDER BY g.game_date
    """).fetchall()

    # Also find games missing tip_loser_player_id
    loser_rows = con.execute("""
        SELECT g.game_id, g.game_date, g.home_team, g.away_team
        FROM games g
        JOIN first_baskets fb ON g.game_id = fb.game_id
        WHERE g.tip_loser_player_id IS NULL
        ORDER BY g.game_date
    """).fetchall()

    if not rows and not name_rows and not loser_rows:
        print("✅ All games already have tip_winner_player_id, tip_winner_player_name, tip_loser_player_id, tip_loser_player_name")
        return

    if rows:
        print(f"🔄 Backfilling tip_winner_player_id for {len(rows)} games...")
        total = 0
        for i, (game_id, game_date, home_team, away_team) in enumerate(rows):
            print(f"  [{i+1}/{len(rows)}] {game_date}: {away_team} @ {home_team} ({game_id})")
            actions = get_pbp(game_id)
            if actions:
                tip = tip_jumpball(actions)
                if tip:
                    con.execute("""UPDATE games SET
                        tip_winner_team=?, tip_winner_player_id=?, tip_winner_player_name=?,
                        tip_loser_player_id=?, tip_loser_player_name=?
                        WHERE game_id=?""",
                            (tip["tip_winner_team"], tip["tip_winner_player_id"], tip["tip_winner_player_name"],
                             tip["tip_loser_player_id"], tip["tip_loser_player_name"], game_id))
                    total += 1
            con.commit()
            export_csv(con)
            print(f"    📄 CSV updated ({i+1} games processed)")
        con.commit()

    if name_rows:
        print(f"🔄 Backfilling tip_winner_player_name for {len(name_rows)} games (have ID but missing name)...")
        for i, (game_id, game_date, home_team, away_team) in enumerate(name_rows):
            print(f"  [{i+1}/{len(name_rows)}] {game_date}: {away_team} @ {home_team} ({game_id})")
            actions = get_pbp(game_id)
            if actions:
                tip = tip_jumpball(actions)
                if tip:
                    con.execute("""UPDATE games SET
                        tip_winner_team=?, tip_winner_player_id=?, tip_winner_player_name=?,
                        tip_loser_player_id=?, tip_loser_player_name=?
                        WHERE game_id=?""",
                        (tip["tip_winner_team"], tip["tip_winner_player_id"], tip["tip_winner_player_name"],
                         tip["tip_loser_player_id"], tip["tip_loser_player_name"], game_id))
            con.commit()
            export_csv(con)
            print(f"    📄 CSV updated ({i+1} games processed)")
        con.commit()

    if loser_rows:
        print(f"🔄 Backfilling tip_loser_player_id for {len(loser_rows)} games...")
        for i, (game_id, game_date, home_team, away_team) in enumerate(loser_rows):
            print(f"  [{i+1}/{len(loser_rows)}] {game_date}: {away_team} @ {home_team} ({game_id})")
            actions = get_pbp(game_id)
            if actions:
                tip = tip_jumpball(actions)
                if tip:
                    con.execute("""UPDATE games SET
                        tip_winner_team=?, tip_winner_player_id=?, tip_winner_player_name=?,
                        tip_loser_player_id=?, tip_loser_player_name=?
                        WHERE game_id=?""",
                            (tip["tip_winner_team"], tip["tip_winner_player_id"], tip["tip_winner_player_name"],
                             tip["tip_loser_player_id"], tip["tip_loser_player_name"], game_id))
            con.commit()
            export_csv(con)
            print(f"    📄 CSV updated ({i+1} games processed)")
        con.commit()
    # Final CSV rewrite
    n = export_csv(con)
    print(f"✅ Done: {CSV_PATH} rewritten ({n} rows)")


# ──────────────────────────────────────────────
# PREDICTION
# ──────────────────────────────────────────────
_SEEN: dict = {}   # game_id -> (data_source, starter ids)   [watch mode]
_DONE: set = set()  # games already predicted from a CONFIRMED lineup


def _retrain_if_needed(con) -> str:
    """If the DB has labelled games that are not in the training matrices yet, add them
    (build_features --incremental) and warm-start retrain (train_pregame --incremental).
    game_ids.json (the games already in the matrices) is the save point, so nothing is rebuilt from scratch.
    Returns a short status for the screen."""
    import subprocess
    try:
        with open("game_ids.json") as f:
            in_training = {str(g) for g in json.load(f)}
    except (OSError, ValueError):
        print("⚠️ game_ids.json missing — run build_features.py once to create the training matrices")
        return "model not updated"
    # Every labelled game not in the training data yet — including ones labelled late with an older date
    labelled = {r[0] for r in con.execute("SELECT game_id FROM first_baskets f JOIN games g USING(game_id)")}
    new_games = len(labelled - in_training)
    if not new_games:
        return "model up to date"
    for script in ("build_features.py", "train_pregame.py"):
        r = subprocess.run([sys.executable, script, "--incremental"], capture_output=True, text=True)
        if script == "train_pregame.py" and r.returncode == 3:     # new model tested worse: old one kept
            print(f"⚠️ {r.stdout.strip().splitlines()[-1]}")
            return f"kept the existing model ({new_games} new game{'s' * (new_games != 1)} added to the data)"
        if r.returncode != 0:
            last = (r.stderr or r.stdout).strip().splitlines()[-1:] or [""]
            print(f"⚠️ {script} failed — predicting with the existing model ({last[0]})")
            return "model update FAILED"
    return f"model updated with {new_games} new game{'s' * (new_games != 1)}"


def sync_data() -> str:
    """Bring everything up to date before predicting, continuing from what's already saved:
    new games + first baskets + tip + lineups, retries for recent gaps, CSV, player logs, model.
    Returns a one-line summary."""
    con = db()
    new = catchup_missing_games()

    # Retry recent games that are missing a first basket or a lineup (older misses are permanent)
    recent = (date.today() - timedelta(days=14)).strftime("%Y%m%d")
    con.execute(FIRST_SHOTS_SCHEMA)
    con.execute(EARLY_SHOTS_SCHEMA)
    for (gid,) in con.execute("""SELECT game_id FROM games WHERE game_date >= ?
                                 AND (game_id NOT IN (SELECT game_id FROM first_baskets)
                                      OR game_id NOT IN (SELECT game_id FROM first_shots)
                                      OR game_id NOT IN (SELECT game_id FROM early_shots))""", (recent,)).fetchall():
        ingest_game(gid, con)
    for (gid,) in con.execute("""SELECT game_id FROM games WHERE game_date >= ?
                                 AND game_id NOT IN (SELECT DISTINCT game_id FROM lineups)""", (recent,)).fetchall():
        st.store_game_lineup(con, gid)
    for key, (a, b) in SEASONS_TO_AUDIT.items():  # whole past seasons vs ESPN's schedule (once each)
        audit_season(con, key, a, b)
    repair_old_gaps(con)                       # older games still missing a first basket (once a week each)
    try:                                       # last season's re-created picks for the "overall" record (once)
        import season_backtest
        season_backtest.fill_last_season(con)
    except Exception as e:  # noqa: BLE001 — never block the update over the record
        print(f"  ⚠️ last season's record not filled in: {e}")
    # NBA.com's official starters + positions for every day already played (teaches the tip-off model
    # who usually jumps); each day is fetched once
    today = date.today().strftime("%Y%m%d")
    played = {r[0] for r in con.execute("SELECT DISTINCT game_date FROM games WHERE game_date < ?", (today,))}
    # ...and re-check days in the last 30 whose lineup file was empty (a failed download, or NBA.com posting late).
    # Not older: a day NBA.com never filled in (11/02/26 has no starters at all) would be re-asked forever.
    retry_from = (date.today() - timedelta(days=30)).strftime("%Y%m%d")
    empty = {d for (d,) in con.execute("SELECT game_date FROM official_lineup_days WHERE players = 0 AND game_date >= ?",
                                       (retry_from,))}
    for d in sorted((played - st.official_lineup_days(con)) | (empty & played)):
        st.store_official_lineups(con, d)
        time.sleep(SLEEP)
    # First baskets with no matching game (preseason / All-Star) are never used
    con.execute("DELETE FROM first_baskets WHERE game_id NOT IN (SELECT game_id FROM games)")
    con.commit()

    n = export_csv(con)
    picks.export_predictions(con)              # fills in yesterday's results in my_predictions.xlsx
    bf.build_player_game_logs()
    model = _retrain_if_needed(con)
    last = con.execute("SELECT MAX(game_date) FROM games g JOIN first_baskets USING(game_id)").fetchone()[0] or ""
    last = f"{int(last[4:6])}/{int(last[6:8])}/{last[:4]}" if last else "?"
    added = f" (+{new} new)" if new else ""
    return f"Data up to date: {n:,} games through {last}{added} · files saved · {model}"


import contextlib
import io


@contextlib.contextmanager
def _quiet(warnings_out: list):
    """Hide progress chatter from data steps; keep warning/error lines to show at the end."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            yield
    finally:
        warnings_out += [l.strip() for l in buf.getvalue().splitlines()
                         if any(k in l for k in ("⚠️", "failed", "Error", "error"))]


TEAM_ALIASES = {"NYK": "NY", "GSW": "GS", "SAS": "SA", "UTA": "UTAH", "NOP": "NO", "WAS": "WSH", "BRK": "BKN",
                "PHO": "PHX", "NOH": "NO"}
TEAM_NAMES = {   # nickname (lower case) -> ESPN code; several spellings allowed
    "hawks": "ATL", "celtics": "BOS", "nets": "BKN", "hornets": "CHA", "bulls": "CHI", "cavaliers": "CLE",
    "cavs": "CLE", "mavericks": "DAL", "mavs": "DAL", "nuggets": "DEN", "pistons": "DET", "warriors": "GS",
    "rockets": "HOU", "pacers": "IND", "clippers": "LAC", "lakers": "LAL", "grizzlies": "MEM", "heat": "MIA",
    "bucks": "MIL", "timberwolves": "MIN", "wolves": "MIN", "pelicans": "NO", "knicks": "NY", "thunder": "OKC",
    "magic": "ORL", "76ers": "PHI", "sixers": "PHI", "suns": "PHX", "trail blazers": "POR", "blazers": "POR",
    "trailblazers": "POR", "kings": "SAC", "spurs": "SA", "raptors": "TOR", "jazz": "UTAH", "wizards": "WSH",
}


def parse_teams(args) -> set:
    """Teams named on the command line or typed at the prompt -> ESPN codes. Accepts nicknames ("Knicks",
    "trail blazers", "sixers"), ESPN codes ("LAL") and NBA.com codes ("NYK"), in any case."""
    import re
    args = [a for a in args if not a.startswith("--")]     # flags aren't teams ('--no-odds' is not the Pelicans)
    text = " " + re.sub(r"[^a-z0-9 ]", " ", " ".join(args).lower()) + " "
    out = set()
    for name, code in TEAM_NAMES.items():
        if f" {name} " in text:
            out.add(code)
    for word in text.split():
        code = TEAM_ALIASES.get(word.upper(), word.upper())
        if code in NBA_TEAMS:
            out.add(code)
    return out


TEAM_LABEL = {   # ESPN code -> name shown in the "which game?" list
    "ATL": "Hawks", "BOS": "Celtics", "BKN": "Nets", "CHA": "Hornets", "CHI": "Bulls", "CLE": "Cavaliers",
    "DAL": "Mavericks", "DEN": "Nuggets", "DET": "Pistons", "GS": "Warriors", "HOU": "Rockets", "IND": "Pacers",
    "LAC": "Clippers", "LAL": "Lakers", "MEM": "Grizzlies", "MIA": "Heat", "MIL": "Bucks", "MIN": "Timberwolves",
    "NO": "Pelicans", "NY": "Knicks", "OKC": "Thunder", "ORL": "Magic", "PHI": "76ers", "PHX": "Suns",
    "POR": "Trail Blazers", "SAC": "Kings", "SA": "Spurs", "TOR": "Raptors", "UTAH": "Jazz", "WSH": "Wizards",
}


def ask_for_teams(games: list) -> set:
    """Show today's games as a numbered list and ask which one to predict. The answer can be the game's number,
    a team name (Knicks) or nothing (Enter = all games). Asks again, with a hint, if the answer doesn't match."""
    name = lambda code: TEAM_LABEL.get(code, code)
    print("\nToday's games:\n")
    for n, g in enumerate(games, 1):
        print(f"  {n}.  {name(g['away_team'])} @ {name(g['home_team'])}   {g.get('start', '')}")
    print("\nWhich game do you want? You can type:")
    g1 = games[0]
    print(f"  • the number next to the game   (for example: 1 = {name(g1['away_team'])} @ {name(g1['home_team'])})")
    print(f"  • a team name                   (for example: {name(g1['home_team'])})")
    print("  • nothing — just press Enter to see ALL games")
    playing = {g[k] for g in games for k in ("home_team", "away_team")}
    while True:
        try:
            answer = input("\nYour choice: ").strip()
        except (EOFError, KeyboardInterrupt):          # Ctrl-D / Ctrl-C at the question: all games
            print()
            return set()
        if not answer:
            return set()
        if answer.isdigit():
            if 1 <= int(answer) <= len(games):
                g = games[int(answer) - 1]
                return {g["home_team"], g["away_team"]}
            print(f"  There's no game {answer} — pick a number from 1 to {len(games)}.")
            continue
        teams = parse_teams([answer])
        if teams & playing:
            return teams & playing
        print("  " + (f"{', '.join(name(t) for t in sorted(teams))} not playing today — pick a game from the list."
                      if teams else f"Didn't recognise \"{answer}\" — type a number from 1 to {len(games)}, "
                                    "or a team name like Lakers."))


def filter_games(games: list, teams: set) -> list:
    """Only the games involving `teams` (all games when `teams` is empty)."""
    return [g for g in games if not teams or g['home_team'] in teams or g['away_team'] in teams]


def predict_today(first_pass: bool = True, teams: set = frozenset(), ui=None) -> int:
    """Predict the first basket for today's games that haven't started (only `teams`' games if given).
    Returns the number of games still waiting on a confirmed lineup (used by --watch).
    `ui` receives the output (header, schedule, game_card, skipped, notes, legend, console.status):
    the terminal display by default; gui.py passes a collector instead."""
    if ui is None:
        import display as ui
    confirmed_only = '--confirmed-only' in sys.argv
    warn: list = []

    if first_pass:
        with ui.console.status("Updating data (new games, lineups, CSV, player stats, model)…"):
            with _quiet(warn):
                summary = sync_data()
        try:
            s = picks.summary(db())
            f, ls, o = s["final"], s["last_season"], s["overall"]
            acc = (f"Model's #1 pick scored first: {f['hits'] / f['games']:.1%} this season ({f['hits']} of {f['games']}) · "
                   f"{ls['hits'] / ls['games']:.1%} last season ({ls['games']:,} games) · "
                   f"{o['hits'] / o['games']:.1%} all time ({o['games']:,} games) · random = 10%")
        except (OSError, KeyError, ValueError, ZeroDivisionError, sqlite3.Error):
            acc = ""
        ui.header(date.today().strftime("%A, %B %-d, %Y"), summary, acc)

    con = db()
    with _quiet(warn):
        games = filter_games(get_todays_games(), teams)
    if first_pass:
        if teams and not games:
            ui.skipped(", ".join(sorted(teams)), "no game today for this team.")
        ui.schedule(games)

    pre_games = [g for g in games if g['status'] == 1 and g['game_id'] not in _DONE]
    if not pre_games:
        if first_pass:
            ui.skipped("No upcoming games", "nothing left to predict today.")
        ui.notes(warn)
        return 0

    pg = pm.load() if os.path.exists('pregame_model.json') else None
    model_version = picks.model_id(pg) if pg else None
    if pg is None:
        ui.notes(warn + ["pregame_model.json is missing — run: python3 train_pregame.py"])
        return 0
    with ui.console.status("Checking injuries and lineups…"), _quiet(warn):
        player_logs = bf.build_player_game_logs()
        # League feed + each game's own summary (the summaries carry Doubtful / Questionable)
        inj_report = st.fetch_injury_report([g['game_id'] for g in pre_games])
        st.save_injury_report(con, date.today().strftime("%Y%m%d"), inj_report)   # kept for later tests
        # Current rosters: a traded player counts for his new team right away
        roster_pos: dict = {}
        rosters = st.fetch_rosters({g[k] for g in pre_games for k in ('home_team', 'away_team')}, positions=roster_pos)
        team_of = {**st.team_by_player(con), **rosters}
        # Positions (ESPN rosters, else NBA.com lineups): a missing centre is replaced by a centre when possible
        groups = st.player_groups(con, date.today().strftime("%Y%m%d"), roster_pos)
    pending, any_projected = 0, False
    early_index = bf.build_early_shot_index(con)        # each player's share of his team's first 5 shots
    import context_features as cf
    ctx = cf.Context(con)                               # tonight's opponent: by position + head-to-head
    fd_games = _fanduel_prices(pre_games, warn, ui)          # FanDuel "First Basket" prices (None if unreachable)
    dk_games = _draftkings_prices(pre_games, warn, ui)       # DraftKings "1st Points Scorer" (None if unreachable)

    for g in pre_games:
        game_id, game_date = g['game_id'], g['game_date']
        home_team, away_team = g['home_team'], g['away_team']
        matchup = f"{away_team} @ {home_team}"

        with _quiet(warn):
            actions = get_pbp(game_id)
        if actions:          # plays exist: it already tipped off (the model is pre-game only)
            ui.skipped(matchup, "already tipped off — skipped.")
            _DONE.add(game_id)
            continue

        # Lineup source, most to least trustworthy:
        #   NBA.com daily lineups (Confirmed) -> NBA.com box score (posted ~30 min before tip)
        #   -> NBA.com daily lineups (Expected) -> our projection (usual starters minus injured).
        # (ESPN is not used: it doesn't publish starters until the game starts.)
        with _quiet(warn):
            daily = st.nba_daily_lineup(home_team, away_team, con, [date.today().strftime("%Y%m%d"), game_date])
            lineup, source = None, None
            if daily and daily['confirmed']:
                lineup, source = daily['teams'], "nba_confirmed"
            if not lineup:
                lineup = st.nba_confirmed_starters(home_team, away_team, con)
                source = "nba_confirmed" if lineup else None
        if not lineup and daily and not confirmed_only:
            lineup, source = daily['teams'], "nba_expected_unconfirmed"
        if not lineup and confirmed_only:
            ui.skipped(matchup, "lineup not confirmed yet — skipped (--confirmed-only).")
            pending += 1
            continue

        notes = []
        if source == "nba_expected_unconfirmed":
            # NBA.com's expected lineup can predate an injury: swap anyone ruled out since
            # (official CONFIRMED lineups are never changed).
            lineup = dict(lineup)
            for team in (home_team, away_team):
                sub = st.substitute_injured(con, team, lineup.get(team, []), inj_report, game_date, team_of=team_of,
                                            groups=groups)
                lineup[team] = sub["players"]
                notes += [f"{r['in']} replaces {r['out'].title()} ({r['status']}) in NBA.com's expected lineup"
                          for r in sub["replaced"]]
                notes += [f"{n.title()} is ruled out but no replacement was found — still listed"
                          for n in sub["unresolved"]]

        if not lineup:                # NBA.com hasn't posted: our projection
            ph = st.project_usual_starters(con, home_team, game_date, inj_report, rosters=rosters, groups=groups)
            pa = st.project_usual_starters(con, away_team, game_date, inj_report, rosters=rosters, groups=groups)
            if not (ph['complete'] and pa['complete']):
                why = (f"no replacement found for ruled-out {', '.join(ph['unresolved'] + pa['unresolved'])}"
                       if ph['unresolved'] or pa['unresolved'] else "not enough recent games to project")
                ui.skipped(matchup, f"no lineup yet and {why} — skipped.")
                pending += 1
                continue
            lineup, source = {home_team: ph['players'], away_team: pa['players']}, "projected_usual_starters"
            notes += [f"{r['in']} starts for {r['out'].title()} ({r['status']}) — {r['evidence']}"
                      for r in ph['replaced'] + pa['replaced']]
            notes += [f"{n} fills an open spot in the usual five (next in line by recent starts and minutes)"
                      for n in ph['filled'] + pa['filled']]
            for name, missed in {**ph['returning'], **pa['returning']}.items():
                notes.append(f"{name.title()} is back from injury (missed {missed} of the last {st.RECENT_GAMES} games)")
            back = st.returning_by(inj_report, ph['dropped'] + pa['dropped'], date.today().isoformat())
            if back:
                notes.append(f"Listed Out but expected back today — may actually play: {', '.join(n.title() for n in back)}")

        home_starters, away_starters = lineup.get(home_team, []), lineup.get(away_team, [])
        if len(home_starters) < 5 or len(away_starters) < 5:
            ui.skipped(matchup, "couldn't find five starters for both teams — skipped.")
            continue

        label = {"nba_confirmed": "CONFIRMED", "nba_expected_unconfirmed": "EXPECTED"}.get(source, "PROJECTED")
        sig = (source, tuple(sorted(p['player_id'] for p in home_starters + away_starters)))
        if _SEEN.get(game_id) == sig:          # --watch: nothing new since the last check
            pending += 0 if label == "CONFIRMED" else 1
            continue
        changed = game_id in _SEEN
        _SEEN[game_id] = sig
        if label == "CONFIRMED":
            _DONE.add(game_id)
        else:
            pending += 1
        any_projected |= label == "PROJECTED"

        # Score the 10 starters
        home_team_stats = bf.compute_team_stats_up_to_date(home_team, game_date, con)
        away_team_stats = bf.compute_team_stats_up_to_date(away_team, game_date, con)
        home_ids, away_ids = [p['player_id'] for p in home_starters], [p['player_id'] for p in away_starters]
        # NBA.com lists positions with its lineups; its starting C usually takes the tip (tip_model.team_jumper)
        home_c = next((p['player_id'] for p in home_starters if p.get('position') == 'C'), None)
        away_c = next((p['player_id'] for p in away_starters if p.get('position') == 'C'), None)
        # Inside the first-basket model: the tip chance from the current tip history (what it was tested with)
        tip_home, tip_away = tm.team_win_probs(con, home_team, away_team, game_date, home_ids, away_ids,
                                               home_c, away_c)
        # Shown to the owner (🏀 Tip-off line + spreadsheet): also counts 2024-25 tips — calls more tips right
        show_home, show_away = tm.team_win_probs(con, home_team, away_team, game_date, home_ids, away_ids,
                                                 home_c, away_c, extended=True)
        rows, info = [], []
        for p in home_starters + away_starters:
            is_home = 1 if p['team'] == home_team else 0
            pstats = bf.compute_player_season_stats(p['player_id'], game_date, p['team'], player_logs)
            pstats['early_shot_share'], pstats['early_shot_count'] = bf.early_shot_share(early_index, p['player_id'], game_date)
            # tonight's opponent (context_features): his position tonight (NBA.com's lineup slot, else his usual one)
            opp = away_team if is_home else home_team
            grp = st.position_group(p.get('position')) or (groups or {}).get(st.norm_name(p['name']))
            pstats['opp_pos_share'] = ctx.opp_pos_share(opp, grp, game_date.replace('-', ''))
            pstats['vs_opp_rate'] = ctx.vs_opp_rate(p['player_id'], opp, game_date.replace('-', ''))
            rows.append(pm.build_row(pstats, home_team_stats if is_home else away_team_stats, is_home,
                                     tip_win_prob=tip_home if is_home else tip_away))
            info.append({'id': p['player_id'], 'name': p['name'], 'team': p['team']})
            if p.get('id_matched') is False:
                notes.append(f"{p['name']}: no past games found — scored with default stats")
        probs = pm.probabilities(pg, rows)
        order = [int(i) for i in np.argsort(probs)[::-1]]

        names = {p['player_id']: p['name'] for p in home_starters + away_starters}
        jumper_ids = {home_team: tm.team_jumper(con, home_team, game_date, home_ids, home_c, extended=True),
                      away_team: tm.team_jumper(con, away_team, game_date, away_ids, away_c, extended=True)}
        jumpers = {team: names.get(pid) for team, pid in jumper_ids.items()}
        tips = season_tip_records(con, [pid for pid in jumper_ids.values() if pid], game_date)
        tip_record = {team: tips.get(str(pid), (0, 0)) for team, pid in jumper_ids.items() if pid}

        # Keep every pick (never overwritten) so the real track record can be measured: `nba.py eval`
        # and my_predictions.xlsx
        picks.log_pick(con, {
            "game_id": game_id, "game_date": game_date, "matchup": matchup, "lineup_type": label,
            "lineup_source": source, "lineup_sig": ",".join(map(str, sig[1])), "model_id": model_version,
            "tip_home": show_home, "home_jumper": jumpers[home_team], "away_jumper": jumpers[away_team],
            "players": [{**info[i], "prob": float(probs[i])} for i in range(len(info))]})
        injuries = {nm: status for nm, status, _ in
                    st.injury_flags([p['name'] for p in home_starters + away_starters], inj_report)}
        top5 = [(info[i]['name'], info[i]['team'], float(probs[i]), pm.fair_american(float(probs[i])))
                for i in order[:5]]
        everyone = [{**info[i], "prob": float(probs[i])} for i in order]       # all 10 starters, for EV
        season_counts = season_first_baskets(con, [p['id'] for p in info], game_date)
        fd_card = _fanduel_card(fd_games, home_team, away_team, top5, everyone, dk_games)
        first_team = ({home_team: float(probs[:len(home_starters)].sum()),
                       away_team: float(probs[len(home_starters):].sum())} if pg.get("type") == "two_stage" else None)
        priced = fd_card.pop("_priced", [])
        if priced:                             # save the prices, so EV can be checked later (ev.paper_results)
            ev.log_odds(con, game_id, game_date, matchup, priced)
        ui.game_card({
            "matchup": matchup, "start": g.get('start', ''), "label": label, "updated": changed,
            "picks": top5, **fd_card,
            # each starter's real record this season: first baskets / games played (games before today)
            "season": {info[i]['name']: season_counts.get(str(info[i]['id']), (0, 0)) for i in order},
            "probs": [[info[i]['name'], info[i]['team'], float(probs[i])] for i in order],   # all 10, for live odds
            "tip": {home_team: show_home, away_team: show_away}, "jumpers": jumpers,
            "tip_record": tip_record,             # each jumper's tips won / tips taken this season
            "first_team": first_team,
            "opening": ev.opening_prices(con, game_date, matchup),   # first price of the day at each book (↓/↑)
            **_team_first_odds(fd_games, home_team, away_team, first_team, dk_games, con, game_date),
            "home": home_team, "away": away_team,
            "home_starters": home_starters, "away_starters": away_starters,
            "injuries": injuries, "notes": notes,
        })

    st.save_seen_lineups(con)                  # keep NBA.com's Expected and Confirmed lists (Lineups tab)
    picks.export_predictions(con)              # my_predictions.xlsx: today's picks show as "pending"
    ui.notes(warn)
    if first_pass:
        ui.legend(any_projected)
    return pending


def season_first_baskets(con, player_ids, before_date: str, season_start: str = picks.SEASON_START) -> dict:
    """player_id -> (first baskets, games played) this season, counting games from `season_start` up to (not
    including) `before_date`. 'Played' = had minutes in the box score; a player with no games gives (0, 0)."""
    ids = [str(i) for i in player_ids]
    if not ids:
        return {}
    rows = con.execute(
        f"""SELECT l.player_id,
                   SUM(l.minutes > 0),
                   SUM(l.minutes > 0 AND f.scorer_player_id = l.player_id)
            FROM lineups l
            JOIN games g ON g.game_id = l.game_id
            LEFT JOIN first_baskets f ON f.game_id = l.game_id
            WHERE g.game_date >= ? AND g.game_date < ? AND l.player_id IN ({",".join("?" * len(ids))})
            GROUP BY l.player_id""", (season_start, before_date, *ids)).fetchall()
    return {str(pid): (int(fb or 0), int(gp or 0)) for pid, gp, fb in rows}


def season_tip_records(con, player_ids, before_date: str, season_start: str = picks.SEASON_START) -> dict:
    """player_id -> (tips won, tips taken) this season, from the opening jump ball of each game from
    `season_start` up to (not including) `before_date`."""
    ids = [str(i) for i in player_ids]
    if not ids:
        return {}
    rows = con.execute(
        f"""SELECT pid, SUM(won), COUNT(*) FROM (
                SELECT tip_winner_player_id AS pid, 1 AS won FROM games WHERE game_date >= ? AND game_date < ?
                UNION ALL
                SELECT tip_loser_player_id, 0 FROM games WHERE game_date >= ? AND game_date < ?)
            WHERE pid IN ({",".join("?" * len(ids))}) GROUP BY pid""",
        (season_start, before_date, season_start, before_date, *ids)).fetchall()
    return {str(pid): (int(won), int(taken)) for pid, won, taken in rows}


def season_records_by_name(con, names, before_date: str, season_start: str = picks.SEASON_START) -> dict:
    """name -> {"fb": (first baskets, games played), "tips": (won, taken)} this season, for names as stored in the
    pick log (the GUI scoreboard has names, not ids). Names are matched to ids through this season's box scores."""
    names = [n for n in dict.fromkeys(names) if n]
    if not names:
        return {}
    # Box scores sometimes drop accents ('Luka Doncic' vs 'Luka Dončić'): match on st.norm_name, not exact text
    by_norm = {st.norm_name(name): str(pid) for pid, name in con.execute(
        """SELECT DISTINCT l.player_id, l.player_name FROM lineups l JOIN games g ON g.game_id = l.game_id
           WHERE g.game_date >= ?""", (season_start,))}
    ids = {n: by_norm[st.norm_name(n)] for n in names if st.norm_name(n) in by_norm}
    fb = season_first_baskets(con, ids.values(), before_date, season_start)
    tips = season_tip_records(con, ids.values(), before_date, season_start)
    return {n: {"fb": fb.get(ids[n], (0, 0)), "tips": tips.get(ids[n], (0, 0))} for n in names if n in ids}


def _fanduel_prices(games: list, warn: list, ui=None):
    """FanDuel First Basket prices for these games, or None (with a note) if FanDuel can't be reached.
    Skipped with --no-odds."""
    if '--no-odds' in sys.argv or not games:
        return None
    import fanduel_odds as fd
    if ui is None:
        import display as ui
    try:
        with ui.console.status("Getting FanDuel first-basket odds…"):
            return fd.fetch_first_basket_odds({g[k] for g in games for k in ('home_team', 'away_team')})
    except Exception as e:                 # odds are extra: never stop the predictions over them
        warn.append(f"FanDuel odds unavailable ({type(e).__name__}) — predictions shown without them")
        return None


def _draftkings_prices(games: list, warn: list, ui=None):
    """DraftKings first-points prices for today's games, or None (with a note) if DraftKings can't be read.
    Skipped with --no-odds."""
    if '--no-odds' in sys.argv or not games:
        return None
    import draftkings_odds as dk
    if ui is None:
        import display as ui
    try:
        with ui.console.status("Getting DraftKings first-basket odds…"):
            direct = dk.fetch_first_basket_odds()
        try:                                 # DraftKings' team market + official opening prices come via ESPN (logged)
            espn = dk.fetch_via_espn(games, db())
            dk.log_espn(db(), date.today().strftime("%Y%m%d"), espn)
            by = {(g["home_code"], g["away_code"]): g for g in espn}
            for g in direct:
                e = by.get((g["home_code"], g["away_code"])) or {}
                g["team_first"], g["team_first_open"] = e.get("team_first", {}), e.get("team_first_open", {})
        except Exception:  # noqa: BLE001
            pass
        return direct
    except Exception as e:                 # odds are extra: never stop the predictions over them
        snap, age = dk.load_snapshot()    # the cloud: DraftKings refuses it — a Mac/browser price from minutes ago
        try:                              # ...and DraftKings' prices as ESPN publishes them (no Mac needed; logged)
            espn = dk.fetch_via_espn(games, db())
            dk.log_espn(db(), date.today().strftime("%Y%m%d"), espn)
        except Exception:  # noqa: BLE001
            espn = []
        if snap is not None:              # fresher where we have it; ESPN for any game it doesn't cover
            have = {(g["home_code"], g["away_code"]) for g in snap}
            return snap + [g for g in espn if (g["home_code"], g["away_code"]) not in have]
        if espn:
            return espn
        warn.append(f"DraftKings odds unavailable ({type(e).__name__}) — FanDuel only")
        return None


def _best_prices(books: dict) -> tuple:
    """{"FanDuel": {name: american}, "DraftKings": {...}} -> ({name: best american}, {name: book}): the price that
    pays the most for each player (ties go to the first book listed)."""
    best, where = {}, {}
    for book, prices in books.items():
        for name, a in (prices or {}).items():
            if name not in best or ev.profit_per_dollar(a) > ev.profit_per_dollar(best[name]):
                best[name], where[name] = a, book
    return best, where


def _team_first_odds(fd_games, home: str, away: str, first_team, dk_games=None, con=None, day: str = "") -> dict:
    """ "Team to Score First" at both books: prices {book: {team: american}}, the day's opening prices (for ↓/↑),
    the better price per team and its value (EV per $1) at our chance. Every price is also logged (team_first_log)."""
    import draftkings_odds as dk
    import fanduel_odds as fd
    books = {}
    for book, games in (("FanDuel", fd_games), ("DraftKings", dk_games)):
        g = fd.game_for(games or [], home, away)
        p = {t: a for t, a in ((g or {}).get("team_first") or {}).items() if t in (home, away)}
        if p:
            books[book] = p
    if con is not None and day:
        for book, p in books.items():
            if book == "FanDuel":                       # DraftKings' are logged with ESPN's times (dk.log_espn)
                for team, a in p.items():
                    dk.log_team_first(con, day, f"{away} @ {home}", book, team, a)
        con.commit()
    if not books or not first_team:
        return {}
    best, where = {}, {}
    for book, p in books.items():
        if book == "DraftKings":                    # ESPN's "First Team to Score" is DraftKings' "Team to Score 1st FG"
            continue                                # (field goals only) — a different bet from first POINTS: shown, not compared
        for team, a in p.items():
            if team not in best or ev.profit_per_dollar(a) > ev.profit_per_dollar(best[team]):
                best[team], where[team] = a, book
    opening = {}
    if con is not None and day:
        con.execute(dk.TEAM_FIRST_SCHEMA)
        for book, team, a in con.execute("SELECT book, team, american FROM team_first_log WHERE game_date = ? AND matchup = ? "
                                         "ORDER BY logged_at", (day, f"{away} @ {home}")):
            opening.setdefault(book, {}).setdefault(team, a)
    return {"team_first_odds": best, "team_first_book": where, "team_first_books": books, "team_first_open": opening,
            "team_first_ev": {t: first_team[t] * ev.profit_per_dollar(a) - (1 - first_team[t]) for t, a in best.items()}}


def _fanduel_card(fd_games, home: str, away: str, top5: list, everyone: list = (), dk_games=None) -> dict:
    """The game card's FanDuel fields; {} when odds are off.
    fanduel {name: american}, fanduel_note str, ev {name: EV per $1}, best_value (the starter with the highest
    EV of all 10, if it is positive) and _priced (rows for ev.log_odds; popped before display)."""
    if fd_games is None and dk_games is None:
        return {}
    import fanduel_odds as fd
    players = list(everyone) or [{"name": n, "team": t, "prob": p} for n, t, p, _ in top5]
    # DraftKings: the same matching rules as FanDuel (names, then a unique last name on the same team)
    dk_game = fd.game_for(dk_games or [], home, away)
    dk_prices = {name: row["american"] for name, row in
                 fd.match_prices(dk_game["players"], [(p["name"], p["team"]) for p in players]).items()} \
        if dk_game and dk_game["players"] else {}
    game = fd.game_for(fd_games or [], home, away)
    if not game or not game["players"]:
        base = {"fanduel": {}, "fanduel_note": "FanDuel hasn't posted first-basket odds for this game yet."
                if fd_games is not None else "FanDuel odds unavailable right now."}
        if not dk_prices:
            return base
        return {**base, "draftkings": dk_prices, "best_book": {}, "ev": {}, "fd_market": None,   # shown, not valued:
                "best_value": None, "_priced": []}                                                  # field goals only
    matched = fd.match_prices(game["players"], [(p["name"], p["team"]) for p in players])
    prices = {name: row["american"] for name, row in matched.items()}
    fd_priced = ev.priced_players(players, prices)          # FanDuel only: what odds_log / paper betting record
    # value uses FanDuel only: DraftKings' player market pays on the first FIELD GOAL (free throws don't count — its
    # bet slip, 2026-11-19), a different bet from our first-POINTS chances (free throws decide ~9% of games)
    best_prices, best_book = _best_prices({"FanDuel": prices})
    priced = ev.priced_players(players, best_prices)        # value at whichever book pays more
    note = ""
    if not fd.is_complete(game["players"]):
        note = f"FanDuel lists only {len(game['players'])} players so far (lineups not all posted)."
    elif any(name not in prices for name, *_ in top5):
        note = "– = FanDuel doesn't offer that player (it prices only the starters it expects)."
    best = priced[0] if priced and ev.is_value(priced[0]["ev"]) else None
    # for live odds on the page: FanDuel's market + each matched player's selection id
    market_id = next((row.get("market_id") for row in matched.values() if row.get("market_id")), None)
    fd_market = {"id": market_id, "runners": {row["selection_id"]: name for name, row in matched.items()
                                              if row.get("selection_id")}} if market_id else None
    return {"fanduel": prices, "fanduel_note": note, "ev": {r["name"]: r["ev"] for r in priced}, "fd_market": fd_market,
            "draftkings": dk_prices, "best_book": best_book,
            "best_value": ({**{k: best[k] for k in ("name", "team", "prob", "american", "ev")},
                            "book": best_book.get(best["name"], "FanDuel")} if best else None),
            "_priced": [r for r in fd_priced if "id" in r]}


def report_without_lineups(con, d: str) -> str:
    """When NBA.com hasn't published lineups: today's games, usual starters minus injured, injuries."""
    games = get_todays_games()
    report = st.fetch_injury_report([g['game_id'] for g in games if g['status'] == 1])
    roster_pos: dict = {}
    rosters = st.fetch_rosters({g[k] for g in games for k in ('home_team', 'away_team')}, positions=roster_pos)
    groups = st.player_groups(con, d, roster_pos)
    lines = [f"NBA DAILY REPORT — {d}", "=" * 60,
             "NBA.com lineups not published yet. Showing USUAL starters (last 5 games) minus players",
             "ruled Out/Doubtful. These are PROJECTIONS, not confirmed lineups.", ""]
    if not games:
        return "\n".join(lines + ["No games found."])
    for g in games:
        h, a = g['home_team'], g['away_team']
        tag = {1: "", 2: " [LIVE]", 3: " [FINAL]"}.get(g['status'], "")
        lines.append(f"{a} @ {h}{tag}")
        for team in (a, h):
            pr = st.project_usual_starters(con, team, g['game_date'], report, rosters=rosters, groups=groups)
            if pr['complete']:
                names = ', '.join(p['name'] + (' (?)' if p['uncertain'] else '') for p in pr['players'])
                lines.append(f"  {team} projected: {names}")
                if pr['dropped']:
                    lines.append(f"     removed for injury: {', '.join(pr['dropped'])}")
                    rt = st.returning_by(report, pr['dropped'], date.today().isoformat())
                    if rt:
                        lines.append(f"     ⚠️ listed Out but est. return is today — may actually play: {', '.join(rt)}")
            else:
                lines.append(f"  {team} projected: not enough recent games stored")
            inj = st.team_injuries(team, report, {})
            for r in inj:
                lines.append(f"     {r['status']:<14} {r['name']}" + (f" — {r['detail']}" if r['detail'] else ""))
        lines.append("")
    lines.append("(?) = Questionable/Day-to-Day: may or may not start.")
    return "\n".join(lines)


def evaluate():
    """Real track record of every logged pick on finished games (see picks.py)."""
    import display as ui
    warn: list = []
    with ui.console.status("Updating data…"), _quiet(warn):
        summary = sync_data()
    ui.track_record(picks.summary(db()), summary)
    ui.paper_betting(ev.paper_results(db()))
    ui.notes(warn)

# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    # Plain `python3 nba.py`, options only (`--watch`) or team codes (`python3 nba.py LAL`) = today's picks
    cmd = (sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") and not parse_teams(sys.argv[1:2])
           else "predict")

    if cmd == "init":
        init()
    elif cmd == "backfill":
        year = int(sys.argv[2]) if len(sys.argv) > 2 else 2024
        days = int(sys.argv[3]) if len(sys.argv) > 3 else None
        backfill(year, days)
    elif cmd == "report":
        import display as ui
        d = sys.argv[2] if len(sys.argv) > 2 else date.today().strftime("%Y%m%d")
        warn: list = []
        with ui.console.status("Updating data and fetching lineups/injuries…"), _quiet(warn):
            summary = sync_data()
            if st._daily_feed(d):
                ids = [g['game_id'] for g in get_todays_games() if g['status'] == 1] if d == date.today().strftime("%Y%m%d") else None
                body = st.daily_report(db(), d, ids)
            else:
                body = report_without_lineups(db(), d)
        ui.report(body, summary)
        ui.notes(warn)
    elif cmd == "lineups":
        st.backfill_lineups(db())
    elif cmd == "predict":
        if '--watch' in sys.argv:
            interval = 300
            if '--interval' in sys.argv:
                interval = int(sys.argv[sys.argv.index('--interval') + 1])
            first = True
            try:
                while True:
                    left = predict_today(first_pass=first, teams=parse_teams(sys.argv[1:]))
                    first = False
                    if not left:
                        print("\n✅ Every game is confirmed or has tipped. Done.")
                        break
                    print(f"\n⏳ {left} game(s) still awaiting a confirmed lineup — rechecking in "
                          f"{interval // 60} min (Ctrl-C to stop)")
                    time.sleep(interval)
                    st._FEED_CACHE.clear(); st._SB_CACHE.clear()
            except KeyboardInterrupt:
                print("\nStopped.")
        else:
            teams = parse_teams(sys.argv[1:])
            if not teams and sys.stdin.isatty() and '--all' not in sys.argv:   # plain run in a terminal: ask
                with _quiet([]):
                    todays = [g for g in get_todays_games() if g['status'] == 1]
                if todays:
                    teams = ask_for_teams(todays)
            predict_today(teams=teams)
    elif cmd == "backfill_tip_players":
        backfill_tip_players()
    elif cmd == "eval":
        evaluate()          # syncs data itself, quietly
    elif cmd == "test":
        # Quick test on one game
        gid = sys.argv[2] if len(sys.argv) > 2 else "401909881"
        actions = get_pbp(gid)
        fb = first_basket(actions)
        tip = tip_jumpball(actions)
        tip_str = (f"{tip['tip_winner_team']} ({tip['tip_winner_player_name']}, ID: {tip['tip_winner_player_id']})"
                   if tip else "Unknown")
        print(f"Game {gid}:")
        if fb:
            print(f"  First basket: {fb['playerName']} ({fb['teamTricode']}) — {fb['shotType']} at {fb['clock']} Q{fb['period']}")
        else:
            print("  First basket: none found (no play-by-play yet?)")
        print(f"  Tip winner: {tip_str}")
    else:
        print("""
Usage:
  python nba.py init                         # Create database
  python nba.py backfill [year] [days]       # Backfill season (default 2024, all days)
  python nba.py backfill_tip_players         # Backfill tip winner player id + name
  python nba.py report [YYYYMMDD]            # Today's lineups (expected/confirmed) + injury report
  python nba.py lineups                      # Backfill starting lineups (run once)
  python nba.py                              # Numbered list of today's games; pick one by number or team name (Enter = all)
  python nba.py knicks                       # Just one team's game (team name or code, any case)
  python nba.py --all                        # Every game, without asking
  python nba.py predict                      # Today's pre-game predictions
  python nba.py predict --watch              # Keep rechecking; print each game as its lineup updates/confirms
  python nba.py predict --confirmed-only     # Skip any game without a CONFIRMED lineup
  python nba.py eval                         # Check accuracy
  python nba.py help                         # This list
  python nba.py test [game_id]               # Test one game
        """)