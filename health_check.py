"""
Health check: looks at everything that's supposed to update by itself and writes what it finds to HEALTH.md in the
NBA folder (no emails). The Mac runs it every 30 minutes as part of sync_from_cloud.py, and Claude reads HEALTH.md
at the start of every session in this folder (.claude/settings.json), so problems get noticed and fixed. Run it any
time:

    python3 health_check.py

What it checks:
  • the website opens and its data file is readable
  • the Cloudflare odds relay answers, and its timer is still allowed to start the live job
  • the nightly update saved new data this morning
  • the live job ran recently during game hours and its last run didn't fail
  • the Mac itself (passed in by sync_from_cloud.py): app running, libraries present, code and data syncing
"""
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
REPO = "SolomonKhalili/First_Basket"            # private: code, data ("state" branch), alerts
SITE_REPO = "SolomonKhalili/first-basket"       # public website + live job
SITE = "https://solomonkhalili.github.io/first-basket/"
RELAY = "https://first-basket-odds.solomonkhalili.workers.dev"
LIVE_GAP_MIN = 90                               # live job starts every 20 min; this long with none = stuck


def _ago(now, t) -> str:
    mins = int((now - t).total_seconds() // 60)
    return f"{mins} min ago" if mins < 120 else f"{mins // 60} hours ago" if mins < 2880 else f"{mins // 1440} days ago"


def find_problems(now: datetime, f: dict) -> list:
    """Pure (testable). now: New York time. f: facts gathered by gather() — see there. -> plain-English problems."""
    p = []
    if f.get("site_status") != 200:
        p.append(f"The website isn't opening (got {f.get('site_status')}).")
    elif not f.get("site_data_ok"):
        p.append("The website opens but its picks data is missing or unreadable.")
    if f.get("relay_status") != 200:
        p.append(f"The live odds relay (Cloudflare) isn't answering (got {f.get('relay_status')}) — odds on the site "
                 "won't refresh.")
    elif "permission: OK" not in (f.get("timer_text") or ""):
        p.append("The Cloudflare timer can't start the live job any more (its GitHub key may have expired): "
                 f"“{(f.get('timer_text') or 'no answer')[:120]}”.")

    saved = f.get("state_saved")
    if now.hour >= 6:
        since = now.replace(hour=4, minute=0, second=0, microsecond=0)
        if saved is None or saved < since:
            p.append("The nightly update didn't run this morning (data last saved "
                     f"{_ago(now, saved) if saved else 'never'}) — last night's results aren't filed.")

    runs = f.get("live_runs")                            # newest first: {"created": dt, "status", "conclusion"}
    if runs is None:
        p.append("Couldn't ask GitHub about the live job (is the `gh` command still signed in on the Mac?).")
        runs = []
    done = [r for r in runs if r["status"] == "completed"]
    if done and done[0]["conclusion"] not in ("success", "skipped", "cancelled"):
        p.append(f"The live job's last run failed ({done[0]['conclusion']}, {_ago(now, done[0]['created'])}).")
    if 10 <= now.hour <= 23 and f.get("live_runs") is not None:
        last = max((r["created"] for r in runs), default=None)
        if last is None or now - last > timedelta(minutes=LIVE_GAP_MIN):
            p.append(f"The live job hasn't started in {_ago(now, last) if last else 'a long time'} — "
                     "GitHub's timer and the Cloudflare backup both seem stuck.")

    p += f.get("data_gaps") or []                         # data_gaps(): stored games missing a piece

    mac = f.get("mac")                                   # {"at": dt, "problems": [...]} or None
    if mac and mac.get("problems") and now - mac["at"] < timedelta(days=2):
        p += [f"Mac: {x}" for x in mac["problems"]]
    return p


def data_gaps(con, today: str, season_start: str = "20260801") -> list:
    """Pure-ish (reads the database only). Added 2026-11-19 after a whole day (11/06) went missing. For this season's
    games up to 2 days ago: any day the cloud hasn't compared with ESPN's schedule, and any stored game missing its
    first basket, either team's starting five, early/first shots, or its players' box-score lines. -> problems."""
    from datetime import date as _d, timedelta as _td
    out = []
    end = (_d(int(today[:4]), int(today[4:6]), int(today[6:])) - _td(days=2)).strftime("%Y%m%d")
    try:
        checked = {k[4:] for (k,) in con.execute("SELECT key FROM audits WHERE key LIKE 'day:%'")}
    except Exception:  # noqa: BLE001 — table not made yet
        checked = set()
    days = {d for (d,) in con.execute("SELECT DISTINCT game_date FROM games WHERE game_date >= ? AND game_date <= ?",
                                      (season_start, end))}
    first = min(days) if days else end
    d, unchecked = _d(int(first[:4]), int(first[4:6]), int(first[6:])), []
    while d.strftime("%Y%m%d") <= end:
        if d.strftime("%Y%m%d") not in checked:
            unchecked.append(d.strftime("%-m/%-d"))
        d += _td(days=1)
    if unchecked:
        out.append(f"{len(unchecked)} day(s) not yet checked against ESPN's schedule for missing games "
                   f"({', '.join(unchecked[:5])}{'…' if len(unchecked) > 5 else ''}).")
    q = lambda sql: [g for (g,) in con.execute(sql, (season_start, end))]
    base = "FROM games g WHERE g.game_date >= ? AND g.game_date <= ?"
    checks = {
        "no first basket": q(f"SELECT g.game_id {base} AND g.game_id NOT IN (SELECT game_id FROM first_baskets)"),
        "a team's starting five missing": q(f"""SELECT g.game_id {base} AND (SELECT COUNT(*) FROM lineups l
                                             WHERE l.game_id = g.game_id AND l.is_starter = 1) != 10"""),
        "early shots missing": q(f"""SELECT g.game_id {base} AND (SELECT COUNT(DISTINCT team) FROM early_shots e
                                  WHERE e.game_id = g.game_id) < 2"""),
        "first shots missing": q(f"""SELECT g.game_id {base} AND (SELECT COUNT(DISTINCT team) FROM first_shots e
                                  WHERE e.game_id = g.game_id) < 2"""),
    }
    checks["a box score that doesn't add up to a full game"] = q(
        f"""SELECT g.game_id {base} AND g.game_id IN (SELECT game_id FROM lineups GROUP BY game_id, team
             HAVING SUM(minutes) < 236)""")
    for what, gids in checks.items():
        if gids:
            out.append(f"{len(gids)} game(s) this season with {what} (e.g. {', '.join(gids[:3])}).")
    try:                                                  # every player who played has a box-score line
        import pickle
        logs = pickle.load(open(HERE / "player_game_logs.pkl", "rb"))
        have = {(str(pid), x["game_id"]) for pid, lst in logs.items() for x in lst}
        miss = {g for g, p in con.execute(f"""SELECT l.game_id, l.player_id FROM lineups l JOIN games g USING(game_id)
                 WHERE l.minutes > 0 AND g.game_date >= ? AND g.game_date <= ?""", (season_start, end))
                if (str(p), g) not in have}
        if miss:
            out.append(f"{len(miss)} game(s) this season missing players' box-score lines (e.g. {', '.join(sorted(miss)[:3])}).")
    except OSError:
        pass
    return out


# ── gathering the facts (network) ─────────────────────────────────────────────
def _get(url: str, headers=None):
    try:
        h = {"User-Agent": "first-basket-health-check", **(headers or {})}   # Cloudflare blocks Python's default
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=20) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # noqa: BLE001 — down / timeout
        return type(e).__name__, ""


