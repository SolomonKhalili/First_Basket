"""Experiment (read-only): tip-off record AGAINST A CERTAIN TEAM as an extra tip-off input.
Every game from Dec 2025 on is predicted from EARLIER games only (same as tip_model.evaluate).
Versions (each blended into the current chance: P = (wins + K2 * current P) / (tips + K2)):
  A. tonight's home jumper vs tonight's opponent team, whoever jumped for them  (all earlier games)
  B. same, this season only (trade-aware)
  C. team vs team: how the home team did on the tip against this opponent, this season (any jumpers)
Run:  python3 experiments/tip_vs_team_test.py"""
import math, os, sqlite3, sys
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); sys.path.insert(0, ".")
import tip_model as tm
import starters as st

con = sqlite3.connect("nba_first_basket.db")
games = con.execute("""SELECT game_id, game_date, home_team, away_team, tip_winner_team,
                              tip_winner_player_id, tip_loser_player_id FROM games
                       WHERE tip_winner_team IS NOT NULL ORDER BY game_date""").fetchall()
season_of = lambda d: "2026-27" if d >= "20260801" else "2025-26"
K2S = [5, 10, 20]


def jumper_vs_team(pid, opp, before, same_season):
    """(wins, tips) for player `pid` on opening tips against team `opp` before `before`."""
    w = n = 0
    for _, d, h, a, wt, wp, lp in games:
        if d >= before or opp not in (h, a) or pid not in (wp, lp):
            continue
        if same_season and season_of(d) != season_of(before):
            continue
        if (wp == pid and wt != opp) or (lp == pid and wt == opp):     # he jumped AGAINST opp (not for them)
            n += 1; w += wp == pid
    return w, n


def team_vs_team(team, opp, before):
    w = n = 0
    for _, d, h, a, wt, _, _ in games:
        if d < before and {h, a} == {team, opp} and season_of(d) == season_of(before):
            n += 1; w += wt == team
    return w, n


res, have = {}, {}
test = [g for g in games if g[1] >= "20251201"]
print(f"Checking {len(test)} games, one at a time (only earlier games used)...", flush=True)
for i, (gid, d, h, a, winner, _, _) in enumerate(test, 1):
    hs, as_ = tm.starters_for_game(con, gid, h), tm.starters_for_game(con, gid, a)
    hc, ac = st.listed_center(con, d, h), st.listed_center(con, d, a)
    p = tm.home_win_prob(con, h, a, d, hs, as_, hc, ac)
    jh, ja = tm.team_jumper(con, h, d, hs, hc), tm.team_jumper(con, a, d, as_, ac)
    s = season_of(d)
    rec = {}
    for name, same in (("A. jumper vs that team (all)", False), ("B. jumper vs that team (season)", True)):
        hw, hn = jumper_vs_team(jh, a, d, same) if jh else (0, 0)
        aw, an = jumper_vs_team(ja, h, d, same) if ja else (0, 0)
        rec[name] = (hw + (an - aw), hn + an)          # home side's wins out of all those tips
    rec["C. team vs team (season)"] = team_vs_team(h, a, d)
    home_won = winner == h
    for name, (w, n) in [("current tip model", (0, 0))] + list(rec.items()):
        hv = have.setdefault((s, name), [0, 0]); hv[0] += n > 0; hv[1] += 1
        for k2 in ([None] if name == "current tip model" else K2S):
            q = p if k2 is None else (w + k2 * p) / (n + k2)
            q = min(max(q, 0.01), 0.99)
            r = res.setdefault((s, name, k2), [0, 0, 0.0])
            r[0] += (q > 0.5) == home_won; r[1] += 1; r[2] += -math.log(q if home_won else 1 - q)
    if i % 200 == 0:
        print(f"  {i}/{len(test)} games done", flush=True)

for s in ("2025-26", "2026-27"):
    print(f"\n{s}:")
    for (ss, name, k2), (right, n, ll) in res.items():
        if ss != s:
            continue
        hv = have[(s, name)]
        label = name if k2 is None else f"{name}, K2={k2}"
        print(f"  {label:44} {right / n:6.1%} right   log-loss {ll / n:.4f}   (history in {hv[0] / hv[1]:.0%} of games)")
print("\nDONE")
