#!/usr/bin/env python3
"""Train the pre-game model (TWO STAGES, see pregame_model.py). Run after build_features.py.

Stage 1 (team): logistic regression of "home team scored first" on logit(P(home wins the tip)).
Stage 2 (player): conditional logit over the 5 starters of the team that scored first (player inputs only).
The previous one-stage model is still trained in the walk-forward test and printed for comparison.

Training data = 2025-26 regular season + every 2026-27 game so far (playoffs excluded; 2024-25 is player history only), so the model
keeps learning this season (trades, rookies, new roles).

Honest test = WALK-FORWARD over 2026-27: each week is predicted by a model trained only on games
before that week, exactly like real use. That is the number saved as `test_top1` / `test_logloss`.

Safety: the saved model is only replaced if the new walk-forward log-loss is not clearly worse than
the previous run's (exit code 3 = kept the previous model).

--incremental: reuse the saved L2 (skip the search) and warm-start from the saved weights.
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import date, timedelta

import numpy as np
from scipy.optimize import minimize

import pregame_model as pm
import tip_model as tm
import starters as st

REG_SEASON_END = "20260412"   # last day of the 2025-26 regular season
NEW_SEASON_START = "20260801"
HISTORY_BEFORE = "20250801"     # 2024-25: player history only, never a training example (nba.apply_history_patch)
L2_GRID = [1e-3, 3e-3, 1e-2, 3e-2, 1e-1, 3e-1]
WORSE_MARGIN = 0.01           # keep the old model if the new walk-forward log-loss is worse by more than this
KEPT_PREVIOUS = 3             # exit code when the previous model is kept

parser = argparse.ArgumentParser()
parser.add_argument('--incremental', action='store_true',
                    help='Reuse the saved L2 and warm-start from the saved weights')
args = parser.parse_args()

# ── Load matrices, in the game order build_features.py wrote ─────────────────
X = np.load("X_train.npy"); y = np.load("y_train.npy"); g = np.load("groups.npy")
names = json.load(open("feature_names.json")); J = {n: i for i, n in enumerate(names)}
gids = [str(x) for x in json.load(open("game_ids.json"))]
con = sqlite3.connect("nba_first_basket.db")
info = {gid: (d, h, a) for gid, d, h, a in con.execute("SELECT game_id, game_date, home_team, away_team FROM games")}
if len(gids) != len(g) or set(g.tolist()) != {10}:
    raise SystemExit("Training matrices don't line up with game_ids.json — rebuild: python3 build_features.py")
dates = [info[gid][0] for gid in gids]

# ── Model rows (tip-off chance once per game: that game's starters + NBA.com's listed C, earlier games only) ──
import build_features as bf
import context_features as cf
ctx = cf.Context(con)                            # opponent-by-position + head-to-head, point-in-time
rows = []
import pickle as _pickle
_LOGS = _pickle.load(open(bf.PLAYER_LOGS_CACHE, "rb")) if os.path.exists(bf.PLAYER_LOGS_CACHE) else {}
for k, gid in enumerate(gids):
    d, home, away = info[gid]
    s = bf.stored_starters(gid, home, away)
    order = (s[home] + s[away])[:10] if s else []   # the matrix row order (build_features: home, then away)
    n_home = len(s[home]) if s else 5
    p_home, p_away = tm.team_win_probs(con, home, away, d, tm.starters_for_game(con, gid, home),
                                       tm.starters_for_game(con, gid, away),
                                       st.listed_center(con, d, home), st.listed_center(con, d, away))
    for j, r in enumerate(X[10 * k:10 * k + 10]):
        is_home = int(r[J["is_home"]])
        p = {"first_basket_rate": r[J["first_basket_rate"]], "L10_first_basket_rate": r[J["L10_first_basket_rate"]],
             "games_played": r[J["player_games_played"]], "shot_attempts_per36": r[J["shot_attempts_per36"]],
             "true_shooting_pct": r[J["true_shooting_pct"]], "minutes_per_game": r[J["minutes_per_game"]],
             "points_per_game": r[J["points_per_game"]],
             "early_shot_share": r[J["early_shot_share"]], "early_shot_count": r[J["early_shot_count"]]}
        if j < len(order):
            pl = order[j]
            team, opp = (home, away) if j < n_home else (away, home)
            p["opp_pos_share"] = ctx.opp_pos_share(opp, ctx.group_that_night(d, team, pl["name"]), d)
            p["vs_opp_rate"] = ctx.vs_opp_rate(pl["player_id"], opp, d)
            p["three_rate"] = bf.three_point_rate(_LOGS, pl["player_id"], d)
        rows.append(pm.build_row(p, {"team_tipoff_win_pct": r[J["team_tipoff_win_pct"]]}, is_home,
                                 tip_win_prob=p_home if is_home else p_away))
M = np.array(rows).reshape(len(gids), 10, -1)   # games x starters x features
M = pm.with_early(M, "first_points")              # early-shot trust of the first-points model (pm.EARLY_M)
Y = y.reshape(len(gids), 10)
MINUTES = X.reshape(len(gids), 10, -1)[:, :, J["minutes_per_game"]]

try:                                   # play-in / playoff games of any season (nba.game_types), not just by date
    import sqlite3 as _sq
    _con = _sq.connect("nba_first_basket.db")
    _con.execute("CREATE TABLE IF NOT EXISTS game_types (game_id TEXT PRIMARY KEY, season_type INTEGER)")
    POSTSEASON = {g for (g,) in _con.execute("SELECT game_id FROM game_types WHERE season_type IN (3, 5)")}
except Exception:  # noqa: BLE001
    POSTSEASON = set()
season = np.array(["history" if d < HISTORY_BEFORE else "playoffs" if g in POSTSEASON else "old" if d <= REG_SEASON_END
                   else "new" if d >= NEW_SEASON_START else "playoffs" for g, d in zip(gids, dates)])
dates = np.array(dates)


# ── Conditional logit ────────────────────────────────────────────────────────
def standardize(idx):
    flat = M[idx].reshape(-1, M.shape[2])
    return flat.mean(0), flat.std(0) + 1e-9


def fit(idx, l2, x0=None):
    mu, sd = standardize(idx)
    Z, Ys = (M[idx] - mu) / sd, Y[idx]

    def nll(w):
        s = Z @ w; s -= s.max(1, keepdims=True); e = np.exp(s); p = e / e.sum(1, keepdims=True)
        return (-np.log((p * Ys).sum(1)).mean() + l2 * (w @ w),
                -((Ys - p)[:, :, None] * Z).sum(1).mean(0) + 2 * l2 * w)
    w = minimize(nll, np.zeros(M.shape[2]) if x0 is None else x0, jac=True, method="L-BFGS-B").x
    return w, mu, sd


def predict(model, idx):
    w, mu, sd = model
    s = ((M[idx] - mu) / sd) @ w
    e = np.exp(s - s.max(1, keepdims=True))
    return e / e.sum(1, keepdims=True)


def top1(P, idx):
    return float((Y[idx][np.arange(len(idx)), P.argmax(1)] == 1).mean())


def logloss(P, idx):
    return float(-np.log((P * Y[idx]).sum(1)).mean())


# ── Two-stage model (team, then player) ────────────────────────────────────
home = M[:, :, pm.HOME] > 0.5                                    # games x 10
home_first = (Y * home).sum(1) == 1                              # did the home team score first?
tip_home = np.array([M[k, home[k], pm.TIP][0] for k in range(len(Y))])
PL = M[:, :, pm.PLAYER_IDX_BY_KIND["first_points"]]               # player inputs only (first-points model)


def fit_team(idx, l2=1e-3):
    x, yy = pm.logit(tip_home[idx]), home_first[idx].astype(float)

    def nll(v):
        p = 1 / (1 + np.exp(-(v[0] + v[1] * x)))
        return -np.mean(yy * np.log(p) + (1 - yy) * np.log(1 - p)) + l2 * (v @ v)
    v = minimize(nll, np.zeros(2)).x
    return {"a": float(v[0]), "b": float(v[1])}


def fit_player(idx, l2, x0=None):
    """Conditional logit over the scoring team's 5 starters."""
    mask = np.where(home_first[idx][:, None], home[idx], ~home[idx])
    flat = PL[idx][mask]
    mu, sd = flat.mean(0), flat.std(0) + 1e-9
    Z = ((PL[idx][mask] - mu) / sd).reshape(len(idx), 5, -1)
    Ys = Y[idx][mask].reshape(len(idx), 5)

    def nll(w):
        s = Z @ w; s -= s.max(1, keepdims=True); e = np.exp(s); p = e / e.sum(1, keepdims=True)
        return (-np.log((p * Ys).sum(1)).mean() + l2 * (w @ w),
                -((Ys - p)[:, :, None] * Z).sum(1).mean(0) + 2 * l2 * w)
    w = minimize(nll, np.zeros(Z.shape[2]) if x0 is None else x0, jac=True, method="L-BFGS-B").x
    return w, mu, sd


