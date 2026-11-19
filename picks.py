"""
Pick log: every prediction `nba.py predict` makes is stored and never overwritten, so the model's real
track record can be measured — overall, by lineup type (CONFIRMED / EXPECTED / PROJECTED) and by model version.

A pick is logged once per (game, lineup type, lineup, model): re-running with nothing new adds nothing; a
lineup change, a lineup becoming expected/confirmed, or a retrained model adds a new entry. Every pick is made before tip-off (`predict` skips started games),
so the last pick logged for a game is the final pre-game pick.
"""
import hashlib
import json
from datetime import datetime

SCHEMA = """CREATE TABLE IF NOT EXISTS pick_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    game_date TEXT,
    logged_at TEXT NOT NULL,          -- local time, ISO format
    matchup TEXT,
    lineup_type TEXT,                 -- CONFIRMED / EXPECTED / PROJECTED
    lineup_source TEXT,               -- nba_confirmed / nba_expected_unconfirmed / projected_usual_starters
    lineup_sig TEXT,                  -- sorted starter ids: detects a lineup change
    model_id TEXT,                    -- trained-through date + weights fingerprint
    pick_player_id TEXT,
    pick_player_name TEXT,
    pick_team TEXT,
    pick_prob REAL,
    tip_home REAL,                    -- P(home team wins the opening tip)
    players_json TEXT,                -- all 10 starters with their probabilities, best first
    home_jumper TEXT,                 -- who we expect to jump for each team
    away_jumper TEXT
)"""


def ensure_table(con):
    con.execute(SCHEMA)
    con.execute("CREATE INDEX IF NOT EXISTS pick_log_game ON pick_log (game_id)")
    cols = {r[1] for r in con.execute("PRAGMA table_info(pick_log)")}
    for col in ("home_jumper", "away_jumper"):          # added 2026-11-17 for my_predictions.xlsx
        if col not in cols:
            con.execute(f"ALTER TABLE pick_log ADD COLUMN {col} TEXT")


def model_id(model: dict) -> str:
    """'20261116-3fa9c2': when the model was trained through + a fingerprint of its weights."""
    digest = hashlib.sha1(json.dumps([round(w, 10) for w in model.get("w", [])]).encode()).hexdigest()[:6]
    return f"{model.get('trained_through', 'unknown')}-{digest}"


