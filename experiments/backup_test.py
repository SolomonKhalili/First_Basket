"""Experiment (2026-11-19): can we guess the backup starter better? (who starts when a regular starter sits)
Events: a team-game where exactly one player who started the team's previous game didn't play at all (0 minutes) and
exactly one new player started. Truth = that new starter. Compared:
  current rule  = starters.pick_replacement (coach's history -> same-position for centres -> next in line)
  learned rule  = a conditional logit over the team's rotation players (point-in-time clues, below), trained on earlier
                  games only: before 2026-01-01 for the Jan–Apr 2026 test, before 2026-10-01 for the 2026-27 test.
Players who didn't play that night (0 minutes) are excluded for every rule (the live code has the injury report)."""
import os, sys, sqlite3
from collections import defaultdict
import numpy as np
from scipy.optimize import minimize
os.chdir("/Users/solomonkhalili/Desktop/NBA"); sys.path.insert(0, ".")
import starters as st

con = sqlite3.connect("nba_first_basket.db")
date_of = dict(con.execute("SELECT game_id, game_date FROM games"))
rows = defaultdict(list)                          # (team) -> [(date, gid, {pid: (name, starter, minutes)})]
tmp = defaultdict(dict)
for gid, team, pid, name, s, m in con.execute("SELECT game_id, team, player_id, player_name, is_starter, minutes FROM lineups"):
    if gid in date_of:
        tmp[(team, gid)][str(pid)] = (name, int(s or 0), float(m or 0))
for (team, gid), d in tmp.items():
    rows[team].append((date_of[gid], gid, d))
for t in rows:
    rows[t].sort()
season_of = lambda d: d[:4] if d[4:6] >= "08" else str(int(d[:4]) - 1)

events = []                                       # (team, k index, date, absent pid, truth pid, candidates)
for team, R in rows.items():
    for k in range(1, len(R)):
        d, gid, cur = R[k]; _, _, prev = R[k - 1]
        if d[4:6] in ("05", "06") or season_of(d) != season_of(R[k - 1][0]):
            continue
        prev_st = {p for p, v in prev.items() if v[1]}
        cur_st = {p for p, v in cur.items() if v[1]}
        absent = [p for p in prev_st if cur.get(p, ("", 0, 0))[2] == 0]
        new = list(cur_st - prev_st)
        if len(prev_st) != 5 or len(cur_st) != 5 or len(absent) != 1 or len(new) != 1:
            continue
        events.append((team, k, d, absent[0], new[0]))
print(len(events), "events (one regular starter sat, one new starter)", flush=True)

def clues(team, k, absent, exclude):
    """Point-in-time clue rows for every rotation player (played in the team's last 10 games this season)."""
    R = rows[team]; d = R[k][0]; s0 = season_of(d)
    past = [R[i] for i in range(k - 1, -1, -1) if season_of(R[i][0]) == s0]          # newest first, this season
    last10, last5 = past[:10], past[:5]
    absent_missed = [g for g in past if g[2].get(absent, ("", 0, 0))[2] == 0]
    cur_run = []
    for g in past:
        if g[2].get(absent, ("", 0, 0))[2] == 0: cur_run.append(g)
        else: break
    cand = {}
    for g in last10:
        for p, (n, s, m) in g[2].items():
            if m > 0 and p != absent and p not in exclude:
                cand.setdefault(p, n)
    out = []
    for p, n in cand.items():
        mins10 = sum(g[2].get(p, ("", 0, 0))[2] for g in last10) / max(len(last10), 1)
        st5 = sum(g[2].get(p, ("", 0, 0))[1] for g in last5)
        st_season = sum(g[2].get(p, ("", 0, 0))[1] for g in past) / max(len(past), 1)
        h_cur = sum(g[2].get(p, ("", 0, 0))[1] for g in cur_run)
        h_all = sum(g[2].get(p, ("", 0, 0))[1] for g in absent_missed)
        last_game_start = past[0][2].get(p, ("", 0, 0))[1] if past else 0
        out.append((p, n, [mins10 / 36, st5 / 5, st_season, min(h_cur, 5) / 5, min(h_all, 10) / 10, last_game_start]))
    return out

_gc = {}
def GROUPS(d):
    if d not in _gc: _gc[d] = st.player_groups(con, d)
    return _gc[d]
data, rule_right = [], []
for team, k, d, absent, truth in events:
    R = rows[team]; cur = R[k][2]
    dnp = {p for p, v in cur.items() if v[2] == 0}
    others = [p for p, v in cur.items() if v[1] and p != truth]
    exclude = set(others) | dnp
    C = clues(team, k, absent, exclude)
    if not any(p == truth for p, _, _ in C):
        continue
    absent_name = R[k - 1][2][absent][0]
    ex_names = {st.norm_name(R[k][2][p][0]) for p in exclude if p in R[k][2]}
    try:
        rep = st.pick_replacement(con, team, absent_name, ex_names, d, groups=GROUPS(d))
    except Exception:
        rep = None
    rule_right.append(bool(rep) and st.norm_name(rep["name"]) == st.norm_name(R[k][2][truth][0]))
    data.append((d, np.array([c[2] for c in C]), np.array([p == truth for p, _, _ in C], float), [c[1] for c in C]))
rule_right = np.array(rule_right)
D = np.array([x[0] for x in data])
print(len(data), "usable events", flush=True)

def fit(ix, l2=0.01):
    def nll(w):
        tot, g = 0.0, np.zeros_like(w)
        for i in ix:
            _, X, y, _ = data[i]; s = X @ w; s -= s.max(); e = np.exp(s); p = e / e.sum()
            tot -= np.log((p * y).sum()); g -= X.T @ (y - p)
        n = len(ix); return tot / n + l2 * w @ w, g / n + 2 * l2 * w
    return minimize(nll, np.zeros(data[0][1].shape[1]), jac=True, method="L-BFGS-B").x

for label, train_before, lo, hi in (("Jan–Apr 2026", "20260101", "20260101", "20260501"),
                                    ("2026-27", "20261001", "20261001", "20991231")):
    tr = np.where(D < train_before)[0]; te = np.where((D >= lo) & (D < hi))[0]
    w = fit(tr)
    learned = np.array([data[i][2][np.argmax(data[i][1] @ w)] == 1 for i in te])
    minutes_only = np.array([data[i][2][np.argmax(data[i][1][:, 0])] == 1 for i in te])
    print(f"{label:14} events {len(te):4}  current rule {rule_right[te].mean():6.1%}   learned {learned.mean():6.1%}"
          f"   most minutes only {minutes_only.mean():6.1%}   weights {np.round(w, 2)}", flush=True)
# RESULT (2026-11-19): 1,019 usable events. Right guess: Jan–Apr 2026 current rule 33.1% vs learned 32.4% (most
# minutes only 27.6%); 2026-27 27.1% vs 28.6% (only 70 events). Not better in both -> current rule kept. The learned
# weights lean on minutes (1.9-2.0), who filled in during his earlier absences (0.9-1.2) and recent starts (0.9).