def fit2(idx, l2, x0=None):
    return (fit_team(idx),) + fit_player(idx, l2, x0)


def predict2(model, idx):
    team, w, mu, sd = model
    return np.vstack([pm.two_stage(team, w, mu, sd, M[k]) for k in idx])


# ── L2 (regularisation strength): chosen on 2025-26 only, so the test season never picks it ──
old_idx = np.where(season == "old")[0]
prev = json.load(open("pregame_model.json")) if os.path.exists("pregame_model.json") else {}
cut = int(len(old_idx) * 0.85)
tr, va = old_idx[:cut], old_idx[cut:]
if args.incremental and prev.get("type") == "two_stage" and prev.get("l2") is not None:
    l2 = prev["l2"]
else:
    l2 = min(L2_GRID, key=lambda v: logloss(predict2(fit2(tr, v), va), va))
l2_one = min(L2_GRID, key=lambda v: logloss(predict(fit(tr, v), va), va))     # for the one-stage comparison

# ── Walk-forward test over 2026-27: week by week, trained only on games before that week ──
new_idx = np.where(season == "new")[0]
train_pool = (season != "playoffs") & (season != "history")
wf_P, wf_idx, one_P = [], [], []
if len(new_idx):
    first, last = min(dates[new_idx]), max(dates[new_idx])     # matrices may hold late-added games out of date order
    week_start = date(int(first[:4]), int(first[4:6]), int(first[6:]))
    while week_start.strftime("%Y%m%d") <= last:
        a = week_start.strftime("%Y%m%d"); b = (week_start + timedelta(days=7)).strftime("%Y%m%d")
        test = new_idx[(dates[new_idx] >= a) & (dates[new_idx] < b)]
        if len(test):
            tr_idx = np.where(train_pool & (dates < a))[0]
            wf_P.append(predict2(fit2(tr_idx, l2), test)); wf_idx.append(test)
            one_P.append(predict(fit(tr_idx, l2_one), test))
        week_start += timedelta(days=7)
