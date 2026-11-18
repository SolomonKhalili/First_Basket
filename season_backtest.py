#!/usr/bin/env python3
"""
Re-create this season's picks for games played BEFORE we started saving live picks, so the scorecard in
my_predictions.xlsx covers the whole season.

Honest by construction — the same week-by-week test the model is graded on:
  * each week's games are predicted by a model trained only on games before that week;
  * inputs are the point-in-time ones in the training matrices (stats from earlier games only);
  * tip-off chance and jumpers use only earlier tips (tip_model), with the starters who actually started
    (what NBA.com's confirmed lineup shows ~30 minutes before tip-off).
Stored in pick_log as lineup_type BACKTEST: the spreadsheet shows them as Pick made "Re-created", Lineup used
"Confirmed" (they used the real starters), so they are never mixed up with live
picks. Games that already have a live pick are skipped. Safe to re-run: old BACKTEST rows are replaced.

Run:  python3 season_backtest.py
"""
import contextlib
import io
import sqlite3
import sys
import warnings
from datetime import date, timedelta

import numpy as np

import build_features as bf
import picks
import pregame_model as pm
import starters as st
import tip_model as tm

warnings.filterwarnings("ignore")
DB = "nba_first_basket.db"
LABEL = "BACKTEST"
MODEL_ID = "week-by-week-backtest"


def load_training():
    """Run train_pregame.py's data loading and two-stage functions without training or saving anything."""
    src = open("train_pregame.py").read().replace("args = parser.parse_args()", "args = parser.parse_args([])")
    head = src[:src.index("# ── Two-stage model")]
    two = src[src.index("# ── Two-stage model"):src.index("# ── L2 (regularisation strength)")]
    ns = {"__name__": "season_backtest"}
    with contextlib.redirect_stdout(io.StringIO()):
        exec(compile(head, "train_pregame.py", "exec"), ns)
        exec(compile(two, "train_pregame.py", "exec"), ns)
    return ns


def main() -> int:
    con = sqlite3.connect(DB)
    picks.ensure_table(con)
    ns = load_training()
    gids, dates, season, home = ns["gids"], ns["dates"], ns["season"], ns["home"]
    l2 = pm.load().get("l2", 1e-3)
    info = {g: (h, a) for g, h, a in con.execute("SELECT game_id, home_team, away_team FROM games")}
    names = dict(con.execute("SELECT player_id, player_name FROM lineups"))
    live = {g for (g,) in con.execute("SELECT DISTINCT game_id FROM pick_log WHERE lineup_type != ?", (LABEL,))}
    con.execute("DELETE FROM pick_log WHERE lineup_type = ?", (LABEL,))

    pool = season != "playoffs"
    this_season = np.where(season == "new")[0]
    first, last = min(dates[this_season]), max(dates[this_season])
    week = date(int(first[:4]), int(first[4:6]), int(first[6:]))
    made = hits = 0
    while week.strftime("%Y%m%d") <= last:
        lo, hi = week.strftime("%Y%m%d"), (week + timedelta(days=7)).strftime("%Y%m%d")
        test = [k for k in this_season if lo <= dates[k] < hi and gids[k] not in live]
        if test:
            model = ns["fit2"](np.where(pool & (dates < lo))[0], l2)      # trained on earlier games only
            P = ns["predict2"](model, test)
            for k, probs in zip(test, P):
                gid, d = gids[k], dates[k]
                h, a = info[gid]
                s = bf.stored_starters(gid, h, a)
                if not s or not home[k][:5].all() or home[k][5:].any():
                    continue                                              # matrix order must be home 5, away 5
                players = [{"id": p["player_id"], "name": p["name"], "team": team, "prob": float(probs[j])}
                           for j, (p, team) in enumerate([(p, h) for p in s[h]] + [(p, a) for p in s[a]])]
                hs, as_ = [p["player_id"] for p in s[h]], [p["player_id"] for p in s[a]]
                hc, ac = st.listed_center(con, d, h), st.listed_center(con, d, a)
                jh, ja = tm.team_jumper(con, h, d, hs, hc, extended=True), tm.team_jumper(con, a, d, as_, ac, extended=True)
                picks.log_pick(con, {
                    "game_id": gid, "game_date": d, "matchup": f"{a} @ {h}", "lineup_type": LABEL,
                    "lineup_source": "week_by_week_backtest", "lineup_sig": ",".join(sorted(hs + as_)),
                    "model_id": MODEL_ID, "players": players,
                    # the tip-off call shown in the spreadsheet uses the extended tip history (as nba.py does);
                    # the first-basket chances above use the model's own tip chance, as in training
                    "tip_home": tm.home_win_prob(con, h, a, d, hs, as_, hc, ac, extended=True),
                    "home_jumper": names.get(jh), "away_jumper": names.get(ja),
                    "logged_at": f"{d[:4]}-{d[4:6]}-{d[6:]}T00:00:00"})
                made += 1
                hits += bool(ns["Y"][k][int(np.argmax(probs))])
        week += timedelta(days=7)
    con.commit()
    print(f"Re-created {made} picks for this season's earlier games (week by week, earlier games only); "
          f"#1 pick right in {hits} ({hits / max(made, 1):.1%}).")
    print(f"Skipped {len(live)} game(s) that already have a live pick.")
    print(f"my_predictions.xlsx: {picks.export_predictions(con)} games written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
