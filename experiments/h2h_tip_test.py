"""Experiment (read-only): head-to-head record between the two jumpers as an extra tip-off input.
Every game from Dec 2025 on is predicted from EARLIER games only (same as tip_model.evaluate).
Blend: P = (home jumper's h2h wins + K2 * current P) / (h2h tips + K2). Bigger K2 = trust h2h less."""
import math, os, sqlite3, sys
os.chdir("/Users/solomonkhalili/Desktop/NBA"); sys.path.insert(0, ".")
import tip_model as tm
import starters as st

con = sqlite3.connect("nba_first_basket.db")
games = con.execute("""SELECT game_id, game_date, home_team, away_team, tip_winner_team FROM games
                       WHERE tip_winner_team IS NOT NULL AND game_date >= '20251201' ORDER BY game_date""").fetchall()
K2S = [None, 2, 5, 10, 20, 40]
res = {}            # (season, K2) -> [right, n, logloss]
have_h2h = {}
print(f"Checking {len(games)} games, one at a time (only earlier games used)...", flush=True)
for i, (gid, d, h, a, winner) in enumerate(games, 1):
    hs, as_ = tm.starters_for_game(con, gid, h), tm.starters_for_game(con, gid, a)
    hc, ac = st.listed_center(con, d, h), st.listed_center(con, d, a)
    p = tm.home_win_prob(con, h, a, d, hs, as_, hc, ac)
    jh, ja = tm.team_jumper(con, h, d, hs, hc), tm.team_jumper(con, a, d, as_, ac)
    w = n = 0
    if jh and ja:
        w, n = con.execute("""SELECT COALESCE(SUM(tip_winner_player_id = ?), 0), COUNT(*) FROM games WHERE game_date < ?
                              AND ((tip_winner_player_id = ? AND tip_loser_player_id = ?)
                                OR (tip_winner_player_id = ? AND tip_loser_player_id = ?))""",
                           (jh, d, jh, ja, ja, jh)).fetchone()
    season = "2026-27" if d >= "20260801" else "2025-26"
    have_h2h.setdefault(season, [0, 0]); have_h2h[season][0] += n > 0; have_h2h[season][1] += 1
    home_won = winner == h
    for k2 in K2S:
        q = p if k2 is None else (w + k2 * p) / (n + k2)
        q = min(max(q, 0.01), 0.99)
        r = res.setdefault((season, k2), [0, 0, 0.0])
        r[0] += (q > 0.5) == home_won; r[1] += 1; r[2] += -math.log(q if home_won else 1 - q)
    if i % 200 == 0:
        print(f"  {i}/{len(games)} games done", flush=True)

for season in sorted(have_h2h):
    hh, nn = have_h2h[season]
    print(f"\n{season}: {nn} games; the two jumpers had met before in {hh} ({hh / nn:.0%})")
    for k2 in K2S:
        right, n, ll = res[(season, k2)]
        name = "current tip model" if k2 is None else f"+ head-to-head (K2={k2})"
        print(f"  {name:28} {right / n:6.1%} right   log-loss {ll / n:.4f}")
print("\nDONE")
