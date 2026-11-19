"""Experiment (2026-11-19, runs on a COPY with 2024-25 downloaded): does the 2024-25 season help? Based on combined_test.py:
(2026-11-18): opponent-defense-by-position (matchups) and head-to-head (this season, M=5),
alone and TOGETHER, as extra step-2 inputs. Same real procedure as h2h_fb_test.py.
(Original h2h notes:)
  his rate vs this team = (first baskets vs them + M * his overall rate) / (starts vs them + M)
Two versions: all earlier meetings, and this-season-only meetings (trade-aware: last season's opponent roster may be
gone). Uses the REAL training procedure: L2 chosen on 2025-26 only, then week-by-week tests on 2026-27 and
Jan–Apr 2026 — same for every version."""
import io, contextlib, os, sys, sqlite3, warnings
import numpy as np
from collections import defaultdict
from datetime import date, timedelta
warnings.filterwarnings("ignore")
os.chdir("/Users/solomonkhalili/Desktop/NBA")
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



ns, _ = build(None)
home = ns["home"]; FULL = ns["PL"].copy(); TIPc = pm.FEATURES.index("tip_win_prob")
def predict_cur(model, ix):
    team, w, mu, sd = model; PLc = ns["PL"]; out = []
    for k in ix:
        hm = home[k]; ph = float(pm.home_scores_first(team, float(ns["M"][k][hm][0, TIPc])))
        s_ = ((PLc[k] - mu) / sd) @ w; o = np.zeros(10)
        for side, pt in ((hm, ph), (~hm, 1 - ph)):
            e = np.exp(s_[side] - s_[side].max()); o[side] = pt * e / e.sum()
        out.append(o)
    return np.vstack(out)
ns["predict2"] = predict_cur
M_ = ns["M"]; FB_ = pm.FEATURES.index("fb_rate_shrunk")
def post_(P, T):
    out = []
    for p, k in zip(P, T):
        fb = M_[k][:, FB_]; q = 0.75 * p + 0.25 * fb / fb.sum(); out.append(pm.stretch_teams(q, home[k], 1.2))
    return np.array(out)
names = pm.PLAYER_FEATURES
print(f"{'':36} {'2026-27':>16} {'Jan-Apr 2026':>16}")
for label, drop in (("current (all inputs)", []), ("without opponent-by-position", ["opp_pos_share"]),
                    ("without head-to-head", ["vs_opp_rate"]), ("without both", ["opp_pos_share", "vs_opp_rate"])):
    keep = [j for j, n in enumerate(names) if n not in drop]
    ns["PL"] = FULL[:, :, keep]
    PA, TA = weekly_P(ns, 0.001, "20261019", max(dates)); PB, TB = weekly_P(ns, 0.001, "20260101", "20260412")
    print(f"{label:36} {sc(post_(PA, TA), TA):>16} {sc(post_(PB, TB), TB):>16}", flush=True)
# Result 2026-11-19 (history model, live settings): current 2.22734 / 2.25270; without opponent-by-position 2.22785 / 2.25382;
# without head-to-head 2.22763 / 2.25304; without both 2.22827 / 2.25438 — both inputs still help in both periods. Kept.
