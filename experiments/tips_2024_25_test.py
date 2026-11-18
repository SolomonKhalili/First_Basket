"""
Experiment, step 2: do 2024-25 opening tips make our tip-off calls better?

Honest comparison, nothing kept: makes a TEMPORARY copy of the database, adds the 2024-25 tips (from `old_tips`)
to the copy's games table, then runs the same point-in-time check as tip_model.evaluate() — every game from
Dec 2025 on is predicted from earlier tips only — on the real database and on the copy. Same games, same rules;
the only difference is the extra season of tip history.
Keep it only if it is better in BOTH seasons.

Run:  python3 experiments/tips_2024_25_test.py
"""
import contextlib
import io
import math
import os
import shutil
import sqlite3
import sys
import tempfile

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ".")
import starters as st  # noqa: E402
import tip_model as tm  # noqa: E402


def score(con):
    """Per season: (games, tips called right, log-loss) — point-in-time, from Dec 2025 on."""
    games = con.execute("""SELECT game_id, game_date, home_team, away_team, tip_winner_team FROM games
                           WHERE tip_winner_team IS NOT NULL AND game_date >= '20251201' ORDER BY game_date""").fetchall()
    out = {}
    for gid, d, h, a, winner in games:
        p = tm.home_win_prob(con, h, a, d, tm.starters_for_game(con, gid, h), tm.starters_for_game(con, gid, a),
                             st.listed_center(con, d, h), st.listed_center(con, d, a))
        s = out.setdefault("2026-27" if d >= "20260801" else "2025-26", [0, 0, 0.0])
        s[0] += 1
        s[1] += (p > 0.5) == (winner == h)
        s[2] += -math.log(p if winner == h else 1 - p)
    return out


def main() -> int:
    real = sqlite3.connect("nba_first_basket.db")
    n_old = real.execute("SELECT COUNT(*) FROM old_tips WHERE tip_winner_player_id != '' AND tip_winner_team IS NOT NULL").fetchone()[0]
    print(f"2024-25 tips available: {n_old}", flush=True)
    tmp = os.path.join(tempfile.mkdtemp(), "copy.db")
    shutil.copy("nba_first_basket.db", tmp)
    copy = sqlite3.connect(tmp)
    copy.execute("""INSERT OR IGNORE INTO games (game_id, game_date, home_team, away_team, status, tip_winner_team,
                        tip_winner_player_id, tip_winner_player_name, tip_loser_player_id, tip_loser_player_name)
                    SELECT game_id, game_date, home_team, away_team, 3, tip_winner_team, tip_winner_player_id,
                           tip_winner_player_name, tip_loser_player_id, tip_loser_player_name
                    FROM old_tips WHERE tip_winner_player_id != '' AND tip_winner_team IS NOT NULL""")
    copy.commit()
    print("Scoring the current tip model (this season's + last season's tips)...", flush=True)
    with contextlib.redirect_stdout(io.StringIO()):
        a = score(real)
    print("Scoring with 2024-25 tips added...", flush=True)
    with contextlib.redirect_stdout(io.StringIO()):
        b = score(copy)
    print(f"\n{'':28} {'tips called right':>20} {'log-loss (lower = better)':>28}")
    better = []
    for season in sorted(a):
        for name, r in (("current", a[season]), ("+ 2024-25 tips", b[season])):
            n, right, ll = r
            print(f"  {season} {name:18} {right:5}/{n:<5} = {right / n:5.1%}        {ll / n:.4f}")
        better.append(b[season][2] < a[season][2] and b[season][1] >= a[season][1])
    print("\nVERDICT:", "BETTER in both seasons — worth keeping" if all(better) else "not better in both seasons — don't keep")
    os.remove(tmp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
