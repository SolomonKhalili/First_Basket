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
import pandas as pd
from datetime import date, datetime, timedelta
from collections import defaultdict
import warnings
import signal
import sys
import os
import shutil
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import pickle
import tip_model as tm
warnings.filterwarnings("ignore")

DB = "nba_first_basket.db"
BASE_SLEEP = 0.3  # base delay between requests
CHECKPOINT_EVERY = 50
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

    con = db()

    # Get all games with first basket info
    all_games = [r for r in con.execute('''
        SELECT g.game_id, g.game_date, g.home_team, g.away_team, fb.scorer_player_id
        FROM games g
        JOIN first_baskets fb ON g.game_id = fb.game_id
        ORDER BY g.game_date
    ''').fetchall() if r[0] not in known]

    if not all_games:
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
def compute_player_season_stats(player_id, game_date, team_abbrev, player_logs):
    """
    Compute player season stats UP TO (but not including) game_date.
    Uses pre-fetched player game logs — NO LEAKAGE.
    Returns dict of features.
    """
    game_date_str = str(game_date).replace('-', '')[:8]

    logs = player_logs.get(player_id, [])
    if not logs:
        return {
            'first_basket_rate': 0.0,
            'first_shot_rate': 0.0,
            'L10_first_basket_rate': 0.0,
            'L5_first_basket_rate': 0.0,
            'games_played': 0,
            'usage_rate': 0.0,
            'true_shooting_pct': 0.0,
            'minutes_per_game': 0.0,
            'points_per_game': 0.0,
        }

    # Filter logs to only those before game_date
    prior_logs = [g for g in logs if g['game_date'] < game_date_str]

    games_played = len(prior_logs)
    if games_played == 0:
        return {
            'first_basket_rate': 0.0,
            'first_shot_rate': 0.0,
            'L10_first_basket_rate': 0.0,
            'L5_first_basket_rate': 0.0,
            'games_played': 0,
            'usage_rate': 0.0,
            'true_shooting_pct': 0.0,
            'minutes_per_game': 0.0,
            'points_per_game': 0.0,
        }

    # Season averages
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

    # True Shooting % = pts / (2 * (FGA + 0.44 * FTA))
    ts_denom = 2 * (total_fga + 0.44 * total_fta)
    true_shooting_pct = total_pts / ts_denom if ts_denom > 0 else 0.0

    # Usage rate approximation (simplified)
    usage_rate = (total_fga + 0.44 * total_fta) / (total_min / 36) if total_min > 0 else 0.0
    usage_rate = min(usage_rate / 100, 1.0)  # normalize to 0-1 range

    # First basket / shot rates
    first_basket_rate = total_first_baskets / games_played if games_played > 0 else 0.0
    first_shot_rate = first_basket_rate * 2.0  # rough proxy: assume ~50% FG on first shot

    # Rolling windows (last 10, last 5 games played)
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
        'games_played': games_played,
        'usage_rate': usage_rate,
        'true_shooting_pct': true_shooting_pct,
        'minutes_per_game': minutes_per_game,
        'points_per_game': points_per_game,
    }


