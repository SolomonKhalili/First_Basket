"""Experiment (read-only, written 2026-11-18): does blending the model's chances with the SPORTSBOOK's chances help?
For each finished game with saved FanDuel prices (odds_log, last snapshot before tip-off): market chance = FanDuel's
implied chance with the margin removed (scaled to 100% over the listed players); blend = w * model + (1-w) * market,
renormalised. Reports top-pick hit rate and log-loss for w = 1 (model only) ... 0 (market only).
Decide only with 150+ games (re-test around 2026-12-20); log-loss is the deciding number."""
import os, sqlite3, sys
from collections import defaultdict
import numpy as np
os.chdir("/Users/solomonkhalili/Desktop/NBA"); sys.path.insert(0, ".")
con = sqlite3.connect("nba_first_basket.db")
scorer = {g: str(p) for g, p in con.execute("SELECT game_id, scorer_player_id FROM first_baskets")}
last = {}
for gid, at in con.execute("SELECT game_id, MAX(logged_at) FROM odds_log GROUP BY game_id"):
    last[gid] = at
rows = defaultdict(list)
for gid, at, pid, p, a in con.execute("SELECT game_id, logged_at, player_id, prob, american FROM odds_log"):
    if gid in scorer and at == last.get(gid) and a:
        rows[gid].append((str(pid), p, (a / 100 if a > 0 else 100 / -a) + 1))
games = [g for g, r in rows.items() if len(r) >= 8]
print(f"finished games with saved prices: {len(games)}  (decide only with 150+)")
if not games:
    sys.exit(0)
for w in (1.0, 0.8, 0.6, 0.5, 0.4, 0.2, 0.0):
    hits, ll, n = 0, 0.0, 0
    for g in games:
        ids = [r[0] for r in rows[g]]
        model = np.array([r[1] for r in rows[g]]); model = model / model.sum()
        imp = np.array([1 / r[2] for r in rows[g]]); market = imp / imp.sum()
        p = w * model + (1 - w) * market; p = p / p.sum()
        actual = scorer[g]
        hits += ids[int(p.argmax())] == actual
        ll += -np.log(p[ids.index(actual)]) if actual in ids else -np.log(1e-3)
        n += 1
    print(f"  model weight {w:.1f}: top pick {hits / n:6.1%}   log-loss {ll / n:.4f}")
print("DONE")
