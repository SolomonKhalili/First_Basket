"""
A simple window on the prediction model: today's first-basket picks in your web browser.

    python3 gui.py            # opens http://127.0.0.1:8765 in your browser (Ctrl-C here to stop)
    python3 gui.py --no-open  # start without opening a browser

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
        self.result["schedule"] = [{k: g.get(k) for k in ("game_id", "start", "home_team", "away_team", "status")}
                                   for g in games]

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
        except Exception as e:  # noqa: BLE001 — any failure is shown on the page instead of lost
            traceback.print_exc()
            self.state["error"] = f"{type(e).__name__}: {e}"
        finally:
            self.state.update(running=False, step="", finished_at=time.strftime("%-I:%M %p"))


def track_record() -> dict:
    import nba
    import picks
    s = picks.summary(nba.db())
    return {"final": s["final"], "by_type": s["by_type"], "recent": s["games"][:12]}


def todays_games() -> list:
    import nba
    with nba._quiet([]):
        games = nba.get_todays_games()
    return [{k: g[k] for k in ("game_id", "start", "home_team", "away_team", "status")} for g in games]


# ──────────────────────────────────────────────
# WEB SERVER
# ──────────────────────────────────────────────
JOB = Job()


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
                self._send(200, to_json({"date": date.today().strftime("%A, %B %-d"), "games": todays_games()}))
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
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"🏀 First Basket picks: {url}   (Ctrl-C to stop)")
    if "--no-open" not in argv:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
