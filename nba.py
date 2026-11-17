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

# ──────────────────────────────────────────────
# SEASON STATS COMPUTATION (for pre-game predictions)
# ──────────────────────────────────────────────
def compute_player_season_stats(player_id: str, game_date: str, team_abbrev: str, player_logs: dict) -> dict:
    """Compute player season stats UP TO (but not including) game_date. No leakage."""
    game_date_str = str(game_date).replace('-', '')[:8]
    logs = player_logs.get(player_id, [])
    if not logs:
        return {
            'first_basket_rate': 0.0, 'first_shot_rate': 0.0,
            'L10_first_basket_rate': 0.0, 'L5_first_basket_rate': 0.0,
            'games_played': 0, 'usage_rate': 0.0,
            'true_shooting_pct': 0.0, 'minutes_per_game': 0.0,
            'points_per_game': 0.0,
        }
    prior_logs = [g for g in logs if g['game_date'] < game_date_str]
    games_played = len(prior_logs)
    if games_played == 0:
        return {
            'first_basket_rate': 0.0, 'first_shot_rate': 0.0,
            'L10_first_basket_rate': 0.0, 'L5_first_basket_rate': 0.0,
            'games_played': 0, 'usage_rate': 0.0,
            'true_shooting_pct': 0.0, 'minutes_per_game': 0.0,
            'points_per_game': 0.0,
        }
    total_min = sum(g['minutes'] for g in prior_logs)
    total_fgm = sum(g['fgm'] for g in prior_logs)
    total_fga = sum(g['fga'] for g in prior_logs)
    total_fg3m = sum(g['fg3m'] for g in prior_logs)
    total_fg3a = sum(g['fg3a'] for g in prior_logs)
    total_ftm = sum(g['ftm'] for g in prior_logs)
    total_fta = sum(g['fta'] for g in prior_logs)
    total_pts = sum(g['pts'] for g in prior_logs)
    total_first_baskets = sum(g['first_basket'] for g in prior_logs)
    minutes_per_game = total_min / games_played
    points_per_game = total_pts / games_played
    ts_denom = 2 * (total_fga + 0.44 * total_fta)
    true_shooting_pct = total_pts / ts_denom if ts_denom > 0 else 0.0
    usage_rate = (total_fga + 0.44 * total_fta) / (total_min / 36) if total_min > 0 else 0.0
    usage_rate = min(usage_rate / 100, 1.0)
    first_basket_rate = total_first_baskets / games_played if games_played > 0 else 0.0
    first_shot_rate = first_basket_rate * 2.0
    L10_logs = prior_logs[:10]
    L5_logs = prior_logs[:5]
    L10_fb = sum(g['first_basket'] for g in L10_logs)
    L10_first_basket_rate = L10_fb / len(L10_logs) if L10_logs else 0.0
    L5_fb = sum(g['first_basket'] for g in L5_logs)
    L5_first_basket_rate = L5_fb / len(L5_logs) if L5_logs else 0.0
    return {
        'first_basket_rate': first_basket_rate,
        'first_shot_rate': first_shot_rate,
        'L10_first_basket_rate': L10_first_basket_rate,
        'L5_first_basket_rate': L5_first_basket_rate,
        'games_played': float(games_played),
        'usage_rate': usage_rate,
        'true_shooting_pct': true_shooting_pct,
        'minutes_per_game': minutes_per_game,
        'points_per_game': points_per_game,
    }