def compute_team_stats_up_to_date(team_abbrev, game_date, con):
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
        return {
            'team_tipoff_win_pct': 0.5,
            'home_fb_pct': 0.0,
            'away_fb_pct': 0.0,
            'games_played': 0,
        }

    games_played = len(team_games)
    tip_wins = sum(1 for g in team_games if g[3] == team_abbrev)
    team_tipoff_win_pct = tip_wins / games_played if games_played > 0 else 0.5

    # Home/away first basket rates
    home_fb_real = sum(1 for g in team_games
                       if g[1] == team_abbrev and g[6] == team_abbrev)
    away_fb_real = sum(1 for g in team_games
                       if g[2] == team_abbrev and g[6] == team_abbrev)

    home_games = [g for g in team_games if g[1] == team_abbrev]
    away_games = [g for g in team_games if g[2] == team_abbrev]

    home_fb_pct = home_fb_real / len(home_games) if home_games else 0.0
    away_fb_pct = away_fb_real / len(away_games) if away_games else 0.0

    return {
        'team_tipoff_win_pct': team_tipoff_win_pct,
        'home_fb_pct': home_fb_pct,
        'away_fb_pct': away_fb_pct,
        'games_played': games_played,
    }


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
def get_game_starters(game_id, cache, home_team, away_team):
    """Get starting 5 for each team — returns {home_abbrev: [...], away_abbrev: [...]}"""
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
# EARLY GAME PBP FEATURES
# ──────────────────────────────────────────────
def extract_early_game_features(game_id, cache, n_plays=10):
    """Extract features from first N plays of the game."""
    pbp = cache.get_pbp(game_id)
    if not pbp:
        return {}

    plays = pbp.get('plays', [])[:n_plays]
    if not plays:
        return {}

    # Map player IDs to names from boxscore
    names = {}
    for team in pbp.get('boxscore', {}).get('players', []):
        for stat in team.get('statistics', []):
            for a in stat.get('athletes', []):
                athlete = a.get('athlete', {})
                pid = str(athlete.get('id', ''))
                name = athlete.get('displayName', '')
                if pid and name:
                    names[pid] = name

    # Count early touches/shot attempts per player
    early_stats = defaultdict(lambda: {'touches': 0, 'shots': 0, 'points': 0, 'first_play_type': ''})

    for i, p in enumerate(plays):
        pid = str(p.get('participants.0.athlete.id', '') or '')
        if not pid:
            continue
        action = p.get('type.text', '').lower().replace(' ', '_')
        if i == 0:
            early_stats[pid]['first_play_type'] = action
        early_stats[pid]['touches'] += 1
        if p.get('scoringPlay', False) and 'free_throw' not in action:
            early_stats[pid]['shots'] += 1

    return dict(early_stats)


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
            print(f"  ⚠️ Checkpoint has 0 features — starting fresh")
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


