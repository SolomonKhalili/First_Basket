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


# The tip-off history the model reads. `extended=True` also counts the 2024-25 opening tips kept in `old_tips`.
# Tested 2026-11-17: the extra season calls more tip winners right (2025-26 62.3% -> 65.1%, 2026-27 63.6% -> 67.2%)
# but makes the FIRST-BASKET picks slightly worse (its tip chances are more confident), so it is used only for the
# tip-off call shown to the owner (card + spreadsheet), never inside the first-basket model.
def _tips(con, extended: bool) -> str:
    if extended and con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='old_tips'").fetchone():
        return """(SELECT game_id, game_date, home_team, away_team, tip_winner_team, tip_winner_player_id,
                          tip_loser_player_id FROM games
                   UNION ALL
                   SELECT game_id, game_date, home_team, away_team, tip_winner_team, tip_winner_player_id,
                          tip_loser_player_id FROM old_tips
                   WHERE tip_winner_player_id != '' AND tip_winner_team IS NOT NULL
                     AND game_id NOT IN (SELECT game_id FROM games))"""
    return "games"


def recent_jumpers(con, team: str, before: str, extended: bool = False) -> list:
    """Who jumped for `team` in its last RECENT_GAMES games before `before`, most recent first."""
    rows = con.execute(
        f"""SELECT CASE WHEN tip_winner_team = ? THEN tip_winner_player_id ELSE tip_loser_player_id END
           FROM {_tips(con, extended)}
           WHERE (home_team = ? OR away_team = ?) AND game_date < ? AND tip_winner_team IS NOT NULL
           ORDER BY game_date DESC, game_id DESC LIMIT ?""",
        (team, team, team, before, RECENT_GAMES)).fetchall()
    return [r[0] for r in rows if r[0]]


REGULAR_JUMPER = 6  # a starter who took this many of the team's last 10 tips jumps even if not listed at C


def team_jumper(con, team: str, before: str, starter_ids=None, listed_center=None, extended: bool = False):
    """Who takes the opening tip for `team`. With tonight's starters (`starter_ids`):
      1. a starter who took >= REGULAR_JUMPER of the team's last 10 tips (the team's clear regular jumper);
      2. otherwise the starter NBA.com lists at C (`listed_center`), when NBA.com has posted the lineup;
      3. otherwise the starter who jumped most for the team in its last 10 games (ties -> most recent);
      4. otherwise the starter with the most opening tips before `before`, for any team.
    Checked on 396 team-games of 2026-27 against NBA.com's lineups: steps 1-2 named the actual jumper 88.1% of
    the time vs 85.1% without the listed centre. Without `starter_ids`: the most frequent recent jumper.
    None if nobody qualifies."""
    jumps = recent_jumpers(con, team, before, extended)
    if starter_ids is not None:
        starters = {str(s) for s in starter_ids}
        jumps = [j for j in jumps if j in starters]
        counts = Counter(jumps)
        if listed_center and str(listed_center) in starters:
            regular = [j for j, n in counts.items() if n >= REGULAR_JUMPER and j != str(listed_center)]
            return regular[0] if regular else str(listed_center)
        if not jumps:
            return most_experienced_jumper(con, starters, before, extended)
    if not jumps:
        return None
    counts = Counter(jumps)
    return max(counts, key=lambda j: (counts[j], -jumps.index(j)))


def most_experienced_jumper(con, player_ids, before: str, extended: bool = False):
    """The player in `player_ids` who took the most opening tips before `before`; None if none ever did."""
    best, best_n = None, 0
    for pid in sorted(player_ids):
        n = con.execute(f"""SELECT COUNT(*) FROM {_tips(con, extended)} WHERE game_date < ?
                           AND ? IN (tip_winner_player_id, tip_loser_player_id)""", (before, pid)).fetchone()[0]
        if n > best_n:
            best, best_n = pid, n
    return best


# The tip-off call shown on each game (extended=True) counts this season's tips double. Tested 2026-11-19 (weights
# 1/2/3/5/8): 2x was the only one with better log-loss in BOTH seasons (2025-26 0.6297 -> 0.6275, 2026-27
# 0.6321 -> 0.6310; tip winners called 65.1% -> 64.6% and 65.6% -> 67.0%). The first-basket model's tip input
# (extended=False) is unchanged.
SEASON_WEIGHT = 2.0


def _season_start(d: str) -> str:
    return f"{d[:4]}0801" if d[4:6] >= "08" else f"{int(d[:4]) - 1}0801"