if wf_idx:
    T = np.concatenate(wf_idx); P = np.vstack(wf_P); P1 = np.vstack(one_P)
    wf_top1, wf_ll = top1(P, T), logloss(P, T)
    minutes_top1 = float((Y[T][np.arange(len(T)), MINUTES[T].argmax(1)] == 1).mean())
    print(f"uniform log-loss {np.log(10):.4f} | L2 {l2} | walk-forward test on {len(T)} games of 2026-27")
    print(f"  TWO-STAGE (team, then player)          top-1 {wf_top1:.3f}   log-loss {wf_ll:.4f}")
    print(f"  one-stage (previous model type)        top-1 {top1(P1, T):.3f}   log-loss {logloss(P1, T):.4f}")
    print(f"  Baseline: most minutes per game        top-1 {minutes_top1:.3f}")
else:
    T, wf_top1, wf_ll, minutes_top1 = [], None, None, None

# ── Final model: every regular-season game, 2025-26 + 2026-27 ─────────────────
all_idx = np.where(train_pool)[0]
x0 = (np.array(prev["w"]) if args.incremental and prev.get("type") == "two_stage"
      and len(prev.get("w", [])) == len(pm.PLAYER_IDX) else None)
team, w, mu, sd = fit2(all_idx, l2, x0=x0)
print(f"  stage 1: P(home scores first) = sigmoid({team['a']:+.3f} {team['b']:+.3f} x logit(P(home wins tip)))")
for k, v in zip(pm.PLAYER_FEATURES, w):
    print(f"  {k:<22}{v:+.3f}")

