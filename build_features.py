#!/usr/bin/env python3
"""
Feature Engineering for NBA First Basket Predictor
Builds player-level features for all 10 starters per game.
No data leakage — all stats computed up to game date only.
Checkpointing enabled: saves progress every 50 games, resumes on restart.
Parallel workers: --workers N (default 2, max 3 to respect ESPN limits)
Incremental mode: --incremental only processes new games since last build
"""

from sportsdataverse.nba import espn_nba_scoreboard, espn_nba_pbp, espn_nba_cdn_boxscore
import sqlite3
import time
import json
import numpy as np
from collections import defaultdict
import warnings
import signal
import sys
import os
import argparse
import pickle
warnings.filterwarnings("ignore")

DB = "nba_first_basket.db"
BASE_SLEEP = 0.3  # base delay between requests
CHECKPOINT_EVERY = 50
# Columns saved in X_train.npy, in order. Only pre-game information (no play-by-play from the game itself).
# The tip-off chance is not stored here: train_pregame.py and nba.py compute it with tip_model.py.
NUMERIC_FEATURES = [
    'is_home', 'first_basket_rate', 'L10_first_basket_rate', 'player_games_played',
    'shot_attempts_per36', 'true_shooting_pct', 'minutes_per_game', 'points_per_game',
    'team_tipoff_win_pct', 'first_shot_share', 'first_shot_starts', 'early_shot_share', 'early_shot_count',
]
CHECKPOINT_FILE = "build_features_checkpoint.npz"
PLAYER_LOGS_CACHE = "player_game_logs.pkl"
MAX_WORKERS = 3   # hard cap to avoid ESPN bans

# ──────────────────────────────────────────────
# DATABASE HELPERS
# ──────────────────────────────────────────────
def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def get_all_games():
    """All games with first basket labels, ordered by date."""
    con = db()
    cur = con.cursor()
    cur.execute('''
        SELECT g.game_id, g.game_date, g.home_team, g.away_team, g.tip_winner_team,
               fb.scorer_player_id, fb.scorer_name, fb.scorer_team
        FROM games g
        JOIN first_baskets fb ON g.game_id = fb.game_id
        ORDER BY g.game_date
    ''')
    return cur.fetchall()


# ──────────────────────────────────────────────
# PLAYER GAME LOGS - PRE-FETCH AND CACHE
# ──────────────────────────────────────────────
def sync_first_basket_flags(player_logs, con) -> int:
    """Make each log entry's first_basket flag match the database's current label for that game (labels can
    change: a retried game, or the 2026-11-17 switch to counting free throws). Returns entries changed."""
    scorer = dict(con.execute("SELECT game_id, scorer_player_id FROM first_baskets"))
    changed = 0
    for pid, logs in player_logs.items():
        for g in logs:
            flag = 1 if scorer.get(g['game_id']) == pid else 0
            if g['first_basket'] != flag:
                g['first_basket'] = flag
                changed += 1
    return changed