def compute_team_stats_up_to_date(team_abbrev: str, game_date: str, con) -> dict:
    """Compute team stats up to game_date."""
    game_date_str = str(game_date).replace('-', '')[:8]
    team_games = con.execute('''
        SELECT g.game_date, g.home_team, g.away_team, g.tip_winner_team,
               fb.scorer_player_id, fb.scorer_name, fb.scorer_team
        FROM games g
        JOIN first_baskets fb ON g.game_id = fb.game_id
        WHERE (g.home_team = ? OR g.away_team = ?)
        AND g.game_date < ?
        ORDER BY g.game_date DESC
    ''', (team_abbrev, team_abbrev, game_date_str)).fetchall()
    if not team_games:
        return {'team_tipoff_win_pct': 0.5, 'home_fb_pct': 0.0, 'away_fb_pct': 0.0, 'games_played': 0}
    games_played = len(team_games)
    tip_wins = sum(1 for g in team_games if g[3] == team_abbrev)
    team_tipoff_win_pct = tip_wins / games_played if games_played > 0 else 0.5
    home_fb_real = sum(1 for g in team_games if g[1] == team_abbrev and g[6] == team_abbrev)
    away_fb_real = sum(1 for g in team_games if g[2] == team_abbrev and g[6] == team_abbrev)
    home_games = [g for g in team_games if g[1] == team_abbrev]
    away_games = [g for g in team_games if g[2] == team_abbrev]
    home_fb_pct = home_fb_real / len(home_games) if home_games else 0.0
    away_fb_pct = away_fb_real / len(away_games) if away_games else 0.0
    return {'team_tipoff_win_pct': team_tipoff_win_pct, 'home_fb_pct': home_fb_pct, 'away_fb_pct': away_fb_pct, 'games_played': float(games_played)}

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
        })
    return games

def get_pbp(game_id: str) -> list[dict]:
    """Play-by-play + player names from ESPN."""
    time.sleep(SLEEP)
    try:
        pbp = espn_nba_pbp(game_id=int(game_id))
    except Exception as e:
        print(f"  ESPN PBP error for {game_id}: {e}")
        return []
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
        tid = p.get('team.id', '')
        home = p.get('homeTeamId', '')
        away = p.get('awayTeamId', '')
        team = p.get('homeTeamAbbrev') if str(tid) == str(home) else p.get('awayTeamAbbrev', '')
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
        }
        # For jumpball, also store loser participant
        if p.get('type.text', '').lower() == 'jumpball':
            loser_id = str(p.get('participants.1.athlete.id', '') or '')
            action['tip_loser_player_id'] = loser_id
            action['tip_loser_player_name'] = names.get(loser_id, '')
        actions.append(action)
    return actions

def first_basket(actions: list[dict]) -> Optional[dict]:
    """First made FG in Q1-Q4 (not free throw)."""
    for a in actions:
        if a['scoringPlay'] and 'free_throw' not in a['actionType'] and 1 <= a['period'] <= 4:
            return a
    return None

def tip_winner(actions: list[dict]):
    """Team, player_id, and player_name that won the opening tip."""
    for a in actions:
        if 'jumpball' in a['actionType'] or 'tip' in a['actionType']:
            pid = a['personId']
            return (a['teamTricode'], pid, a['playerName'])
    return None


def tip_jumpball(actions: list[dict]) -> Optional[dict]:
    """Extract both jumpball participants: winner + loser from transformed PBP."""
    for a in actions:
        if a.get('actionType') == 'jumpball':
            winner_id = a.get('personId', '')
            winner_name = a.get('playerName', '')
            loser_id = a.get('tip_loser_player_id', '')
            loser_name = a.get('tip_loser_player_name', '')
            team = a.get('teamTricode', '')
            return {
                'tip_winner_team': team,
                'tip_winner_player_id': winner_id,
                'tip_winner_player_name': winner_name,
                'tip_loser_player_id': loser_id,
                'tip_loser_player_name': loser_name,
            }
    return None

def get_injured_players() -> set[str]:
    """Names of players definitely OUT. Day-to-Day players are NOT included
    (they often start); use starters.fetch_injuries() to see them."""
    out, _unc = st.split_injuries(st.fetch_injuries())
    return out


def get_recent_starters(team_abbrev: str, con, n_games: int = 5, injured: set = None) -> list[dict]:
    """Projected starting 5 from stored lineups (last n completed games before today)."""
    if injured is None:
        injured = get_injured_players()
    res = st.recent_starters(con, team_abbrev, date.today().strftime("%Y%m%d"),
                             n_games=n_games, out_names=injured)
    return res["players"] if res["complete"] else []


