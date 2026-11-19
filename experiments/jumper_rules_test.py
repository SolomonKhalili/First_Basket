"""Experiment (read-only, 2026-11-18): how often each jumper rule names the player who ACTUALLY took the opening tip.
Point-in-time (only tips before each game). Starters = the box-score starters of that game; listed C = NBA.com's
lineup that night. Periods: 2026-27 and Jan–Apr 2026."""
import os, sys, sqlite3
from collections import Counter
os.chdir("/Users/solomonkhalili/Desktop/NBA"); sys.path.insert(0, ".")
import tip_model as tm
import starters as st
con = sqlite3.connect("nba_first_basket.db")
games = con.execute("""SELECT game_id, game_date, home_team, away_team, tip_winner_team, tip_winner_player_id,
                       tip_loser_player_id FROM games WHERE tip_winner_team IS NOT NULL ORDER BY game_date""").fetchall()
starters = {}
for gid, team, pid in con.execute("SELECT game_id, team, player_id FROM lineups WHERE is_starter = 1"):
    starters.setdefault((gid, team), set()).add(str(pid))
names = {str(p): n for p, n in con.execute("SELECT player_id, player_name FROM lineups")}
centers = {}
for d, t, n, pos in con.execute("SELECT game_date, team, player_name, position FROM official_lineups"):
    if st.position_group(pos) == "C":
        centers[(d, t)] = st.norm_name(n)
by_norm = {st.norm_name(n): p for p, n in names.items()}

# every team's tip history, in order: (date, game_id, jumper id)
hist = {}
for gid, d, h, a, wt, wp, lp in games:
    for team in (h, a):
        j = wp if wt == team else lp
        if j:
            hist.setdefault(team, []).append((d, gid, str(j)))
ever = {}  # player -> list of dates he jumped (any team)
for gid, d, h, a, wt, wp, lp in games:
    for p in (wp, lp):
        if p:
            ever.setdefault(str(p), []).append(d)

def recent(team, d, n=10):
    return [j for (dd, g, j) in reversed(hist.get(team, [])) if dd < d][:n]

def rule_current(team, d, gid, s, lc):
    return tm.team_jumper(con, team, d, s, lc)

def rule_decay(team, d, gid, s, lc, bonus=0.0, half=0.8):
    jumps = recent(team, d, 15)
    score = Counter()
    for i, j in enumerate(jumps):
        if j in s:
            score[j] += half ** i
    if lc and lc in s:
        score[lc] += bonus
    if score:
        return max(score, key=score.get)
    if lc and lc in s:
        return lc
    return max(s, key=lambda p: sum(1 for x in ever.get(p, []) if x < d), default=None)

def rule_last_game(team, d, gid, s, lc):
    r = recent(team, d, 1)
    if r and r[0] in s:
        return r[0]
    return rule_current(team, d, gid, s, lc)

def rule_any_team_recent(team, d, gid, s, lc):
    """most tips taken for ANY team in the last 45 days among tonight's starters (traded / returning jumpers)"""
    from datetime import date, timedelta
    lo = (date(int(d[:4]), int(d[4:6]), int(d[6:])) - timedelta(days=45)).strftime("%Y%m%d")
    c = {p: sum(1 for x in ever.get(p, []) if lo <= x < d) for p in s}
    best = max(c, key=c.get, default=None)
    if best and c[best] >= 2:
        return best
    return rule_current(team, d, gid, s, lc)

RULES = {"current rule (88.1%)": rule_current, "recency-weighted": lambda *a: rule_decay(*a),
         "recency-weighted + listed C bonus 0.6": lambda *a: rule_decay(*a, bonus=0.6),
         "recency-weighted + listed C bonus 1.2": lambda *a: rule_decay(*a, bonus=1.2),
         "last game's jumper if starting": rule_last_game,
         "most tips any team, last 45 days": rule_any_team_recent}
for lo, hi, label in (("20261019", "20991231", "2026-27"), ("20260101", "20260412", "Jan–Apr 2026")):
    right, n = Counter(), 0
    for gid, d, h, a, wt, wp, lp in games:
        if not (lo <= d <= hi):
            continue
        for team in (h, a):
            actual = str(wp if wt == team else lp)
            s = starters.get((gid, team))
            if not s or not actual or actual == "None":
                continue
            lc = by_norm.get(centers.get((d, team), ""))
            n += 1
            for name, f in RULES.items():
                right[name] += f(team, d, gid, s, lc) == actual
    print(f"\n{label}: {n} team-games")
    for name in RULES:
        print(f"  {name:42} {right[name] / n:6.1%}")
print("\nDONE")
