"""Experiment (read-only, 2026-11-19): early free-throw drawing (FTA per 36, point-in-time) as a step-2 input.
Based on combined_test.py (same real procedure). Original header:
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
os.chdir("/Users/solomonkhalili/Desktop/NBA"); sys.path.insert(0, ".")
import pregame_model as pm
import build_features as bf
SRC = open("train_pregame.py").read().replace("args = parser.parse_args()", "args = parser.parse_args([])")
HEAD = SRC[:SRC.index("# ── Two-stage model")]
TWO = SRC[SRC.index("# ── Two-stage model"):SRC.index("# ── L2 (regularisation strength)")]
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



import pickle
logs = pickle.load(open("player_game_logs.pkl", "rb"))
print("Building free-throw input...", flush=True)
def fta_feature(M=300):                 # shrunk FTA per 36 min, games before that night; league prior from all logs
    tot_fta = sum(g["fta"] for v in logs.values() for g in v); tot_min = sum(g["minutes"] for v in logs.values() for g in v)
    prior = 36 * tot_fta / max(tot_min, 1)
    out = np.full((len(gids), 10), prior)
    for k, gid in enumerate(gids):
        d, h, a = info[gid]
        s = bf.stored_starters(gid, h, a)
        if not s: continue
        for j, p in enumerate((s[h] + s[a])[:10]):
            g = [x for x in logs.get(str(p["player_id"]), []) if x["game_date"] < d]
            mins = sum(x["minutes"] for x in g); fta = sum(x["fta"] for x in g)
            out[k, j] = (fta + prior * M / 36) / ((mins + M) / 36)      # per 36 min, M minutes at the league rate
    return out
ft = fta_feature()
print("FTA/36 range:", round(float(ft.min()), 2), round(float(ft.max()), 2), "mean", round(float(ft.mean()), 2), flush=True)
print(f"\n{'':24} {'L2':>6} {'2026-27':>22} {'Jan–Apr 2026':>22}", flush=True)
for name, extra in (("current model", None), ("+ free-throw drawing", ft)):
    ns, l2 = build(extra)
    a1, l1 = weekly(ns, l2, "20261019", max(dates)); a2, l2b = weekly(ns, l2, "20260101", "20260412")
    print(f"{name:24} {l2:>6} {a1:9.1%} {l1:10.4f}   {a2:9.1%} {l2b:10.4f}", flush=True)
# Result 2026-11-19: current 20.9% / 2.2377 and 15.1% / 2.2725; + FTA/36 20.5% / 2.2381 and 15.1% / 2.2739 — worse in both, not adopted.
