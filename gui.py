"""
A simple window on the prediction model: today's first-basket picks in your web browser.

    python3 gui.py            # opens http://127.0.0.1:8765 in your browser (Ctrl-C here to stop)
    python3 gui.py --no-open  # start without opening a browser
    python3 gui.py --port 8766

It runs the same `nba.py predict` steps (data update, lineups, injuries, model, FanDuel odds) and shows
the result as cards instead of terminal text. Picks are logged exactly as a terminal run logs them.
Only reachable from this computer (127.0.0.1). Uses the Python standard library only.
"""
import contextlib
import json
import os
import sys
import threading
import time
import traceback
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import accounts

HOST, PORT = "127.0.0.1", 8765
PAGE = Path(__file__).parent / "gui" / "index.html"


# ──────────────────────────────────────────────
# COLLECTOR: receives what predict_today would print
# ──────────────────────────────────────────────
class Collector:
    """Stands in for display.py: same function names, but keeps the output as data for the page."""

    def __init__(self, on_step=lambda msg: None):
        self.result = {"date": "", "summary": [], "schedule": [], "cards": [], "skipped": [], "notes": []}
        self._on_step = on_step
        self.console = self                     # predict_today calls ui.console.status(...)

    def status(self, msg: str):
        self._on_step(msg.rstrip("…."))
        return contextlib.nullcontext()

    def header(self, day_label: str, *lines: str):
        self.result["date"] = day_label
        self.result["summary"] = [line for line in lines if line]

    def schedule(self, games: list):
        self.result["schedule"] = [{k: g.get(k) for k in GAME_KEYS} for g in games]

    def game_card(self, card: dict):
        self.result["cards"].append(card)

    def skipped(self, matchup: str, reason: str):
        self.result["skipped"].append({"matchup": matchup, "reason": reason})

    def notes(self, lines: list):
        self.result["notes"] += list(lines)

    def legend(self, any_projected: bool):
        pass


CARDS_FILE = Path(__file__).parent / "todays_cards.json"


def _saved_odds(con, game_id, players) -> dict:
    """The last FanDuel prices saved for this game before tip-off (odds_log), with value and best deal — so a
    rebuilt card still shows what the line was. {} if none were saved."""
    import ev
    try:
        last = con.execute("SELECT MAX(logged_at) FROM odds_log WHERE game_id = ?", (str(game_id),)).fetchone()[0]
        if not last:
            return {}
        prices = {r["player_name"]: int(r["american"]) for r in con.execute(
            "SELECT player_name, american FROM odds_log WHERE game_id = ? AND logged_at = ? AND american IS NOT NULL",
            (str(game_id), last))}
    except Exception:  # noqa: BLE001 — no odds table yet
        return {}
    if not prices:
        return {}
    priced = ev.priced_players([{"name": x["name"], "team": x["team"], "prob": x["prob"]} for x in players], prices)
    best = priced[0] if priced and ev.is_value(priced[0]["ev"]) else None
    return {"fanduel": {r["name"]: r["american"] for r in priced} or prices, "fanduel_note": "",
            "ev": {r["name"]: r["ev"] for r in priced}, "best_book": {r["name"]: "FanDuel" for r in priced},
            "best_value": ({**{k: best[k] for k in ("name", "team", "prob", "american", "ev")}, "book": "FanDuel"}
                           if best else None)}