def build_player_game_logs():
    """
    Pre-fetch all boxscores and build a player game log index.
    Returns dict: {player_id: [{game_date, game_id, minutes, fgm, fga, fg3m, fg3a, ftm, fta, pts, plus_minus, first_basket}, ...]}
    Sorted by game_date DESC (most recent first).
    Incremental: loads the cache and fetches box scores only for labeled games not in it yet.
    """
    player_logs = defaultdict(list)
    if os.path.exists(PLAYER_LOGS_CACHE):
        print(f"📂 Loading cached player game logs from {PLAYER_LOGS_CACHE}")
        with open(PLAYER_LOGS_CACHE, 'rb') as f:
            player_logs.update(pickle.load(f))
    known = {g['game_id'] for logs in player_logs.values() for g in logs}
    hist = os.path.join("data_patches", "history_2024_25.json.gz")   # 2024-25 box scores (player history, nba.py)
    if os.path.exists(hist):
        import gzip, json as _json
        for pid, lst in _json.loads(gzip.open(hist, "rt").read())["logs"].items():
            add = [g for g in lst if g["game_id"] not in known]
            if add:
                player_logs[pid].extend(add); player_logs[pid].sort(key=lambda g: g["game_date"], reverse=True)
        known = {g['game_id'] for logs in player_logs.values() for g in logs}
    import glob as _glob, json as _json2                 # games only NBA.com has (data_patches/*.json, "logs")
    for f in sorted(_glob.glob(os.path.join("data_patches", "*.json"))):
        try:
            games = _json2.load(open(f)).get("games", [])
        except Exception:  # noqa: BLE001 — a broken patch never blocks the logs
            continue
        for gm in games:
            for pid, entry in (gm.get("logs") or {}).items():
                if entry["game_id"] not in known:
                    player_logs[pid].append(dict(entry))
                    player_logs[pid].sort(key=lambda g: g["game_date"], reverse=True)
    known = {g['game_id'] for logs in player_logs.values() for g in logs}

    con = db()
    # A game saved while it was still being played has too few box-score lines: drop them so it's fetched again
    # (2026-11-19: PHX @ SAC 11/18/26 had 20 of 26 players). Compared with the players who had minutes (lineups).
    played = dict(con.execute("""SELECT l.game_id, COUNT(*) FROM lineups l JOIN games g USING(game_id)
                                 WHERE l.minutes > 0 AND g.game_date >= '20260801' GROUP BY l.game_id"""))
    have_n = defaultdict(int)
    for logs in player_logs.values():
        for g in logs:
            have_n[g['game_id']] += 1
    short = {gid for gid, n in played.items() if 0 < have_n.get(gid, 0) < n}
    if short:
        for pid in list(player_logs):
            player_logs[pid] = [g for g in player_logs[pid] if g['game_id'] not in short]
        known -= short
    changed = sync_first_basket_flags(player_logs, con) + len(short)

    # Get all games with first basket info
    all_games = [r for r in con.execute('''
        SELECT g.game_id, g.game_date, g.home_team, g.away_team, fb.scorer_player_id
        FROM games g
        JOIN first_baskets fb ON g.game_id = fb.game_id
        ORDER BY g.game_date
    ''').fetchall() if r[0] not in known]

    if not all_games:
        if changed:
            with open(PLAYER_LOGS_CACHE, 'wb') as f:
                pickle.dump(dict(player_logs), f)
        return dict(player_logs)

    print(f"🔄 Adding box scores for {len(all_games)} games to player game logs...")

    for idx, (game_id, game_date, home_team, away_team, scorer_id) in enumerate(all_games):
        if idx % 100 == 0:
            print(f"  [{idx}/{len(all_games)}] {game_date}: {away_team} @ {home_team}")

        time.sleep(BASE_SLEEP)
        try:
            bs = espn_nba_cdn_boxscore(game_id=int(game_id), return_parsed=True)
            if not bs or 'boxscore_player' not in bs:
                continue

            bp = bs['boxscore_player']

            # Process all players in this game
            for row in bp.iter_rows(named=True):
                pid = str(row.get('athlete_id', ''))
                if not pid:
                    continue

                minutes_raw = row.get('minutes')
                minutes = 0 if minutes_raw in ('--', '', None) else int(minutes_raw)
                if minutes == 0:
                    continue  # DNP

                # Parse combined columns
                fg_str = row.get('field_goals_made_field_goals_attempted', '0-0') or '0-0'
                fg3_str = row.get('three_point_field_goals_made_three_point_field_goals_attempted', '0-0') or '0-0'
                ft_str = row.get('free_throws_made_free_throws_attempted', '0-0') or '0-0'

                def parse_made_att(s):
                    try:
                        parts = s.split('-')
                        return int(parts[0]), int(parts[1])
                    except:
                        return 0, 0

                fgm, fga = parse_made_att(fg_str)
                fg3m, fg3a = parse_made_att(fg3_str)
                ftm, fta = parse_made_att(ft_str)

                pts = int(row.get('points', 0) or 0)
                plus_minus = int(row.get('plus_minus', 0) or 0)

                # Check if this player scored first basket
                first_basket = 1 if pid == str(scorer_id) else 0

                player_logs[pid].append({
                    'game_date': game_date,
                    'game_id': game_id,
                    'minutes': minutes,
                    'fgm': fgm, 'fga': fga,
                    'fg3m': fg3m, 'fg3a': fg3a,
                    'ftm': ftm, 'fta': fta,
                    'pts': pts,
                    'plus_minus': plus_minus,
                    'first_basket': first_basket,
                })

        except Exception as e:
            print(f"  ⚠️ Boxscore error for {game_id}: {e}")
            continue

    # Sort each player's logs by game_date DESC (most recent first)
    for pid in player_logs:
        player_logs[pid].sort(key=lambda x: x['game_date'], reverse=True)

    print(f"  Built logs for {len(player_logs)} players")

    # Save cache
    with open(PLAYER_LOGS_CACHE, 'wb') as f:
        pickle.dump(dict(player_logs), f)
    print(f"  💾 Saved player game logs cache to {PLAYER_LOGS_CACHE}")

    return dict(player_logs)


