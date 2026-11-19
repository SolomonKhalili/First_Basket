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



print("Building both-teams labels...", flush=True)
first_made = {}
for gid, team, seq, pid in con.execute("SELECT game_id, team, seq, player_id FROM early_shots ORDER BY game_id, team, seq"):
    first_made.setdefault((gid, team), str(pid))
OTHER = np.zeros((len(gids), 10)); has_other = np.zeros(len(gids), bool)
OWN = np.zeros((len(gids), 10)); has_own = np.zeros(len(gids), bool)
for k, gid in enumerate(gids):
    d, h, a = info[gid]
    s = bf.stored_starters(gid, h, a)
    if not s: continue
    order = (s[h] + s[a])[:10]
    for team in (h, a):
        pid = first_made.get((gid, team))
        if not pid: continue
        for j, p in enumerate(order):
            if str(p["player_id"]) == pid and (j < 5) == (team == h):
                (OTHER if True else OWN)[k, j] = 1
    # keep only the team that did NOT score first in OTHER (the scoring team is already the normal label)
print("extra examples available:", int(OTHER.sum()), flush=True)

def make_fit(W):
    def fit_player_aug(idx, l2, x0=None, ns=None):
        home, PL, Yl = NS["home"], NS["PL"], Y
        hf = NS["home_first"]
        mask = np.where(hf[idx][:, None], home[idx], ~home[idx])
        omask = mask                                            # the scoring team's five: its first shot
        lab_other = OTHER[idx] * omask
        ok = lab_other.sum(1) == 1
        Zs = PL[idx][mask].reshape(len(idx), 5, -1); Ys = Yl[idx][mask].reshape(len(idx), 5)
        Zo = PL[idx][omask].reshape(len(idx), 5, -1)[ok]; Yo = lab_other[omask].reshape(len(idx), 5)[ok]
        flat = PL[idx][mask]; mu, sd = flat.mean(0), flat.std(0) + 1e-9
        Z = np.concatenate([(Zs - mu) / sd, (Zo - mu) / sd]); YY = np.concatenate([Ys, Yo])
        wt = np.concatenate([np.ones(len(Zs)), np.full(len(Zo), W)]); wt /= wt.mean()
        def nll(w):
            s = Z @ w; s -= s.max(1, keepdims=True); e = np.exp(s); p = e / e.sum(1, keepdims=True)
            return (-(wt * np.log((p * YY).sum(1) + 1e-12)).mean() + l2 * (w @ w),
                    -(wt[:, None, None] * (YY - p)[:, :, None] * Z).sum(1).mean(0) + 2 * l2 * w)
        from scipy.optimize import minimize
        w = minimize(nll, np.zeros(Z.shape[2]) if x0 is None else x0, jac=True, method="L-BFGS-B").x
        return w, mu, sd
    return fit_player_aug

print(f"\n{'':28} {'L2':>6} {'2026-27':>22} {'Jan–Apr 2026':>22}", flush=True)
for W in (0.0, 0.05, 0.1):
    ns, l2 = build(None)
    NS = ns
    if W > 0:
        ns["fit_player"] = make_fit(W)
    a1, l1 = weekly(ns, l2, "20261019", max(dates)); a2, l2b = weekly(ns, l2, "20260101", "20260412")
    print(f"{'other team weight ' + str(W):28} {l2:>6} {a1:9.1%} {l1:10.6f}   {a2:9.1%} {l2b:10.6f}", flush=True)
