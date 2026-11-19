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


from scipy.optimize import minimize
tipw = dict(con.execute("SELECT game_id, tip_winner_team FROM games"))
ns, _ = build(None)
home = ns["home"]; PLx = ns["PL"]; TIPc = pm.FEATURES.index("tip_win_prob")
# +1 if the team that scored first had won the tip, -1 if not (0 if unknown)
won = np.zeros(len(gids))
for k, gid in enumerate(gids):
    d_, h_, a_ = info[gid]; tw = tipw.get(gid)
    if tw and Y[k].sum() == 1:
        scorer_home = bool(Y[k][home[k]].sum())
        won[k] = 1 if (tw == h_) == scorer_home else -1
print("scoring team had won the tip:", round(float((won == 1).mean() / max((won != 0).mean(), 1e-9) * 100), 1), "%", flush=True)

def make(LAM):
    def fit_player_split(idx, l2, x0=None):
        hf = ns["home_first"]
        mask = np.where(hf[idx][:, None], home[idx], ~home[idx])
        flat = PLx[idx][mask]; mu, sd = flat.mean(0), flat.std(0) + 1e-9
        Z = ((PLx[idx][mask] - mu) / sd).reshape(len(idx), 5, -1); Ys = Y[idx][mask].reshape(len(idx), 5)
        sgn = won[idx]; nf = Z.shape[2]
        def nll(v):
            w, dlt = v[:nf], v[nf:]
            s_ = Z @ w + sgn[:, None] * (Z @ dlt); s_ -= s_.max(1, keepdims=True); e = np.exp(s_); p = e / e.sum(1, keepdims=True)
            g = (Ys - p)[:, :, None] * Z
            return (-np.log((p * Ys).sum(1)).mean() + l2 * (w @ w) + LAM * (dlt @ dlt),
                    np.concatenate([-g.sum(1).mean(0) + 2 * l2 * w, -(sgn[:, None] * g.sum(1)).mean(0) + 2 * LAM * dlt]))
        v = minimize(nll, np.zeros(2 * nf), jac=True, method="L-BFGS-B").x
        return (v[:nf], v[nf:]), mu, sd
    return fit_player_split

def predict_split(model, ix):
    team, (w, dlt), mu, sd = model; out = []
    for k in ix:
        hm = home[k]; t_h = float(ns["M"][k][hm][0, TIPc]); ph = float(pm.home_scores_first(team, t_h))
        s_w = ((PLx[k] - mu) / sd) @ (w + dlt); s_l = ((PLx[k] - mu) / sd) @ (w - dlt); o = np.zeros(10)
        for side, pt, tt in ((hm, ph, t_h), (~hm, 1 - ph, 1 - t_h)):
            q = tt * .66 / (tt * .66 + (1 - tt) * .34)          # P(this team won the tip | it scored first)
            ew = np.exp(s_w[side] - s_w[side].max()); el = np.exp(s_l[side] - s_l[side].max())
            o[side] = pt * (q * ew / ew.sum() + (1 - q) * el / el.sum())
        out.append(o)
    return np.vstack(out)

M_ = ns["M"]; FB_ = pm.FEATURES.index("fb_rate_shrunk")
def post_(P, T):
    out = []
    for p, k in zip(P, T):
        fb = M_[k][:, FB_]; q = 0.75 * p + 0.25 * fb / fb.sum(); out.append(pm.stretch_teams(q, home[k], 1.2))
    return np.array(out)
PA, TA = weekly_P(ns, 0.001, "20261019", max(dates)); PB, TB = weekly_P(ns, 0.001, "20260101", "20260412")
print(f"{'':34} {'2026-27':>16} {'Jan-Apr 2026':>16}")
print(f"{'current (live settings)':34} {sc(post_(PA, TA), TA):>16} {sc(post_(PB, TB), TB):>16}", flush=True)
orig_fp, orig_pred = ns["fit_player"], ns["predict2"]
for LAM in (0.003, 0.01, 0.03, 0.1, 1.0):
    ns["fit_player"] = make(LAM); ns["predict2"] = predict_split
    PA, TA = weekly_P(ns, 0.001, "20261019", max(dates)); PB, TB = weekly_P(ns, 0.001, "20260101", "20260412")
    print(f"{'split by tip, hold ' + str(LAM):34} {sc(post_(PA, TA), TA):>16} {sc(post_(PB, TB), TB):>16}", flush=True)
# Result 2026-11-19 (on the history model, live settings): current 2.22734 / 2.25270; split by tip (hold 0.003-1.0)
# 2.22767-2.22915 / 2.25228-2.25265 — a little better in Jan-Apr, worse this season. Not adopted.