# ──────────────────────────────────────────────
# SEASON STATS COMPUTATION (no leakage — up to game_date)
# ──────────────────────────────────────────────
def three_point_rate(player_logs, player_id, game_date) -> float:
    """3-point attempts / field-goal attempts in his games BEFORE game_date, shrunk toward 0.38 with 100 attempts."""
    d = str(game_date).replace('-', '')[:8]
    g = [x for x in player_logs.get(str(player_id), []) if x['game_date'] < d]
    fga = sum(x.get('fga', 0) for x in g); f3 = sum(x.get('fg3a', 0) for x in g)
    return (f3 + 0.38 * 100) / (fga + 100)


def compute_player_season_stats(player_id, game_date, team_abbrev, player_logs):
    """A player's stats from his games BEFORE game_date (no leakage). Logs are newest first.
    Keys: first_basket_rate, L10_first_basket_rate, games_played, shot_attempts_per36
    ((FGA + 0.44 x FTA) per 36 minutes), true_shooting_pct, minutes_per_game, points_per_game."""
    game_date_str = str(game_date).replace('-', '')[:8]
    prior = [g for g in player_logs.get(player_id, []) if g['game_date'] < game_date_str]
    n = len(prior)
    if n == 0:
        return {'first_basket_rate': 0.0, 'L10_first_basket_rate': 0.0, 'games_played': 0,
                'shot_attempts_per36': 0.0, 'true_shooting_pct': 0.0,
                'minutes_per_game': 0.0, 'points_per_game': 0.0, 'three_rate': 0.38}

    total_min = sum(g['minutes'] for g in prior)
    total_fga = sum(g['fga'] for g in prior)
    total_fta = sum(g['fta'] for g in prior)
    total_pts = sum(g['pts'] for g in prior)
    shots = total_fga + 0.44 * total_fta
    last10 = prior[:10]
    return {
        'first_basket_rate': sum(g['first_basket'] for g in prior) / n,
        'L10_first_basket_rate': sum(g['first_basket'] for g in last10) / len(last10),
        'games_played': n,
        'shot_attempts_per36': shots * 36 / total_min if total_min > 0 else 0.0,
        'true_shooting_pct': total_pts / (2 * shots) if shots > 0 else 0.0,
        'minutes_per_game': total_min / n,
        'points_per_game': total_pts / n,
        'three_rate': three_point_rate(player_logs, player_id, game_date),
    }


def build_first_shot_index(con) -> dict:
    """{player_id: (dates, took)} for every game he STARTED that has first-shot data for his team, sorted by date.
    took[i] = 1 if he took his team's first field-goal attempt that game. Used by first_shot_share."""
    from bisect import insort
    rows = con.execute("""SELECT l.player_id, g.game_date, (fs.player_id = l.player_id)
                          FROM lineups l JOIN games g ON g.game_id = l.game_id
                          JOIN first_shots fs ON fs.game_id = l.game_id AND fs.team = l.team
                          WHERE l.is_starter = 1""").fetchall()
    per = defaultdict(list)
    for pid, d, took in rows:
        insort(per[str(pid)], (d, int(took or 0)))
    return {pid: ([d for d, _ in v], [t for _, t in v]) for pid, v in per.items()}


