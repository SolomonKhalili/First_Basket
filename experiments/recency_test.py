"""Experiment (read-only): should RECENT games count more when the model learns?
Each training game gets weight 0.5 ** (age in days / HALF_LIFE), age measured from the day the model is trained
(so with HALF_LIFE=60, a game 60 days old counts half, 120 days old a quarter). None = every game counts the same
(current). Same for both stages. Uses the REAL training procedure: L2 chosen on 2025-26 only (with the same
weighting), then week-by-week tests on 2026-27 and Jan–Apr 2026.
Run:  python3 experiments/recency_test.py            (add --control to separate the weighting from the L2 change)"""
import io, contextlib, os, sys, warnings
import numpy as np
from datetime import date, datetime, timedelta
from scipy.optimize import minimize
warnings.filterwarnings("ignore")
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); sys.path.insert(0, ".")
import pregame_model as pm
SRC = open("train_pregame.py").read().replace("args = parser.parse_args()", "args = parser.parse_args([])")
HEAD = SRC[:SRC.index("# ── Two-stage model")]
TWO = SRC[SRC.index("# ── Two-stage model"):SRC.index("# ── L2 (regularisation strength)")]
print("Loading data...", flush=True)
ns = {"__name__": "x"}
with contextlib.redirect_stdout(io.StringIO()):
    exec(compile(HEAD, "head", "exec"), ns)
    exec(compile(TWO, "two", "exec"), ns)
dates, season, Y = ns["dates"], ns["season"], ns["Y"]
home, home_first, tip_home, PL = ns["home"], ns["home_first"], ns["tip_home"], ns["PL"]
predict2, L2_GRID = ns["predict2"], ns["L2_GRID"]
day = np.array([datetime.strptime(d, "%Y%m%d").toordinal() for d in dates])


def weights(idx, half_life):
    if half_life is None:
        return np.ones(len(idx))
    w = 0.5 ** ((day[idx].max() - day[idx]) / half_life)
    return w / w.mean()


def fit2(idx, l2, half_life):
    sw = weights(idx, half_life)
    x, yy = pm.logit(tip_home[idx]), home_first[idx].astype(float)

    def nll_t(v):
        p = 1 / (1 + np.exp(-(v[0] + v[1] * x)))
        return -np.mean(sw * (yy * np.log(p) + (1 - yy) * np.log(1 - p))) + 1e-3 * (v @ v)
    v = minimize(nll_t, np.zeros(2)).x
    team = {"a": float(v[0]), "b": float(v[1])}
    mask = np.where(home_first[idx][:, None], home[idx], ~home[idx])
    flat = PL[idx][mask]
    mu, sd = flat.mean(0), flat.std(0) + 1e-9
    Z = ((PL[idx][mask] - mu) / sd).reshape(len(idx), 5, -1)
    Ys = Y[idx][mask].reshape(len(idx), 5)

    def nll(w):
        s = Z @ w; s -= s.max(1, keepdims=True); e = np.exp(s); p = e / e.sum(1, keepdims=True)
        return (-(sw * np.log((p * Ys).sum(1))).mean() + l2 * (w @ w),
                -(sw[:, None] * ((Ys - p)[:, :, None] * Z).sum(1)).mean(0) + 2 * l2 * w)
    w = minimize(nll, np.zeros(Z.shape[2]), jac=True, method="L-BFGS-B").x
    return team, w, mu, sd


def weekly(l2, hl, lo, hi):
    pool = season != "playoffs"; ws = date(int(lo[:4]), int(lo[4:6]), int(lo[6:])); P, T = [], []
    while ws.strftime("%Y%m%d") <= hi:
        a_, b_ = ws.strftime("%Y%m%d"), (ws + timedelta(days=7)).strftime("%Y%m%d")
        te = np.where(pool & (dates >= a_) & (dates < b_) & (dates <= hi))[0]
        if len(te):
            P.append(predict2(fit2(np.where(pool & (dates < a_))[0], l2, hl), te)); T.append(te)
        ws += timedelta(days=7)
    T = np.concatenate(T); P = np.vstack(P)
    return (Y[T][np.arange(len(T)), P.argmax(1)] == 1).mean(), -np.log((P * Y[T]).sum(1)).mean()


old = np.where(season == "old")[0]; cut = int(len(old) * 0.85)
tr, va = old[:cut], old[cut:]
ll = lambda P, ix: -np.log((P * Y[ix]).sum(1)).mean()
print(f"\n{'':36} {'L2':>6} {'2026-27':>22} {'Jan–Apr 2026':>22}", flush=True)
RUNS = [(None, None), (30, None), (60, None), (120, None), (240, None), (480, None)]
if "--control" in sys.argv:      # same L2 values, no recency: is a gain from the weighting or from the L2 change?
    RUNS = [(None, 0.1), (None, 0.3), (480, 0.001)]
for hl, fixed_l2 in RUNS:
    l2 = fixed_l2 or min(L2_GRID, key=lambda v: ll(predict2(fit2(tr, v, hl), va), va))     # the real procedure
    a1, l1 = weekly(l2, hl, "20261019", max(dates)); a2, l2b = weekly(l2, hl, "20260101", "20260412")
    name = ("every game counts the same" if hl is None else f"half-life {hl} days") + (" [L2 fixed]" if fixed_l2 else "")
    print(f"{name:36} {l2:>6} {a1:6.1%} (ll {l1:.4f})   {a2:6.1%} (ll {l2b:.4f})", flush=True)
print("\nDONE")
