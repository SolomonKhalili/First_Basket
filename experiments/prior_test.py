"""Experiment (2026-11-19): a better STARTING GUESS for the season first-basket rate. Today every starter's rate is
shrunk toward 1 in 10 with 30 imaginary games. Tested: shrink toward (A) his position group's league rate
(G / F / C from NBA.com's lineup that night, point-in-time), or (B) his share of his team's starters' points
(x 0.5, team scores first about half the time), at several shrink strengths. Same walk-forward test as
opensource_test.py (FanDuel model, live settings). Harness copied from opensource_test.py."""

import io, contextlib, os, sys, sqlite3, warnings
import numpy as np
from collections import defaultdict
from datetime import date, timedelta
warnings.filterwarnings("ignore")
os.chdir("/Users/solomonkhalili/Desktop/NBA"); sys.path.insert(0, ".")
import pregame_model as pm
import build_features as bf
SRC = open("train_pregame.py").read().replace("args = parser.parse_args()", "args = parser.parse_args([])")
HEAD = SRC[:SRC.index("# ── Two-stage model")]
TWO = SRC[SRC.index("# ── Two-stage model"):SRC.index("# ── L2 (regularisation strength)")]
import pregame_model as pm
print("Loading data...", flush=True)
ns0 = {"__name__": "x"}
with contextlib.redirect_stdout(io.StringIO()):
    exec(compile(HEAD, "head", "exec"), ns0)
gids, dates, season, Y = ns0["gids"], ns0["dates"], ns0["season"], ns0["Y"]
con = sqlite3.connect("nba_first_basket.db")
info = {g: (d, h, a) for g, d, h, a in con.execute("SELECT game_id, game_date, home_team, away_team FROM games")}
scorer = dict(con.execute("SELECT game_id, scorer_player_id FROM first_baskets"))
starts = defaultdict(list)                     # player -> [(date, opponent, scored first?)]
for gid, team, pid in con.execute("SELECT game_id, team, player_id FROM lineups WHERE is_starter = 1"):
    if gid not in info or gid not in scorer:
        continue
    d, h, a = info[gid]
    starts[str(pid)].append((d, a if team == h else h, str(scorer[gid]) == str(pid)))
season_of = lambda d: "2026-27" if d >= "20260801" else "2025-26"

def feature(M, same_season):
    out = np.full((len(gids), 10), 0.1)
    meetings = []
    for k, gid in enumerate(gids):
        d, h, a = info[gid]
        s = bf.stored_starters(gid, h, a)
        if not s:
            continue
        for j, p in enumerate((s[h] + s[a])[:10]):
            opp = a if j < len(s[h]) else h
            prior = [x for x in starts.get(str(p["player_id"]), []) if x[0] < d]
            r_all = (sum(x[2] for x in prior) + 30 * 0.1) / (len(prior) + 30)
            vs = [x for x in prior if x[1] == opp and (not same_season or season_of(x[0]) == season_of(d))]
            meetings.append(len(vs))
            out[k, j] = (sum(x[2] for x in vs) + M * r_all) / (len(vs) + M)
    return out, np.mean(meetings)

def build(extra):
    ns = dict(ns0)
    with contextlib.redirect_stdout(io.StringIO()):
        exec(compile(TWO, "two", "exec"), ns)
    if extra is not None:
        ns["PL"] = np.concatenate([ns["PL"], extra if extra.ndim == 3 else extra[:, :, None]], axis=2)
        def predict2(model, ix, PL=ns["PL"], home=ns["home"], tip=ns["tip_home"]):
            team, w, mu, sd = model; out = []
            for k in ix:
                hm = home[k]; ph = float(pm.home_scores_first(team, tip[k]))
                s_ = ((PL[k] - mu) / sd) @ w; o = np.zeros(10)
                for side, pt in ((hm, ph), (~hm, 1 - ph)):
                    e = np.exp(s_[side] - s_[side].max()); o[side] = pt * e / e.sum()
                out.append(o)
            return np.vstack(out)
        ns["predict2"] = predict2
    old = np.where(season == "old")[0]; cut = int(len(old) * 0.85)
    tr, va = old[:cut], old[cut:]
    ll = lambda P, ix: -np.log((P * Y[ix]).sum(1)).mean()
    l2 = min(ns0["L2_GRID"], key=lambda v: ll(ns["predict2"](ns["fit2"](tr, v), va), va))     # the real procedure
    return ns, l2