# ──────────────────────────────────────────────
# INGESTION
# ──────────────────────────────────────────────
def ingest_game(game_id: str, con):
    """Fetch PBP, extract first basket + tip, store."""
    actions = get_pbp(game_id)
    if not actions:
        return

    fb = first_basket(actions)
    tip = tip_jumpball(actions)

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
            games_df = df[(df['status_type_state'] == 'post') & (df['season_type'] == 2)]

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
                            INSERT OR IGNORE INTO games (game_id, game_date, home_team, away_team)
                            VALUES (?, ?, ?, ?)
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
            games_df = df[(df['status_type_state'] == 'post') & (df['season_type'] == 2)]
            for _, r in games_df.iterrows():
                gid = str(r['game_id'])
                home = str(r['home_abbreviation'])
                away = str(r['away_abbreviation'])
                if home not in NBA_TEAMS or away not in NBA_TEAMS or gid in have:
                    continue
                con.execute("INSERT OR IGNORE INTO games (game_id, game_date, home_team, away_team) VALUES (?, ?, ?, ?)", (gid, day[:10], home, away))
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


CSV_PATH = "first_basket_data.csv"

def export_csv(con):
    """Rewrite the CSV from the database with all current data including tip winner player name."""
    rows = con.execute("""
        SELECT g.game_date, g.game_id, g.home_team, g.away_team,
               g.tip_winner_team, g.tip_winner_player_id, g.tip_winner_player_name,
               g.tip_loser_player_id, g.tip_loser_player_name,
               fb.scorer_name, fb.scorer_team, fb.shot_type, fb.clock
        FROM games g
        JOIN first_baskets fb ON g.game_id = fb.game_id
        ORDER BY g.game_date
    """).fetchall()

    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["game_start", "game_id", "home_team", "away_team", "tip_winner", "tip_winner_player", "tip_winner_player_name", "tip_loser_player", "tip_loser_player_name", "first_basket_scorer",
                         "scorer_team", "shot_type", "clock"])
        for row in rows:
            game_date, game_id, home_team, away_team, tip_winner_team, tip_winner_player_id, tip_winner_player_name, tip_loser_player_id, tip_loser_player_name, scorer_name, scorer_team, shot_type, clock = row
            d = game_date.replace('-', '')
            game_start = f"{d[4:6]}/{d[6:8]}/{d[2:4]}"
            writer.writerow([
                game_start,
                game_id,
                home_team,
                away_team,
                tip_winner_team or '',
                tip_winner_player_id or '',
                tip_winner_player_name or '',
                tip_loser_player_id or '',
                tip_loser_player_name or '',
                scorer_name,
                scorer_team,
                shot_type,
                clock,
            ])
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
    """If the DB has labeled games newer than the training matrices, append them
    (build_features --incremental) and warm-start retrain (train_pregame --incremental).
    The matrices' own last game is the save point, so nothing is rebuilt from scratch.
    Returns a short status for the screen."""
    import subprocess
    try:
        with open("game_ids.json") as f:
            last_gid = str(json.load(f)[-1])
    except (OSError, ValueError, IndexError):
        print("⚠️ game_ids.json missing — run build_features.py once to create the training matrices")
        return "model not updated"
    row = con.execute("SELECT game_date FROM games WHERE game_id = ?", (last_gid,)).fetchone()
    trained_through = row[0] if row else ""
    new_games = con.execute("""SELECT COUNT(*) FROM games g JOIN first_baskets f USING(game_id)
                               WHERE g.game_date > ?""", (trained_through,)).fetchone()[0]
    if not new_games:
        return "model up to date"
    for script in ("build_features.py", "train_pregame.py"):
        r = subprocess.run([sys.executable, script, "--incremental"], capture_output=True, text=True)
        if r.returncode != 0:
            last = (r.stderr or r.stdout).strip().splitlines()[-1:] or [""]
            print(f"⚠️ {script} failed — predicting with the existing model ({last[0]})")
            return "model update FAILED"
    return f"model updated with {new_games} new games"


