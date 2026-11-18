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
