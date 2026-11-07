#!/usr/bin/env python3
"""
View all games with tip winner and first basket scorer.
"""
import sqlite3

DB = "nba_first_basket.db"

def main():
    con = sqlite3.connect(DB)
    cur = con.cursor()

    # Join games + first_baskets
    cur.execute("""
        SELECT
            g.game_date,
            g.away_team || ' @ ' || g.home_team AS matchup,
            g.tip_winner_team AS tip_winner,
            fb.scorer_name AS first_basket_scorer,
            fb.scorer_team AS scorer_team,
            fb.shot_type,
            fb.clock
        FROM games g
        JOIN first_baskets fb ON g.game_id = fb.game_id
        ORDER BY g.game_date, g.game_id
    """)

    rows = cur.fetchall()
    if not rows:
        print("No data yet. Run: python nba.py backfill 2025")
        return

    print(f"{'Date':<8} | {'Matchup':<18} | {'Tip':<4} | {'First Basket Scorer':<20} | {'Team':<4} | {'Shot':<22} | {'Clock':<6}")
    print("-" * 100)
    for r in rows:
        date, matchup, tip, scorer, team, shot, clock = r
        # Convert YYYYMMDD -> MM/DD/YY
        d = str(date)
        display_date = f"{d[4:6]}/{d[6:8]}/{d[2:4]}"
        print(f"{display_date:<8} | {matchup:<18} | {tip:<4} | {scorer:<20} | {team:<4} | {shot:<22} | {clock:<6}")

    print(f"\nTotal: {len(rows)} games")

    # Export option
    export = input("\nExport to CSV? (y/n): ").strip().lower()
    if export == 'y':
        import csv
        cur.execute("""
            SELECT g.game_date, g.game_id, g.home_team, g.away_team, g.tip_winner_team,
                   fb.scorer_name, fb.scorer_team, fb.shot_type, fb.clock, fb.x, fb.y
            FROM games g
            JOIN first_baskets fb ON g.game_id = fb.game_id
            ORDER BY g.game_date
        """)
        rows = cur.fetchall()
        with open('first_basket_data.csv', 'w', newline='') as f:
            w = csv.writer(f)
            # Header with friendly date
            w.writerow(["game_start", "game_id", "home_team", "away_team", "tip_winner",
                       "first_basket_scorer", "scorer_team", "shot_type", "clock", "x", "y"])
            for r in rows:
                d = r[0]  # game_date as YYYYMMDD
                game_start = f"{d[4:6]}/{d[6:8]}/{d[2:4]}"
                w.writerow([game_start] + list(r[1:]))
        print("Exported to first_basket_data.csv")

if __name__ == "__main__":
    main()