"""Experiment (read-only, 2026-11-18):
  A. recency weighting — training games weighted by 0.5 ** (age in days / half-life) in stage 2 (and stage 1)
  B. mixing the model with the simple rule (each starter's shrunk season first-basket rate, normalised per game)
Week by week on 2026-27 and Jan–Apr 2026, trained only on earlier games. Full log-loss decides."""
import io, contextlib, os, sys
import numpy as np
from datetime import date, timedelta
from scipy.optimize import minimize
os.chdir("/Users/solomonkhalili/Desktop/NBA"); sys.path.insert(0, ".")
src = open("train_pregame.py").read().replace("args = parser.parse_args()", "args = parser.parse_args([])")
ns = {"__name__": "x"}
with contextlib.redirect_stdout(io.StringIO()):
    exec(compile(src[:src.index("# ── L2 (regularisation strength)")], "t", "exec"), ns)
import pregame_model as pm
dates, season, Y, home, home_first, tip_home, PL, M = (ns[k] for k in ("dates", "season", "Y", "home", "home_first", "tip_home", "PL", "M"))
FB = pm.FEATURES.index("fb_rate_shrunk")
dnum = np.array([date(int(d[:4]), int(d[4:6]), int(d[6:])).toordinal() for d in dates])

def fit_w(idx, half, now, l2=1e-3):
    wts = 0.5 ** ((now - dnum[idx]) / half) if half else np.ones(len(idx)); wts = wts / wts.mean()
    x, yy = pm.logit(tip_home[idx]), home_first[idx].astype(float)
    tv = minimize(lambda v: -np.mean(wts * (yy * np.log(1 / (1 + np.exp(-(v[0] + v[1] * x)))) +
                                            (1 - yy) * np.log(1 - 1 / (1 + np.exp(-(v[0] + v[1] * x)))))) + l2 * (v @ v), np.zeros(2)).x
    mask = np.where(home_first[idx][:, None], home[idx], ~home[idx])
    flat = PL[idx][mask]; mu, sd = flat.mean(0), flat.std(0) + 1e-9
    Z = ((PL[idx][mask] - mu) / sd).reshape(len(idx), 5, -1); Ys = Y[idx][mask].reshape(len(idx), 5)
    def nll(w):
        s = Z @ w; s -= s.max(1, keepdims=True); e = np.exp(s); p = e / e.sum(1, keepdims=True)
        return (-(wts * np.log((p * Ys).sum(1))).mean() + l2 * (w @ w),
                -(wts[:, None] * ((Ys - p)[:, :, None] * Z).sum(1)).mean(0) + 2 * l2 * w)
    w = minimize(nll, np.zeros(Z.shape[2]), jac=True, method="L-BFGS-B").x
    return {"a": tv[0], "b": tv[1]}, w, mu, sd

def run(lo, hi, half=None, mix=0.0):
    pool = season != "playoffs"; ws = date(int(lo[:4]), int(lo[4:6]), int(lo[6:])); P, T = [], []
    while ws.strftime("%Y%m%d") <= hi:
        a_, b_ = ws.strftime("%Y%m%d"), (ws + timedelta(days=7)).strftime("%Y%m%d")
        te = np.where(pool & (dates >= a_) & (dates < b_) & (dates <= hi))[0]
        if len(te):
            tr = np.where(pool & (dates < a_))[0]
            team, w, mu, sd = fit_w(tr, half, ws.toordinal())
            for k in te:
                p = pm.two_stage(team, w, mu, sd, M[k])
                if mix:
                    simple = M[k][:, FB] / M[k][:, FB].sum()
                    p = (1 - mix) * p + mix * simple
                P.append(p); T.append(k)
        ws += timedelta(days=7)
    P, T = np.array(P), np.array(T)
    return (Y[T][np.arange(len(T)), P.argmax(1)] == 1).mean(), -np.log((P * Y[T]).sum(1)).mean()

print(f"{'':36} {'2026-27':>20} {'Jan–Apr 2026':>20}")
for name, kw in (("current (all games equal)", {}), ("recency, half-life 365 days", dict(half=365)),
                 ("recency, half-life 180 days", dict(half=180)), ("recency, half-life 90 days", dict(half=90)),
                 ("mix 20% simple rule", dict(mix=0.2)), ("mix 40% simple rule", dict(mix=0.4))):
    a1, l1 = run("20261019", max(dates), **kw); a2, l2 = run("20260101", "20260412", **kw)
    print(f"{name:36} {a1:6.1%} ll {l1:.4f}   {a2:6.1%} ll {l2:.4f}", flush=True)
print("DONE")