def _gh(*args) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60, check=True).stdout


def _when(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET)


def gather() -> dict:
    f = {}
    f["site_status"], _ = _get(SITE)
    st, body = _get(SITE + "data.json")
    try:
        f["site_data_ok"] = st == 200 and "ct" in json.loads(body)
    except ValueError:
        f["site_data_ok"] = False
    f["relay_status"], _ = _get(RELAY + "/prices", {"Origin": "https://solomonkhalili.github.io"})
    _, f["timer_text"] = _get(RELAY + "/timer-check")
    try:
        f["state_saved"] = _when(_gh("api", f"repos/{REPO}/branches/state", "--jq", ".commit.commit.committer.date").strip())
    except (subprocess.CalledProcessError, ValueError):
        f["state_saved"] = None
    try:
        runs = json.loads(_gh("run", "list", "-R", SITE_REPO, "-w", "live-picks.yml", "-L", "10",
                              "--json", "createdAt,status,conclusion"))
        f["live_runs"] = [{"created": _when(r["createdAt"]), "status": r["status"], "conclusion": r["conclusion"]}
                          for r in runs]
    except (OSError, subprocess.SubprocessError, ValueError):
        f["live_runs"] = None                            # couldn't ask GitHub
    f["mac"] = None
    try:
        import sqlite3
        con = sqlite3.connect(HERE / "nba_first_basket.db")
        f["data_gaps"] = data_gaps(con, datetime.now(ET).strftime("%Y%m%d"))
    except Exception as e:  # noqa: BLE001
        f["data_gaps"] = [f"Couldn't check the stored games for gaps ({type(e).__name__})."]
    return f


# ── writing HEALTH.md ─────────────────────────────────────────────────────────
HERE = Path(__file__).resolve().parent
OUT = HERE / "HEALTH.md"
HISTORY_KEEP = 30


def render(problems: list, now: datetime, history: list) -> str:
    """Pure (testable). history: earlier '- time — text' lines, newest first."""
    stamp = now.strftime("%a %b %-d, %-I:%M %p ET")
    head = (f"# ⚠️ PROBLEMS — First Basket needs attention\n\nChecked {stamp}:\n\n" + "\n".join(f"- {x}" for x in problems)
            if problems else f"# ✅ All good — everything is updating by itself\n\nChecked {stamp}.")
    hist = "\n".join(history[:HISTORY_KEEP]) or "(none yet)"
    return (f"{head}\n\n## Recent problems (newest first)\n\n{hist}\n\n"
            "_Written every 30 min by health_check.py (run from sync_from_cloud.py on the Mac)._\n")


def write_report(problems: list, now: datetime) -> None:
    """Update HEALTH.md; a problem is added to the history when it first appears or comes back."""
    try:
        old = OUT.read_text()
    except OSError:
        old = ""
    current = {ln[2:] for ln in old.split("## Recent problems")[0].splitlines() if ln.startswith("- ")}
    history = [ln for ln in old.split("## Recent problems")[-1].splitlines() if ln.startswith("- ")] if old else []
    stamp = now.strftime("%b %-d %-I:%M %p")
    new = [f"- {stamp} — {x}" for x in problems if x not in current]
    fixed = [f"- {stamp} — ✅ fixed: {x}" for x in sorted(current - set(problems))]
    tmp = OUT.with_name(".HEALTH.md.writing")
    tmp.write_text(render(problems, now, new + fixed + history))
    os.replace(tmp, OUT)


def run(mac_problems=None) -> list:
    """Check everything (mac_problems: what the Mac found about itself), write HEALTH.md, return the problems."""
    now = datetime.now(ET)
    f = gather()
    f["mac"] = {"at": now, "problems": mac_problems or []}
    problems = find_problems(now, f)
    write_report(problems, now)
    return problems


def main() -> int:
    problems = run()
    for x in problems or ["Everything is updating by itself."]:
        print(("⚠️  " if problems else "✅ ") + x)
    print(f"(written to {OUT.name})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
