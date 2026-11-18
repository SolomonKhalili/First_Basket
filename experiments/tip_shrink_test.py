"""Experiment (read-only): how much to trust a jumper's tip record.
tip_model.K = imaginary 50/50 tips each jumper starts with (bigger = trust his record less).
Every game from Dec 2025 on is predicted from EARLIER games only (tip_model.evaluate).
Run:  python3 experiments/tip_shrink_test.py"""
import os, sqlite3, sys
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); sys.path.insert(0, ".")
import tip_model as tm

con = sqlite3.connect("nba_first_basket.db")
for k in (3, 5, 10, 20, 40):
    tm.K = k
    print(f"\nK = {k}{'  (current)' if k == 10 else ''}", flush=True)
    tm.evaluate(con)
print("\nDONE")