def weekly(ns, l2, lo, hi):
    pool = season != "playoffs"; ws = date(int(lo[:4]), int(lo[4:6]), int(lo[6:])); P, T = [], []
    while ws.strftime("%Y%m%d") <= hi:
        a_, b_ = ws.strftime("%Y%m%d"), (ws + timedelta(days=7)).strftime("%Y%m%d")
        te = np.where(pool & (dates >= a_) & (dates < b_) & (dates <= hi))[0]
        if len(te):
            P.append(ns["predict2"](ns["fit2"](np.where(pool & (dates < a_))[0], l2), te)); T.append(te)
        ws += timedelta(days=7)
    T = np.concatenate(T); P = np.vstack(P)
    return (Y[T][np.arange(len(T)), P.argmax(1)] == 1).mean(), -np.log((P * Y[T]).sum(1)).mean()



dates_ = np.array(dates)
y24 = dates_ < "20250801"
season[y24 & (dates_ >= "20250414")] = "playoffs"          # 2024-25 play-in/playoffs (not labelled in game_types)
print("2024-25 regular-season games in the matrix:", int((y24 & (season != "playoffs")).sum()), flush=True)

def weekly2(ns, l2, lo, hi, use24):
    pool = (season != "playoffs") & (use24 | ~y24); ws = date(int(lo[:4]), int(lo[4:6]), int(lo[6:])); P, T = [], []
    while ws.strftime("%Y%m%d") <= hi:
        a_, b_ = ws.strftime("%Y%m%d"), (ws + timedelta(days=7)).strftime("%Y%m%d")
        te = np.where((season != "playoffs") & (dates >= a_) & (dates < b_) & (dates <= hi))[0]
        if len(te):
            P.append(ns["predict2"](ns["fit2"](np.where(pool & (dates < a_))[0], l2), te)); T.append(te)
        ws += timedelta(days=7)
    T = np.concatenate(T); P = np.vstack(P)
    return (Y[T][np.arange(len(T)), P.argmax(1)] == 1).mean(), -np.log((P * Y[T]).sum(1)).mean(), len(T)


import pregame_model as pm
FB = pm.FEATURES.index("fb_rate_shrunk"); MINS = pm.FEATURES.index("minutes_per_game")
def weekly_P(ns, l2, lo, hi):
    pool = (season != "playoffs") & ~y24; ws = date(int(lo[:4]), int(lo[4:6]), int(lo[6:])); P, T = [], []
    while ws.strftime("%Y%m%d") <= hi:
        a_, b_ = ws.strftime("%Y%m%d"), (ws + timedelta(days=7)).strftime("%Y%m%d")
        te = np.where((season != "playoffs") & (dates >= a_) & (dates < b_) & (dates <= hi))[0]
        if len(te):
            P.append(ns["predict2"](ns["fit2"](np.where(pool & (dates < a_))[0], l2), te)); T.append(te)
        ws += timedelta(days=7)
    return np.vstack(P), np.concatenate(T)
def weekly_P(ns, l2, lo, hi):
    pool = (season != "playoffs") & ~y24; ws = date(int(lo[:4]), int(lo[4:6]), int(lo[6:])); P, T = [], []
    while ws.strftime("%Y%m%d") <= hi:
        a_, b_ = ws.strftime("%Y%m%d"), (ws + timedelta(days=7)).strftime("%Y%m%d")
        te = np.where((season != "playoffs") & (dates >= a_) & (dates < b_) & (dates <= hi))[0]
        if len(te):
            P.append(ns["predict2"](ns["fit2"](np.where(pool & (dates < a_))[0], l2), te)); T.append(te)
        ws += timedelta(days=7)
    return np.vstack(P), np.concatenate(T)
def sc(P, T): return f"{(Y[T][np.arange(len(T)), P.argmax(1)] == 1).mean():6.1%} {-np.log((P * Y[T]).sum(1)).mean():.5f}"





from bisect import bisect_left
import context_features as cf, starters as st
X = np.load("X_train.npy"); fnames = __import__("json").load(open("feature_names.json")); J = {n: i for i, n in enumerate(fnames)}
X3 = X.reshape(len(gids), 10, -1)
RATE, GP, PTS = X3[:, :, J["first_basket_rate"]], X3[:, :, J["player_games_played"]], X3[:, :, J["points_per_game"]]
ctx = cf.Context(con)
GRP = np.full((len(gids), 10), "", dtype=object)
for k, gid in enumerate(gids):
    d, h, a = info[gid]; s_ = bf.stored_starters(gid, h, a)
    if not s_: continue
    for j, p in enumerate((s_[h] + s_[a])[:10]):
        GRP[k, j] = ctx.group_that_night(d, h if j < len(s_[h]) else a, p["name"]) or ""