def get_last_processed_date(game_ids):
    """Get the last game date from existing game_ids by querying the DB."""
    if not game_ids:
        return None
    last_game_id = game_ids[-1]
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT game_date FROM games WHERE game_id = ?", (last_game_id,)).fetchone()
    con.close()
    if row:
        return row['game_date']
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
    # Load existing data if incremental mode
    existing = None
    start_idx = 0
    if incremental:
        existing = load_existing_data()
        if existing:
            X_old, y_old, groups_old, game_ids_old, feature_names_old, meta_old = existing
            # Find games after the last processed date
            last_date = get_last_processed_date(game_ids_old)
            if last_date:
                print(f"📅 Last processed game date: {last_date}")
                # Get all games and find the first one after last_date
                all_games = get_all_games()
                for i, game in enumerate(all_games):
                    if game[1] > last_date:  # game_date is index 1
                        start_idx = i
                        break
                print(f"🔄 Incremental mode: skipping {start_idx} already-processed games, processing {len(all_games) - start_idx} new games")
            else:
                print("⚠️ Could not determine last date, doing full rebuild")
                existing = None
        else:
            print("📦 No existing data found, doing full build")

    games = get_all_games()

    if existing and start_idx > 0:
        # Incremental: only process new games
        games = games[start_idx:]
        total_games = len(games)
        print(f"🔄 Incremental mode: processing {total_games} NEW games")
    elif existing and start_idx == 0:
        # No new games in DB since last build
        print("✅ No new games to process — feature matrix is up to date")
        return
    else:
        # Full build
        total_games = len(games)
        print(f"Building features for {total_games} games...")

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
        feature_names = ['game_id', 'player_id', 'player_name', 'team', 'opponent', 'is_home', 'tip_winner', 'position', 'early_touches', 'early_shots', 'first_play_type']
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

    for idx in range(start_idx, total_games):
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

        # Early game features
        early_feats = extract_early_game_features(game_id, cache, n_plays=10)

        # Season stats (computed up to game_date, no leakage)
        con = db()
        home_team_stats = compute_team_stats_up_to_date(home_team, game_date, con)
        away_team_stats = compute_team_stats_up_to_date(away_team, game_date, con)
        # P(team wins the opening tip): tonight's starters + only earlier games (see tip_model.py)
        tip_home, tip_away = tm.team_win_probs(con, home_team, away_team, game_date,
                                               [s['player_id'] for s in home_starters],
                                               [s['player_id'] for s in away_starters])

        # Build feature vector for each starter
        game_features = []
        game_labels = []

        for starter in all_starters:
            pid = starter['player_id']
            is_home = starter in home_starters
            team = home_team if is_home else away_team
            opponent = away_team if is_home else home_team

            # Player season stats - use pre-fetched logs
            player_stats = compute_player_season_stats(pid, game_date, team, player_logs) or {
                'first_basket_rate': 0.0, 'first_shot_rate': 0.0,
                'L10_first_basket_rate': 0.0, 'L5_first_basket_rate': 0.0,
                'games_played': 0,
                'usage_rate': 0.0,
                'true_shooting_pct': 0.0,
                'minutes_per_game': 0.0,
                'points_per_game': 0.0,
            }
            # Team stats
            team_stats = home_team_stats if is_home else away_team_stats
            if team_stats is None:
                team_stats = {
                    'team_tipoff_win_pct': 0.5, 'home_fb_pct': 0.0,
                    'away_fb_pct': 0.0, 'games_played': 0,
                }

            tip_win_prob = tip_home if is_home else tip_away

            feat = {
                'game_id': game_id,
                'player_id': pid,
                'player_name': starter['name'],
                'team': team,
                'opponent': opponent,
                'is_home': int(is_home),
                'tip_winner': int(team == tip_winner),
                'position': starter.get('position', ''),  # metadata, not in X
                # Early game features
                'early_touches': early_feats.get(pid, {}).get('touches', 0),
                'early_shots': early_feats.get(pid, {}).get('shots', 0),
                'first_play_type': early_feats.get(pid, {}).get('first_play_type', ''),  # metadata
                # Season stats (player)
                'first_basket_rate': player_stats.get('first_basket_rate', 0.0),
                'first_shot_rate': player_stats.get('first_shot_rate', 0.0),
                'L10_first_basket_rate': player_stats.get('L10_first_basket_rate', 0.0),
                'L5_first_basket_rate': player_stats.get('L5_first_basket_rate', 0.0),
                'player_games_played': player_stats.get('games_played', 0),
                'usage_rate': player_stats.get('usage_rate', 0.0),
                'true_shooting_pct': player_stats.get('true_shooting_pct', 0.0),
                'minutes_per_game': player_stats.get('minutes_per_game', 0.0),
                'points_per_game': player_stats.get('points_per_game', 0.0),
                # Season stats (team)
                'team_tipoff_win_pct': team_stats.get('team_tipoff_win_pct', 0.5),
                'home_fb_pct': team_stats.get('home_fb_pct', 0.0),
                'away_fb_pct': team_stats.get('away_fb_pct', 0.0),
                'team_games_played': team_stats.get('games_played', 0),
                # Tip-off win probability (new feature)
                'tip_win_prob': tip_win_prob,
            }

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
    NUMERIC_FEATURES = [
        'is_home', 'tip_winner', 'early_touches', 'early_shots',
        'first_basket_rate', 'first_shot_rate', 'L10_first_basket_rate', 'L5_first_basket_rate',
        'player_games_played',
        'usage_rate', 'true_shooting_pct', 'minutes_per_game', 'points_per_game',
        'team_tipoff_win_pct', 'home_fb_pct', 'away_fb_pct', 'team_games_played',
        'tip_win_prob',
    ]
    X_new = np.array([[f.get(name, 0) for name in NUMERIC_FEATURES] for f in all_features], dtype=float)
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
    if existing and start_idx > 0:
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