def sync_data() -> str:
    """Bring everything up to date before predicting, continuing from what's already saved:
    new games + first baskets + tip + lineups, retries for recent gaps, CSV, player logs, model.
    Returns a one-line summary."""
    con = db()
    new = catchup_missing_games()

    # Retry recent games that are missing a first basket or a lineup (older misses are permanent)
    recent = (date.today() - timedelta(days=14)).strftime("%Y%m%d")
    for (gid,) in con.execute("""SELECT game_id FROM games WHERE game_date >= ?
                                 AND game_id NOT IN (SELECT game_id FROM first_baskets)""", (recent,)).fetchall():
        ingest_game(gid, con)
    for (gid,) in con.execute("""SELECT game_id FROM games WHERE game_date >= ?
                                 AND game_id NOT IN (SELECT DISTINCT game_id FROM lineups)""", (recent,)).fetchall():
        st.store_game_lineup(con, gid)
    # First baskets with no matching game (preseason / All-Star) are never used
    con.execute("DELETE FROM first_baskets WHERE game_id NOT IN (SELECT game_id FROM games)")
    con.commit()

    n = export_csv(con)
    bf.build_player_game_logs()
    model = _retrain_if_needed(con)
    last = con.execute("SELECT MAX(game_date) FROM games g JOIN first_baskets USING(game_id)").fetchone()[0] or ""
    last = f"{int(last[4:6])}/{int(last[6:8])}" if last else "?"
    added = f" (+{new} new)" if new else ""
    return f"Data up to date: {n:,} games through {last}{added} · CSV saved · {model}"


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


def predict_today(first_pass: bool = True) -> int:
    """Predict the first basket for today's games that haven't started.
    Returns the number of games still waiting on a confirmed lineup (used by --watch)."""
    import display as ui
    confirmed_only = '--confirmed-only' in sys.argv
    warn: list = []

    if first_pass:
        with ui.console.status("Updating data (new games, lineups, CSV, player stats, model)…"):
            with _quiet(warn):
                summary = sync_data()
        try:
            acc = f"Model's top pick was right in {pm.load()['test_top1']:.1%} of this season's games (random = 10%)"
        except (OSError, KeyError, ValueError):
            acc = ""
        ui.header(date.today().strftime("%A, %B %-d, %Y"), summary, acc)

    con = db()
    with _quiet(warn):
        games = get_todays_games()
    if first_pass:
        ui.schedule(games)

    pre_games = [g for g in games if g['status'] == 1 and g['game_id'] not in _DONE]
    if not pre_games:
        if first_pass:
            ui.skipped("No upcoming games", "nothing left to predict today.")
        ui.notes(warn)
        return 0

    pg = pm.load() if os.path.exists('pregame_model.json') else None
    if pg is None:
        ui.notes(warn + ["pregame_model.json is missing — run: python3 train_pregame.py"])
        return 0
    with ui.console.status("Checking injuries and lineups…"), _quiet(warn):
        player_logs = bf.build_player_game_logs()
        # League feed + each game's own summary (the summaries carry Doubtful / Questionable)
        inj_report = st.fetch_injury_report([g['game_id'] for g in pre_games])
    pending, any_projected = 0, False

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
                sub = st.substitute_injured(con, team, lineup.get(team, []), inj_report, game_date)
                lineup[team] = sub["players"]
                notes += [f"{r['in']} replaces {r['out'].title()} ({r['status']}) in NBA.com's expected lineup"
                          for r in sub["replaced"]]
                notes += [f"{n.title()} is ruled out but no replacement was found — still listed"
                          for n in sub["unresolved"]]

        if not lineup:                # NBA.com hasn't posted: our projection
            ph = st.project_usual_starters(con, home_team, game_date, inj_report)
            pa = st.project_usual_starters(con, away_team, game_date, inj_report)
            if not (ph['complete'] and pa['complete']):
                why = (f"no replacement found for ruled-out {', '.join(ph['unresolved'] + pa['unresolved'])}"
                       if ph['unresolved'] or pa['unresolved'] else "not enough recent games to project")
                ui.skipped(matchup, f"no lineup yet and {why} — skipped.")
                pending += 1
                continue
            lineup, source = {home_team: ph['players'], away_team: pa['players']}, "projected_usual_starters"
            notes += [f"{r['in']} starts for {r['out'].title()} ({r['status']}) — {r['evidence']}"
                      for r in ph['replaced'] + pa['replaced']]
            for name, missed in {**ph['returning'], **pa['returning']}.items():
                notes.append(f"{name.title()} is back from injury (missed {missed} of the last 5 games)")
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
        home_team_stats = compute_team_stats_up_to_date(home_team, game_date, con)
        away_team_stats = compute_team_stats_up_to_date(away_team, game_date, con)
        home_ids, away_ids = [p['player_id'] for p in home_starters], [p['player_id'] for p in away_starters]
        tip_home, tip_away = tm.team_win_probs(con, home_team, away_team, game_date, home_ids, away_ids)
        rows, info = [], []
        for p in home_starters + away_starters:
            is_home = 1 if p['team'] == home_team else 0
            pstats = compute_player_season_stats(p['player_id'], game_date, p['team'], player_logs)
            rows.append(pm.build_row(pstats, home_team_stats if is_home else away_team_stats, is_home,
                                     tip_win_prob=tip_home if is_home else tip_away))
            info.append({'id': p['player_id'], 'name': p['name'], 'team': p['team']})
            if p.get('id_matched') is False:
                notes.append(f"{p['name']}: no past games found — scored with default stats")
        probs = pm.probabilities(pg, rows)
        order = [int(i) for i in np.argsort(probs)[::-1]]

        top = info[order[0]]
        con.execute("""INSERT OR REPLACE INTO predictions
                       (game_id, predicted_scorer_id, predicted_scorer_name, confidence, model_version)
                       VALUES (?, ?, ?, ?, ?)""",
                    (game_id, top['id'], top['name'], float(probs[order[0]]), f'pregame_clogit_{source}'))
        con.commit()

        names = {p['player_id']: p['name'] for p in home_starters + away_starters}
        jumpers = {home_team: names.get(tm.team_jumper(con, home_team, game_date, home_ids)),
                   away_team: names.get(tm.team_jumper(con, away_team, game_date, away_ids))}
        injuries = {nm: status for nm, status, _ in
                    st.injury_flags([p['name'] for p in home_starters + away_starters], inj_report)}
        ui.game_card({
            "matchup": matchup, "start": g.get('start', ''), "label": label, "updated": changed,
            "picks": [(info[i]['name'], info[i]['team'], float(probs[i]), pm.fair_american(float(probs[i])))
                      for i in order[:5]],
            "tip": {home_team: tip_home, away_team: tip_away}, "jumpers": jumpers,
            "home": home_team, "away": away_team,
            "home_starters": home_starters, "away_starters": away_starters,
            "injuries": injuries, "notes": notes,
        })

    ui.notes(warn)
    if first_pass:
        ui.legend(any_projected)
    return pending