def first_shot_share(index: dict, player_id, game_date) -> tuple:
    """(share of his starts BEFORE game_date in which he took the team's first shot, number of those starts)."""
    from bisect import bisect_left
    dates, took = index.get(str(player_id), ([], []))
    n = bisect_left(dates, str(game_date).replace('-', '')[:8])
    return (sum(took[:n]) / n, n) if n else (0.0, 0)


def build_early_shot_index(con) -> dict:
    """{player_id: (dates, mine, team_total)} over the games he STARTED that have early-shot data: how many of his
    team's first (up to 5) field-goal attempts he took, and how many the team took. Sorted by date."""
    from bisect import insort
    rows = con.execute("""SELECT l.player_id, g.game_date, SUM(es.player_id = l.player_id), COUNT(*)
                          FROM lineups l JOIN games g ON g.game_id = l.game_id
                          JOIN early_shots es ON es.game_id = l.game_id AND es.team = l.team
                          WHERE l.is_starter = 1 GROUP BY l.game_id, l.team, l.player_id""").fetchall()
    per = defaultdict(list)
    for pid, d, mine, tot in rows:
        insort(per[str(pid)], (d, int(mine or 0), int(tot)))
    return {pid: ([d for d, _, _ in v], np.cumsum([0] + [m for _, m, _ in v]), np.cumsum([0] + [t for _, _, t in v]))
            for pid, v in per.items()}


def early_shot_share(index: dict, player_id, game_date) -> tuple:
    """(his share of his team's first shots over his starts BEFORE game_date, number of those team shots)."""
    from bisect import bisect_left
    if str(player_id) not in index:
        return (0.0, 0)
    dates, mine, tot = index[str(player_id)]
    n = bisect_left(dates, str(game_date).replace('-', '')[:8])
    return (float(mine[n]) / float(tot[n]), int(tot[n])) if tot[n] else (0.0, 0)


def compute_team_stats_up_to_date(team_abbrev, game_date, con):
    """Team stats from games BEFORE game_date: how often the team won the opening tip.
    Games without a recorded tip are left out (not counted as losses). 0.5 with no history."""
    game_date_str = str(game_date).replace('-', '')[:8]
    rows = con.execute('''
        SELECT g.tip_winner_team FROM games g
        JOIN first_baskets fb ON g.game_id = fb.game_id
        WHERE (g.home_team = ? OR g.away_team = ?) AND g.game_date < ? AND g.tip_winner_team IS NOT NULL
    ''', (team_abbrev, team_abbrev, game_date_str)).fetchall()
    return {'team_tipoff_win_pct': sum(r[0] == team_abbrev for r in rows) / len(rows) if rows else 0.5,
            'games_played': len(rows)}


# ──────────────────────────────────────────────
# ESPN DATA FETCHERS (with caching)
# ──────────────────────────────────────────────
class ESPNCache:
    def __init__(self):
        self.scoreboard_cache = {}
        self.boxscore_cache = {}
        self.pbp_cache = {}

    def get_scoreboard(self, game_date):
        key = game_date
        if key not in self.scoreboard_cache:
            time.sleep(BASE_SLEEP)
            df = espn_nba_scoreboard(dates=[key], return_as_pandas=True)
            self.scoreboard_cache[key] = df
        return self.scoreboard_cache[key]

    def get_boxscore(self, game_id):
        key = str(game_id)
        if key not in self.boxscore_cache:
            time.sleep(BASE_SLEEP)
            try:
                bs = espn_nba_cdn_boxscore(game_id=int(game_id), return_parsed=True)
                self.boxscore_cache[key] = bs
            except Exception as e:
                print(f"  Boxscore error for {game_id}: {e}")
                self.boxscore_cache[key] = None
        return self.boxscore_cache[key]

    def get_pbp(self, game_id):
        key = str(game_id)
        if key not in self.pbp_cache:
            time.sleep(BASE_SLEEP)
            try:
                pbp = espn_nba_pbp(game_id=int(game_id))
                self.pbp_cache[key] = pbp
            except Exception as e:
                print(f"  PBP error for {game_id}: {e}")
                self.pbp_cache[key] = None
        return self.pbp_cache[key]

