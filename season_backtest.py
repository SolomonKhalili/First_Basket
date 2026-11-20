#!/usr/bin/env python3
"""
Re-create picks for games played BEFORE we started saving live picks: this season (so the scorecard in
my_predictions.xlsx covers the whole season) and last season from January 2026 (LAST_SEASON_FROM — the model needs a
week of games to learn from first) through the Finals, play-in and playoffs included, so the page can show an "overall" record next to this season's.

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
LAST_SEASON_FROM = "20251101"          # last season graded from here: the first ~10 days had no earlier games to learn from
LAST_SEASON_PLAN = f"{LAST_SEASON_FROM}+playoffs+mix+stretch"   # what fill_last_season last built (re-built when this changes)


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


def with_simple_mix(P, M, test, kind: str = "first_points"):
    """The live model's final step (pregame_model.probabilities): SIMPLE_MIX of each starter's shrunk season
    first-basket rate. Without it the grading scored a different model than the one making picks (fixed 2026-11-19:
    last season 14.6% -> 15.1%, playoffs 13.8% -> 18.4%)."""
    col, home = pm.FEATURES.index("fb_rate_shrunk"), pm.FEATURES.index("is_home")
    out = []
    for p, k in zip(P, test):
        fb = M[k][:, col]
        k_, mix = pm.SETTINGS[kind]                                 # each model's own settings (pregame_model)
        q = (1 - mix) * p + mix * fb / fb.sum() if fb.sum() > 0 else p
        out.append(pm.stretch_teams(q, M[k][:, home] > 0.5, k_))     # same final steps as the live model
    return out


def recreate(con, ns, idx, live, l2, info, names, pool) -> tuple:
    """Week by week over the games `idx`: each week predicted by a model trained only on earlier games."""
    gids, dates, home = ns["gids"], ns["dates"], ns["home"]
    made = hits = 0
    first, last = min(dates[idx]), max(dates[idx])
    week = date(int(first[:4]), int(first[4:6]), int(first[6:]))
    made = hits = 0
    while week.strftime("%Y%m%d") <= last:
        lo, hi = week.strftime("%Y%m%d"), (week + timedelta(days=7)).strftime("%Y%m%d")
        test = [k for k in idx if lo <= dates[k] < hi and gids[k] not in live]
        if test:
            model = ns["fit2"](np.where(pool & (dates < lo))[0], l2)      # trained on earlier games only
            P = with_simple_mix(ns["predict2"](model, test), ns["M"], test)    # exactly what the live model does
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
    return made, hits


def last_season_games(season, dates):
    """Last season's games to grade: regular season from LAST_SEASON_FROM, plus play-in, playoffs and Finals
    (predicted by models trained on regular-season games before each week, as always)."""
    return np.where(((season == "old") & (dates >= LAST_SEASON_FROM)) | (season == "playoffs"))[0]


def fill_last_season(con) -> int:
    """Re-create last season's picks once (and again only if LAST_SEASON_PLAN changes) — run by sync_data, so the
    cloud fills in the "overall" record by itself. Returns how many picks were added (0 = already there)."""
    picks.ensure_table(con)
    con.execute("CREATE TABLE IF NOT EXISTS backtest_meta (key TEXT PRIMARY KEY, value TEXT)")
    import json
    try:                                          # games in the training data: when that grows (a restored game),
        n = len(json.load(open("game_ids.json")))  # last season is graded again so it's included
    except (OSError, ValueError):
        n = 0
    plan = f"{LAST_SEASON_PLAN}:{n}"
    done = con.execute("SELECT value FROM backtest_meta WHERE key = 'last_season'").fetchone()
    if done and done[0] == plan:                  # same games as last time: nothing to do (re-grades ~8 s a night)
        return 0
    # Re-grade on a private copy of the database, then swap the new picks in with ONE transaction, so the website
    # (published every minute during games, possibly mid-re-grade) never sees the record half-empty (2026-11-19:
    # it showed 17 games instead of 1,457 while a re-grade was running), and a failure leaves the old picks as they are.
    import sqlite3
    work = sqlite3.connect(":memory:")
    con.commit()
    con.backup(work)
    work.execute("DELETE FROM pick_log WHERE lineup_type = ?", (LABEL,))
    ns = load_training()
    gids, dates, season = ns["gids"], ns["dates"], ns["season"]
    info = {g: (h, a) for g, h, a in work.execute("SELECT game_id, home_team, away_team FROM games")}
    names = dict(work.execute("SELECT player_id, player_name FROM lineups"))
    live = {g for (g,) in work.execute("SELECT DISTINCT game_id FROM pick_log WHERE lineup_type != ?", (LABEL,))}
    made = hits = 0
    for idx in (np.where(season == "new")[0], last_season_games(season, dates)):
        m, h = recreate(work, ns, idx, live, pm.load().get("l2", 1e-3), info, names, season != "playoffs")
        made += m; hits += h
    before = con.execute("SELECT COUNT(*) FROM pick_log WHERE lineup_type = ?", (LABEL,)).fetchone()[0]
    if before and made < before * 0.9:            # far fewer than before: something went wrong — keep the old ones
        raise RuntimeError(f"re-grade made {made} picks, had {before}")
    cols = [r[1] for r in con.execute("PRAGMA table_info(pick_log)") if r[1] != "id"]
    rows = work.execute(f"SELECT {', '.join(cols)} FROM pick_log WHERE lineup_type = ? ORDER BY id", (LABEL,)).fetchall()
    with con:                                     # one transaction: readers see the old picks or the new ones
        con.execute("DELETE FROM pick_log WHERE lineup_type = ?", (LABEL,))
        con.executemany(f"INSERT INTO pick_log ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", rows)
    con.execute("INSERT OR REPLACE INTO backtest_meta VALUES ('last_season', ?)", (plan,))
    con.commit()
    print(f"  📈 Re-graded both seasons: {made} games, #1 pick right in {hits / max(made, 1):.1%}")
    return made


FG_PLAN = "fg-v2-stretch"


def fill_fg_grades(con) -> int:
    """The field-goal model's honest picks for every past regular-season and playoff game that has an earlier week to
    learn from: each week predicted by a field-goal model trained only on earlier games (same as the main grading),
    with the live model's final simple-rule mix. Stored per starter in table fg_grades — used to score us against
    DraftKings' closing odds (its first-scorer bet pays on the first FIELD GOAL). Re-run when the training data grows."""
    import json
    con.execute("""CREATE TABLE IF NOT EXISTS fg_grades (game_id TEXT, player_id TEXT, player_name TEXT, prob REAL,
                   PRIMARY KEY (game_id, player_id))""")
    con.execute("CREATE TABLE IF NOT EXISTS backtest_meta (key TEXT PRIMARY KEY, value TEXT)")
    try:
        n = len(json.load(open("game_ids.json")))
    except (OSError, ValueError):
        n = 0
    plan = f"{FG_PLAN}:{n}"
    done = con.execute("SELECT value FROM backtest_meta WHERE key = 'fg_grades'").fetchone()
    if done and done[0] == plan:
        return 0
    ns = load_training()
    gids, dates, season, Y = ns["gids"], ns["dates"], ns["season"], ns["Y"]
    fg = dict(con.execute("SELECT game_id, scorer_player_id FROM first_fg"))
    info = {g: (h, a) for g, h, a in con.execute("SELECT game_id, home_team, away_team FROM games")}
    starters, y_fg = [], np.zeros_like(Y)
    for k, gid in enumerate(gids):
        h, a = info[gid]
        s = bf.stored_starters(gid, h, a)
        order = (s[h] + s[a])[:10] if s else []
        starters.append(order)
        for j, pl in enumerate(order):
            if str(pl["player_id"]) == str(fg.get(gid)):
                y_fg[k, j] = 1
    if not fg or y_fg.sum() < 0.9 * Y.sum():
        return 0                                          # first_fg not filled yet: next time
    pool, l2 = season != "playoffs", pm.load().get("l2", 1e-3)
    idx = np.where((dates >= LAST_SEASON_FROM))[0]
    con.execute("DELETE FROM fg_grades")
    ns["Y"] = y_fg
    made = 0
    try:
        first, last = min(dates[idx]), max(dates[idx])
        week = date(int(first[:4]), int(first[4:6]), int(first[6:]))
        while week.strftime("%Y%m%d") <= last:
            lo, hi = week.strftime("%Y%m%d"), (week + timedelta(days=7)).strftime("%Y%m%d")
            test = [k for k in idx if lo <= dates[k] < hi and len(starters[k]) == 10]
            if test:
                P = with_simple_mix(ns["predict2"](ns["fit2"](np.where(pool & (dates < lo))[0], l2), test), ns["M"], test,
                                    "first_field_goal")
                for k, probs in zip(test, P):
                    probs = np.asarray(probs) / np.sum(probs)
                    con.executemany("INSERT OR REPLACE INTO fg_grades VALUES (?, ?, ?, ?)",
                                    [(gids[k], str(pl["player_id"]), pl["name"], float(probs[j]))
                                     for j, pl in enumerate(starters[k])])
                    made += 1
            week += timedelta(days=7)
    finally:
        ns["Y"] = Y
    con.execute("INSERT OR REPLACE INTO backtest_meta VALUES ('fg_grades', ?)", (plan,))
    con.commit()
    print(f"  📈 Field-goal picks re-created for {made} games (for us vs DraftKings)")
    return made


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
    made = hits = 0
    for idx in (np.where(season == "new")[0], last_season_games(season, dates)):
        m, h_ = recreate(con, ns, idx, live, l2, info, names, pool)
        made += m; hits += h_
    con.commit()
    print(f"Re-created {made} picks (this season + last season from {LAST_SEASON_FROM}; week by week, earlier games only); "
          f"#1 pick right in {hits} ({hits / max(made, 1):.1%}).")
    print(f"Skipped {len(live)} game(s) that already have a live pick.")
    print(f"my_predictions.xlsx: {picks.export_predictions(con)} games written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
