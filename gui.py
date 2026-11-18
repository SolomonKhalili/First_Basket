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
import sys
import threading
import time
import traceback
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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
            st._FEED_CACHE.clear()
            out = Collector(self._step)
            nba.predict_today(first_pass=True, teams=teams, ui=out)
            self.state["result"] = out.result
            threading.Thread(target=BOARD.refresh, daemon=True).start()   # new picks -> board knows them
        except Exception as e:  # noqa: BLE001 — any failure is shown on the page instead of lost
            traceback.print_exc()
            self.state["error"] = f"{type(e).__name__}: {e}"
        finally:
            self.state.update(running=False, step="", finished_at=time.strftime("%-I:%M %p"))


def track_record() -> dict:
    import ev
    import nba
    import picks
    s = picks.summary(nba.db())
    paper = ev.paper_results(nba.db())
    paper["rows"] = paper["rows"][:5]
    return {"final": s["final"], "by_type": s["by_type"], "recent": s["games"][:12], "paper": paper}


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
            top3 = [p["name"] for p in json.loads(row[4])[:3]]
            out[gid] = {"id": str(row[0]), "name": row[1], "team": row[2], "prob": row[3], "top3": top3,
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
            mine = latest_picks(nba.db(), [g["game_id"] for g in games])
            for g in games:
                g["our_pick"] = mine.get(g["game_id"])
                g["first_basket"] = self.first_basket(g["game_id"]) if g["status"] != 1 else None
                g["result"] = result_of(g["first_basket"], g["our_pick"])
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


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, kind: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            if self.path in ("/", "/index.html"):
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/api/status":
                self._send(200, to_json(JOB.state))
            elif self.path == "/api/games":
                self._send(200, to_json(BOARD.snapshot()))
            elif self.path == "/api/record":
                self._send(200, to_json(track_record()))
            else:
                self._send(404, b'{"error": "not found"}')
        except Exception as e:  # noqa: BLE001 — report any failure to the page as a 500
            traceback.print_exc()
            self._send(500, to_json({"error": f"{type(e).__name__}: {e}"}))

    def do_POST(self):
        if self.path != "/api/predict":
            return self._send(404, b'{"error": "not found"}')
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._send(400, b'{"error": "bad JSON"}')
        import nba
        teams = nba.parse_teams(body.get("teams") or [])
        started = JOB.start(teams)
        self._send(202 if started else 409, to_json({"started": started}))

    def log_message(self, fmt, *args):          # keep the terminal quiet
        pass


def main(argv) -> int:
    port = int(argv[argv.index("--port") + 1]) if "--port" in argv else PORT
    try:
        server = ThreadingHTTPServer((HOST, port), Handler)
    except OSError:
        print(f"Port {port} is busy — the GUI may already be running: open http://{HOST}:{port}\n"
              f"(or start another copy with: python3 gui.py --port {port + 1})")
        return 1
    url = f"http://{HOST}:{port}"
    print(f"🏀 First Basket picks: {url}   (Ctrl-C to stop)")
    threading.Thread(target=BOARD.run_forever, daemon=True).start()
    if "--no-open" not in argv:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
