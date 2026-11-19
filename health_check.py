"""
Health check: looks at everything that's supposed to update by itself and raises an alert when something isn't.

Runs on GitHub four times a day (.github/workflows/health-check.yml), so it works with the Mac off. When it finds a
problem it opens an issue titled "⚠️ First Basket needs attention" in this repo — GitHub emails it and shows it in
the GitHub app. When everything is fine again it closes that issue by itself. You can also run it any time:

    python3 health_check.py            # prints what it finds (needs the gh command, signed in)

What it checks:
  • the website opens and its data file is readable
  • the Cloudflare odds relay answers, and its timer is still allowed to start the live job
  • the nightly update saved new data this morning
  • the live job ran recently during game hours and its last run didn't fail
  • the Mac's own report (sync_from_cloud.py sends it every 30 min): app running, libraries present, code and data
    syncing. A Mac that is simply off is fine — only problems it reports raise an alert.
"""
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
REPO = "SolomonKhalili/First_Basket"            # private: code, data ("state" branch), alerts
SITE_REPO = "SolomonKhalili/first-basket"       # public website + live job
SITE = "https://solomonkhalili.github.io/first-basket/"
RELAY = "https://first-basket-odds.solomonkhalili.workers.dev"
TITLE = "⚠️ First Basket needs attention"
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

    runs = f.get("live_runs") or []                      # newest first: {"created": dt, "status", "conclusion"}
    done = [r for r in runs if r["status"] == "completed"]
    if done and done[0]["conclusion"] not in ("success", "skipped", "cancelled"):
        p.append(f"The live job's last run failed ({done[0]['conclusion']}, {_ago(now, done[0]['created'])}).")
    if 10 <= now.hour <= 23:
        last = max((r["created"] for r in runs), default=None)
        if last is None or now - last > timedelta(minutes=LIVE_GAP_MIN):
            p.append(f"The live job hasn't started in {_ago(now, last) if last else 'a long time'} — "
                     "GitHub's timer and the Cloudflare backup both seem stuck.")

    mac = f.get("mac")                                   # {"at": dt, "problems": [...]} or None
    if mac and mac.get("problems") and now - mac["at"] < timedelta(days=2):
        p += [f"Mac: {x}" for x in mac["problems"]]
    return p


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
    except (subprocess.CalledProcessError, ValueError):
        f["live_runs"] = []
    raw = os.environ.get("MAC_HEALTH") or ""
    if not raw:
        try:
            raw = _gh("variable", "get", "MAC_HEALTH", "-R", REPO)
        except subprocess.CalledProcessError:
            raw = ""
    try:
        m = json.loads(raw)
        f["mac"] = {"at": _when(m["at"]), "problems": m.get("problems", [])}
    except (ValueError, KeyError, TypeError):
        f["mac"] = None
    return f


# ── raising / clearing the alert ──────────────────────────────────────────────
def alert(problems: list, now: datetime) -> str:
    """Open, update or close the alert issue. Returns a line for the log."""
    found = json.loads(_gh("issue", "list", "-R", REPO, "--state", "open", "--search", f'in:title "{TITLE}"',
                           "--json", "number,body"))
    issue = found[0] if found else None
    stamp = now.strftime("%a %b %-d, %-I:%M %p ET")
    body = ("The automatic health check found something that isn't updating by itself:\n\n"
            + "\n".join(f"- {x}" for x in problems)
            + f"\n\nChecked {stamp}. This closes by itself once everything is working again. "
              "To get it fixed, open Claude and say: *fix the health check alert*.")
    if problems and not issue:
        _gh("issue", "create", "-R", REPO, "--title", TITLE, "--body", body)
        return "alert opened"
    if problems and issue:
        old = {ln for ln in issue["body"].splitlines() if ln.startswith("- ")}
        new = {f"- {x}" for x in problems}
        _gh("issue", "edit", str(issue["number"]), "-R", REPO, "--body", body)
        if new - old:                                   # something new broke: a comment sends a fresh email
            _gh("issue", "comment", str(issue["number"]), "-R", REPO, "--body",
                "New problem:\n\n" + "\n".join(sorted(new - old)))
        return "alert updated"
    if issue:
        _gh("issue", "close", str(issue["number"]), "-R", REPO, "--comment", f"✅ Everything is working again ({stamp}).")
        return "alert closed"
    return "all good"


def main() -> int:
    now = datetime.now(ET)
    problems = find_problems(now, gather())
    for x in problems or ["Everything is updating by itself."]:
        print(("⚠️  " if problems else "✅ ") + x)
    if "--report" in sys.argv:
        print(alert(problems, now))
    return 0


if __name__ == "__main__":
    sys.exit(main())
