"""
Expected value (EV): is FanDuel's price better or worse than the model's chance says it should be?

    EV per $1 = chance × profit-if-it-wins − (1 − chance) × $1

Example: model 20% on a +460 price -> 0.20 × 4.60 − 0.80 × 1 = +$0.12 per $1 bet.
  EV > 0  the price pays more than the model thinks it should ("value")
  EV < 0  the price pays less (FanDuel's margin makes most prices negative)

EV is only as good as the model's chances, so every priced pick is also saved (odds_log) and settled
afterwards: `paper_results` answers "if I had bet $1 on every +EV pick, would I be up or down?".
Nothing here places a bet.
"""
from datetime import datetime

STAKE = 1.0                 # paper bets: a flat $1 on each +EV pick
VALUE_MIN = 0.005           # "value" = EV of at least +$0.01 per $1 once rounded to cents (not +$0.00)


def is_value(ev_: float) -> bool:
    return ev_ is not None and ev_ >= VALUE_MIN


# ──────────────────────────────────────────────
# THE MATH
# ──────────────────────────────────────────────
def profit_per_dollar(american: int) -> float:
    """Profit on a $1 bet that wins: +460 -> 4.60, -150 -> 0.667."""
    return american / 100 if american > 0 else 100 / -american


def implied_chance(american: int) -> float:
    """The chance the price implies (FanDuel's margin included): +400 -> 20%."""
    return 1 / (1 + profit_per_dollar(american))


def ev_per_dollar(chance: float, american: int) -> float:
    """Average profit per $1 if this bet were made many times, using the model's chance."""
    return chance * profit_per_dollar(american) - (1 - chance)


def edge(chance: float, american: int) -> float:
    """Model chance minus the price's implied chance (in percentage points / 100)."""
    return chance - implied_chance(american)


def priced_players(players: list[dict], prices: dict) -> list[dict]:
    """players [{name, team, prob, ...}] + prices {name: american} -> those with a price, with EV, best EV first."""
    out = [{**p, "american": prices[p["name"]], "ev": ev_per_dollar(p["prob"], prices[p["name"]]),
            "edge": edge(p["prob"], prices[p["name"]])}
           for p in players if p["name"] in prices]
    return sorted(out, key=lambda r: -r["ev"])


# ──────────────────────────────────────────────
# SAVING PRICES (so EV can be checked against what actually happened)
# ──────────────────────────────────────────────
SCHEMA = """CREATE TABLE IF NOT EXISTS odds_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT, game_date TEXT, logged_at TEXT, matchup TEXT,
    player_id TEXT, player_name TEXT, team TEXT,
    prob REAL, american INTEGER, ev REAL)"""


def ensure_table(con):
    con.execute(SCHEMA)
    con.execute("CREATE INDEX IF NOT EXISTS odds_log_game ON odds_log (game_id, logged_at)")


def _latest_snapshot(con, game_id: str) -> list:
    last = con.execute("SELECT MAX(logged_at) FROM odds_log WHERE game_id = ?", (game_id,)).fetchone()[0]
    if not last:
        return []
    return con.execute("""SELECT player_id, american, prob FROM odds_log WHERE game_id = ? AND logged_at = ?""",
                       (game_id, last)).fetchall()