cache = ESPNCache()

# ──────────────────────────────────────────────
# STARTER EXTRACTION
# ──────────────────────────────────────────────
def stored_starters(game_id, home_team, away_team):
    """Starting fives saved in the `lineups` table (ESPN box scores), or None unless both teams have exactly 5."""
    con = db()
    out = {}
    for team in (home_team, away_team):
        rows = con.execute("""SELECT player_id, player_name FROM lineups
                              WHERE game_id = ? AND team = ? AND is_starter = 1""", (str(game_id), team)).fetchall()
        if len(rows) != 5:
            return None
        out[team] = [{'player_id': str(pid), 'name': name, 'position': '', 'jersey': ''} for pid, name in rows]
    return out


def get_game_starters(game_id, cache, home_team, away_team):
    """Get starting 5 for each team — returns {home_abbrev: [...], away_abbrev: [...]}.
    Uses the saved lineups first (no download); falls back to ESPN only for games without them."""
    saved = stored_starters(game_id, home_team, away_team)
    if saved:
        return saved
    # Try CDN boxscore (boxscore_player is DataFrame with starter column)
    bs = cache.get_boxscore(game_id)
    if bs and 'boxscore_player' in bs:
        bp = bs['boxscore_player']
        starters_df = bp.filter(bp['starter'] == True)
        starters = {}
        for team_abbrev in [home_team, away_team]:
            team_starters_df = starters_df.filter(starters_df['team_abbreviation'] == team_abbrev)
            if team_starters_df.height == 0:
                continue
            team_starters = []
            for row in team_starters_df.iter_rows(named=True):
                team_starters.append({
                    'player_id': str(row['athlete_id']),
                    'name': row['athlete_display_name'],
                    'position': row['athlete_position'],
                    'jersey': str(row['athlete_jersey']),
                })
            if team_starters:
                starters[team_abbrev] = team_starters
        if len(starters) == 2:
            return starters

    # Fallback: PBP boxscore — first 5 athletes per team are starters
    pbp = cache.get_pbp(game_id)
    if not pbp:
        return None
    boxscore = pbp.get('boxscore', {})
    if not boxscore:
        return None

    players_by_team = boxscore.get('players', [])
    if not players_by_team:
        return None

    starters = {}
    for team_data in players_by_team:
        team_info = team_data.get('team', {})
        team_abbrev = team_info.get('abbreviation', '')
        team_starters = []
        for stat in team_data.get('statistics', []):
            for a in stat.get('athletes', []):
                athlete = a.get('athlete', {})
                pid = str(athlete.get('id', ''))
                name = athlete.get('displayName', '')
                position = athlete.get('position', {}).get('abbreviation', '')
                jersey = athlete.get('jersey', '')
                team_starters.append({
                    'player_id': pid,
                    'name': name,
                    'position': position,
                    'jersey': jersey,
                })
        # First 5 = starters
        if team_starters:
            starters[team_abbrev] = team_starters[:5]

    return starters if len(starters) == 2 else None




# ──────────────────────────────────────────────
# CHECKPOINT HELPERS (verified save/load)
# ──────────────────────────────────────────────
def save_checkpoint(all_features, all_labels, feature_names, idx, total_games):
    """Save progress checkpoint with verification."""
    if not all_features:
        print(f"  ⚠️ Skipping empty checkpoint at game {idx}")
        return
    if feature_names is None:
        print(f"  ⚠️ Skipping checkpoint - no feature names at game {idx}")
        return

    tmp_file = CHECKPOINT_FILE + ".tmp.npz"
    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(os.path.abspath(tmp_file)), exist_ok=True)

        np.savez_compressed(
            tmp_file,
            features=np.array(all_features, dtype=object),
            labels=np.array(all_labels, dtype=object),
            feature_names=np.array(feature_names, dtype=object),
            idx=np.int64(idx),
            total=np.int64(total_games)
        )
        # Verify
        verify = np.load(tmp_file, allow_pickle=True)
        if len(verify['features']) == 0:
            raise ValueError("Saved checkpoint has 0 features")
        # Atomic rename (replaces existing)
        os.replace(tmp_file, CHECKPOINT_FILE)
        print(f"  💾 Checkpoint saved at game {idx}/{total_games} ({len(all_features)} samples)")
    except Exception as e:
        print(f"  ❌ Checkpoint save failed: {e}")
        if os.path.exists(tmp_file):
            os.remove(tmp_file)

