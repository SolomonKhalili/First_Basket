"""Experiment (read-only, 2026-11-18): does the jumpers' HEIGHT improve the tip-off chance?
Jumpers are predicted exactly as on game day (tip_model.team_jumper with that game's starters + NBA.com's listed C).
Baseline: the current model's z = logit(rate_home) - logit(rate_away) + logit(home edge).
Height model: P(home wins tip) = sigmoid(b * z + c * (height_home - height_away)), b and c fitted only on earlier
games (Oct–Dec 2025 for the Jan–Apr test; everything before 2026-27 for the 2026-27 test). Log-loss decides."""
import os, sys, sqlite3
import numpy as np
from scipy.optimize import minimize
os.chdir("/Users/solomonkhalili/Desktop/NBA"); sys.path.insert(0, ".")
import tip_model as tm
import starters as st
con = sqlite3.connect("nba_first_basket.db")
import json; H = json.load(open("player_heights.json"))
AVG = float(np.mean(list(H.values())))
rows = []
for gid, d, h, a, tw in con.execute("""SELECT game_id, game_date, home_team, away_team, tip_winner_team FROM games
                                       WHERE tip_winner_team IS NOT NULL AND game_date >= '20251101' ORDER BY game_date""").fetchall():
    sh, sa = tm.starters_for_game(con, gid, h), tm.starters_for_game(con, gid, a)
    if not sh or not sa:
        continue
    jh = tm.team_jumper(con, h, d, sh, st.listed_center(con, d, h)); ja = tm.team_jumper(con, a, d, sa, st.listed_center(con, d, a))
    rh, ra = tm.jumper_rate(con, jh, d), tm.jumper_rate(con, ja, d)
    z = tm._logit(rh) - tm._logit(ra) + tm._logit(tm.home_advantage(con, d))
    dh = H.get(str(jh), AVG) - H.get(str(ja), AVG)
    def wn(j):
        if not j: return 0, 0
        return con.execute("""SELECT COALESCE(SUM(tip_winner_player_id = ?), 0), COUNT(*) FROM games WHERE game_date < ?
                              AND ? IN (tip_winner_player_id, tip_loser_player_id)""", (j, d, j)).fetchone()
    rows.append((d, z, dh, tw == h, str(jh) in H and str(ja) in H, wn(jh), wn(ja), H.get(str(jh), AVG), H.get(str(ja), AVG),
                 tm._logit(tm.home_advantage(con, d))))
D = np.array([r[0] for r in rows]); Z = np.array([r[1] for r in rows]); DH = np.array([r[2] for r in rows])
Yh = np.array([r[3] for r in rows], dtype=float); KNOWN = np.array([r[4] for r in rows])
print(f"games: {len(rows)} · both jumpers' heights known: {KNOWN.mean():.0%}")
ll = lambda p, y: -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))
sig = lambda x: 1 / (1 + np.exp(-x))
def fit(X, y):
    return minimize(lambda v: ll(np.clip(sig(X @ v), 1e-6, 1 - 1e-6), y) + 1e-3 * (v @ v), np.zeros(X.shape[1])).x
for (tr_lo, tr_hi, te_lo, te_hi, label) in (("20251101", "20251231", "20260101", "20260412", "Jan–Apr 2026"),
                                            ("20251101", "20260630", "20261019", "20991231", "2026-27")):
    tr = (D >= tr_lo) & (D <= tr_hi); te = (D >= te_lo) & (D <= te_hi)
    base = sig(Z[te])
    vb = fit(np.column_stack([Z[tr]]), Yh[tr]); recal = sig(np.column_stack([Z[te]]) @ vb)
    vh = fit(np.column_stack([Z[tr], DH[tr]]), Yh[tr]); withh = sig(np.column_stack([Z[te], DH[te]]) @ vh)
    vo = fit(np.column_stack([DH[tr]]), Yh[tr]); only = sig(np.column_stack([DH[te]]) @ vo)
    acc = lambda p: np.mean((p > .5) == (Yh[te] > .5))
    print(f"\n{label} ({te.sum()} games)   [height weight c = {vh[1]:+.3f} per inch]")
    for name, p in (("current tip model", base), ("current, recalibrated", recal),
                    ("current + height", withh), ("height only", only)):
        print(f"  {name:24} right {acc(p):6.1%}   log-loss {ll(np.clip(p, 1e-6, 1 - 1e-6), Yh[te]):.4f}")

# height as the STARTING GUESS for jumpers with few tips: rate = (wins + K * prior(height)) / (tips + K)
for slope in (0.01, 0.02, 0.03):
    Zp = []
    for r in rows:
        (wh, nh), (wa, na), hh, ha, edge = r[5], r[6], r[7], r[8], r[9]
        pr = lambda hgt: min(0.7, max(0.3, 0.5 + slope * (hgt - 82)))
        rh = (wh + tm.K * pr(hh)) / (nh + tm.K); ra = (wa + tm.K * pr(ha)) / (na + tm.K)
        Zp.append(tm._logit(rh) - tm._logit(ra) + edge)
    Zp = np.array(Zp)
    for te_lo, te_hi, label in (("20260101", "20260412", "Jan–Apr 2026"), ("20261019", "20991231", "2026-27")):
        te = (D >= te_lo) & (D <= te_hi); p = np.clip(sig(Zp[te]), 1e-6, 1 - 1e-6)
        print(f"height as starting guess ({slope:.2f}/inch) {label:13} right {np.mean((p > .5) == (Yh[te] > .5)):6.1%}   log-loss {ll(p, Yh[te]):.4f}")
print("\nDONE")
