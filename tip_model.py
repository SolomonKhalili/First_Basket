#!/usr/bin/env python3
"""
Tip-off model: P(home team wins the opening tip), computed point-in-time.

  * Each team's jumper = the player who actually jumped for that team most often in its last
    RECENT_GAMES games (from games.tip_winner/loser_player_id), restricted to tonight's starters
    when they are known.
  * Each jumper's tip win rate uses only games BEFORE the game date, shrunk toward 50%.
  * P(home wins) = log5 / Bradley-Terry on the two rates, plus home-court advantage
    (also measured only from earlier games).

Nothing is stored: the probability is recomputed from the games table, so it is always current
and never sees the game being predicted. Run this file for an honest accuracy check.
"""
import math
import sqlite3
from collections import Counter

DB = "nba_first_basket.db"
PRIOR_RATE = 0.5
K = 10              # shrinkage: each jumper starts with 10 imaginary tips at 50%
RECENT_GAMES = 10   # a team's jumper = who jumped most for it in its last 10 games
DEFAULT_HCA = 0.52  # home tip win rate before any history
HCA_WEIGHT = 100    # shrinkage for the home-court rate


def _logit(p: float) -> float:
    return math.log(p / (1 - p))


def recent_jumpers(con, team: str, before: str) -> list:
    """Who jumped for `team` in its last RECENT_GAMES games before `before`, most recent first."""
    rows = con.execute(
        """SELECT CASE WHEN tip_winner_team = ? THEN tip_winner_player_id ELSE tip_loser_player_id END
           FROM games
           WHERE (home_team = ? OR away_team = ?) AND game_date < ? AND tip_winner_team IS NOT NULL
           ORDER BY game_date DESC, game_id DESC LIMIT ?""",
        (team, team, team, before, RECENT_GAMES)).fetchall()
    return [r[0] for r in rows if r[0]]


def team_jumper(con, team: str, before: str, starter_ids=None):
    """Most frequent recent jumper (ties -> most recent). If `starter_ids` is given, only a player in
    tonight's starting five counts. None if nobody qualifies."""
    jumps = recent_jumpers(con, team, before)
    if starter_ids is not None:
        starters = {str(s) for s in starter_ids}
        jumps = [j for j in jumps if j in starters]
    if not jumps:
        return None
    counts = Counter(jumps)
    return max(counts, key=lambda j: (counts[j], -jumps.index(j)))


def jumper_rate(con, player_id, before: str) -> float:
    """Shrunk tip win rate from every tip he took before `before`."""
    if not player_id:
        return PRIOR_RATE
    wins, n = con.execute(
        """SELECT COALESCE(SUM(tip_winner_player_id = ?), 0), COUNT(*) FROM games
           WHERE game_date < ? AND ? IN (tip_winner_player_id, tip_loser_player_id)""",
        (player_id, before, player_id)).fetchone()
    return (wins + K * PRIOR_RATE) / (n + K)


def home_advantage(con, before: str) -> float:
    wins, n = con.execute(
        """SELECT COALESCE(SUM(tip_winner_team = home_team), 0), COUNT(*) FROM games
           WHERE game_date < ? AND tip_winner_team IS NOT NULL""", (before,)).fetchone()
    return (wins + HCA_WEIGHT * DEFAULT_HCA) / (n + HCA_WEIGHT)


def home_win_prob(con, home: str, away: str, before, home_starters=None, away_starters=None) -> float:
    """P(home team wins the opening tip), using only games before `before` (YYYYMMDD)."""
    before = str(before).replace("-", "")[:8]
    rh = jumper_rate(con, team_jumper(con, home, before, home_starters), before)
    ra = jumper_rate(con, team_jumper(con, away, before, away_starters), before)
    z = _logit(rh) - _logit(ra) + _logit(home_advantage(con, before))
    return 1 / (1 + math.exp(-z))


def team_win_probs(con, home: str, away: str, before, home_starters=None, away_starters=None) -> tuple:
    """(P(home wins tip), P(away wins tip)) — they always add to 1."""
    p = home_win_prob(con, home, away, before, home_starters, away_starters)
    return p, 1 - p


def starters_for_game(con, game_id, team):
    """Starting five stored for a finished game (known before tip), or None if not stored."""
    ids = [r[0] for r in con.execute(
        "SELECT player_id FROM lineups WHERE game_id = ? AND team = ? AND is_starter = 1", (game_id, team))]
    return ids or None


def evaluate(con, since: str = "20251201"):
    """Point-in-time accuracy at predicting the tip winner, per season."""
    games = con.execute(
        """SELECT game_id, game_date, home_team, away_team, tip_winner_team FROM games
           WHERE tip_winner_team IS NOT NULL AND game_date >= ? ORDER BY game_date""", (since,)).fetchall()
    by_season = {}
    for gid, d, h, a, winner in games:
        p = home_win_prob(con, h, a, d, starters_for_game(con, gid, h), starters_for_game(con, gid, a))
        home_won = winner == h
        s = by_season.setdefault("2026-27" if d >= "20260801" else "2025-26", [0, 0, 0.0, 0])
        s[0] += (p > 0.5) == home_won
        s[1] += 1
        s[2] += -math.log(p if home_won else 1 - p)
        s[3] += home_won
    for season, (right, n, ll, home_wins) in sorted(by_season.items()):
        print(f"  {season}: {right}/{n} = {right / n:.1%} tip winners called "
              f"(always-home = {home_wins / n:.1%}), log-loss {ll / n:.4f} (coin flip = 0.6931)")


if __name__ == "__main__":
    print("Tip-off model — point-in-time check (only games before each game are used)")
    evaluate(sqlite3.connect(DB))