def load_checkpoint():
    """Load progress checkpoint if exists and valid."""
    if not os.path.exists(CHECKPOINT_FILE):
        return None, None, None, 0
    try:
        data = np.load(CHECKPOINT_FILE, allow_pickle=True)
        print(f"📂 Checkpoint keys: {list(data.keys())}")

        features = data['features']
        labels = data['labels']
        feature_names = data['feature_names']
        idx = int(data['idx'])
        total = int(data['total'])

        # Convert from object arrays
        if isinstance(features, np.ndarray) and features.dtype == object:
            features = features.tolist()
        if isinstance(labels, np.ndarray) and labels.dtype == object:
            labels = labels.tolist()
        if isinstance(feature_names, np.ndarray) and feature_names.dtype == object:
            feature_names = feature_names.tolist()

        if len(features) == 0:
            print("  ⚠️ Checkpoint has 0 features — starting fresh")
            return None, None, None, 0

        print(f"📂 Resuming from checkpoint: game {idx}/{total} ({len(features)} samples)")
        return features, labels, feature_names, idx
    except Exception as e:
        print(f"⚠️ Checkpoint load failed: {e}")
        import traceback
        traceback.print_exc()
        return None, None, None, 0

def cleanup_checkpoint():
    """Remove checkpoint file after successful completion."""
    if os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)


def load_existing_data():
    """Load existing training data if present. Returns (X, y, groups, game_ids, feature_names, meta) or None."""
    required_files = ['X_train.npy', 'y_train.npy', 'groups.npy', 'game_ids.json', 'feature_names.json']
    for f in required_files:
        if not os.path.exists(f):
            return None
    try:
        X = np.load('X_train.npy')
        y = np.load('y_train.npy')
        groups = np.load('groups.npy')
        with open('game_ids.json') as f:
            game_ids = json.load(f)
        with open('feature_names.json') as f:
            feature_names = json.load(f)
        meta = {}
        if os.path.exists('meta.json'):
            with open('meta.json') as f:
                meta = json.load(f)
        print(f"📂 Loaded existing data: {len(game_ids)} games, {X.shape[0]} samples, {X.shape[1]} features")
        return X, y, groups, game_ids, feature_names, meta
    except Exception as e:
        print(f"⚠️ Failed to load existing data: {e}")
        return None




def append_to_arrays(X_old, y_old, groups_old, game_ids_old, X_new, y_new, groups_new, game_ids_new):
    """Append new data to existing arrays."""
    X_combined = np.vstack([X_old, X_new])
    y_combined = np.concatenate([y_old, y_new])
    groups_combined = np.concatenate([groups_old, groups_new])
    game_ids_combined = game_ids_old + game_ids_new
    return X_combined, y_combined, groups_combined, game_ids_combined


