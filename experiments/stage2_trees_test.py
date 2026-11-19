"""Experiment (read-only, 2026-11-18): stage 2 (which PLAYER, given his team scores first) with gradient-boosted
trees (XGBoost, pairwise ranking within each team's 5 starters) instead of the linear conditional logit, and a blend
of the two. Week by week on 2026-27 and Jan–Apr 2026, trained only on earlier games; trees' scores are turned into
chances with a temperature fitted on the training games. Same stage 1 for all. Full log-loss decides."""
import io, contextlib, os, sys
import numpy as np
from datetime import date, timedelta
from scipy.optimize import minimize_scalar
import xgboost as xgb
os.chdir("/Users/solomonkhalili/Desktop/NBA"); sys.path.insert(0, ".")
src = open("train_pregame.py").read().replace("args = parser.parse_args()", "args = parser.parse_args([])")
ns = {"__name__": "x"}
with contextlib.redirect_stdout(io.StringIO()):
    exec(compile(src[:src.index("# ── L2 (regularisation strength)")], "t", "exec"), ns)
import pregame_model as pm
dates, season, Y, home, home_first, tip_home, PL = (ns[k] for k in ("dates", "season", "Y", "home", "home_first", "tip_home", "PL"))

def team_rows(idx):
    """the scoring team's 5 starters per game -> X (n*5, f), y (n*5), groups"""
    mask = np.where(home_first[idx][:, None], home[idx], ~home[idx])
    return PL[idx][mask], Y[idx][mask], np.full(len(idx), 5)

def softmax_side(s, side):
    e = np.exp(s[side] - s[side].max()); return e / e.sum()

def full_probs(stage1, s, k):
    ph = float(pm.home_scores_first(stage1, tip_home[k])); o = np.zeros(10)
    o[home[k]] = ph * softmax_side(s, home[k]); o[~home[k]] = (1 - ph) * softmax_side(s, ~home[k]); return o

def run(kind, lo, hi, depth=3, n_est=200, blend=0.5):
    pool = season != "playoffs"; ws = date(int(lo[:4]), int(lo[4:6]), int(lo[6:])); P, T = [], []
    while ws.strftime("%Y%m%d") <= hi:
        a_, b_ = ws.strftime("%Y%m%d"), (ws + timedelta(days=7)).strftime("%Y%m%d")
        te = np.where(pool & (dates >= a_) & (dates < b_) & (dates <= hi))[0]
        if len(te):
            tr = np.where(pool & (dates < a_))[0]
            team, w, mu, sd = ns["fit2"](tr, 1e-3)
            lin = lambda k: ((PL[k] - mu) / sd) @ w
            if kind != "linear":
                X, y, g = team_rows(tr)
                m = xgb.XGBRanker(objective="rank:pairwise", n_estimators=n_est, max_depth=depth, learning_rate=0.05,
                                  subsample=0.8, colsample_bytree=0.8, min_child_weight=20, random_state=0)
                m.fit(X, y, group=g)
                ys = y.reshape(-1, 5); raw = m.predict(X).reshape(-1, 5)
                def nll(t):
                    s = raw * t; s = s - s.max(1, keepdims=True); p = np.exp(s); p /= p.sum(1, keepdims=True)
                    return -np.log((p * ys).sum(1) + 1e-12).mean()
                temp = minimize_scalar(nll, bounds=(0.01, 20), method="bounded").x
                tree = lambda k: m.predict(PL[k]) * temp
            for k in te:
                s = lin(k) if kind == "linear" else tree(k) if kind == "trees" else blend * lin(k) + (1 - blend) * tree(k)
                P.append(full_probs(team, s, k)); T.append(k)
        ws += timedelta(days=7)
    P = np.array(P); T = np.array(T)
    return (Y[T][np.arange(len(T)), P.argmax(1)] == 1).mean(), -np.log((P * Y[T]).sum(1)).mean()

print(f"{'':34} {'2026-27':>20} {'Jan–Apr 2026':>20}")
for name, kw in (("linear (current)", dict(kind="linear")), ("trees, depth 2", dict(kind="trees", depth=2)),
                 ("trees, depth 3", dict(kind="trees", depth=3)), ("50/50 blend linear + trees d2", dict(kind="blend", depth=2))):
    a1, l1 = run(lo="20261019", hi=max(dates), **kw); a2, l2 = run(lo="20260101", hi="20260412", **kw)
    print(f"{name:34} {a1:6.1%} ll {l1:.4f}   {a2:6.1%} ll {l2:.4f}", flush=True)
print("DONE")