def log_odds(con, game_id: str, game_date: str, matchup: str, rows: list[dict],
             logged_at: str | None = None) -> bool:
    """Save one snapshot of a game's priced players (rows from priced_players, each with 'id').
    Skipped (returns False) when nothing changed since the game's last snapshot."""
    ensure_table(con)
    if not rows:
        return False
    key = lambda pid, a, p: (str(pid), int(a), round(float(p), 4))
    new = sorted(key(r["id"], r["american"], r["prob"]) for r in rows)
    if new == sorted(key(*r) for r in _latest_snapshot(con, game_id)):
        return False
    at = logged_at or datetime.now().isoformat(timespec="seconds")
    note_market(con, game_date, matchup, "FanDuel", opened=at)          # first time we saw this market: "opened at"
    con.executemany(
        """INSERT INTO odds_log (game_id, game_date, logged_at, matchup, player_id, player_name, team, prob,
                                 american, ev) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        [(game_id, game_date, at, matchup, str(r["id"]), r["name"], r["team"], float(r["prob"]),
          int(r["american"]), float(r["ev"])) for r in rows])
    con.commit()
    return True


# ──────────────────────────────────────────────
# PAPER BETTING RESULTS
# ──────────────────────────────────────────────
def paper_results(con, min_ev: float = VALUE_MIN) -> dict:
    """Settle a $1 paper bet on every player with EV >= min_ev in each finished game's LAST saved snapshot
    (the prices closest to tip-off). Returns {bets, wins, staked, profit, roi, games, rows [...newest first]}."""
    ensure_table(con)
    rows = con.execute(
        """SELECT o.game_id, o.game_date, o.matchup, o.player_id, o.player_name, o.team, o.prob, o.american, o.ev,
                  f.scorer_player_id, f.scorer_name
           FROM odds_log o
           JOIN (SELECT game_id, MAX(logged_at) AS last FROM odds_log GROUP BY game_id) l
             ON l.game_id = o.game_id AND l.last = o.logged_at
           JOIN first_baskets f ON f.game_id = o.game_id
           WHERE o.ev >= ?
           ORDER BY o.game_date DESC, o.game_id, o.ev DESC""", (min_ev,)).fetchall()
    bets = []
    for gid, gdate, matchup, pid, name, team, prob, american, ev, scorer_id, scorer in rows:
        won = str(pid) == str(scorer_id)
        bets.append({"game_id": gid, "game_date": gdate, "matchup": matchup, "player": name, "team": team,
                     "prob": prob, "american": american, "ev": ev, "won": won, "scorer": scorer,
                     "profit": STAKE * profit_per_dollar(american) if won else -STAKE})
    staked = STAKE * len(bets)
    profit = sum(b["profit"] for b in bets)
    return {"bets": len(bets), "wins": sum(b["won"] for b in bets), "staked": staked, "profit": profit,
            "roi": profit / staked if staked else 0.0, "games": len({b["game_id"] for b in bets}),
            "expected_profit": sum(STAKE * b["ev"] for b in bets), "rows": bets}


def implied(american: int) -> float:
    """Sportsbook price -> the chance it implies (margin included)."""
    return 100 / (american + 100) if american > 0 else -american / (-american + 100)


def clv_results(con, min_ev: float = VALUE_MIN) -> dict:
    """Closing line value: for every player the model first called a good deal (EV >= min_ev) in a finished game,
    compare FanDuel's price at that moment with its last price before tip-off. A price that got SHORTER (e.g. +900 ->
    +750) means the market moved toward our view — the earliest sign the model finds real value.
    clv = closing implied chance / implied chance when picked - 1 (+0.10 = the market moved 10% our way).
    `all_players` is the same over every priced player, as a yardstick for how prices drift anyway."""
    ensure_table(con)
    rows = con.execute(
        """SELECT o.game_id, o.player_id, o.player_name, o.logged_at, o.american, o.ev
           FROM odds_log o JOIN first_baskets f ON f.game_id = o.game_id
           ORDER BY o.game_id, o.player_id, o.logged_at""").fetchall()
    by = {}
    for gid, pid, name, at, american, ev in rows:
        by.setdefault((gid, pid), []).append((at, american, ev, name))

    def summary(pairs):
        moved = [c for c in pairs if abs(c) > 1e-9]
        return {"picks": len(pairs), "shorter": sum(c > 1e-9 for c in pairs), "longer": sum(c < -1e-9 for c in pairs),
                "avg_clv": sum(pairs) / len(pairs) if pairs else 0.0,
                "beat_rate": sum(c > 0 for c in moved) / len(moved) if moved else 0.0}
    value, everyone, rows_out = [], [], []
    for (gid, pid), snaps in by.items():
        close = snaps[-1][1]
        everyone.append(implied(close) / implied(snaps[0][1]) - 1)
        first = next((s for s in snaps if s[2] is not None and s[2] >= min_ev), None)
        if first:
            c = implied(close) / implied(first[1]) - 1
            value.append(c)
            rows_out.append({"game_id": gid, "player": first[3], "picked": first[1], "closed": close, "clv": c})
    return {**summary(value), "all_players": summary(everyone), "rows": rows_out}


def market_results(con) -> dict:
    """Who guesses better, the model or FanDuel? For each finished game, the last FanDuel board before tip-off: FanDuel's
    chance for each player = its price's implied chance, scaled so the listed players add up to 100% (the book's cut
    removed). Whoever gave the actual first scorer the higher chance "was closer" that game. Also the average log-loss
    of each (lower = better) for games where the scorer was on FanDuel's board."""
    import math
    ensure_table(con)
    rows = con.execute(
        """SELECT o.game_id, o.player_id, o.prob, o.american, f.scorer_player_id
           FROM odds_log o
           JOIN (SELECT game_id, MAX(logged_at) AS last FROM odds_log GROUP BY game_id) l
             ON l.game_id = o.game_id AND l.last = o.logged_at
           JOIN first_baskets f ON f.game_id = o.game_id""").fetchall()
    games = {}
    for gid, pid, prob, american, scorer in rows:
        games.setdefault(gid, {"scorer": str(scorer), "rows": []})["rows"].append((str(pid), prob, american))
    us = them = ties = 0
    ll_us, ll_them = [], []
    for g in games.values():
        total = sum(implied(a) for _, _, a in g["rows"])
        hit = next((r for r in g["rows"] if r[0] == g["scorer"]), None)
        if not hit or not total:
            continue                                   # scorer not on FanDuel's board: can't compare this game
        p_us, p_them = hit[1], implied(hit[2]) / total
        us += p_us > p_them; them += p_them > p_us; ties += p_us == p_them
        ll_us.append(-math.log(max(p_us, 1e-9))); ll_them.append(-math.log(max(p_them, 1e-9)))
    n = len(ll_us)
    return {"games": n, "us_closer": us, "fanduel_closer": them, "ties": ties,
            "logloss_us": sum(ll_us) / n if n else None, "logloss_fanduel": sum(ll_them) / n if n else None}


