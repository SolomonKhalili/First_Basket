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
        import nba                      # same export as `nba.py predict`, so there's one CSV format
        n = nba.export_csv(nba.db())
        print(f"Exported {n} games to {nba.CSV_PATH}")

if __name__ == "__main__":
    main()