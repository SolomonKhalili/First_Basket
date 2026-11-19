"""Experiment (read-only, 2026-11-19): blend our FIRST-FIELD-GOAL chances (fg_grades, week-by-week, never saw the game)
with DraftKings' "1st Points Scorer" prices (settles on the first field goal; dk_odds_log, last price logged that day).
Market chance = 1/decimal odds, scaled to 100% over the same starters the model scores. Label = first_fg scorer.
Blend on the log scale: p ∝ model^w * market^(1-w). Pick w on one season, check it on the other. Log-loss decides."""
import math, os, sqlite3, sys
from collections import defaultdict
os.chdir("/Users/solomonkhalili/Desktop/NBA"); sys.path.insert(0, ".")
import starters as st

con = sqlite3.connect("nba_first_basket.db")
games = {gid: (d, f"{a} @ {h}") for gid, d, h, a in con.execute("SELECT game_id, game_date, home_team, away_team FROM games")}
label = {g: str(p) for g, p in con.execute("SELECT game_id, scorer_player_id FROM first_fg")}
model = defaultdict(dict)
for gid, pid, name, p in con.execute("SELECT game_id, player_id, player_name, prob FROM fg_grades"):
    model[gid][str(pid)] = (st.norm_name(name), p)
last = {}
for d, m, at in con.execute("SELECT game_date, matchup, MAX(logged_at) FROM dk_odds_log GROUP BY game_date, matchup"):
    last[(d, m)] = at
price = defaultdict(dict)
for d, m, at, name, a in con.execute("SELECT game_date, matchup, logged_at, player_name, american FROM dk_odds_log"):
    if at == last.get((d, m)) and a:
        price[(d, m)][st.norm_name(name)] = 1 / ((a / 100 if a > 0 else 100 / -a) + 1)

rows = []                       # (season, [(model p, market p, is scorer)])
for gid, players in model.items():
    if gid not in games or gid not in label: continue
    d, m = games[gid]
    mk = price.get((d, m))
    if not mk: continue
    both = [(p, mk[n], pid == label[gid]) for pid, (n, p) in players.items() if n in mk]
    if len(both) < 8 or not any(x[2] for x in both): continue      # need most starters priced and the scorer among them
    ms, ks = sum(x[0] for x in both), sum(x[1] for x in both)
    rows.append(("2026-27" if d >= "20260801" else "2025-26", [(p / ms, k / ks, y) for p, k, y in both]))

def score(w, season):
    hits = ll = n = 0
    for s, g in rows:
        if s != season: continue
        b = [math.exp(w * math.log(p) + (1 - w) * math.log(k)) for p, k, _ in g]; t = sum(b); b = [x / t for x in b]
        ll -= math.log(next(x for x, (_, _, y) in zip(b, g) if y)); n += 1
        hits += g[max(range(len(b)), key=b.__getitem__)][2]
    return n, round(hits / n * 100, 1), round(ll / n, 4)

for s in ("2025-26", "2026-27"):
    print(s, "games:", score(1, s)[0])
print(f"{'model weight':>12} {'2025-26 (right %, log-loss)':>30} {'2026-27':>22}")
for w in (1.0, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.0):
    print(f"{w:12.1f} {str(score(w, '2025-26')[1:]):>30} {str(score(w, '2026-27')[1:]):>22}")
# Result 2026-11-19 (307 games: 92 of 2025-26, 215 of 2026-27): model alone 17.4% / 2.2133 and 18.6% / 2.2422;
# DraftKings alone 21.7% / 2.2275 and 14.9% / 2.2578 (our model is closer to what happened in both); best blend w=0.8
# 2.2130 / 2.2420 — a 0.0003 gain, noise. Not adopted. FanDuel blend: re-test ~2026-12-20 (market_blend_test.py).