# ── Field-goal model (for DraftKings, whose bet pays on the first FIELD GOAL — free throws don't count) ──────
# Same inputs, labels = who made the game's first field goal (table first_fg). Adopted 2026-11-19: better than the
# first-points model at predicting the first field goal in both test periods (2026-27 log-loss 2.2473 -> 2.2466,
# Jan–Apr 2026 2.2718 -> 2.2707, #1 pick 14.2% -> 14.7%).
try:
    _fg = dict(con.execute("SELECT game_id, scorer_player_id FROM first_fg"))
    Y_FG = np.zeros_like(Y)
    for k, gid in enumerate(gids):
        _h, _a = info[gid][1], info[gid][2]
        _s = bf.stored_starters(gid, _h, _a)
        for j, pl in enumerate(((_s[_h] + _s[_a])[:10]) if _s else []):
            if str(pl["player_id"]) == str(_fg.get(gid)):
                Y_FG[k, j] = 1
    if Y_FG.sum() > 0.9 * Y.sum():                  # only when first_fg covers (nearly) every game
        _Y, _hf = Y, home_first
        Y = Y_FG                                        # field-goal labels for BOTH stages: which team made the first
        home_first = (Y_FG * home).sum(1) == 1          # field goal (team stage), then which of its starters (player)
        fg_idx = all_idx[Y_FG[all_idx].sum(1) == 1]     # games where a starter made the first field goal
        _PL = PL
        PL = pm.with_early(M, "first_field_goal")[:, :, pm.PLAYER_IDX_BY_KIND["first_field_goal"]]   # its own inputs
        team_fg, w_fg, mu_fg, sd_fg = fit2(fg_idx, l2)
        Y, home_first, PL = _Y, _hf, _PL
        json.dump({"type": "two_stage", "labels": "first_field_goal", "team_labels": "first_field_goal",
                   "early_m": pm.EARLY_M["first_field_goal"], "features": pm.FEATURES,
                   "player_features": pm.PLAYER_FEATURES_BY_KIND["first_field_goal"], "feature_version": pm.FEATURE_VERSION, "team": team_fg, "w": w_fg.tolist(), "mu": mu_fg.tolist(),
                   "sd": sd_fg.tolist(), "l2": l2, "train_games": int(len(all_idx)),
                   "trained_through": str(max(dates[all_idx]))}, open("pregame_model_fg.json", "w"), indent=2)
        print("saved pregame_model_fg.json (first field goal — for DraftKings)")
except Exception as e:  # noqa: BLE001 — never block the main model over the DraftKings one
    print(f"⚠️ field-goal model not trained: {e}")

prev_ll = prev.get("test_logloss") if prev.get("test_scheme") == "walk_forward" else None
if wf_ll is not None and prev_ll is not None and wf_ll > prev_ll + WORSE_MARGIN:
    print(f"⚠️ Kept the previous model: new walk-forward log-loss {wf_ll:.4f} is worse than {prev_ll:.4f}")
    sys.exit(KEPT_PREVIOUS)

json.dump({"type": "two_stage", "early_m": pm.EARLY_M["first_points"], "feature_version": pm.FEATURE_VERSION, "features": pm.FEATURES, "player_features": pm.PLAYER_FEATURES_BY_KIND["first_points"],
           "team": team, "w": w.tolist(), "mu": mu.tolist(), "sd": sd.tolist(), "l2": l2,
           "train_games": int(len(all_idx)), "trained_through": str(max(dates[all_idx])),
           "test_scheme": "walk_forward", "test_games": int(len(T)),
           "test_top1": wf_top1, "test_logloss": wf_ll, "baseline_minutes_top1": minutes_top1},
          open("pregame_model.json", "w"), indent=2)
print(f"saved pregame_model.json (two-stage; trained on {len(all_idx)} games through {max(dates[all_idx])})")
