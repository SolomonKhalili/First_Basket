"""Experiment (read-only, 2026-11-19): train step 2 on BOTH teams' first scorers (research idea: more examples per game).
The other team's first made field goal (early_shots) is an extra example, weight W. Based on combined_test.py:
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




print("Building player columns...", flush=True)
pids = {}
ROWP = np.full((len(gids), 10), -1)
for k, gid in enumerate(gids):
    d, h, a = info[gid]
    s_ = bf.stored_starters(gid, h, a)
    if not s_: continue
    for j, p in enumerate((s_[h] + s_[a])[:10]):
        ROWP[k, j] = pids.setdefault(str(p["player_id"]), len(pids))
NP = len(pids); print("players:", NP, flush=True)
ONEHOT = np.zeros((len(gids), 10, NP), dtype=np.float32)
for k in range(len(gids)):
    for j in range(10):
        if ROWP[k, j] >= 0: ONEHOT[k, j, ROWP[k, j]] = 1

def make_fit(LAM, nbase):
    def fit_player_re(idx, l2, x0=None):
        home, PL, hf = NS["home"], NS["PL"], NS["home_first"]
        mask = np.where(hf[idx][:, None], home[idx], ~home[idx])
        flat = PL[idx][mask]
        mu = np.concatenate([flat[:, :nbase].mean(0), np.zeros(NP)]); sd = np.concatenate([flat[:, :nbase].std(0) + 1e-9, np.ones(NP)])
        Z = ((PL[idx][mask] - mu) / sd).reshape(len(idx), 5, -1); Ys = Y[idx][mask].reshape(len(idx), 5)
        pen = np.concatenate([np.full(nbase, l2), np.full(NP, LAM)])
        def nll(w):
            s = Z @ w; s -= s.max(1, keepdims=True); e = np.exp(s); p = e / e.sum(1, keepdims=True)
            return (-np.log((p * Ys).sum(1)).mean() + (pen * w) @ w,
                    -((Ys - p)[:, :, None] * Z).sum(1).mean(0) + 2 * pen * w)
        from scipy.optimize import minimize
        w = minimize(nll, np.zeros(Z.shape[2]), jac=True, method="L-BFGS-B").x
        return w, mu, sd
    return fit_player_re

ns0_, L2BASE = build(None); nbase = ns0_["PL"].shape[2]
print(f"\n{'':28} {'2026-27':>22} {'Jan–Apr 2026':>22}", flush=True)
a1, l1 = weekly(ns0_, L2BASE, "20261019", max(dates)); a2, l2b = weekly(ns0_, L2BASE, "20260101", "20260412")
print(f"{'current':28} {a1:9.1%} {l1:10.5f}   {a2:9.1%} {l2b:10.5f}", flush=True)
for LAM in (0.003, 0.01, 0.03, 0.1, 10.0):
    ns, _unused = build(ONEHOT); l2 = L2BASE            # normal stats keep the current setting
    NS = ns
    ns["fit_player"] = make_fit(LAM, nbase)
    a1, l1 = weekly(ns, l2, "20261019", max(dates)); a2, l2b = weekly(ns, l2, "20260101", "20260412")
    print(f"{'personal bonus, hold ' + str(LAM):28} {a1:9.1%} {l1:10.5f}   {a2:9.1%} {l2b:10.5f}", flush=True)
# Result 2026-11-19 (normal stats at the current L2): current 2.23766 / 2.27248; bonus hold 0.003 -> 2.24608 / 2.28704,
# 0.01 -> 2.23918 / 2.27658, 0.03 -> 2.23796 / 2.27371, 0.1 -> 2.23773 / 2.27282, 10 -> same as current. Worse in both — not adopted.
