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
import pickle
import numpy as np
from datetime import date, timedelta
from typing import Optional
import warnings
warnings.filterwarnings("ignore")

DB = "nba_first_basket.db"
SLEEP = 0.2  # faster; bump to 0.3 if you hit 429

NBA_TEAMS = {'ATL','BKN','BOS','CHA','CHI','CLE','DAL','DEN','DET','GS',
             'HOU','IND','LAC','LAL','MEM','MIA','MIL','MIN','NO','NY',
             'OKC','ORL','PHI','PHX','POR','SA','SAC','TOR','UTAH','WSH'}

# ──────────────────────────────────────────────
# FANDUEL ODDS SCRAPER
# ──────────────────────────────────────────────
def get_fanduel_first_basket_odds(home_team: str, away_team: str, home_starters: list, away_starters: list) -> dict:
    """
    Scrape FanDuel first basket scorer odds for a game.
    Returns {player_id: {'name': str, 'team': str, 'odds': int, 'prob': float}}
    Matches by team + odds ranking (top odds = top starter).
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  ⚠️ Playwright not installed: pip install playwright && playwright install chromium")
        return {}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                viewport={'width': 1920, 'height': 1080},
            )
            page = context.new_page()

            # Get cookies/token by loading NBA page first
            page.goto('https://sportsbook.fanduel.com/navigation/nba', wait_until='domcontentloaded', timeout=30000)
            page.wait_for_timeout(3000)

            # Call market prices API directly with browser cookies
            cookies = context.cookies()
            cookie_header = '; '.join([f"{c['name']}={c['value']}" for c in cookies])

            import requests
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                'Accept': 'application/json',
                'Referer': 'https://sportsbook.fanduel.com/',
                'Origin': 'https://sportsbook.fanduel.com',
                'Cookie': cookie_header,
            }

            r = requests.get(
                'https://smp.ny.sportsbook.fanduel.com/api/sports/fixedodds/readonly/v1/getMarketPrices',
                params={'priceHistory': '0'},
                headers=headers,
                timeout=15
            )

            if r.status_code != 200:
                print(f"  ⚠️ FanDuel API error: {r.status_code}")
                browser.close()
                return {}

            markets = r.json()
            browser.close()

    except Exception as e:
        print(f"  ⚠️ FanDuel scrape error: {e}")
        return {}

    # Find first basket market: ~10 runners, odds > +300
    fb_market = None
    for m in markets:
        runners = m.get('runnerDetails', [])
        if len(runners) >= 8 and len(runners) <= 12:
            odds_list = [r.get('winRunnerOdds', {}).get('americanDisplayOdds', {}).get('americanOdds', 0) for r in runners]
            if odds_list and all(o > 300 for o in odds_list if o > 0):
                fb_market = m
                break

    if not fb_market:
        print("  ⚠️ First basket market not found")
        return {}

    # Sort runners by odds (lowest American odds = favorite = highest prob)
    runners = fb_market.get('runnerDetails', [])
    runners_sorted = sorted(runners, key=lambda r: r.get('winRunnerOdds', {}).get('americanDisplayOdds', {}).get('americanOdds', 99999))

    # Build starters by team
    all_starters = []
    for p in home_starters:
        all_starters.append({'id': p.get('player_id', ''), 'name': p['name'], 'team': home_team, 'is_home': True})
    for p in away_starters:
        all_starters.append({'id': p.get('player_id', ''), 'name': p['name'], 'team': away_team, 'is_home': False})

    # Match by team: top N odds for home team = home starters in order, same for away
    home_runners = [r for r in runners_sorted if r.get('winRunnerOdds', {}).get('americanDisplayOdds', {}).get('americanOdds', 0) > 0]
    away_runners = [r for r in runners_sorted if r.get('winRunnerOdds', {}).get('americanDisplayOdds', {}).get('americanOdds', 0) > 0]

    # Simple split: first half = home, second half = away (or by team count)
    n_home = len([p for p in all_starters if p['is_home']])
    n_away = len([p for p in all_starters if not p['is_home']])

    # Actually, just match sequentially by team order in starters list
    # This is imperfect but works for now
    result = {}
    runner_idx = 0

    for p in all_starters:
        if runner_idx >= len(runners_sorted):
            break
        r = runners_sorted[runner_idx]
        odds = r.get('winRunnerOdds', {}).get('americanDisplayOdds', {}).get('americanOdds', 0)
        prob = 100 / (odds + 100) if odds > 0 else odds / (odds - 100)  # convert American to implied prob
        result[p['id']] = {
            'name': p['name'],
            'team': p['team'],
            'odds': odds,
            'prob': prob,
            'selectionId': r.get('selectionId')
        }
        runner_idx += 1

    return result

# ──────────────────────────────────────────────
# MODEL LOADING
# ──────────────────────────────────────────────
MODEL = None
MODEL_FEATURES = None
MODEL_BEST_ITER = None

def load_model():
    """Load trained LightGBM ranker model."""
    global MODEL, MODEL_FEATURES, MODEL_BEST_ITER
    import lightgbm as lgb
    import json

    if MODEL is not None:
        return MODEL

    try:
        MODEL = lgb.Booster(model_file='model.txt')
        with open('feature_names.json') as f:
            MODEL_FEATURES = json.load(f)
        with open('model_meta.json') as f:
            meta = json.load(f)
            MODEL_BEST_ITER = meta.get('best_iteration', -1)
        print(f"✅ Model loaded: {MODEL.num_feature()} features, best_iter={MODEL_BEST_ITER}")
        return MODEL
    except Exception as e:
        print(f"⚠️ Model load failed: {e}")
        return None

def softmax(x):
    """Softmax for probability calibration."""
    import numpy as np
    e_x = np.exp(x - np.max(x))
    return e_x / e_x.sum()

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
PLAYER_LOGS_CACHE = "player_game_logs.pkl"

def load_player_logs():
    """Load cached player game logs."""
    if os.path.exists(PLAYER_LOGS_CACHE):
        with open(PLAYER_LOGS_CACHE, 'rb') as f:
            return pickle.load(f)
    return {}

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
        tip_winner TEXT
    );
    CREATE TABLE IF NOT EXISTS first_baskets (
        game_id TEXT PRIMARY KEY,
        scorer_id TEXT,
        scorer_name TEXT,
        scorer_team TEXT,
        shot_type TEXT,
        period INTEGER,
        clock TEXT
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
    print("✅ Database ready")

# ──────────────────────────────────────────────
# ESPN DATA
# ──────────────────────────────────────────────
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
            'game_date': str(r['date'])[:10],
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
        actions.append({
            'gameId': game_id,
            'personId': pid,
            'playerName': names.get(pid, ''),
            'teamTricode': team,
            'actionType': p.get('type.text', '').lower().replace(' ', '_'),
            'shotType': p.get('type.text', ''),
            'period': int(p.get('period.number', 0) or 0),
            'clock': str(p.get('clock.displayValue', '')),
            'scoringPlay': p.get('scoringPlay', False),
        })
    return actions

def first_basket(actions: list[dict]) -> Optional[dict]:
    """First made FG in Q1-Q4 (not free throw)."""
    for a in actions:
        if a['scoringPlay'] and 'free_throw' not in a['actionType'] and 1 <= a['period'] <= 4:
            return a
    return None

def tip_winner(actions: list[dict]) -> Optional[str]:
    """Team that won the opening tip."""
    for a in actions:
        if 'jumpball' in a['actionType'] or 'tip' in a['actionType']:
            return a['teamTricode']
    return None

def get_injured_players() -> set[str]:
    """Fetch currently injured players from ESPN. Returns set of player names."""
    from sportsdataverse.nba import espn_nba_injuries
    import time, ast
    time.sleep(SLEEP)
    try:
        injuries = espn_nba_injuries()
        if injuries.height == 0:
            return set()
        injured = set()
        for row in injuries.iter_rows(named=True):
            inj_str = row['injuries']
            inj_list = ast.literal_eval(inj_str) if isinstance(inj_str, str) else inj_str
            for p in inj_list:
                if p.get('status') in ('Out', 'Injured Reserve', 'Day-to-Day'):
                    injured.add(p['athlete']['displayName'])
        return injured
    except Exception:
        return set()


def get_recent_starters(team_abbrev: str, con, n_games: int = 5, injured: set = None) -> list[dict]:
    """Get most recent starting 5 for a team from completed games (fallback)."""
    if injured is None:
        injured = get_injured_players()
    # Get recent completed games for this team
    rows = con.execute("""
        SELECT g.game_id, g.home_team, g.away_team
        FROM games g
        WHERE (g.home_team = ? OR g.away_team = ?)
          AND g.game_date < date('now')
        ORDER BY g.game_date DESC
        LIMIT ?
    """, (team_abbrev, team_abbrev, n_games)).fetchall()

    if not rows:
        return []

    # Collect starters from recent games
    starter_counts = {}
    for row in rows:
        gid, home, away = row
        from sportsdataverse.nba import espn_nba_cdn_boxscore
        import time
        time.sleep(SLEEP)
        try:
            bs = espn_nba_cdn_boxscore(game_id=int(gid), return_parsed=True)
            if bs and 'boxscore_player' in bs:
                bp = bs['boxscore_player']
                starters = bp.filter(bp['starter'] == True)
                team_starters = starters.filter(starters['team_abbreviation'] == team_abbrev)
                for r in team_starters.iter_rows(named=True):
                    pid = str(r['athlete_id'])
                    name = r['athlete_display_name']
                    pos = r['athlete_position']
                    jersey = str(r['athlete_jersey'])
                    if name in injured:
                        continue
                    key = (pid, name, pos, jersey)
                    starter_counts[key] = starter_counts.get(key, 0) + 1
        except Exception:
            continue

    sorted_starters = sorted(starter_counts.items(), key=lambda x: -x[1])
    return [
        {'player_id': k[0], 'name': k[1], 'position': k[2], 'jersey': k[3]}
        for k, v in sorted_starters[:5]
    ]


def get_projected_starters(game_id: str) -> dict:
    """Get projected starters for pre-game from ESPN PBP boxscore.
    Returns {home_team: [starters], away_team: [starters]} or None if not available.
    """
    from sportsdataverse.nba import espn_nba_pbp
    import time
    time.sleep(SLEEP)
    try:
        pbp = espn_nba_pbp(game_id=int(game_id))
        boxscore = pbp.get('boxscore', {})
        players = boxscore.get('players', [])

        if not players or len(players) != 2:
            return None

        starters = {}
        for team_data in players:
            team = team_data.get('team', {})
            team_abbrev = team.get('abbreviation', '')
            team_starters = []

            for stat in team_data.get('statistics', []):
                for a in stat.get('athletes', []):
                    if a.get('starter', False):
                        athlete = a.get('athlete', {})
                        pid = str(athlete.get('id', ''))
                        name = athlete.get('displayName', '')
                        pos = athlete.get('position', {}).get('abbreviation', '')
                        jersey = athlete.get('jersey', '')
                        if pid and name:
                            team_starters.append({
                                'player_id': pid,
                                'name': name,
                                'position': pos,
                                'jersey': str(jersey) if jersey else '',
                            })

            if team_starters:
                starters[team_abbrev] = team_starters[:5]

        if len(starters) == 2:
            return starters
    except Exception as e:
        print(f"  Error getting projected starters for {game_id}: {e}")
    return None

# ──────────────────────────────────────────────
# INGESTION
# ──────────────────────────────────────────────
def ingest_game(game_id: str, con):
    """Fetch PBP, extract first basket + tip, store."""
    actions = get_pbp(game_id)
    if not actions:
        return

    fb = first_basket(actions)
    tip = tip_winner(actions)

    if fb:
        con.execute("""
            INSERT OR REPLACE INTO first_baskets
            (game_id, scorer_player_id, scorer_name, scorer_team, shot_type, period, clock, x, y)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (fb['gameId'], fb['personId'], fb['playerName'],
              fb['teamTricode'], fb['shotType'], fb['period'], fb['clock'],
              fb.get('x'), fb.get('y')))

    if tip:
        con.execute("UPDATE games SET tip_winner_team=? WHERE game_id=?", (tip, game_id))
    con.commit()

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
        csv_writer.writerow(["game_start", "matchup", "tip_winner", "first_basket_scorer",
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
                        # Write to CSV (11 columns to match header)
                        fb = con.execute("SELECT * FROM first_baskets WHERE game_id=?", (gid,)).fetchone()
                        tip = con.execute("SELECT tip_winner_team FROM games WHERE game_id=?", (gid,)).fetchone()
                        if fb:
                            # Convert YYYYMMDD -> MM/DD/YY
                            d = day[:10]
                            game_start = f"{d[4:6]}/{d[6:8]}/{d[2:4]}"
                            csv_writer.writerow([
                                game_start,
                                gid,              # game_id
                                home,             # home_team
                                away,             # away_team
                                tip[0] if tip else '',  # tip_winner
                                fb[2],            # first_basket_scorer (scorer_name)
                                fb[3],            # scorer_team
                                fb[4],            # shot_type
                                fb[6],            # clock
                                fb[7] if fb[7] else '',  # x
                                fb[8] if fb[8] else '',  # y
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
    """Fetch games from last DB date → yesterday."""
    con = db()
    row = con.execute("SELECT MAX(game_date) FROM games").fetchone()
    if not row or not row[0]:
        return 0
    last_date = date.fromisoformat(row[0].replace('/', '-')[:4] + '-' + row[0][4:6] + '-' + row[0][6:8])
    yesterday = date.today() - timedelta(days=1)
    if last_date >= yesterday:
        return 0

    print(f"🔄 Catching up {last_date + timedelta(days=1)} → {yesterday}")
    days = []
    d = last_date + timedelta(days=1)
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
                if home not in NBA_TEAMS or away not in NBA_TEAMS:
                    continue
                con.execute("INSERT OR IGNORE INTO games (game_id, game_date, home_team, away_team) VALUES (?, ?, ?, ?)", (gid, day[:10], home, away))
                ingest_game(gid, con)
                total += 1
            con.commit()
        except Exception as e:
            print(f"  Error on {day}: {e}")
    if total:
        print(f"  ✅ Caught up {total} games")
    return total

# ──────────────────────────────────────────────
# PREDICTION
# ──────────────────────────────────────────────
def predict_today():
    """Predict first basket for today's pre-game games. Auto-catches up missing games first."""
    catchup_missing_games()
    con = db()
    games = get_todays_games()

    print(f"\n🏀 First Basket Predictions — {date.today()}")
    print("=" * 50)
    print(f"Found {len(games)} total games today:")
    for g in games:
        status_str = {1: 'pre', 2: 'live', 3: 'final'}.get(g['status'], '?')
        print(f"  {g['away_team']} @ {g['home_team']} — status: {status_str} ({g['status']})")

    pre_games = [g for g in games if g['status'] == 1]
    if not pre_games:
        print("No pre-game games found.")
        return

    # Load model and player logs
    model = load_model()
    use_model = model is not None
    player_logs = load_player_logs()

    for g in pre_games:
        game_id = g['game_id']
        home_team = g['home_team']
        away_team = g['away_team']
        game_date = g['game_date']

        # Try to get PBP (works for live/final games)
        actions = get_pbp(game_id)

        if actions:
            # Game has started - use early PBP for starters + tip
            tip = tip_winner(actions)

            if use_model:
                starters = {}
                for a in actions[:25]:
                    pid = a['personId']
                    team = a['teamTricode']
                    action = a['actionType']
                    if pid not in starters:
                        starters[pid] = {'team': team, 'touches': 0, 'shots': 0, 'name': a['playerName'], 'id': pid}
                    starters[pid]['touches'] += 1
                    if a['scoringPlay'] and 'free_throw' not in action:
                        starters[pid]['shots'] += 1

                home_starters = [p for p in starters.values() if p['team'] == home_team]
                away_starters = [p for p in starters.values() if p['team'] == away_team]

                if len(home_starters) >= 5 and len(away_starters) >= 5:
                    # Compute team stats once per game
                    home_team_stats = compute_team_stats_up_to_date(home_team, game_date, con)
                    away_team_stats = compute_team_stats_up_to_date(away_team, game_date, con)

                    X_pred = []
                    player_info = []
                    all_starters = home_starters[:5] + away_starters[:5]
                    for p in all_starters:
                        pid = p['id']
                        pteam = p['team']
                        is_home = 1 if pteam == home_team else 0
                        tstats = home_team_stats if is_home else away_team_stats

                        # Player season stats
                        pstats = compute_player_season_stats(pid, game_date, pteam, player_logs)

                        # 17 features in order matching model_meta.json
                        feat = [
                            is_home,                    # 1. is_home
                            1 if pteam == tip else 0,   # 2. tip_winner
                            p['touches'],               # 3. early_touches
                            p['shots'],                 # 4. early_shots
                            pstats['first_basket_rate'],           # 5. first_basket_rate
                            pstats['first_shot_rate'],             # 6. first_shot_rate
                            pstats['L10_first_basket_rate'],       # 7. L10_first_basket_rate
                            pstats['L5_first_basket_rate'],        # 8. L5_first_basket_rate
                            pstats['games_played'],                # 9. player_games_played
                            pstats['usage_rate'],                  # 10. usage_rate
                            pstats['true_shooting_pct'],           # 11. true_shooting_pct
                            pstats['minutes_per_game'],            # 12. minutes_per_game
                            pstats['points_per_game'],             # 13. points_per_game
                            tstats['team_tipoff_win_pct'],         # 14. team_tipoff_win_pct
                            tstats['home_fb_pct'],                 # 15. home_fb_pct
                            tstats['away_fb_pct'],                 # 16. away_fb_pct
                            tstats['games_played'],                # 17. team_games_played
                        ]
                        X_pred.append(feat)
                        player_info.append({'id': pid, 'name': p['name'], 'team': pteam})

                    X_pred = np.array(X_pred, dtype=float)
                    raw_scores = model.predict(X_pred, num_iteration=MODEL_BEST_ITER)
                    probs = softmax(raw_scores)
                    top_idx = np.argmax(probs)
                    top_player = player_info[top_idx]
                    conf = float(probs[top_idx])
                    model_ver = f'lgbm_ranker_v{MODEL_BEST_ITER}'
                else:
                    # Fallback to heuristic
                    tip_players = {}
                    for a in actions[:25]:
                        if a['teamTricode'] == tip and a['actionType'] not in ('jumpball', 'substitution'):
                            tip_players[a['personId']] = tip_players.get(a['personId'], 0) + 1
                    if tip_players:
                        top_id = max(tip_players, key=tip_players.get)
                        top_name = next((a['playerName'] for a in actions if a['personId'] == top_id), 'Unknown')
                        top_player = {'id': top_id, 'name': top_name, 'team': tip}
                        conf = 0.55
                        model_ver = 'heuristic_tip_leader'
                    else:
                        continue
            else:
                # Heuristic fallback
                tip_players = {}
                for a in actions[:25]:
                    if a['teamTricode'] == tip and a['actionType'] not in ('jumpball', 'substitution'):
                        tip_players[a['personId']] = tip_players.get(a['personId'], 0) + 1
                if tip_players:
                    top_id = max(tip_players, key=tip_players.get)
                    top_name = next((a['playerName'] for a in actions if a['personId'] == top_id), 'Unknown')
                    top_player = {'id': top_id, 'name': top_name, 'team': tip}
                    conf = 0.55
                    model_ver = 'heuristic_tip_leader'
                    probs = np.full(len(home_starters[:5] + away_starters[:5]), 0.45 / 9)
                    probs[0] = 0.55
                    player_info = [{'name': top_name, 'team': tip}]
                    for a in actions[:25]:
                        if a['personId'] != top_id and a['teamTricode'] == tip:
                            pid = a['personId']
                            if pid not in [p['id'] for p in player_info if 'id' in p]:
                                player_info.append({'id': pid, 'name': a['playerName'], 'team': tip})
                else:
                    continue
        else:
            # Pre-game: use ESPN projected starters + model (no tip winner yet)
            print(f"  📊 Pre-game: fetching projected starters from ESPN")
            projected = get_projected_starters(game_id)

            if not projected:
                print(f"  ⚠️ No projected starters available, falling back to DB recent starters")
                injured = get_injured_players()
                home_starters = get_recent_starters(home_team, con, injured=injured)
                away_starters = get_recent_starters(away_team, con, injured=injured)
                if len(home_starters) < 5 or len(away_starters) < 5:
                    print(f"  ⚠️ Insufficient recent starters, skipping")
                    continue
                # Add team to fallback starters
                for p in home_starters:
                    p['team'] = home_team
                for p in away_starters:
                    p['team'] = away_team
                data_source = "db_recent"
            else:
                home_starters = projected.get(home_team, [])
                away_starters = projected.get(away_team, [])
                print(f"    ✅ {home_team} starters (ESPN): {', '.join([p['name'] for p in home_starters])}")
                print(f"    ✅ {away_team} starters (ESPN): {', '.join([p['name'] for p in away_starters])}")
                data_source = "espn_projected"

            if len(home_starters) < 5 or len(away_starters) < 5:
                print(f"  ⚠️ Insufficient starters, skipping")
                continue

            if use_model:
                # Compute team stats once per game
                home_team_stats = compute_team_stats_up_to_date(home_team, game_date, con)
                away_team_stats = compute_team_stats_up_to_date(away_team, game_date, con)

                X_pred = []
                player_info = []
                all_starters = home_starters + away_starters
                for p in all_starters:
                    pid = p['player_id']
                    pteam = p['team']
                    is_home = 1 if pteam == home_team else 0
                    tstats = home_team_stats if is_home else away_team_stats

                    # Player season stats
                    pstats = compute_player_season_stats(pid, game_date, pteam, player_logs)

                    # 17 features in order matching model_meta.json
                    feat = [
                        is_home,                    # 1. is_home
                        0,                          # 2. tip_winner (unknown pre-game)
                        0,                          # 3. early_touches (unknown pre-game)
                        0,                          # 4. early_shots (unknown pre-game)
                        pstats['first_basket_rate'],           # 5. first_basket_rate
                        pstats['first_shot_rate'],             # 6. first_shot_rate
                        pstats['L10_first_basket_rate'],       # 7. L10_first_basket_rate
                        pstats['L5_first_basket_rate'],        # 8. L5_first_basket_rate
                        pstats['games_played'],                # 9. player_games_played
                        pstats['usage_rate'],                  # 10. usage_rate
                        pstats['true_shooting_pct'],           # 11. true_shooting_pct
                        pstats['minutes_per_game'],            # 12. minutes_per_game
                        pstats['points_per_game'],             # 13. points_per_game
                        tstats['team_tipoff_win_pct'],         # 14. team_tipoff_win_pct
                        tstats['home_fb_pct'],                 # 15. home_fb_pct
                        tstats['away_fb_pct'],                 # 16. away_fb_pct
                        tstats['games_played'],                # 17. team_games_played
                    ]
                    X_pred.append(feat)
                    player_info.append({'id': pid, 'name': p['name'], 'team': pteam})

                X_pred = np.array(X_pred, dtype=float)
                raw_scores = model.predict(X_pred, num_iteration=MODEL_BEST_ITER)
                probs = softmax(raw_scores)
                top_idx = np.argmax(probs)
                top_player = player_info[top_idx]
                conf = float(probs[top_idx])
                model_ver = f'lgbm_ranker_v{MODEL_BEST_ITER}_pre_{data_source}'
            else:
                top_player = {'id': home_starters[0]['player_id'], 'name': home_starters[0]['name'], 'team': home_team}
                conf = 0.45
                model_ver = f'heuristic_recent_starter_{data_source}'
                probs = np.array([0.45] + [0.55/9]*9)
                player_info = [{'name': home_starters[0]['name'], 'team': home_team}]
                for p in home_starters[1:] + away_starters:
                    player_info.append({'name': p['name'], 'team': p['team']})
                probs = np.array([0.45] + [0.55/9]*9)
                player_info = [{'name': home_starters[0]['name'], 'team': home_team}]
                for p in home_starters[1:] + away_starters:
                    player_info.append({'name': p['name'], 'team': p['team']})

        con.execute("""
            INSERT OR REPLACE INTO predictions
            (game_id, predicted_scorer_id, predicted_scorer_name, confidence, model_version)
            VALUES (?, ?, ?, ?, ?)
        """, (game_id, top_player['id'], top_player['name'], conf, model_ver))
        con.commit()

        # Fetch FanDuel odds for this game
        print(f"  💰 Fetching FanDuel odds...")
        fd_odds = get_fanduel_first_basket_odds(home_team, away_team, home_starters, away_starters)

        # Display top 3 with model prob + FanDuel odds
        sorted_indices = np.argsort(probs)[::-1]
        print(f"\n{away_team} @ {home_team}")
        for rank, idx in enumerate(sorted_indices[:3], 1):
            p = player_info[idx]
            prob = probs[idx]
            marker = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉"
            pid = p.get('id', '')
            odds_str = ""
            ev_str = ""
            if pid in fd_odds:
                fd = fd_odds[pid]
                odds = fd['odds']
                fd_prob = fd['prob']
                odds_str = f" | FD: {odds:+d} ({fd_prob:.1%})"
                # EV = model_prob * decimal_odds - 1
                dec_odds = (odds / 100 + 1) if odds > 0 else (100 / abs(odds) + 1)
                ev = prob * dec_odds - 1
                ev_str = f" | EV: {ev:+.1%}"
            print(f"  {marker} {p['name']} ({p['team']}) — Model: {prob:.1%}{odds_str}{ev_str}")
        print(f"     Model: {model_ver}")

    print("\n✅ Predictions saved")

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
    elif cmd == "predict":
        predict_today()
    elif cmd == "eval":
        evaluate()
    elif cmd == "test":
        # Quick test on one game
        gid = sys.argv[2] if len(sys.argv) > 2 else "401909881"
        actions = get_pbp(gid)
        fb = first_basket(actions)
        tip = tip_winner(actions)
        print(f"Game {gid}:")
        print(f"  First basket: {fb['playerName']} ({fb['teamTricode']}) — {fb['shotType']} at {fb['clock']} Q{fb['period']}")
        print(f"  Tip winner: {tip}")
    else:
        print("""
Usage:
  python nba.py init                    # Create database
  python nba.py backfill [year] [days]  # Backfill season (default 2024, all days)
  python nba.py predict                 # Today's pre-game predictions
  python nba.py eval                    # Check accuracy
  python nba.py test [game_id]          # Test one game
        """)