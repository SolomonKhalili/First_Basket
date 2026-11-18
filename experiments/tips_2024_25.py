"""
Experiment, step 1: download ONLY the opening tip of every 2024-25 regular-season game (who jumped, who won).

Stored in its own table `old_tips` — NOT in `games` — so the first-basket model never sees 2024-25. Reuses the
program's own readers (nba.get_pbp + nba.tip_jumpball), so the tips are read exactly like this season's.
Resumable like a save file: finished days are recorded in `old_tips_days`; re-running continues where it stopped.

Run:  python3 experiments/tips_2024_25.py
Then: python3 experiments/tips_2024_25_test.py      (keeps nothing; just compares tip calls with vs without)
"""
import os
import sqlite3
import sys
from datetime import date, timedelta

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ".")
import nba  # noqa: E402

START, END = date(2024, 10, 22), date(2025, 4, 13)        # 2024-25 regular season
SCHEMA = """CREATE TABLE IF NOT EXISTS old_tips (
    game_id TEXT PRIMARY KEY, game_date TEXT, home_team TEXT, away_team TEXT,
    tip_winner_team TEXT, tip_winner_player_id TEXT, tip_winner_player_name TEXT,
    tip_loser_player_id TEXT, tip_loser_player_name TEXT)"""


def main() -> int:
    con = sqlite3.connect("nba_first_basket.db")
    con.execute(SCHEMA)
    con.execute("CREATE TABLE IF NOT EXISTS old_tips_days (game_date TEXT PRIMARY KEY, games INTEGER)")
    done = {d for (d,) in con.execute("SELECT game_date FROM old_tips_days")}
    days = []
    d = START
    while d <= END:
        if d.strftime("%Y%m%d") not in done:
            days.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    print(f"2024-25 opening tips: {len(done)} days already saved, {len(days)} to go.", flush=True)
    for k, day in enumerate(days, 1):
        try:
            df = nba.espn_nba_scoreboard(dates=[day], return_as_pandas=True)
        except Exception as e:  # noqa: BLE001
            print(f"  {day}: scoreboard failed ({e}) — will retry next run", flush=True)
            continue
        n = 0
        if df is not None and len(df):
            games = df[(df["status_type_state"] == "post") & (df["season_type"] == 2)]
            for _, r in games.iterrows():
                gid, home, away = str(r["game_id"]), str(r["home_abbreviation"]), str(r["away_abbreviation"])
                if home not in nba.NBA_TEAMS or away not in nba.NBA_TEAMS:
                    continue
                if con.execute("SELECT 1 FROM old_tips WHERE game_id = ?", (gid,)).fetchone():
                    n += 1
                    continue
                tip = nba.tip_jumpball(nba.get_pbp(gid)) or {}
                con.execute("INSERT OR REPLACE INTO old_tips VALUES (?,?,?,?,?,?,?,?,?)",
                            (gid, day, home, away, tip.get("tip_winner_team"), tip.get("tip_winner_player_id"),
                             tip.get("tip_winner_player_name"), tip.get("tip_loser_player_id"),
                             tip.get("tip_loser_player_name")))
                n += 1
        con.execute("INSERT OR REPLACE INTO old_tips_days VALUES (?, ?)", (day, n))
        con.commit()
        total = con.execute("SELECT COUNT(*) FROM old_tips").fetchone()[0]
        print(f"  {day[4:6]}/{day[6:]}/{day[2:4]}: {n:2} games   (day {k} of {len(days)}, {total} tips saved)", flush=True)
    found = con.execute("SELECT COUNT(*), SUM(tip_winner_player_id IS NOT NULL AND tip_winner_player_id != '') FROM old_tips").fetchone()
    print(f"\nDONE — {found[0]} games, {found[1]} with both jumpers known.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
