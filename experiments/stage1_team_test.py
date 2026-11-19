"""Experiment (read-only, 2026-11-18): stage 1 (which TEAM scores first) beyond the tip-off chance.
Point-in-time team rates, shrunk toward the league with K imaginary games:
  conv  = P(team scores first | it WON the tip)      (first-possession offence)
  steal = P(team scores first | it LOST the tip)     (first-possession defence)
Blend: P(home first) = tip*conv_h + (1-tip)*steal_h, mirrored for the away team, then logistic calibration.
Week-by-week on 2026-27 and Jan–Apr 2026; stage-1 log-loss decides (coin flip 0.6931)."""
import io, contextlib, os, sys, sqlite3
import numpy as np
from datetime import date, timedelta
from scipy.optimize import minimize
os.chdir("/Users/solomonkhalili/Desktop/NBA"); sys.path.insert(0, ".")
src = open("train_pregame.py").read().replace("args = parser.parse_args()", "args = parser.parse_args([])")
ns = {"__name__": "x"}
with contextlib.redirect_stdout(io.StringIO()):
    exec(compile(src[:src.index("# ── L2 (regularisation strength)")], "t", "exec"), ns)
import pregame_model as pm
gids, dates, season, home_first, tip_home = (ns[k] for k in ("gids", "dates", "season", "home_first", "tip_home"))
con = sqlite3.connect("nba_first_basket.db")
info = {g: (d, h, a, tw) for g, d, h, a, tw in con.execute("SELECT game_id, game_date, home_team, away_team, tip_winner_team FROM games")}
first_team = dict(con.execute("SELECT game_id, scorer_team FROM first_baskets"))
# all labelled games (incl. ones not in the matrices), in date order, for the running team rates
events = sorted((info[g][0], info[g][1], info[g][2], info[g][3], first_team[g]) for g in first_team if g in info and info[g][3])

def team_feats(K):
    """per matrix game: (conv_h, steal_h, conv_a, steal_a), point-in-time"""
    from collections import defaultdict
    won, won_sc, lost, lost_sc = defaultdict(int), defaultdict(int), defaultdict(int), defaultdict(int)
    lg = {"w": 0, "ws": 0}
    by_date = {}
    for e in events: by_date.setdefault(e[0], []).append(e)
    order = sorted(set(dates) | set(by_date))
    feat = {}
    want = {}
    for k, g in enumerate(gids): want.setdefault(dates[k], []).append(k)
    for d in order:
        pw = (lg["ws"] + 6.4) / (lg["w"] + 10)                       # league P(tip winner scores first)
        for k in want.get(d, []):
            _, h, a, _ = info[gids[k]]
            c = lambda t: (won_sc[t] + K * pw) / (won[t] + K)
            s_ = lambda t: (lost_sc[t] + K * (1 - pw)) / (lost[t] + K)
            feat[k] = (c(h), s_(h), c(a), s_(a))
        for (_, h, a, tw, ft) in by_date.get(d, []):
            tl = a if tw == h else h
            won[tw] += 1; won_sc[tw] += ft == tw; lost[tl] += 1; lost_sc[tl] += ft == tl
            lg["w"] += 1; lg["ws"] += ft == tw
    return np.array([feat[k] for k in range(len(gids))])

def blend(F, tip):
    ch, sh, ca, sa = F.T
    ph = tip * ch + (1 - tip) * sh                 # home's view
    pa = (1 - tip) * ca + tip * sa                 # away's view
    p = ph / (ph + pa)
    return np.clip(p, 1e-4, 1 - 1e-4)

def fit(X, y, l2=1e-3):
    def nll(v):
        p = 1 / (1 + np.exp(-(X @ v)))
        return -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)) + l2 * (v[1:] @ v[1:])
    return minimize(nll, np.zeros(X.shape[1])).x

def run(make_X, lo, hi):
    ws = date(int(lo[:4]), int(lo[4:6]), int(lo[6:])); ll, acc, n = 0.0, 0, 0
    pool = season != "playoffs"
    while ws.strftime("%Y%m%d") <= hi:
        a_, b_ = ws.strftime("%Y%m%d"), (ws + timedelta(days=7)).strftime("%Y%m%d")
        te = np.where(pool & (dates >= a_) & (dates < b_) & (dates <= hi))[0]
        tr = np.where(pool & (dates < a_))[0]
        if len(te):
            X = make_X(); v = fit(X[tr], home_first[tr].astype(float))
            p = 1 / (1 + np.exp(-(X[te] @ v))); y = home_first[te]
            ll += -np.sum(np.where(y, np.log(p), np.log(1 - p))); acc += np.sum((p > .5) == y); n += len(te)
        ws += timedelta(days=7)
    return ll / n, acc / n

one = np.ones(len(gids)); lt = pm.logit(tip_home)
variants = {"current (tip only)": lambda: np.column_stack([one, lt])}
for K in (10, 30, 60):
    F = team_feats(K); B = pm.logit(blend(F, tip_home))
    variants[f"tip + team conversion blend (K={K})"] = (lambda B=B: np.column_stack([one, lt, B]))
    variants[f"team conversion blend only (K={K})"] = (lambda B=B: np.column_stack([one, B]))
print(f"{'':42} {'2026-27':>18} {'Jan–Apr 2026':>18}")
for name, mk in variants.items():
    l1, a1 = run(mk, "20261019", max(dates)); l2_, a2 = run(mk, "20260101", "20260412")
    print(f"{name:42} {a1:6.1%} ll {l1:.4f}   {a2:6.1%} ll {l2_:.4f}", flush=True)
print("DONE")