def opening_prices(con, game_date: str, matchup: str) -> dict:
    """Each book's FIRST saved price today for every player in this game: {"FanDuel": {name: american},
    "DraftKings": {name: american}} — the page compares it with the current price (↓ payout dropped / ↑ rose)."""
    ensure_table(con)
    out = {"FanDuel": {}, "DraftKings": {}}
    for book, table in (("FanDuel", "odds_log"), ("DraftKings", "dk_odds_log")):
        try:
            for name, american in con.execute(
                    f"SELECT player_name, american FROM {table} WHERE game_date = ? AND matchup = ? ORDER BY logged_at",
                    (game_date, matchup)):
                out[book].setdefault(name, american)
        except Exception:  # noqa: BLE001 — table not there yet
            pass
    return out


def _clock(at) -> str:
    """A log time -> "{2026-11-19T14:03:00Z}" (UTC): the page turns it into the viewer's own local time.
    FanDuel's log stores New York ISO times; DraftKings' log stores New York "YYYY-MM-DD HH:MM:SS" (or epoch ms)."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    if str(at).endswith(" 00:00:00"):
        return "opening"                       # DraftKings' official opening price (from ESPN)
    if str(at).endswith(" 23:59:59"):
        return "closing"                       # DraftKings' last price before tip-off (from ESPN, past games)
    try:
        t = datetime.fromtimestamp(int(at) / 1000, timezone.utc) if str(at).isdigit() \
            else datetime.fromisoformat(str(at)).replace(tzinfo=ZoneInfo("America/New_York"))
        return "{" + t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") + "}"
    except (ValueError, OSError):
        return str(at)


def odds_moves(con) -> list:
    """One row per player per book per game, newest day first: [date, game, player, book, opened, closed, moved,
    times changed]. "closed" = the last price saved (before tip-off once the game has started). Both books' logs."""
    ensure_table(con)
    seen = {}
    for book, table in (("FanDuel", "odds_log"), ("DraftKings", "dk_odds_log"), ("team", "team_first_log")):
        try:
            q = con.execute(f"SELECT game_date, matchup, player_name, american, logged_at FROM {table} ORDER BY logged_at") \
                if book != "team" else con.execute(
                "SELECT game_date, matchup, team || ' — team scores first', american, logged_at, book FROM team_first_log "
                "ORDER BY logged_at")
        except Exception:  # noqa: BLE001 — table not there yet
            continue
        for d, m, name, a, at, *bk in q:
            r = seen.setdefault((d, m, name, bk[0] if bk else book), [a, a, 0, [(at, a)]])
            if a != r[1]:
                r[2] += 1
                r[3].append((at, a))
            r[1] = a
    rows = []
    times = {}
    try:
        con.execute(MARKET_TIMES)
        times = {(d, m, b): (o, c) for d, m, b, o, c in con.execute("SELECT * FROM market_times")}
    except Exception:  # noqa: BLE001
        pass
    for (d, m, name, book), (first, last, changes, steps) in seen.items():
        opened_at, closed_at = times.get((d, m, book), (None, None))
        moved = ("same" if first == last else
                 "↓ payout dropped" if profit_per_dollar(last) < profit_per_dollar(first) else "↑ payout rose")
        rows.append([f"{d[4:6]}/{d[6:8]}/{d[2:4]}", m, name, book, f"{first:+d}", f"{last:+d}", moved, changes,
                     " → ".join(f"{a:+d} ({_clock(at)})" for at, a in steps),
                     _clock(opened_at) if opened_at else _clock(steps[0][0]), _clock(closed_at) if closed_at else "–"])
    rows.sort(key=lambda r: (r[0][6:] + r[0][:5], r[1], r[2], r[3]), reverse=True)
    return rows


MARKET_TIMES = """CREATE TABLE IF NOT EXISTS market_times (
    game_date TEXT, matchup TEXT, book TEXT, opened_at TEXT, closed_at TEXT, PRIMARY KEY (game_date, matchup, book))"""


def note_market(con, game_date: str, matchup: str, book: str, opened: str | None = None, closed: str | None = None):
    """When a book's first-basket market was first seen open, and when it was first seen closed (kept once each)."""
    con.execute(MARKET_TIMES)
    con.execute("INSERT OR IGNORE INTO market_times (game_date, matchup, book) VALUES (?, ?, ?)", (game_date, matchup, book))
    if opened:
        con.execute("UPDATE market_times SET opened_at = ? WHERE game_date = ? AND matchup = ? AND book = ? AND opened_at IS NULL",
                    (opened, game_date, matchup, book))
    if closed:
        con.execute("UPDATE market_times SET closed_at = ? WHERE game_date = ? AND matchup = ? AND book = ? AND closed_at IS NULL",
                    (closed, game_date, matchup, book))