def add_logged_cards(result: dict) -> dict:
    """A game that already tipped off gets no new card (the model is pre-game only). Rebuild its card from the
    picks we logged BEFORE tip-off (pick_log), so tapping a live or finished game still shows our predictions."""
    import nba
    import pregame_model as pm
    have = {c.get("matchup") for c in result.get("cards", [])}
    todo = [g for g in result.get("schedule", [])
            if g.get("game_id") and f"{g.get('away_team')} @ {g.get('home_team')}" not in have]
    if not todo:
        return result
    con = nba.db()
    try:
        made = []
        for g in todo:
            row = con.execute("""SELECT * FROM pick_log WHERE game_id = ? ORDER BY id DESC LIMIT 1""",
                              (g["game_id"],)).fetchone()
            if not row:
                continue
            players = sorted(json.loads(row["players_json"]), key=lambda x: -x["prob"])
            home, away = g.get("home_team"), g.get("away_team")
            day = str(row["game_date"]).replace("-", "")
            recs = nba.season_records_by_name(con, [x["name"] for x in players], day)
            starters = lambda team: [{"player_id": x["id"], "name": x["name"], "team": x["team"]}  # noqa: E731
                                     for x in players if x["team"] == team]
            jumpers = {home: row["home_jumper"], away: row["away_jumper"]}
            tip_home = row["tip_home"]
            made.append({
                "game_id": g["game_id"], "matchup": row["matchup"], "start": g.get("start", ""),
                "label": row["lineup_type"], "updated": False, "from_log": True,
                "picks": [(x["name"], x["team"], x["prob"], pm.fair_american(x["prob"])) for x in players[:5]],
                "season": {x["name"]: (recs.get(x["name"]) or {}).get("fb", (0, 0)) for x in players},
                "tip": {home: tip_home, away: None if tip_home is None else 1 - tip_home} if tip_home is not None else {},
                "jumpers": jumpers,
                "tip_record": {t: (recs.get(n) or {}).get("tips", (0, 0)) for t, n in jumpers.items() if n},
                "first_team": None, "home": home, "away": away,
                "home_starters": starters(home), "away_starters": starters(away), "injuries": {}, "notes": [],
                "probs": [[x["name"], x["team"], x["prob"]] for x in players],
                **_saved_odds(con, g["game_id"], players),
            })
    finally:
        con.close()
    return dict(result, cards=result.get("cards", []) + made)