print("rows with a position:", round((GRP != "").mean() * 100, 1), "%", flush=True)
# league rate by group from games BEFORE each date (cumulative)
order = np.argsort(dates, kind="stable"); cum = {g: [0, 0] for g in "GFC"}; GR = {}
last = None; snap = {}
for k in order:
    d = dates[k]
    if d != last:
        snap[d] = {g: (c[1] + 0.1 * 50) / (c[0] + 50) for g, c in cum.items()}; last = d
    for j in range(10):
        g = GRP[k, j]
        if g: cum[g][0] += 1; cum[g][1] += Y[k, j]
for g in "GFC": print(g, "rate", round(cum[g][1] / max(cum[g][0], 1), 4), cum[g][0], "starts", flush=True)
POSP = np.array([[snap[dates[k]].get(GRP[k, j], 0.1) if GRP[k, j] else 0.1 for j in range(10)] for k in range(len(gids))])
home_ = ns0["M"][:, :, pm.FEATURES.index("is_home")] > 0.5
SH = np.zeros_like(PTS)
for k in range(len(gids)):
    for side in (home_[k], ~home_[k]):
        t = PTS[k, side].sum(); SH[k, side] = 0.5 * PTS[k, side] / t if t > 0 else 0.1
SH = np.clip(SH, 0.02, 0.3)
print("building inputs...", flush=True)
ns, _ = build(np.zeros((len(gids), 10, 1)))
home = ns["home"]; M_ = ns["M"]; FB_ = pm.FEATURES.index("fb_rate_shrunk"); TIPc = pm.FEATURES.index("tip_win_prob")
def predict_cur(model, ix):                # always uses the CURRENT ns["PL"]
    team, w, mu, sd = model; PLc = ns["PL"]; out = []
    for k in ix:
        hm = home[k]; ph = float(pm.home_scores_first(team, float(M_[k][hm][0, TIPc])))
        s_ = ((PLc[k] - mu) / sd) @ w; o = np.zeros(10)
        for side, pt in ((hm, ph), (~hm, 1 - ph)):
            e = np.exp(s_[side] - s_[side].max()); o[side] = pt * e / e.sum()
        out.append(o)
    return np.vstack(out)
ns["predict2"] = predict_cur
BASE = ns["PL"].copy(); names = list(pm.PLAYER_FEATURES)
def post_(P, T):
    out = []
    for p, k in zip(P, T):
        fb = M_[k][:, FB_]; q = 0.75 * p + 0.25 * fb / fb.sum(); out.append(pm.stretch_teams(q, home[k], 1.2))
    return np.array(out)
def run(label, PL):
    ns["PL"] = PL
    PA, TA = weekly_P(ns, 0.001, "20261019", max(dates)); PB, TB = weekly_P(ns, 0.001, "20260101", "20260412")
    print(f"{label:40} {sc(post_(PA, TA), TA):>16} {sc(post_(PB, TB), TB):>16}", flush=True)

FBi = names.index("fb_rate_shrunk")
M0 = ns["M"].copy()
def run2(label, prior, m):
    fb = (RATE * GP + m * prior) / (GP + m)
    PL = BASE[:, :, :len(names)].copy(); PL[:, :, FBi] = fb; Mx = M0.copy(); Mx[:, :, FB_] = fb
    global M_; M_ = Mx; ns["M"] = Mx
    run(label, PL)
print(f"{'':40} {'2026-27':>16} {'Jan-Apr 2026':>16}")
run2("current (1 in 10, 30 games)", 0.1, 30)
for m in (15, 30, 60):
    run2(f"position, {m} games", POSP, m)
    run2(f"scoring share, {m} games", SH, m)
    run2(f"half/half, {m} games", 0.5 * POSP + 0.5 * SH, m)
# RESULT (2026-11-19, (top pick right %, log-loss), lower log-loss better). NOT ADOPTED — nothing better in both periods.
#   current (1 in 10, 30 games) ... 2026-27 2.22477   Jan–Apr 2026 2.25075
#   position, 15/30/60 games ...... 2.22531 / 2.22499 / 2.22516 (worse)   2.25021 / 2.25011 / 2.25032 (better)
#   scoring share, 15/30/60 ....... 2.22920 / 2.22830 / 2.22723 (worse)   2.25042 / 2.25037 / 2.25051
#   half/half, 15/30/60 ........... 2.22675 / 2.22606 / 2.22547 (worse)   2.25024 / 2.25008 / 2.25010
# Centers do score first more (12.0% of starts vs 9.5% guards / 9.6% forwards), but the tip and early-shot inputs
# already carry that, so a position starting guess adds nothing this season.