def log_pick(con, p: dict) -> bool:
    """Store one pick. `p`: game_id, game_date, matchup, lineup_type, lineup_source, lineup_sig, model_id,
    tip_home, players [{id, name, team, prob}] (any order), optional logged_at.
    Returns False (and stores nothing) if the game's latest pick has the same lineup type, lineup and model."""
    ensure_table(con)
    last = con.execute("""SELECT lineup_type, lineup_sig, model_id FROM pick_log
                          WHERE game_id = ? ORDER BY id DESC LIMIT 1""", (p["game_id"],)).fetchone()
    if last and tuple(last) == (p["lineup_type"], p["lineup_sig"], p["model_id"]):
        return False
    players = sorted(p["players"], key=lambda x: -x["prob"])
    top = players[0]
    con.execute(
        """INSERT INTO pick_log (game_id, game_date, logged_at, matchup, lineup_type, lineup_source, lineup_sig,
                                 model_id, pick_player_id, pick_player_name, pick_team, pick_prob, tip_home, players_json,
                                 home_jumper, away_jumper)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (p["game_id"], p.get("game_date"), p.get("logged_at") or datetime.now().isoformat(timespec="seconds"),
         p.get("matchup"), p["lineup_type"], p.get("lineup_source"), p["lineup_sig"], p["model_id"],
         top["id"], top["name"], top["team"], float(top["prob"]), p.get("tip_home"),
         json.dumps([{**x, "prob": float(x["prob"])} for x in players]),     # full precision: the spreadsheet
                                                                          # shows exactly what the screen shows
         p.get("home_jumper"), p.get("away_jumper")))
    con.commit()
    return True


def _score(rows):
    """rows: [(pick_id, top3_ids, scorer_id)] -> {'games', 'hits', 'top3'}"""
    return {"games": len(rows),
            "hits": sum(pid == scorer for pid, _, scorer in rows),
            "top3": sum(scorer in top3 for _, top3, scorer in rows)}


def summary(con) -> dict:
    """Track record on finished games.
    final    = this season: the last pick made for each game (what you would have bet)
    overall  = the same over every graded game (re-created picks for last season from Jan 2026 included)
    by_type  = for each lineup type, the last pick of that type per game
    by_model = for each model version, the last pick it made per game
    games    = one row per finished game (final pick), newest first"""
    ensure_table(con)
    rows = con.execute(
        """SELECT p.id, p.game_id, p.game_date, p.logged_at, p.matchup, p.lineup_type, p.model_id,
                  p.pick_player_id, p.pick_player_name, p.pick_prob, p.players_json,
                  f.scorer_player_id, f.scorer_name
           FROM pick_log p JOIN first_baskets f ON f.game_id = p.game_id
           ORDER BY p.logged_at, p.id""").fetchall()
    final, everything, by_type, by_model = {}, {}, {}, {}
    for r in rows:   # later rows overwrite earlier ones -> the last pick wins
        everything[r[1]] = r                       # every graded game (last season from Jan 2026 + this season)
        if (r[2] or "") < SEASON_START:
            continue                               # the rest is this season only
        final[r[1]] = r
        by_type.setdefault(r[5], {})[r[1]] = r
        by_model.setdefault(r[6], {})[r[1]] = r

    def item(r):
        top3 = [x["id"] for x in json.loads(r[10])[:3]]
        return r[7], top3, r[11]

    games = [{"game_id": r[1], "game_date": r[2], "matchup": r[4], "lineup_type": r[5], "pick": r[8],
              "prob": r[9], "actual": r[12], "hit": r[7] == r[11]}
             for r in sorted(final.values(), key=lambda r: (r[2] or "", r[3]), reverse=True)]
    first = min((r[2] for r in everything.values() if r[2]), default="")
    return {"final": _score([item(r) for r in final.values()]),
            "overall": {**_score([item(r) for r in everything.values()]), "since": first},
            "last_season": {**_score([item(r) for g, r in everything.items() if g not in final]), "since": first,
                            "name": LAST_SEASON_NAME},
            "by_type": {k: _score([item(r) for r in v.values()]) for k, v in by_type.items()},
            "by_model": {k: _score([item(r) for r in v.values()]) for k, v in by_model.items()},
            "games": games}


PREDICTIONS_FILE = "my_predictions.xlsx"               # Excel file with two tabs: Scorecard and Games
SEASON_START, SEASON_NAME = "20260801", "2026-27"      # the spreadsheet covers this season only
LAST_SEASON_NAME = "2025-26"
# Picks re-created by season_backtest.py (games before live picks began) used the real starting fives, i.e. the
# confirmed lineup; what differs is WHEN the pick was made, shown in its own "Pick made" column.
LINEUP_NAMES = {"BACKTEST": "Confirmed"}
PICK_MADE = {"BACKTEST": "Re-created"}                  # every other pick: "Live"
# Related fields side by side (owner's request): each group reads prediction -> what actually happened -> right?
COLUMNS = ["date", "game",
           "pick_1", "pick_1_chance", "actual_first_scorer", "pick_1_right",
           "pick_2", "pick_2_chance", "top_2_right", "how_scored",
           "predicted_tip_winner", "tip_chance", "actual_tip_winner", "tip_right",
           "team_to_score_first", "team_chance", "team_right",
           "pick_made", "lineup",
           "running_all", "running_pick_1"]
# The header the spreadsheet shows for each column (plain English; the keys above are only used inside the code),
# and how wide each column is.
LABELS = {
    "date": "Date",
    "game": "Game (away @ home)",
    "pick_made": "Pick made",
    "lineup": "Lineup used",
    "pick_1": "Our #1 pick to score first",
    "pick_1_chance": "#1 chance",
    "pick_2": "Our #2 pick to score first",
    "pick_2_chance": "#2 chance",
    "predicted_tip_winner": "Predicted tip-off winner",
    "tip_chance": "Tip chance",
    "team_to_score_first": "Predicted team to score first",
    "team_chance": "Team chance",
    "actual_first_scorer": "Who actually scored first",
    "how_scored": "How the first basket was scored",
    "actual_tip_winner": "Actual tip-off winner",
    "pick_1_right": "#1 pick right?",
    "top_2_right": "#1 or #2 right?",
    "tip_right": "Tip-off call right?",
    "team_right": "Team call right?",
    "running_all": "Running: all predictions right",
    "running_pick_1": "Running: #1 pick right",
}
WIDTHS = {"date": 9, "game": 12, "pick_1": 24, "pick_1_chance": 8, "pick_1_right": 9,
          "pick_2": 24, "pick_2_chance": 8, "top_2_right": 10, "actual_first_scorer": 24, "how_scored": 24,
          "predicted_tip_winner": 26, "tip_chance": 8, "actual_tip_winner": 26, "tip_right": 9,
          "team_to_score_first": 10, "team_chance": 9, "team_right": 10, "pick_made": 11, "lineup": 12,
          "running_all": 16, "running_pick_1": 14}
CHECKS = ("pick_1_right", "top_2_right", "tip_right")   # what the counters count (owner: no counter for team to score first)
YES_NO = ("pick_1_right", "top_2_right", "tip_right", "team_right")


def prediction_rows(con) -> list:
    """One dict per game this season — our final pre-game pick next to what actually happened — newest first
    (today at the top, opening night at the bottom). Results read 'pending' until the first basket is known."""
    ensure_table(con)
    rows = con.execute("""SELECT game_id, game_date, matchup, lineup_type, players_json, tip_home, home_jumper, away_jumper
                          FROM pick_log WHERE game_date >= ? ORDER BY logged_at, id""", (SEASON_START,)).fetchall()
    final = {}
    for r in rows:                       # later picks replace earlier ones: the last pick before the game
        final[r[0]] = r
    actual = {g: (sid, n, t, how) for g, sid, n, t, how in con.execute(
        "SELECT game_id, scorer_player_id, scorer_name, scorer_team, shot_type FROM first_baskets")}
    tips = {g: (t, n) for g, t, n in con.execute(
        "SELECT game_id, tip_winner_team, tip_winner_player_name FROM games")}
    out = []
    for gid, d, matchup, lineup, pj, tip_home, hj, aj in sorted(final.values(), key=lambda r: (r[1] or "", r[0]), reverse=True):
        away, home = [x.strip() for x in (matchup or " @ ").split("@")]
        players = sorted(json.loads(pj), key=lambda x: -x["prob"])
        team_p = {}
        for x in players:
            team_p[x["team"]] = team_p.get(x["team"], 0.0) + x["prob"]
        total = sum(team_p.values()) or 1.0
        first_team = max(team_p, key=team_p.get)
        th = tip_home if tip_home is not None else 0.5
        tip_team, tip_p, jumper = (home, th, hj) if th >= 0.5 else (away, 1 - th, aj)
        a = actual.get(gid)
        tw = tips.get(gid, (None, None))
        def yes(ok):
            return "pending" if not a else ("YES" if ok else "NO")
        out.append({
            "date": f"{d[4:6]}/{d[6:8]}/{d[2:4]}" if d else "", "game": matchup, "pick_made": PICK_MADE.get(lineup, "Live"),
            "lineup": LINEUP_NAMES.get(lineup, (lineup or "").title()),
            "pick_1": f"{players[0]['name']} ({players[0]['team']})", "pick_1_chance": f"{players[0]['prob']:.1%}",
            "pick_2": f"{players[1]['name']} ({players[1]['team']})", "pick_2_chance": f"{players[1]['prob']:.1%}",
            "predicted_tip_winner": f"{jumper} ({tip_team})" if jumper else tip_team, "tip_chance": f"{tip_p:.0%}",
            "team_to_score_first": first_team, "team_chance": f"{team_p[first_team] / total:.0%}",
            "actual_first_scorer": f"{a[1]} ({a[2]})" if a else "", "how_scored": a[3] if a else "",
            "actual_tip_winner": (f"{tw[1]} ({tw[0]})" if tw[1] else (tw[0] or "")) if a else "",
            "pick_1_right": yes(a and a[0] == players[0]["id"]),
            "top_2_right": yes(a and a[0] in (players[0]["id"], players[1]["id"])),
            "tip_right": yes(a and tw[0] == tip_team) if (not a or tw[0]) else "",
            "team_right": yes(a and a[2] == first_team),
        })
    add_running_counts(out)
    return out


LINEUP_COLUMNS = ["Date", "Game (away @ home)", "Team",
                  "NBA.com EARLY lineup (marked 'Expected', posted before it is confirmed)",
                  "NBA.com FINAL lineup (marked 'Confirmed', near tip-off)",
                  "Who ACTUALLY started (box score after the game)",
                  "Did the EARLY lineup match who started?", "Did the FINAL lineup match who started?"]
SNAPSHOTS_SINCE = "20261117"      # the day we started saving NBA.com's early ('Expected') lineups
POSITION_ORDER = {"PG": 0, "G": 1, "SG": 1, "SF": 2, "F": 3, "PF": 3, "C": 4}


def _same_five(listed: list, actual: list) -> str:
    """YES if all 5 listed starters started (matched by ESPN id, or by name when the id is unknown)."""
    if not listed or not actual:
        return ""
    import starters as st
    ids = {a[0] for a in actual if a[0]}
    names = {st.norm_name(a[1]) for a in actual}
    hits = sum((pid in ids) or (st.norm_name(name) in names) for pid, name in listed)
    return "YES" if hits == 5 and len(listed) == 5 else "NO"


def lineup_rows(con) -> list:
    """The Lineups tab: for every team in every game this season (newest first), the starters NBA.com listed as
    Expected, the starters it Confirmed, and who actually started (ESPN box score)."""
    import starters as st
    st.ensure_official_tables(con)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    games = {}
    if "games" in tables:
        for gid, d, h, a in con.execute("SELECT game_id, game_date, home_team, away_team FROM games WHERE game_date >= ?",
                                         (SEASON_START,)):
            games[(d, h, a)] = gid
    for gid, d, matchup in con.execute("SELECT DISTINCT game_id, game_date, matchup FROM pick_log WHERE game_date >= ?",
                                       (SEASON_START,)):
        away, home = [x.strip() for x in (matchup or " @ ").split("@")]
        games.setdefault((d, home, away), gid)
    snap = {}
    for d, team, status, pid, name, pos in con.execute(
            "SELECT game_date, team, status, player_id, player_name, position FROM lineup_snapshots"):
        snap.setdefault((d, team, status), []).append((pid, name, pos))
    for d, team, pid, name, pos in con.execute(
            "SELECT game_date, team, player_id, player_name, position FROM official_lineups WHERE status = 'Confirmed'"):
        if (d, team, "Confirmed") not in snap or len(snap[(d, team, "Confirmed")]) < 5:
            snap.setdefault((d, team, "Confirmed_old"), []).append((pid, name, pos))
    empty_days = {d for (d,) in con.execute("SELECT game_date FROM official_lineup_days WHERE players = 0")}
    empty_days |= {d for (d,) in con.execute(          # filled from ESPN only: NBA.com still posted nothing
        "SELECT game_date FROM official_lineups GROUP BY game_date HAVING SUM(status != 'ESPN') = 0")}
    finished_games = set()
    if "first_baskets" in tables and "games" in tables:
        finished_games = {(d, h, a) for d, h, a in con.execute(
            "SELECT g.game_date, g.home_team, g.away_team FROM games g JOIN first_baskets f USING (game_id)")}
    actual = {}
    if "lineups" in tables:
        for gid, team, pid, name in con.execute(
                "SELECT game_id, team, player_id, player_name FROM lineups WHERE is_starter = 1"):
            actual.setdefault((gid, team), []).append((str(pid), name))

    def ordered(players):
        return sorted(players, key=lambda p: POSITION_ORDER.get(p[2], 5))

    out = []
    for (d, h, a), gid in sorted(games.items(), key=lambda kv: (kv[0][0], kv[1]), reverse=True):
        for team in (a, h):
            exp = ordered(snap.get((d, team, "Expected"), []))
            conf = ordered(snap.get((d, team, "Confirmed")) or snap.get((d, team, "Confirmed_old"), []))
            act = actual.get((gid, team), [])
            order = {st.norm_name(p[1]): k for k, p in enumerate(conf or exp)}
            act = sorted(act, key=lambda p: order.get(st.norm_name(p[1]), 9))
            played = bool(act)
            finished = played or (d, h, a) in finished_games
            # every cell says something: a name list, a YES/NO, or why it is empty
            if exp:
                early = ", ".join(p[1] for p in exp)
            elif d < SNAPSHOTS_SINCE:
                early = ("Not available: NBA.com overwrites its early lineup with the final one, and we only started saving "
                         "copies on 11/17/26")
            else:
                early = "Not available: NBA.com hadn't posted an early lineup when the program ran"
            if conf:
                final = ", ".join(p[1] for p in conf)
            elif not finished:
                final = "Not confirmed yet (NBA.com confirms ~30 min before tip-off)"
            elif d in empty_days:
                final = ("Not available: NBA.com never posted starting lineups this day (who actually started is in the next "
                         "column)")
            else:
                final = "Missing: the final lineup wasn't saved"
            started = ", ".join(p[1] for p in act) if played else (
                "Game not played yet" if not finished else "Box score not available")
            no_early = ("Not available: no early lineup saved before 11/17/26" if d < SNAPSHOTS_SINCE
                        else "Not available: NBA.com posted no early lineup")
            if not played:
                early_ok = "Waiting for the game" if exp and not finished else no_early if not exp else "Missing: box score not available"
                final_ok = "Waiting for the game" if conf else ("Waiting for the final lineup" if not finished
                                                                else "Missing: the final lineup wasn't saved")
            else:
                early_ok = _same_five([(p[0], p[1]) for p in exp], act) if exp else no_early
                final_ok = _same_five([(p[0], p[1]) for p in conf], act) if conf else (
                    "Not available: NBA.com posted no lineup this day" if d in empty_days
                    else "Missing: the final lineup wasn't saved")
            out.append([f"{d[4:6]}/{d[6:8]}/{d[2:4]}", f"{a} @ {h}", team, early, final, started, early_ok, final_ok])
    return out


def export_predictions(con, path: str = PREDICTIONS_FILE) -> int:
    """Write my_predictions.xlsx: a 'Scorecard' tab (how many right, out of how many), a 'Games' tab (one row per
    game, newest first, header row frozen with a filter button) and a 'Lineups' tab (NBA.com expected vs confirmed
    vs actual starters, per team). Returns the number of games."""
    import spreadsheet as sh
    out = prediction_rows(con)
    card = scorecard(out, today=datetime.now().strftime("%m/%d/%y"))
    card_styles = {(0, 0): sh.TITLE}
    for r, line in enumerate(card):
        if line and line[0] == "":                                    # the Correct / Out of / Percent header
            card_styles.update({(r, 0): sh.HEADER, **{(r, c): sh.HEADER_CENTER for c in (1, 2, 3)}})
        elif len(line) == 4:                                          # a result line: numbers centred
            bold = line[0] == "ALL OUR PREDICTIONS COMBINED"
            card_styles.update({(r, 0): sh.BOLD if bold else sh.NORMAL,
                                **{(r, c): sh.CENTER_BOLD if bold else sh.CENTER for c in (1, 2, 3)}})
    games = [[LABELS[c] for c in COLUMNS]] + [[row[c] for c in COLUMNS] for row in out]
    styles = {(0, c): sh.HEADER for c in range(len(COLUMNS))}
    for r, row in enumerate(out, 1):
        for c, key in enumerate(COLUMNS):
            if key in YES_NO:
                styles[(r, c)] = {"YES": sh.YES, "NO": sh.NO}.get(row[key], sh.MUTED)
    lineups = [LINEUP_COLUMNS] + lineup_rows(con)
    lineup_styles = {(0, c): sh.HEADER for c in range(len(LINEUP_COLUMNS))}
    for r, row in enumerate(lineups[1:], 1):
        for c in (6, 7):
            lineup_styles[(r, c)] = {"YES": sh.YES, "NO": sh.NO}.get(row[c], sh.MUTED)
    sh.write_xlsx(path, [
        {"name": "Scorecard", "rows": card, "styles": card_styles, "widths": [44, 12, 12, 12]},
        {"name": "Games", "rows": games, "styles": styles, "widths": [WIDTHS[c] for c in COLUMNS],
         "freeze_row": 1, "freeze_col": 2, "filter_row": 1, "heights": {0: 34}},
        {"name": "Lineups", "rows": lineups, "styles": lineup_styles,
         "widths": [9, 12, 6, 68, 68, 68, 36, 36], "freeze_row": 1, "freeze_col": 3, "filter_row": 1,
         "heights": {0: 50}},
    ])
    return len(out)


SCORECARD = [("pick_1_right", "Our #1 pick scored first"),
             ("top_2_right", "Our #1 or #2 pick scored first"),
             ("tip_right", "We called the tip-off winner")]


def add_running_counts(rows: list) -> None:
    """Fill the two running-count columns, counting from the oldest game (bottom of the file) up, so the newest
    finished game — near the top — shows the season total so far. Games not played yet are left blank."""
    all_right = all_n = p1_right = p1_n = 0
    for r in reversed(rows):                                # rows are newest first: count from the bottom up
        answers = [r[k] for k in CHECKS if r[k] in ("YES", "NO")]
        if r["pick_1_right"] not in ("YES", "NO"):
            r["running_all"] = r["running_pick_1"] = ""
            continue
        all_right += answers.count("YES"); all_n += len(answers)
        p1_right += r["pick_1_right"] == "YES"; p1_n += 1
        r["running_all"] = f"{all_right} of {all_n} ({all_right / all_n:.0%})"
        r["running_pick_1"] = f"{p1_right} of {p1_n} ({p1_right / p1_n:.0%})"


def scorecard(rows: list, today: str = "") -> list:
    """The Scorecard tab: for each question, how many of this season's finished games we got right ('pending' and
    blank results are not counted). `today` (MM/DD/YY) lets it say which games are still to play today."""
    finished = [r for r in rows if r["pick_1_right"] in ("YES", "NO")]
    pending = [r for r in rows if r["pick_1_right"] not in ("YES", "NO")]
    recreated = sum(r.get("pick_made") == "Re-created" for r in finished)
    first = (min((r["date"] for r in finished if r["date"]), key=lambda d: d[6:] + d[:5])[:5] if finished else "")
    lines = [[f"SCORECARD — {SEASON_NAME} season"],
             [f"{len(finished)} finished games" + (f" since {first}" if first else "")]]
    if recreated:
        n_live = len(finished) - recreated
        lines.append([f"{recreated} re-created week by week, {n_live} live pick{'' if n_live == 1 else 's'}"])
    if pending:
        lines.append([f"{len(pending)} game{'' if len(pending) == 1 else 's'} still to play"
                      + (" today" if all(r["date"] == today for r in pending) else "")])
    lines += [[], ["", "Correct", "Out of", "Percent"]]
    for key, label in SCORECARD:
        done = [r[key] for r in rows if r[key] in ("YES", "NO")]
        right = done.count("YES")
        lines.append([label, right, len(done), f"{right / len(done):.0%}" if done else "-"])
    every = [r[k] for r in rows for k in CHECKS if r[k] in ("YES", "NO")]
    right = every.count("YES")
    lines.append(["ALL OUR PREDICTIONS COMBINED", right, len(every), f"{right / len(every):.0%}" if every else "-"])
    if recreated:
        lines += [[], ["Live = picked before the game by running nba.py."],
                  ["Re-created = games before live picks began: picked afterwards, week by week, with the real"],
                  ["starting lineup and only games played before (as if we had run nba.py before tip-off)."]]
    return lines
