"""Experiment (read-only, 2026-11-19): research ideas for the shown tip-off call — (a) new jumpers' Elo moves faster
for their first N tips, (b) smaller home edge. Point-in-time, same 1,238 games as the Elo adoption test."""
import math, os, sqlite3, sys
os.chdir("/Users/solomonkhalili/Desktop/NBA"); sys.path.insert(0, ".")
import tip_model as tm, starters as st
con = sqlite3.connect("nba_first_basket.db")
games = con.execute("""SELECT game_id, game_date, home_team, away_team, tip_winner_team FROM games
                       WHERE tip_winner_team IS NOT NULL AND game_date >= '20251201' ORDER BY game_date""").fetchall()
ctx = [(gid, d, h, a, w, tm.starters_for_game(con, gid, h), tm.starters_for_game(con, gid, a),
        st.listed_center(con, d, h), st.listed_center(con, d, a)) for gid, d, h, a, w in games]
CFG = {"k_new": 24, "n_new": 0, "home": 15}
def elo_ratings(con, before, extended=True):
    key = (before, tuple(CFG.values()))
    if key in tm._elo_cache: return tm._elo_cache[key]
    R, N = {}, {}
    for th, tw, wp, lp in con.execute(f"""SELECT home_team, tip_winner_team, tip_winner_player_id, tip_loser_player_id
            FROM {tm._tips(con, extended)} WHERE tip_winner_team IS NOT NULL AND game_date < ?
            AND COALESCE(tip_winner_player_id,'') != '' AND COALESCE(tip_loser_player_id,'') != '' ORDER BY game_date, game_id""", (before,)):
        adj = CFG["home"] if tw == th else -CFG["home"]
        rw, rl = R.get(wp, 1500.0), R.get(lp, 1500.0); e = 1 / (1 + 10 ** ((rl - rw - adj) / 400))
        kw = CFG["k_new"] if N.get(wp, 0) < CFG["n_new"] else tm.ELO_K
        kl = CFG["k_new"] if N.get(lp, 0) < CFG["n_new"] else tm.ELO_K
        R[wp], R[lp] = rw + kw * (1 - e), rl - kl * (1 - e); N[wp] = N.get(wp, 0) + 1; N[lp] = N.get(lp, 0) + 1
    tm._elo_cache.clear(); tm._elo_cache[key] = R
    return R
tm.elo_ratings = elo_ratings
def run():
    res = {}
    old = tm.ELO_HOME; tm.ELO_HOME = CFG["home"]
    for gid, d, h, a, w, hs, as_, hc, ac in ctx:
        p = tm.home_win_prob(con, h, a, d, hs, as_, hc, ac, True)
        s = res.setdefault("2026-27" if d >= "20260801" else "2025-26", [0, 0, 0.0]); hw = w == h
        s[0] += (p > .5) == hw; s[1] += 1; s[2] -= math.log(p if hw else 1 - p)
    tm.ELO_HOME = old
    return {k: (round(r / n * 100, 1), round(ll / n, 5)) for k, (r, n, ll) in sorted(res.items())}
for k_new, n_new, home in ((24, 0, 15), (40, 15, 15), (48, 15, 15), (40, 25, 15), (24, 0, 5), (24, 0, 0), (40, 15, 5)):
    CFG.update(k_new=k_new, n_new=n_new, home=home)
    print(f"new-jumper K {k_new} for first {n_new} tips, home edge {home}:", run(), flush=True)
# Result 2026-11-19: current 0.62738 / 0.62734; faster new jumpers (K40, 15 tips) 0.62681 / 0.62775; home edge 5
# 0.62850 / 0.62582; both 0.62795 / 0.62621 — each helps one season and hurts the other. Not adopted.