def day_cards(day: str) -> dict:
    """A past day for the page's "Yesterday" view: every game we picked that day, rebuilt from the picks logged before
    tip-off (with the last saved FanDuel line), plus who scored first, our rank for him and the final score.
    day = 'YYYYMMDD'. {"date", "cards", "games", "hits", "graded"}."""
    import requests
    import nba
    from datetime import datetime
    con = nba.db()
    try:
        ids = [r[0] for r in con.execute(
            "SELECT game_id FROM pick_log WHERE REPLACE(game_date, '-', '') = ? GROUP BY game_id ORDER BY MIN(id)", (day,))]
        scorer = {str(g): n for g, n in con.execute(
            f"SELECT game_id, scorer_name FROM first_baskets WHERE game_id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
    finally:
        con.close()
    finals = {}
    try:                                                   # final scores + start times (one ESPN request)
        ev = requests.get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
                          params={"dates": day}, timeout=20).json().get("events", [])
        for e in ev:
            comp = e["competitions"][0]
            sc = {c["homeAway"]: c.get("score") for c in comp["competitors"]}
            finals[str(e["id"])] = {"home_score": sc.get("home"), "away_score": sc.get("away"),
                                    "state": e["status"]["type"]["state"], "detail": e["status"]["type"].get("shortDetail", "")}
    except Exception:  # noqa: BLE001 — scores are extra
        pass
    sched = []
    for gid in ids:
        con = nba.db()
        try:
            m = con.execute("SELECT matchup FROM pick_log WHERE game_id = ? ORDER BY id DESC LIMIT 1", (gid,)).fetchone()[0]
        finally:
            con.close()
        away, home = [x.strip() for x in m.split("@")]
        sched.append({"game_id": gid, "away_team": away, "home_team": home, "start": finals.get(str(gid), {}).get("detail", "")})
    res = add_logged_cards({"cards": [], "schedule": sched})
    from starters import norm_name
    games, hits, graded = [], 0, 0
    for c in res["cards"]:
        gid = str(c["game_id"]); who = scorer.get(gid)
        if not who and finals.get(gid, {}).get("state") in ("in", "post"):     # not filed yet: ESPN's play-by-play
            try:
                import nba
                with nba._quiet([]):
                    fb = nba.first_basket(nba.get_pbp(gid))
                who = fb["playerName"] if fb else None
            except Exception:  # noqa: BLE001
                who = None
        order = [n for n, *_ in c.get("probs", [])]
        rank = next((i + 1 for i, n in enumerate(order) if who and norm_name(n) == norm_name(who)), None)
        if who:
            graded += 1; hits += rank == 1
        games.append({"game_id": gid, "matchup": c["matchup"], "away": c["away"], "home": c["home"],
                      "first_basket": who, "rank": rank, **finals.get(gid, {})})
    label = datetime.strptime(day, "%Y%m%d").strftime("%A, %B %-d")
    return {"date": label, "cards": res["cards"], "games": games, "hits": hits, "graded": graded}


def keep_todays_cards(result: dict, path: Path = CARDS_FILE) -> dict:
    """Games that already tipped off are skipped by later runs (the model is pre-game only), so their picks card
    would vanish. Keep every card made today: a new run's card replaces that game's old one; the others stay."""
    try:
        saved = json.loads(path.read_text())
    except (OSError, ValueError):
        saved = {}
    old = saved.get("cards", []) if saved.get("date") == result.get("date") else []
    best = {}                                     # one saved card per game (a full card beats one rebuilt from the log)
    for c in old:
        k = c.get("matchup")
        if k not in best or (best[k].get("from_log") and not c.get("from_log")):
            best[k] = c
    old = list(best.values())
    # cards are matched by matchup ("DET @ NO"): the model's own cards carry no game id
    real_keys = {c.get("matchup") for c in old if not c.get("from_log")}
    # a fresh card replaces that game's saved one; a card rebuilt from the log never replaces a real pre-game card,
    # but does replace an older rebuilt one (it may now carry more, e.g. the saved odds)
    fresh = [c for c in result.get("cards", []) if not (c.get("from_log") and c.get("matchup") in real_keys)]
    new_keys = {c.get("matchup") for c in fresh}
    kept = [dict(c, earlier=True) for c in old if c.get("matchup") not in new_keys]
    cards = [dict(c, earlier=bool(c.get("from_log"))) for c in fresh] + kept
    order = {f"{g.get('away_team')} @ {g.get('home_team')}": i for i, g in enumerate(result.get("schedule", []))}
    cards.sort(key=lambda c: order.get(c.get("matchup"), len(order)))
    have = {c.get("matchup") for c in cards}
    result = dict(result, cards=cards,
                  skipped=[x for x in result.get("skipped", []) if x.get("matchup") not in have])
    try:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(to_json({"date": result.get("date"), "cards": cards}))
        os.replace(tmp, path)
    except OSError:
        pass
    return result


# ──────────────────────────────────────────────
# LIVE FANDUEL ODDS: the page asks here when it opens and every ~30 s (browsers can't ask FanDuel directly)
# ──────────────────────────────────────────────
FD_CACHE_SECONDS = 20                                   # everyone looking within 20 s shares one FanDuel request
_fd_cache: dict = {}
_fd_lock = threading.Lock()


def fd_relay(fd_path: str, query: dict):
    """/api/fd/prices?ids=<market ids>: FanDuel's current First Basket prices (fanduel_odds.fetch_prices).
    -> (status, JSON bytes). Nothing else is passed through."""
    import requests
    import fanduel_odds as fd
    if fd_path != "prices":
        return 404, b'{"error": "not found"}'
    ids = sorted({i for i in (query.get("ids") or [""])[0].split(",") if i and len(i) < 40})[:20]
    key = ",".join(ids)
    with _fd_lock:
        hit = _fd_cache.get(key)
        if hit and time.time() - hit[0] < FD_CACHE_SECONDS:
            return 200, hit[1]
    try:
        body = to_json(fd.fetch_prices(ids))
    except (requests.RequestException, ValueError) as e:
        return 502, to_json({"error": f"FanDuel unreachable: {type(e).__name__}"})
    with _fd_lock:
        if len(_fd_cache) > 200:
            _fd_cache.clear()
        _fd_cache[key] = (time.time(), body)
    return 200, body


def to_json(obj) -> bytes:
    """JSON that tolerates numpy numbers (the model's probabilities)."""
    return json.dumps(obj, default=lambda o: o.item() if hasattr(o, "item") else str(o)).encode()


# ──────────────────────────────────────────────
# JOB: one prediction run at a time, in the background
# ──────────────────────────────────────────────
class Job:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = {"running": False, "step": "", "error": "", "finished_at": None, "result": None}

    def start(self, teams: set) -> bool:
        with self.lock:
            if self.state["running"]:
                return False
            self.state.update(running=True, step="Starting", error="")
        threading.Thread(target=self._run, args=(teams,), daemon=True).start()
        return True

    def _step(self, msg: str):
        self.state["step"] = msg

    def _run(self, teams: set):
        try:
            import nba
            import starters as st
            # Each click is a fresh look: forget what --watch mode remembers between passes
            nba._SEEN.clear()
            nba._DONE.clear()
            st._FEED_CACHE.clear(); st._SB_CACHE.clear()
            out = Collector(self._step)
            nba.predict_today(first_pass=True, teams=teams, ui=out)
            self.state["result"] = keep_todays_cards(add_logged_cards(out.result))
            threading.Thread(target=BOARD.refresh, daemon=True).start()   # new picks -> board knows them
        except Exception as e:  # noqa: BLE001 — any failure is shown on the page instead of lost
            traceback.print_exc()
            self.state["error"] = f"{type(e).__name__}: {e}"
        finally:
            self.state.update(running=False, step="", finished_at=time.strftime("%-I:%M %p"))


# ──────────────────────────────────────────────
# DATA VIEW: the CSV and my_predictions.xlsx as tabs in the app
# ──────────────────────────────────────────────
DATA_TABS = ("scorecard", "picks", "lineups", "games")
LINEUP_LABELS = ["Date", "Game", "Team", "NBA.com early lineup (Expected)", "NBA.com final lineup (Confirmed)",
                 "Who actually started", "Early lineup right?", "Final lineup right?"]
GAME_LABELS = {"game_start": "Date", "away_team": "Away", "home_team": "Home", "tip_winner": "Tip won by",
               "tip_winner_player_name": "Tip winner", "tip_loser_player_name": "Tip loser",
               "first_basket_scorer": "First basket", "scorer_team": "Team", "shot_type": "How", "clock": "Clock"}


DATA_FILES = ("nba_first_basket.db", "first_basket_data.csv", "my_predictions.xlsx")


def data_version() -> str:
    """Changes whenever the database, first_basket_data.csv or my_predictions.xlsx is updated (latest file time), so
    the Data view can reload by itself. The tabs read the database the two files are written from."""
    base = Path(__file__).parent
    times = [(base / f).stat().st_mtime_ns for f in DATA_FILES if (base / f).exists()]
    return str(max(times)) if times else "0"


def data_tab(tab: str) -> dict:
    """One tab of the Data view: {"tab", "columns": [{"key", "label", "yesno"}], "rows": [[...]]}.
    scorecard / picks / lineups are the my_predictions.xlsx tabs (same functions as the file);
    games is first_basket_data.csv (newest first, without the id columns)."""
    import nba
    import picks
    con = nba.db()
    if tab == "scorecard":
        return {"tab": tab, "version": data_version(), "lines": picks.scorecard(picks.prediction_rows(con),
                                                       today=time.strftime("%m/%d/%y"))}
    if tab == "picks":
        rows = picks.prediction_rows(con)
        cols = [{"key": k, "label": picks.LABELS[k], "yesno": k in picks.YES_NO} for k in picks.COLUMNS]
        return {"tab": tab, "version": data_version(), "columns": cols, "rows": [[r[k] for k in picks.COLUMNS] for r in rows]}
    if tab == "lineups":
        cols = [{"key": f"c{i}", "label": label, "yesno": i >= 6} for i, label in enumerate(LINEUP_LABELS)]
        return {"tab": tab, "version": data_version(), "columns": cols, "rows": picks.lineup_rows(con)}
    if tab == "games":
        keep = [nba.CSV_HEADER.index(k) for k in GAME_LABELS]
        cols = [{"key": k, "label": GAME_LABELS[k], "yesno": False} for k in GAME_LABELS]
        rows = [[r[i] for i in keep] for r in reversed(nba.csv_rows(con))]
        return {"tab": tab, "version": data_version(), "columns": cols, "rows": rows}
    raise ValueError(f"unknown tab {tab!r} (use one of {', '.join(DATA_TABS)})")


def track_record() -> dict:
    import ev
    import nba
    import picks
    s = picks.summary(nba.db())
    paper = ev.paper_results(nba.db())
    paper["rows"] = paper["rows"][:5]
    clv = ev.clv_results(nba.db())
    clv.pop("rows", None)
    return {"final": s["final"], "overall": s["overall"], "last_season": s["last_season"], "by_type": s["by_type"],
            "recent": s["games"][:12],
            "paper": paper, "clv": clv, "market": ev.market_results(nba.db())}


GAME_KEYS = ("game_id", "start", "start_utc", "home_team", "away_team", "status", "home_score", "away_score",
             "detail")


def latest_picks(con, game_ids) -> dict:
    """game_id -> our latest logged pick {id, name, team, prob, top3 [names], home_jumper, away_jumper, tip_home}
    (the pick that counts)."""
    import picks
    picks.ensure_table(con)
    out = {}
    for gid in game_ids:
        row = con.execute("""SELECT pick_player_id, pick_player_name, pick_team, pick_prob, players_json,
                                    home_jumper, away_jumper, tip_home
                             FROM pick_log WHERE game_id = ? ORDER BY id DESC LIMIT 1""", (gid,)).fetchone()
        if row:
            ranked = [p["name"] for p in json.loads(row[4])]          # every starter, most likely first
            out[gid] = {"id": str(row[0]), "name": row[1], "team": row[2], "prob": row[3], "top3": ranked[:3],
                        "ranked": ranked,
                        "home_jumper": row[5], "away_jumper": row[6], "tip_home": row[7]}
    return out


def result_of(first: dict, pick: dict):
    """'hit' (our #1 scored first), 'top3', 'miss', or None when either side is unknown."""
    if not first or not pick:
        return None
    if first["id"] == pick["id"]:
        return "hit"
    from starters import norm_name
    return "top3" if norm_name(first["name"]) in {norm_name(n) for n in pick["top3"]} else "miss"


def rank_of(first: dict, pick: dict):
    """Where the first scorer was on our list: 1 = our pick, 2 = our #2, … None = not on it (or unknown)."""
    if not first or not pick:
        return None
    if first["id"] == pick["id"]:
        return 1
    from starters import norm_name
    names = [norm_name(n) for n in pick.get("ranked") or pick.get("top3") or []]
    who = norm_name(first["name"])
    return names.index(who) + 1 if who in names else None


class LiveBoard:
    """Today's scoreboard, refreshed in the background every `interval` seconds: scores, game clock, and for
    games that have started, who scored the first basket and whether it was our pick."""

    def __init__(self, interval: int = 60):
        self.interval = interval
        self.data = {"date": "", "games": [], "updated": None}
        self._first = {}                       # game_id -> first basket {id, name, team} (never changes once found)
        self._lock = threading.Lock()

    def first_basket(self, game_id: str):
        if game_id not in self._first:
            import nba
            with nba._quiet([]):
                fb = nba.first_basket(nba.get_pbp(game_id))
            if fb:
                self._first[game_id] = {"id": str(fb["personId"]), "name": fb["playerName"], "team": fb["teamTricode"]}
        return self._first.get(game_id)

    def refresh(self):
        import nba
        with self._lock:
            with nba._quiet([]):
                games = [{k: g.get(k) for k in GAME_KEYS} for g in nba.get_todays_games()]
            con = nba.db()
            mine = latest_picks(con, [g["game_id"] for g in games])
            # real records this season for each pick and jumper: first baskets / games, tips won / taken
            names = [n for p in mine.values() for n in (p["name"], p.get("away_jumper"), p.get("home_jumper"))]
            recs = nba.season_records_by_name(con, names, date.today().strftime("%Y%m%d"))
            for g in games:
                g["our_pick"] = mine.get(g["game_id"])
                if g["our_pick"]:
                    op = g["our_pick"]
                    g["records"] = {k: recs.get(op.get(k) or "") for k in ("name", "away_jumper", "home_jumper")}
                g["first_basket"] = self.first_basket(g["game_id"]) if g["status"] != 1 else None
                g["result"] = result_of(g["first_basket"], g["our_pick"])
                g["rank"] = rank_of(g["first_basket"], g["our_pick"])
            self.data = {"date": date.today().strftime("%A, %B %-d"), "games": games,
                         "updated": time.strftime("%-I:%M %p")}

    def snapshot(self) -> dict:
        if self.data["updated"] is None:       # first visit before the background loop has run
            self.refresh()
        return self.data

    def run_forever(self):
        while True:
            try:
                self.refresh()
            except Exception:  # noqa: BLE001 — a failed refresh is retried next minute
                traceback.print_exc()
            time.sleep(self.interval)


# ──────────────────────────────────────────────
# WEB SERVER
# ──────────────────────────────────────────────
JOB = Job()
BOARD = LiveBoard()
LOGIN_PAGE = Path(__file__).parent / "gui" / "login.html"
COOKIE = "fb_session"
# Requests that came in through a sharing link (Cloudflare / ngrok / a proxy) carry one of these headers
FORWARDED = ("X-Forwarded-For", "Cf-Connecting-Ip", "X-Real-Ip", "Forwarded", "Ngrok-Agent-Ips")


class AutoUpdater:
    """Keeps everything current with nobody clicking anything (viewers can't press "Get picks"):
      • once at start-up;
      • every `minutes` on game days, from 10 AM ET until the last game has started (lineups, injuries, odds);
      • every night at 4 AM ET, game day or not: files last night's results, refreshes the spreadsheet and CSV,
        retrains the model when there is new data.
    Each run is the same update as the "Get picks" button (sync_data, then today's picks if any)."""

    NIGHTLY_HOUR = 4

    def __init__(self, minutes: int = 30):
        self.every = minutes * 60
        self.last = 0.0
        self.last_nightly = None             # the ET date the nightly update last ran

    @staticmethod
    def window_open(now_et, games) -> bool:
        upcoming = [g for g in games if g.get("status") == 1]
        return bool(upcoming) and now_et.hour >= 10

    def due(self, now_et, games, now: float) -> bool:
        return self.window_open(now_et, games) and (self.last == 0 or now - self.last >= self.every)

    def nightly_due(self, now_et) -> bool:
        return now_et.hour == self.NIGHTLY_HOUR and self.last_nightly != now_et.date()

    def run_forever(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        time.sleep(5)
        first = True
        while True:
            try:
                games = BOARD.snapshot()["games"]
                now, now_et = time.time(), datetime.now(ZoneInfo("America/New_York"))
                nightly = self.nightly_due(now_et)
                if not JOB.state["running"] and (first or nightly or self.due(now_et, games, now)):
                    if JOB.start(set()):
                        self.last = now
                        if nightly:
                            self.last_nightly = now_et.date()
                    first = False
            except Exception:  # noqa: BLE001 — try again next minute
                traceback.print_exc()
            time.sleep(60)


def _esc(s: str) -> str:
    import html
    return html.escape(s or "", quote=True)


class Handler(BaseHTTPRequestHandler):
    # ── responses ──
    def _send(self, code: int, body: bytes, kind: str = "application/json", headers: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, where: str, headers: dict | None = None):
        self._send(303, b"", "text/plain", {"Location": where, **(headers or {})})

    def _login_page(self, error: str = "", user: str = "", code: int = 200):
        page = LOGIN_PAGE.read_text().replace("{{ERROR}}", _esc(error)).replace("{{USER}}", _esc(user))
        self._send(code, page.encode(), "text/html; charset=utf-8")

    # ── who is asking ──
    def _from_outside(self) -> bool:
        return any(self.headers.get(h) for h in FORWARDED)

    def _client_ip(self) -> str:
        for h in ("Cf-Connecting-Ip", "X-Real-Ip"):
            if self.headers.get(h):
                return self.headers[h].strip()
        xff = self.headers.get("X-Forwarded-For")
        return xff.split(",")[0].strip() if xff else self.client_address[0]

    def _token(self) -> str:
        from http.cookies import SimpleCookie
        c = SimpleCookie()
        try:
            c.load(self.headers.get("Cookie") or "")
        except Exception:  # noqa: BLE001 — a malformed cookie is just "not logged in"
            return ""
        return c[COOKIE].value if COOKIE in c else ""

    def _who(self):
        """{"user", "role"} for this request; None = must log in. Without accounts: owner, but only on this Mac."""
        if not accounts.enabled():
            return None if self._from_outside() else {"user": "", "role": "owner"}
        return accounts.session(self._token())

    def _gate(self, path: str):
        """None if allowed; otherwise sends the right refusal and returns True."""
        if not accounts.enabled() and self._from_outside():
            self._send(403, b"Sharing is off: create an account first (python3 gui.py add-user NAME --owner).",
                       "text/plain; charset=utf-8")
            return True
        if self._who() is None:
            if path.startswith("/api/"):
                self._send(401, b'{"error": "log in first"}')
            else:
                self._redirect("/login")
            return True
        return None

    # ── GET ──
    def do_GET(self):
        try:
            from urllib.parse import parse_qs, urlparse
            path = urlparse(self.path).path
            if path == "/login":
                if not accounts.enabled():
                    return self._redirect("/")
                return self._redirect("/") if self._who() else self._login_page()
            if path == "/logout":
                accounts.end_session(self._token())
                return self._redirect("/login" if accounts.enabled() else "/",
                                      {"Set-Cookie": f"{COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax"})
            if self._gate(path):
                return
            who = self._who()
            if path in ("/", "/index.html"):
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/me":
                self._send(200, to_json({"user": who["user"], "role": who["role"], "logins": accounts.enabled()}))
            elif path == "/api/status":
                self._send(200, to_json(JOB.state))
            elif path == "/api/games":
                self._send(200, to_json(BOARD.snapshot()))
            elif path == "/api/data_version":
                self._send(200, to_json({"version": data_version()}))
            elif path == "/api/data":
                tab = (parse_qs(urlparse(self.path).query).get("tab") or ["picks"])[0]
                if tab not in DATA_TABS:
                    self._send(400, to_json({"error": f"unknown tab {tab!r}"}))
                else:
                    self._send(200, to_json(data_tab(tab)))
            elif path == "/api/yesterday":                  # the page's "Yesterday" view
                from datetime import datetime as _dt, timedelta
                from zoneinfo import ZoneInfo
                y = (_dt.now(ZoneInfo("America/New_York")) - timedelta(days=1)).strftime("%Y%m%d")
                self._send(200, to_json(day_cards(y)))
            elif path == "/api/record":
                self._send(200, to_json(track_record()))
            elif path == "/api/bets":                       # my bets (kept on the relay, see bets.py)
                import bets
                self._send(200, to_json({"user": self._bets_user(), "bets": bets.load(self._bets_user())}))
            elif path == "/api/results":                    # first scorer of recent games: settles older bets
                import bets
                import nba
                self._send(200, to_json(bets.results(nba.db())))
            elif path.startswith("/api/fd/"):                # live FanDuel odds for the page (see fd_relay)
                code, body = fd_relay(path[len("/api/fd/"):], parse_qs(urlparse(self.path).query))
                self._send(code, body)
            else:                                    # nothing else is ever served: no model files, no code
                self._send(404, b'{"error": "not found"}')
        except Exception as e:  # noqa: BLE001 — report any failure to the page as a 500
            traceback.print_exc()
            self._send(500, to_json({"error": f"{type(e).__name__}: {e}"}))

    # ── POST ──
    def do_POST(self):
        from urllib.parse import urlparse
        path = urlparse(self.path).path
        length = min(int(self.headers.get("Content-Length") or 0), 10_000)
        raw = self.rfile.read(length) if length else b""
        if path == "/login":
            return self._login(raw)
        if self._gate(path):
            return
        if path in ("/api/bets", "/api/bets/delete"):      # anyone logged in keeps their own bets
            return self._bets_post(path, raw)
        if path != "/api/predict":
            return self._send(404, b'{"error": "not found"}')
        if self._who()["role"] != "owner":          # viewers never run the model
            return self._send(403, b'{"error": "view-only account"}')
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return self._send(400, b'{"error": "bad JSON"}')
        import nba
        teams = nba.parse_teams(body.get("teams") or [])
        started = JOB.start(teams)
        self._send(202 if started else 409, to_json({"started": started}))

    def _bets_user(self) -> str:
        import bets
        return (self._who() or {}).get("user") or bets.OWNER

    def _bets_post(self, path: str, raw: bytes):
        import bets
        try:
            body = json.loads(raw or b"{}")
            out = bets.remove(self._bets_user(), str(body.get("id") or "")) if path.endswith("/delete") \
                else bets.add(self._bets_user(), body)
        except ValueError as e:
            return self._send(400, to_json({"error": str(e)}))
        except Exception as e:  # noqa: BLE001 — relay down / offline
            traceback.print_exc()
            return self._send(502, to_json({"error": f"couldn't save right now ({type(e).__name__}) — try again"}))
        self._send(200, to_json({"user": self._bets_user(), "bets": out}))

    def _login(self, raw: bytes):
        from urllib.parse import parse_qs
        if not accounts.enabled():
            return self._redirect("/")
        form = parse_qs(raw.decode("utf-8", "replace"))
        user = (form.get("username") or [""])[0].strip().lower()[:64]
        password = (form.get("password") or [""])[0][:256]
        keys = (f"user:{user}", f"ip:{self._client_ip()}")
        wait = accounts.locked_for(*keys)
        if wait:
            return self._login_page(f"Too many wrong tries — wait {wait // 60 + 1} min and try again.", user, 429)
        role = accounts.verify(user, password)
        if not role:
            accounts.record_failure(*keys)
            return self._login_page("Wrong username or password.", user, 401)
        accounts.clear_failures(*keys)
        token = accounts.new_session(user, role)
        https = (self.headers.get("X-Forwarded-Proto") or "").lower() == "https"
        cookie = (f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={accounts.SESSION_SECONDS}"
                  + ("; Secure" if https else ""))
        self._redirect("/", {"Set-Cookie": cookie})

    def log_message(self, fmt, *args):          # keep the terminal quiet
        pass


def manage_accounts(argv) -> int:
    """python3 gui.py add-user NAME [--owner] | remove-user NAME | users"""
    import getpass
    cmd = argv[0]
    if cmd == "users":
        users = accounts.load_users()
        if not users:
            print("No accounts: logins are off and the app only opens on this Mac.")
        for name, u in sorted(users.items()):
            print(f"  {name:20s} {u['role']:7s} created {u.get('created', '?')}")
        return 0
    if len(argv) < 2:
        print(f"usage: python3 gui.py {cmd} NAME" + (" [--owner]" if cmd == "add-user" else ""))
        return 1
    name = argv[1]
    if cmd == "remove-user":
        print(f"Removed {name}." if accounts.remove_user(name) else f"No account called {name}.")
        return 0
    role = "owner" if "--owner" in argv else "viewer"
    if not accounts.enabled() and role != "owner":
        print("Tip: make your own account first with --owner, or you'll be locked out of 'Get picks'.")
    pw = getpass.getpass(f"Password for {name}: ")
    if pw != getpass.getpass("Type it again: "):
        print("The two passwords don't match. Nothing saved.")
        return 1
    try:
        name = accounts.add_user(name, pw, role)
    except ValueError as e:
        print(f"Not saved: {e}")
        return 1
    print(f"Saved: {name} ({role}). Logins are now ON.")
    return 0


def main(argv) -> int:
    if argv and argv[0] in ("add-user", "remove-user", "users"):
        return manage_accounts(argv)
    port = int(argv[argv.index("--port") + 1]) if "--port" in argv else PORT
    try:
        server = ThreadingHTTPServer((HOST, port), Handler)
    except OSError:
        print(f"Port {port} is busy — the GUI may already be running: open http://{HOST}:{port}\n"
              f"(or start another copy with: python3 gui.py --port {port + 1})")
        return 1
    url = f"http://{HOST}:{port}"
    print(f"🏀 First Basket picks: {url}   (Ctrl-C to stop)")
    n = len(accounts.load_users())
    print(f"   Logins: ON ({n} account{'s' if n != 1 else ''})" if n else
          "   Logins: OFF — only this Mac can open it (add accounts to share: python3 gui.py add-user NAME --owner)")
    threading.Thread(target=BOARD.run_forever, daemon=True).start()
    if "--no-auto" not in argv:
        print("   Updates by itself: every 30 min on game days + every night at 4 AM ET (turn off with --no-auto)")
        threading.Thread(target=AutoUpdater().run_forever, daemon=True).start()
    if "--no-open" not in argv:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