def render_game_card(home_team: str, away_team: str, lineup_type: str,
                     home_starters: list[dict], away_starters: list[dict],
                     injury_report: list[dict], dropped: list[str] = None,
                     picks: list[tuple] = None, today_iso: str = None) -> str:
    """
    Render a single game card for display in reports/predict output.
    Returns formatted string with lineups, injuries, and top picks.
    """
    from datetime import date
    today = today_iso or date.today().strftime("%Y-%m-%d")
    dropped = dropped or []
    picks = picks or []
    lines = []

    # Build name->team map from injury report
    injury_team = {r.get('name'): r.get('team') for r in injury_report if r.get('name')}

    # Header
    lines.append(f"{lineup_type} LINEUP: {away_team} @ {home_team}")

    # Home starters (including dropped for home team)
    lines.append(f"  {home_team} STARTERS")
    all_home = list(home_starters)
    for nm in dropped:
        if injury_team.get(nm) == home_team and nm not in [p.get('name') for p in all_home]:
            team = injury_team.get(nm)
            pname = f"{nm} ({team})" if team else nm
            all_home.append({'name': pname})

    for p in all_home:
        nm = p.get('name', '')
        tag = ""
        base_name = nm.split(' (')[0]
        emoji_prefix = ""
        if base_name in dropped:
            tag = " — REMOVED from lineup"
            for r in injury_report:
                if r.get('name') == base_name and r.get('status') == 'Out' and r.get('return') == today:
                    tag += " (may actually play)"
                    break
        for r in injury_report:
            if r.get('name') == base_name and r.get('status') == 'Day-To-Day':
                emoji_prefix = "🟡 "
                break
        lines.append(f"    {emoji_prefix}{nm}{tag}")

    # Away starters (including dropped for away team)
    lines.append(f"  {away_team} STARTERS")
    all_away = list(away_starters)
    for nm in dropped:
        if injury_team.get(nm) == away_team and nm not in [p.get('name') for p in all_away]:
            team = injury_team.get(nm)
            pname = f"{nm} ({team})" if team else nm
            all_away.append({'name': pname})

    for p in all_away:
        nm = p.get('name', '')
        tag = ""
        base_name = nm.split(' (')[0]
        emoji_prefix = ""
        if base_name in dropped:
            tag = " — REMOVED from lineup"
            for r in injury_report:
                if r.get('name') == base_name and r.get('status') == 'Out' and r.get('return') == today:
                    tag += " (may actually play)"
                    break
        for r in injury_report:
            if r.get('name') == base_name and r.get('status') == 'Day-To-Day':
                emoji_prefix = "🟡 "
                break
        lines.append(f"    {emoji_prefix}{nm}{tag}")

    # Injury watch
    lines.append(f"  INJURY WATCH")
    for r in injury_report:
        if r.get('team') in (home_team, away_team):
            lines.append(f"    {r['status']:<14} {r['name']}" + (f" — {r['detail']}" if r.get('detail') else ""))

    # Top picks
    if picks:
        lines.append(f"  TOP PICKS")
        for rank, (pid, team, prob, odds, ev) in enumerate(picks[:5], 1):
            name = ""
            for p in home_starters + away_starters:
                if p.get('id') == pid or p.get('player_id') == pid:
                    name = p.get('name', '')
                    break
            if name:
                lines.append(f"    {rank}. {name} ({team}) — {prob:.0%}  fair {odds:+d}")

    return "\n".join(lines)