# ──────────────────────────────────────────────
# MAIN FEATURE BUILDER (with checkpointing + incremental)
# ──────────────────────────────────────────────
def build_features(incremental: bool = False):
    """Build the training matrices. incremental=True keeps the saved matrices and adds every labelled game
    that is not in them yet — whatever its date, so a game labelled late is still added."""
    existing = load_existing_data() if incremental else None
    if incremental and not existing:
        print("📦 No existing data found, doing full build")
    if existing:
        X_old, y_old, groups_old, game_ids_old, feature_names_old, meta_old = existing
        if list(feature_names_old) != NUMERIC_FEATURES:
            raise SystemExit("The saved training matrices have different columns than this code builds:\n"
                             f"  saved: {feature_names_old}\n  code:  {NUMERIC_FEATURES}\n"
                             "Rebuild them once: python3 build_features.py   (without --incremental)")

    games = get_all_games()
    appending = bool(existing)          # incremental run that adds games to the saved matrices
    start_idx = 0
    if appending:
        have = {str(g) for g in game_ids_old}
        games = [g for g in games if str(g[0]) not in have]
        if not games:
            print("✅ No new games to process — feature matrix is up to date")
            return
        print(f"🔄 Incremental mode: adding {len(games)} games not yet in the training data")
    else:
        print(f"Building features for {len(games)} games...")
    total_games = len(games)
    first_shots = None                   # built on first use (needs the database)

    # Pre-build player game logs (cached)
    player_logs = build_player_game_logs()

    # In incremental mode: clear old checkpoint (it's from a full run, not this incremental range)
    if incremental and os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)
        print("🗑️ Cleared old checkpoint (from full run)")

    # Load checkpoint (only for crash recovery within THIS run)
    # In incremental mode: don't overwrite start_idx if already calculated from last processed date
    checkpoint_features, checkpoint_labels, checkpoint_feature_names, checkpoint_idx = load_checkpoint()
    if checkpoint_features is not None:
        all_features = checkpoint_features
        all_labels = checkpoint_labels
        feature_names = checkpoint_feature_names
        # Only use checkpoint idx if we don't already have a valid incremental start_idx
        if start_idx == 0:
            start_idx = checkpoint_idx
    else:
        all_features = []
        all_labels = []
        feature_names = ['game_id'] + NUMERIC_FEATURES
        if start_idx == 0:
            start_idx = 0

    # Use mutable container for current index (fixes signal handler scoping)
    current_idx = [start_idx]

    # Setup signal handler for graceful shutdown
    def signal_handler(sig, frame):
        print(f"\n🛑 Interrupted at game {current_idx[0]} — saving checkpoint...")
        print(f"  Debug: all_features={len(all_features)}, all_labels={len(all_labels)}, feature_names={feature_names is not None}")
        save_checkpoint(all_features, all_labels, feature_names, current_idx[0], total_games)
        sys.exit(0)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # `games` is already cut down to the new games when appending, so start at its first entry
    for idx in range(0 if appending else start_idx, total_games):
        current_idx[0] = idx
        game = games[idx]
        game_id, game_date, home_team, away_team, tip_winner, scorer_id, scorer_name, scorer_team = game

        print(f"  [{idx}/{total_games}] {game_date}: {away_team} @ {home_team}")

        # Get starters
        starters_by_team = get_game_starters(game_id, cache, home_team, away_team)
        if not starters_by_team:
            print(f"    SKIP {game_id}: no boxscore")
            continue

        home_starters = starters_by_team.get(home_team, [])
        away_starters = starters_by_team.get(away_team, [])

        if not home_starters or not away_starters:
            print(f"    SKIP {game_id}: missing starters for {home_team} or {away_team}")
            continue

        all_starters = home_starters + away_starters
        if len(all_starters) != 10:
            print(f"    SKIP {game_id}: {len(all_starters)} starters (need 10)")
            continue

        # Season stats (computed up to game_date, no leakage)
        con = db()
        if first_shots is None:
            first_shots = build_first_shot_index(con)     # who took each team's first shot, by player
            early = build_early_shot_index(con)           # each player's share of his team's first 5 shots
        home_team_stats = compute_team_stats_up_to_date(home_team, game_date, con)
        away_team_stats = compute_team_stats_up_to_date(away_team, game_date, con)

        # Build feature vector for each starter
        game_features = []
        game_labels = []

        for starter in all_starters:
            pid = starter['player_id']
            is_home = starter in home_starters
            player_stats = compute_player_season_stats(pid, game_date, home_team if is_home else away_team, player_logs)
            team_stats = home_team_stats if is_home else away_team_stats
            feat = {
                'game_id': game_id,                          # used to group the 10 starters of a game
                'is_home': int(is_home),
                'first_basket_rate': player_stats['first_basket_rate'],
                'L10_first_basket_rate': player_stats['L10_first_basket_rate'],
                'player_games_played': player_stats['games_played'],
                'shot_attempts_per36': player_stats['shot_attempts_per36'],
                'true_shooting_pct': player_stats['true_shooting_pct'],
                'minutes_per_game': player_stats['minutes_per_game'],
                'points_per_game': player_stats['points_per_game'],
                'team_tipoff_win_pct': team_stats['team_tipoff_win_pct'],
            }
            feat['first_shot_share'], feat['first_shot_starts'] = first_shot_share(first_shots, pid, game_date)
            feat['early_shot_share'], feat['early_shot_count'] = early_shot_share(early, pid, game_date)
            game_features.append(feat)
            game_labels.append(1 if pid == scorer_id else 0)

        if sum(game_labels) != 1:
            print(f"  WARNING: game {game_id} label mismatch")
            continue

        all_features.extend(game_features)
        all_labels.extend(game_labels)

        if feature_names is None:
            feature_names = list(feat.keys())

        # Checkpoint every CHECKPOINT_EVERY games
        if (idx + 1) % CHECKPOINT_EVERY == 0:
            save_checkpoint(all_features, all_labels, feature_names, idx + 1, total_games)

    # Convert new features to arrays
    X_new = np.array([[f[name] for name in NUMERIC_FEATURES] for f in all_features], dtype=float)
    y_new = np.array(all_labels, dtype=int)
    groups_new = []  # for ranking: group by game_id
    current_gid = None
    count = 0
    for f in all_features:
        gid = f.get('game_id')
        if gid is None:
            print(f"  WARNING: feature missing game_id: {f}")
            continue
        if gid != current_gid:
            if current_gid is not None:
                groups_new.append(count)
            current_gid = gid
            count = 1
        else:
            count += 1
    if current_gid is not None:
        groups_new.append(count)

    game_ids_new = list(dict.fromkeys(f['game_id'] for f in all_features if f.get('game_id') is not None))
    print(f"  Debug: new_features={len(all_features)}, new_games={len(groups_new)}, sum={sum(groups_new)}")
    if len(groups_new) == 0:
        print("  ERROR: no groups formed — all_features empty or malformed")
        return

    # In incremental mode: append to existing data
    if appending:
        print(f"📎 Appending {len(groups_new)} new games to existing {len(game_ids_old)} games...")
        X, y, groups, game_ids = append_to_arrays(X_old, y_old, groups_old, game_ids_old, X_new, y_new, np.array(groups_new), game_ids_new)
    else:
        # Full build: use new data as-is
        X, y, groups, game_ids = X_new, y_new, np.array(groups_new), game_ids_new

    print(f"Built {X.shape[0]} samples, {X.shape[1]} features")
    print(f"Groups (games): {len(groups)}")
    print(f"Positive rate: {y.mean():.3f}")

    # Save final outputs
    np.save('X_train.npy', X)
    np.save('y_train.npy', y)
    np.save('groups.npy', groups)
    with open('game_ids.json', 'w') as f:   # game order of groups.npy, so training never guesses it
        json.dump(game_ids, f)
    with open('feature_names.json', 'w') as f:
        json.dump(NUMERIC_FEATURES, f)
    with open('meta.json', 'w') as f:
        json.dump({
            'n_samples': int(X.shape[0]),
            'n_features': int(X.shape[1]),
            'n_games': len(groups),
            'feature_names': NUMERIC_FEATURES,
            'positive_rate': float(y.mean()),
            'all_feature_names': feature_names,  # includes metadata
        }, f, indent=2)

    cleanup_checkpoint()
    print("✅ Saved: X_train.npy, y_train.npy, groups.npy, feature_names.json, meta.json")
    return X, y, groups, feature_names

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Build NBA first basket features')
    parser.add_argument('--incremental', action='store_true',
                        help='Only process new games since last build (appends to existing arrays)')
    parser.add_argument('--workers', type=int, default=2,
                        help='Number of parallel workers (max 3)')
    args = parser.parse_args()
    build_features(incremental=args.incremental)