def jumper_rate(con, player_id, before: str, extended: bool = False) -> float:
    """Shrunk tip win rate from every tip he took before `before` (this season's tips count double for the shown call)."""
    if not player_id:
        return PRIOR_RATE
    w = SEASON_WEIGHT if extended else 1.0
    wins, n = con.execute(
        f"""SELECT COALESCE(SUM((tip_winner_player_id = ?) * (CASE WHEN game_date >= ? THEN ? ELSE 1.0 END)), 0),
                  COALESCE(SUM(CASE WHEN game_date >= ? THEN ? ELSE 1.0 END), 0) FROM {_tips(con, extended)}
           WHERE game_date < ? AND ? IN (tip_winner_player_id, tip_loser_player_id)""",
        (player_id, _season_start(before), w, _season_start(before), w, before, player_id)).fetchone()
    return (wins + K * PRIOR_RATE) / (n + K)


def home_advantage(con, before: str, extended: bool = False) -> float:
    wins, n = con.execute(
        f"""SELECT COALESCE(SUM(tip_winner_team = home_team), 0), COUNT(*) FROM {_tips(con, extended)}
           WHERE game_date < ? AND tip_winner_team IS NOT NULL""", (before,)).fetchone()
    return (wins + HCA_WEIGHT * DEFAULT_HCA) / (n + HCA_WEIGHT)


# Opponent-strength rating (Elo) for the tip-off call shown to the owner (extended=True): every opening tip moves both
# jumpers' ratings, more for beating a strong jumper — so who you beat counts, not just how often you won.
# Tested 2026-11-19 (point-in-time, 1,238 games): blended 50/50 with the rate model, K=24 was better in BOTH seasons
# (log-loss 2025-26 0.6275 -> 0.6274, 2026-27 0.6310 -> 0.6273; tip winners 64.6 -> 64.5%, 67.0 -> 67.9%).
# A plain head-to-head adjustment was worse at every weight (most pairs meet only 1-2 times). The first-basket
# model's tip input (extended=False) is unchanged.
ELO_K, ELO_HOME, ELO_BLEND = 24, 15, 0.5
_elo_cache: dict = {}


def elo_ratings(con, before: str, extended: bool = True) -> dict:
    """{player_id: rating} from every opening tip before `before` (YYYYMMDD), start 1500."""
    key = (before, extended, con.execute(f"SELECT COUNT(*) FROM {_tips(con, extended)}").fetchone()[0])
    if key in _elo_cache:
        return _elo_cache[key]
    R = {}
    for th, tw, wp, lp in con.execute(
            f"""SELECT home_team, tip_winner_team, tip_winner_player_id, tip_loser_player_id FROM {_tips(con, extended)}
                WHERE tip_winner_team IS NOT NULL AND game_date < ? AND COALESCE(tip_winner_player_id, '') != ''
                  AND COALESCE(tip_loser_player_id, '') != '' ORDER BY game_date, game_id""", (before,)):
        adj = ELO_HOME if tw == th else -ELO_HOME
        rw, rl = R.get(wp, 1500.0), R.get(lp, 1500.0)
        e = 1 / (1 + 10 ** ((rl - rw - adj) / 400))
        R[wp], R[lp] = rw + ELO_K * (1 - e), rl - ELO_K * (1 - e)
    _elo_cache.clear()
    _elo_cache[key] = R
    return R


def home_win_prob(con, home: str, away: str, before, home_starters=None, away_starters=None,
                  home_center=None, away_center=None, extended: bool = False) -> float:
    """P(home team wins the opening tip), using only games before `before` (YYYYMMDD).
    `home_center` / `away_center`: the starter NBA.com lists at C, when known.
    `extended`: also count the 2024-25 tips (tip-off call shown to the owner only — see _tips)."""
    before = str(before).replace("-", "")[:8]
    jh = team_jumper(con, home, before, home_starters, home_center, extended)
    ja = team_jumper(con, away, before, away_starters, away_center, extended)
    rh, ra = jumper_rate(con, jh, before, extended), jumper_rate(con, ja, before, extended)
    z = _logit(rh) - _logit(ra) + _logit(home_advantage(con, before, extended))
    if extended:                     # the shown call: blend in the opponent-strength rating
        R = elo_ratings(con, before, extended)
        pe = 1 / (1 + 10 ** ((R.get(str(ja or ""), 1500.0) - R.get(str(jh or ""), 1500.0) - ELO_HOME) / 400))
        z = (1 - ELO_BLEND) * z + ELO_BLEND * _logit(pe)
    return 1 / (1 + math.exp(-z))


def team_win_probs(con, home: str, away: str, before, home_starters=None, away_starters=None,
                   home_center=None, away_center=None, extended: bool = False) -> tuple:
    """(P(home wins tip), P(away wins tip)) — they always add to 1."""
    p = home_win_prob(con, home, away, before, home_starters, away_starters, home_center, away_center, extended)
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
        import starters as st
        p = home_win_prob(con, h, a, d, starters_for_game(con, gid, h), starters_for_game(con, gid, a),
                          st.listed_center(con, d, h), st.listed_center(con, d, a))
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