def report_without_lineups(con, d: str) -> str:
    """When NBA.com hasn't published lineups: today's games, usual starters minus injured, injuries."""
    games = get_todays_games()
    report = st.fetch_injury_report([g['game_id'] for g in games if g['status'] == 1])
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
            pr = st.project_usual_starters(con, team, g['game_date'], report)
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
    """Check prediction accuracy."""
    con = db()
    q = """
    SELECT p.predicted_scorer_name, p.confidence, fb.scorer_name,
           (p.predicted_scorer_id = fb.scorer_player_id) AS correct
    FROM predictions p
    JOIN first_baskets fb ON p.game_id = fb.game_id
    """
    rows = con.execute(q).fetchall()
    if not rows:
        print("No predictions to evaluate yet.")
        return

    total = len(rows)
    correct = sum(r['correct'] for r in rows)
    print(f"\n📊 Accuracy: {correct}/{total} = {correct/total:.1%}")
    for r in rows:
        mark = "✅" if r['correct'] else "❌"
        print(f"  {mark} Pred: {r['predicted_scorer_name']} | Actual: {r['scorer_name']} ({r['confidence']:.0%})")

# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "init":
        init()
    elif cmd == "backfill":
        year = int(sys.argv[2]) if len(sys.argv) > 2 else 2024
        days = int(sys.argv[3]) if len(sys.argv) > 3 else None
        backfill(year, days)
    elif cmd == "report":
        sync_data()
        d = sys.argv[2] if len(sys.argv) > 2 else date.today().strftime("%Y%m%d")
        if st._daily_feed(d):
            print(st.daily_report(db(), d))
        else:
            print(report_without_lineups(db(), d))
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
                    left = predict_today(first_pass=first)
                    first = False
                    if not left:
                        print("\n✅ Every game is confirmed or has tipped. Done.")
                        break
                    print(f"\n⏳ {left} game(s) still awaiting a confirmed lineup — rechecking in "
                          f"{interval // 60} min (Ctrl-C to stop)")
                    time.sleep(interval)
                    st._FEED_CACHE.clear()
            except KeyboardInterrupt:
                print("\nStopped.")
        else:
            predict_today()
    elif cmd == "backfill_tip_players":
        backfill_tip_players()
    elif cmd == "eval":
        sync_data()
        evaluate()
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
  python nba.py predict                      # Today's pre-game predictions
  python nba.py predict --watch              # Keep rechecking; print each game as its lineup updates/confirms
  python nba.py predict --confirmed-only     # Skip any game without a CONFIRMED lineup
  python nba.py eval                         # Check accuracy
  python nba.py test [game_id]               # Test one game
        """)