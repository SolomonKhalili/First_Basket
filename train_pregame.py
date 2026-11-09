#!/usr/bin/env python3
"""Train the pre-game conditional-logit model. Run after build_features.py.
Train = 2025-26 regular season, honest forward test = 2026-27 games so far."""
import json, sqlite3
import numpy as np
from scipy.optimize import minimize
import pregame_model as pm

REG_SEASON_END = "20260412"   # last day of the 2025-26 regular season
NEW_SEASON_START = "20260801"

X = np.load("X_train.npy"); y = np.load("y_train.npy"); g = np.load("groups.npy")
names = json.load(open("feature_names.json")); J = {n: i for i, n in enumerate(names)}
con = sqlite3.connect("nba_first_basket.db")
dates = [r[0] for r in con.execute(
    "SELECT g.game_date FROM games g JOIN first_baskets f USING(game_id) ORDER BY g.game_date")]
assert len(dates) == len(g), "DB games and groups.npy are out of sync - rebuild features"
REG_END = sum(d <= REG_SEASON_END for d in dates)
TEST0 = sum(d < NEW_SEASON_START for d in dates)
n = len(g); c = np.cumsum([0] + g.tolist())

rows = []
for r in X:
    p = {"first_basket_rate": r[J["first_basket_rate"]], "L10_first_basket_rate": r[J["L10_first_basket_rate"]],
         "games_played": r[J["player_games_played"]], "usage_rate": r[J["usage_rate"]],
         "true_shooting_pct": r[J["true_shooting_pct"]], "minutes_per_game": r[J["minutes_per_game"]],
         "points_per_game": r[J["points_per_game"]]}
    rows.append(pm.build_row(p, {"team_tipoff_win_pct": r[J["team_tipoff_win_pct"]]}, int(r[J["is_home"]])))
M = np.array(rows)
VA0 = int(REG_END * 0.85)
mu = M[:c[VA0]].mean(0); sd = M[:c[VA0]].std(0) + 1e-9
Z = (M - mu) / sd

def nll(w, a, b, l2):
    Zs = Z[c[a]:c[b]].reshape(b - a, 10, -1); ys = y[c[a]:c[b]].reshape(b - a, 10)
    s = Zs @ w; s -= s.max(1, keepdims=True); e = np.exp(s); p = e / e.sum(1, keepdims=True)
    return (-np.log((p * ys).sum(1)).mean() + l2 * (w @ w),
            -((ys - p)[:, :, None] * Zs).sum(1).mean(0) + 2 * l2 * w)

def fit(a, b, l2):
    return minimize(nll, np.zeros(Z.shape[1]), args=(a, b, l2), jac=True, method="L-BFGS-B").x

def score(w, a, b):
    Zs = Z[c[a]:c[b]].reshape(b - a, 10, -1); ys = y[c[a]:c[b]].reshape(b - a, 10)
    s = Zs @ w; e = np.exp(s - s.max(1, keepdims=True)); p = e / e.sum(1, keepdims=True)
    return (ys[np.arange(b - a), p.argmax(1)] == 1).mean(), -np.log((p * ys).sum(1)).mean()

best = min(((score(fit(0, VA0, l2), VA0, REG_END)[1], l2) for l2 in [3e-4, 1e-3, 3e-3, 1e-2, 3e-2]))
l2 = best[1]
print(f"uniform log-loss {np.log(10):.4f} | train={REG_END} reg-season games, forward test={n - TEST0} games")
w = fit(0, REG_END, l2)
t1, ll = score(w, TEST0, n)
print(f"FORWARD TEST  top-1 {t1:.3f}   log-loss {ll:.4f}")
for k, v in zip(pm.FEATURES, w): print(f"  {k:<22}{v:+.3f}")
json.dump({"features": pm.FEATURES, "w": w.tolist(), "mu": mu.tolist(), "sd": sd.tolist(),
           "l2": l2, "train_games": int(REG_END), "test_top1": float(t1), "test_logloss": float(ll)},
          open("pregame_model.json", "w"), indent=2)
print("saved pregame_model.json")
