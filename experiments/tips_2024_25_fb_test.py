"""
Experiment, step 3: do 2024-25 opening tips make the FIRST-BASKET predictions better?
(The tip chance feeds step 1 of the model — which team scores first — so this is the test that matters.)

Runs train_pregame.py's own data loading twice — on the real database and on a TEMPORARY copy whose games table
also holds the 2024-25 tips — then the same honest test for both: L2 chosen on 2025-26 only, each week of 2026-27
and of Jan–Apr 2026 predicted by a model trained only on earlier games. Nothing is saved.

Run:  python3 experiments/tips_2024_25_fb_test.py
"""
import contextlib
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import warnings
from datetime import date, timedelta

import numpy as np

warnings.filterwarnings("ignore")
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ".")

SRC = open("train_pregame.py").read().replace("args = parser.parse_args()", "args = parser.parse_args([])")
HEAD = SRC[:SRC.index("# ── Two-stage model")]
TWO = SRC[SRC.index("# ── Two-stage model"):SRC.index("# ── L2 (regularisation strength)")]


def load(db_path):
    ns = {"__name__": "x"}
    code = (HEAD + TWO).replace('sqlite3.connect("nba_first_basket.db")', f'sqlite3.connect("{db_path}")')
    with contextlib.redirect_stdout(io.StringIO()):
        exec(compile(code, "train_pregame.py", "exec"), ns)
    return ns


def weekly(ns, l2, lo, hi):
    dates, season, Y = ns["dates"], ns["season"], ns["Y"]
    pool = season != "playoffs"
    ws = date(int(lo[:4]), int(lo[4:6]), int(lo[6:]))
    P, T = [], []
    while ws.strftime("%Y%m%d") <= hi:
        a_, b_ = ws.strftime("%Y%m%d"), (ws + timedelta(days=7)).strftime("%Y%m%d")
        te = np.where(pool & (dates >= a_) & (dates < b_) & (dates <= hi))[0]
        if len(te):
            P.append(ns["predict2"](ns["fit2"](np.where(pool & (dates < a_))[0], l2), te))
            T.append(te)
        ws += timedelta(days=7)
    T = np.concatenate(T)
    P = np.vstack(P)
    return (Y[T][np.arange(len(T)), P.argmax(1)] == 1).mean(), -np.log((P * Y[T]).sum(1)).mean()


def run(name, db_path):
    ns = load(db_path)
    Y, season = ns["Y"], ns["season"]
    old = np.where(season == "old")[0]
    cut = int(len(old) * 0.85)
    tr, va = old[:cut], old[cut:]
    ll = lambda P, ix: -np.log((P * Y[ix]).sum(1)).mean()
    l2 = min(ns["L2_GRID"], key=lambda v: ll(ns["predict2"](ns["fit2"](tr, v), va), va))     # the real procedure
    a1, l1 = weekly(ns, l2, "20261019", max(ns["dates"]))
    a2, l2b = weekly(ns, l2, "20260101", "20260412")
    print(f"{name:34} {l2:>6} {a1:6.1%} (ll {l1:.4f})   {a2:6.1%} (ll {l2b:.4f})", flush=True)
    return l1, l2b


def main() -> int:
    tmp = os.path.join(tempfile.mkdtemp(), "copy.db")
    shutil.copy("nba_first_basket.db", tmp)
    copy = sqlite3.connect(tmp)
    copy.execute("""INSERT OR IGNORE INTO games (game_id, game_date, home_team, away_team, status, tip_winner_team,
                        tip_winner_player_id, tip_winner_player_name, tip_loser_player_id, tip_loser_player_name)
                    SELECT game_id, game_date, home_team, away_team, 3, tip_winner_team, tip_winner_player_id,
                           tip_winner_player_name, tip_loser_player_id, tip_loser_player_name
                    FROM old_tips WHERE tip_winner_player_id != '' AND tip_winner_team IS NOT NULL""")
    copy.commit()
    copy.close()
    print("First-basket model, week by week (this takes a few minutes)...\n", flush=True)
    print(f"{'':34} {'L2':>6} {'2026-27':>22} {'Jan–Apr 2026':>22}", flush=True)
    a = run("current tip history", "nba_first_basket.db")
    b = run("+ 2024-25 tips", tmp)
    os.remove(tmp)
    print("\nVERDICT:", "BETTER in both periods — worth keeping" if b[0] < a[0] and b[1] < a[1]
          else "not better in both periods — don't keep")
    return 0


if __name__ == "__main__":
    sys.exit(main())
