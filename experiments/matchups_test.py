"""Experiment (read-only): matchups. For each starter, the share of his OPPONENT's conceded first baskets (before
this game) that went to players of his position group (G/F/C, from NBA.com's lineup slot that night), shrunk toward
the league share. Added to step 2 of the two-stage model; tested week by week on 2026-27 and Jan–Apr 2026."""
import io, contextlib, os, sys, sqlite3, warnings
import numpy as np
from datetime import date, timedelta
warnings.filterwarnings("ignore")
os.chdir("/Users/solomonkhalili/Desktop/NBA"); sys.path.insert(0, ".")
src = open("train_pregame.py").read().replace("args = parser.parse_args()", "args = parser.parse_args([])")
head = src.split("# ── L2 (regularisation strength)")[0]
ns = {"__name__": "x"}
with contextlib.redirect_stdout(io.StringIO()):
    exec(compile(head, "head", "exec"), ns)
import starters as st
gids, dates, season, Y, M = ns["gids"], ns["dates"], ns["season"], ns["Y"], ns["M"]
con = sqlite3.connect("nba_first_basket.db")
info = {g: (d, h, a) for g, d, h, a in con.execute("SELECT game_id, game_date, home_team, away_team FROM games")}
slot = {}                                       # (date, team, norm name) -> group from NBA.com's lineup that night
for d, t, n, p in con.execute("SELECT game_date, team, player_name, position FROM official_lineups"):
    slot[(d, t, st.norm_name(n))] = st.position_group(p)
# matrix row order: the 10 starters per game in build order; recover names via the saved lineups (same source)
print("Matching each training row to its player and position...", flush=True)
import build_features as bf
row_group = np.full((len(gids), 10), None, dtype=object)
for k, gid in enumerate(gids):
    d, h, a = info[gid]
    s = bf.stored_starters(gid, h, a)
    if not s: continue
    order = s[h] + s[a]                         # build_features: home starters then away starters
    for j, p in enumerate(order[:10]):
        team = h if j < 5 else a
        row_group[k, j] = slot.get((d, team, st.norm_name(p["name"])))
# opponent concessions by group, point-in-time
scorer = {g: (sp, stm) for g, sp, stm in con.execute("SELECT game_id, scorer_player_id, scorer_team FROM first_baskets")}
names = {pid: n for pid, n in con.execute("SELECT player_id, player_name FROM lineups")}
conceded = {}                                   # team -> {group: count}
league = {"G": 0, "F": 0, "C": 0}
feat = np.zeros((len(gids), 10))
order_idx = np.argsort(dates, kind="stable")
by_date = {}
for k in order_idx: by_date.setdefault(dates[k], []).append(k)
K = 10
for d in sorted(by_date):
    tot_l = sum(league.values()) or 1
    for k in by_date[d]:
        _, h, a = info[gids[k]]
        for j in range(10):
            grp = row_group[k, j]
            if grp is None: feat[k, j] = 0.4; continue
            opp = a if j < 5 else h
            c = conceded.get(opp, {}); n = sum(c.values())
            prior = league[grp] / tot_l if tot_l > 1 else 0.33
            feat[k, j] = (c.get(grp, 0) + K * prior) / (n + K)
    for k in by_date[d]:
        _, h, a = info[gids[k]]
        sp, stm = scorer.get(gids[k], (None, None))
        if not sp: continue
        grp = slot.get((d, stm, st.norm_name(names.get(sp, ""))))
        if not grp: continue
        opp = a if stm == h else h
        conceded.setdefault(opp, {}).setdefault(grp, 0); conceded[opp][grp] += 1; league[grp] += 1
print(f"rows with a known position: {np.mean([g is not None for g in row_group.ravel()]):.1%}", flush=True)
# rerun the two-stage walk-forward with/without the matchup input in step 2
body = src.split("# ── L2 (regularisation strength)")[0]
two = open("train_pregame.py").read()
tail = two[two.index("# ── Two-stage model"):two.index("# ── L2 (regularisation strength)")]
def run(extra, lo, hi):
    n2 = dict(ns)
    with contextlib.redirect_stdout(io.StringIO()):
        exec(compile(tail, "tail", "exec"), n2)
    if extra:
        n2["PL"] = np.concatenate([n2["PL"], feat[:, :, None]], axis=2)
        import pregame_model as pm
        def predict2(model, idx, PL=n2["PL"], home=n2["home"]):
            team, w, mu, sd = model; out = []
            for k in idx:
                h = home[k]; ph = float(pm.home_scores_first(team, n2["tip_home"][k]))
                s_ = ((PL[k] - mu) / sd) @ w; o = np.zeros(10)
                for side, pt in ((h, ph), (~h, 1 - ph)):
                    e = np.exp(s_[side] - s_[side].max()); o[side] = pt * e / e.sum()
                out.append(o)
            return np.vstack(out)
        n2["predict2"] = predict2
    pool = season != "playoffs"
    ws = date(int(lo[:4]), int(lo[4:6]), int(lo[6:])); P, T = [], []
    while ws.strftime("%Y%m%d") <= hi:
        a_, b_ = ws.strftime("%Y%m%d"), (ws + timedelta(days=7)).strftime("%Y%m%d")
        te = np.where(pool & (dates >= a_) & (dates < b_) & (dates <= hi))[0]
        if len(te):
            P.append(n2["predict2"](n2["fit2"](np.where(pool & (dates < a_))[0], 1e-3), te)); T.append(te)
        ws += timedelta(days=7)
    T = np.concatenate(T); P = np.vstack(P)
    return (Y[T][np.arange(len(T)), P.argmax(1)] == 1).mean(), -np.log((P * Y[T]).sum(1)).mean(), len(T)
print(f"\n{'':36} {'2026-27':>22} {'Jan–Apr 2026':>22}")
for extra, name in ((False, "two-stage (current)"), (True, "two-stage + matchup input")):
    a1, l1, n1 = run(extra, "20261019", max(dates)); a2, l2_, n2_ = run(extra, "20260101", "20260412")
    print(f"{name:36} {a1:6.1%} (ll {l1:.4f})   {a2:6.1%} (ll {l2_:.4f})", flush=True)
print("\nDONE")
