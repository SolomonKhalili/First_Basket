"""Experiment (2026-11-19): let player history FADE with age instead of counting every past game equally.
Each past game counts 0.5 ** (age in days / half-life). Applied to the season first-basket rate (its shrink uses the
faded game count) and, in the "all stats" version, also to minutes, points, shots per 36 and true shooting.
Run twice: in the live folder (2024-25 history) and in a copy that also has 2023-24 (NBA_DIR=that copy).
Same walk-forward harness as prior_test.py / opensource_test.py (FanDuel model, live settings)."""

import io, contextlib, os, sys, sqlite3, warnings
import numpy as np
from collections import defaultdict
from datetime import date, timedelta
warnings.filterwarnings("ignore")
os.chdir(os.environ.get("NBA_DIR", "/Users/solomonkhalili/Desktop/NBA")); sys.path.insert(0, ".")
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

import gzip, json, pickle
from bisect import bisect_left
from datetime import date as _d
logs = pickle.load(open("player_game_logs.pkl", "rb"))
known = {g["game_id"] for L in logs.values() for g in L}
for pid, lst in json.loads(gzip.open("data_patches/history_2024_25.json.gz", "rt").read())["logs"].items():
    add = [g for g in lst if g["game_id"] not in known]
    if add: logs.setdefault(pid, []); logs[pid] = logs[pid] + add
scorer = dict(con.execute("SELECT game_id, scorer_player_id FROM first_baskets"))
day = lambda s: _d(int(s[:4]), int(s[4:6]), int(s[6:8])).toordinal()
A = {}
for pid, L in logs.items():
    L = sorted(L, key=lambda g: g["game_date"])
    A[str(pid)] = (np.array([day(g["game_date"]) for g in L]), [g["game_date"] for g in L],
                   np.array([[1.0 if str(scorer.get(g["game_id"])) == str(pid) else 0.0, g["minutes"], g["pts"],
                              g["fga"] + 0.44 * g["fta"]] for g in L]))
PIDS = np.full((len(gids), 10), "", dtype=object)
for k, gid in enumerate(gids):
    d, h, a = info[gid]; s_ = bf.stored_starters(gid, h, a)
    if s_:
        for j, p in enumerate((s_[h] + s_[a])[:10]): PIDS[k, j] = str(p["player_id"])
ix = {n: names.index(n) for n in ("fb_rate_shrunk", "shot_attempts_per36", "true_shooting", "minutes_per_game", "points_per_game")}
B0 = BASE[:, :, :len(names)].copy(); M0 = ns["M"].copy()
def faded(H, all_stats):
    PL = B0.copy(); Mx = M0.copy()
    for k in range(len(gids)):
        dd = dates[k]; t = day(dd)
        for j in range(10):
            a_ = A.get(PIDS[k, j])
            if a_ is None: continue
            i = bisect_left(a_[1], dd)
            if i == 0: continue
            w = 0.5 ** ((t - a_[0][:i]) / H) if H else np.ones(i); V = a_[2][:i]; n = w.sum()
            fb, mins, pts, sh = (w @ V) / n
            PL[k, j, ix["fb_rate_shrunk"]] = Mx[k, j, FB_] = pm.shrunk(fb, n, pm.M_SEASON)
            if all_stats:
                PL[k, j, ix["minutes_per_game"]] = mins; PL[k, j, ix["points_per_game"]] = np.log1p(pts)
                PL[k, j, ix["shot_attempts_per36"]] = sh * 36 / mins if mins > 0 else 0.0
                PL[k, j, ix["true_shooting"]] = pts / (2 * sh) if sh > 0 else 0.0
    return PL, Mx
def run3(label, H, all_stats):
    global M_
    PL, Mx = faded(H, all_stats); M_ = Mx; ns["M"] = Mx; run(label, PL)
print(f"{'':40} {'2026-27':>16} {'Jan-Apr 2026':>16}")
M_ = M0; ns["M"] = M0; run("current", B0)
run3("no fading (check: should match current)", None, True)
for H in (730, 365, 180, 90):
    run3(f"fade, half-life {H} days, first-basket only", H, False)
    run3(f"fade, half-life {H} days, all stats", H, True)
# RESULT (2026-11-19, log-loss, lower is better). NOT ADOPTED — every fade made 2026-27 worse.
#   live folder (2024-25 history): current 2.22477 / 2.25075 (no-fade check matches exactly)
#     half-life 730 / 365 / 180 / 90 days, first-basket only: 2.22541 / 2.22628 / 2.22841 / 2.23252 (2026-27, worse)
#     ... all stats: 2.22550 / 2.22653 / 2.22927 / 2.23496; Jan–Apr slightly better (best 2.25005) except 90 days
#     (top pick right went 20.0% -> 21.9% at 180 days, but that's ~4 of 215 games; log-loss is the rule)
#   with 2023-24 also loaded: current 2.23340 / 2.24893; every fade 2.23366 or worse this season.
#   So fading doesn't rescue 2023-24 either: its harm isn't just old box-score stats (the early-shot index and
#   tip history also take in that season unfaded).
