"""
Keeps this Mac's NBA folder up to date with the cloud copy and with the code on GitHub's main (changes made from the
phone or a cloud session arrive here by themselves; see update_code) (GitHub keeps the "live" data — see
.github/workflows/update-site.yml). Runs by itself every 30 minutes and at login (launcher/com.firstbasket.sync.plist);
you can also run it any time:

    python3 sync_from_cloud.py

Backup: if GitHub's automatic update is late (over 70 min on game days, or no nightly run by 5 AM), it also starts the
cloud update (see nudge_cloud). Otherwise it only DOWNLOADS: the newest database, first_basket_data.csv, my_predictions.xlsx and model files from the private
repo's "state" branch replace the copies here. Nothing is uploaded. Only files that changed are touched, and each is
swapped in whole (written to a temporary file, then renamed), so the app never reads a half-written file.
Note: anything the Mac app saved locally since the last cloud update is replaced by the cloud copy — the cloud is
the main copy now.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES = ["nba_first_basket.db", "first_basket_data.csv", "my_predictions.xlsx", "pregame_model.json", "X_train.npy",
         "y_train.npy", "groups.npy", "game_ids.json", "meta.json", "feature_names.json", "player_game_logs.pkl", "todays_cards.json"]
LOG = HERE / "launcher" / "sync.log"


def git(*args, binary: bool = False):
    r = subprocess.run(["git", *args], cwd=HERE, capture_output=True, timeout=180)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode(errors="replace").strip()[:300])
    return r.stdout if binary else r.stdout.decode().strip()


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(exist_ok=True)
        if LOG.exists() and LOG.stat().st_size > 200_000:          # keep the log small
            LOG.write_text("\n".join(LOG.read_text().splitlines()[-500:]) + "\n")
        with LOG.open("a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def sync() -> list:
    """Download changed files from the cloud. Returns the names of the files that were updated."""
    git("fetch", "-q", "origin", "state")
    changed = []
    for name in FILES:
        try:
            remote = git("rev-parse", f"FETCH_HEAD:{name}")
        except RuntimeError:
            continue                                                  # not in the cloud copy
        local = HERE / name
        if local.exists() and git("hash-object", str(local)) == remote:
            continue                                                  # already the same
        tmp = local.with_name(f".{name}.downloading")
        tmp.write_bytes(git("cat-file", "blob", remote, binary=True))
        os.replace(tmp, local)                                        # swap in whole
        changed.append(name)
    return changed


# ──────────────────────────────────────────────
# CODE: changes saved to GitHub from anywhere (a phone or cloud session) reach this Mac by themselves.
# ──────────────────────────────────────────────
def code_update_plan(up_to_date: bool, mac_has_own_commits: bool, incoming: set, dirty: set,
                     same: frozenset = frozenset()) -> tuple:
    """Pure (testable). Should this Mac's NBA folder take GitHub's newest `main`?
    incoming = files GitHub changed; dirty = files edited on this Mac but not saved to GitHub yet; same = dirty files
    whose content here already equals GitHub's (nothing to lose).
    -> ("none",) / ("skip", reason) / ("update", data files to reset first).
    Never overwrites work in progress: a code file edited here that GitHub also changed means skip. Data files
    (FILES) are fine to reset, because sync() downloads the cloud's newest copy right after."""
    if up_to_date:
        return ("none",)
    if mac_has_own_commits:
        return ("skip", "this Mac has changes GitHub doesn't have yet")
    clash = sorted(f for f in incoming & dirty if f not in FILES and f not in same)
    if clash:
        return ("skip", f"edited here and on GitHub: {', '.join(clash[:3])}")
    return ("update", sorted(incoming & dirty))                      # reset these, then take GitHub's


def update_code() -> str:
    """Fast-forward this folder to GitHub's main when it's safe (see code_update_plan); restart the Mac app if its
    files changed. Returns a line for the log."""
    git("fetch", "-q", "origin", "main")
    head, new = git("rev-parse", "HEAD"), git("rev-parse", "FETCH_HEAD")
    base = git("merge-base", "HEAD", "FETCH_HEAD")
    incoming = set(git("diff", "--name-only", "HEAD", "FETCH_HEAD").splitlines()) if head != new else set()
    tracked_edits = set(git("diff", "--name-only", "HEAD").splitlines())   # edited here, not saved to GitHub
    untracked = set(git("ls-files", "--others", "--exclude-standard").splitlines())   # new here, never saved
    dirty = tracked_edits | untracked
    same = set()
    for f in incoming & dirty:
        try:
            if (HERE / f).exists() and git("hash-object", str(HERE / f)) == git("rev-parse", f"FETCH_HEAD:{f}"):
                same.add(f)
        except RuntimeError:
            pass                                                      # removed on GitHub: not "same"
    plan = code_update_plan(head == new, base != head, incoming, dirty, frozenset(same))
    if plan[0] == "none":
        return "code already up to date"
    if plan[0] == "skip":
        return f"code update skipped ({plan[1]})"
    for f in plan[1]:                                                 # data (cloud copy comes next) or identical
        if f in untracked:
            (HERE / f).unlink()
        else:
            git("checkout", "--", f)
    git("merge", "--ff-only", "-q", "FETCH_HEAD")
    if any(f.endswith(".py") and not f.startswith("tests/") or f.startswith("gui/") for f in incoming):
        try:                                                          # the app restarts with the new code
            subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.firstbasket.app"],
                           capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass                                                      # not a Mac / app not installed
    return f"code updated from GitHub ({len(incoming)} files)"


# ──────────────────────────────────────────────
# BACKUP: GitHub's timed runs are "best effort" (they can be late or skipped). If the cloud has gone quiet,
# this Mac starts the cloud update itself. The cloud still does the work; this only presses its Run button.
# ──────────────────────────────────────────────
REPO = "SolomonKhalili/First_Basket"
LIVE_REPO = "SolomonKhalili/first-basket"         # the public website repo, where the live job runs
STALE_MIN = 70                        # hourly updates: start one if the last run is older than this


def backup_action(now_et, last_run_et, busy: bool):
    """Pure (testable). now_et / last_run_et: New York times (last_run_et None = never ran).
    -> "nightly", "hourly" or None (nothing to do)."""
    from datetime import timedelta
    if busy:
        return None                                                   # a run is already going
    if 5 <= now_et.hour < 10:                                         # morning: did the nightly run happen?
        since = now_et.replace(hour=4, minute=0, second=0, microsecond=0)
        return "nightly" if last_run_et is None or last_run_et < since else None
    if 10 <= now_et.hour <= 23:                                       # game-day hours
        if last_run_et is None or now_et - last_run_et > timedelta(minutes=STALE_MIN):
            return "hourly"                                           # (skips itself if no games are left)
    return None


def nudge_cloud() -> None:
    import json
    from datetime import datetime
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")

    def gh(*args):
        r = subprocess.run(["gh", *args, "-R", REPO], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip()[:200])
        return r.stdout

    repo, wf, kinds = REPO, "update-site.yml", {"hourly": "hourly", "nightly": "nightly"}
    try:                                              # the live job (public website repo), once it's set up
        runs = json.loads(subprocess.run(["gh", "run", "list", "-R", LIVE_REPO, "-w", "live-picks.yml", "-L", "5",
                                          "--json", "createdAt,status"], capture_output=True, text=True,
                                         timeout=60, check=True).stdout)
        repo, wf, kinds = LIVE_REPO, "live-picks.yml", {"hourly": "live", "nightly": "once"}
    except (subprocess.CalledProcessError, ValueError):
        runs = json.loads(gh("run", "list", "-w", "update-site.yml", "-L", "5", "--json", "createdAt,status"))
    busy = any(r["status"] in ("queued", "in_progress", "waiting", "pending", "requested") for r in runs)
    last = max((datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00")).astimezone(et) for r in runs),
               default=None)
    kind = backup_action(datetime.now(et), last, busy)
    if kind:
        subprocess.run(["gh", "workflow", "run", wf, "-R", repo, "-f", f"kind={kinds[kind]}"], check=True,
                       capture_output=True, timeout=60)
        log(f"backup: the cloud hadn't updated since {last.strftime('%-I:%M %p') if last else 'ever'} — "
            f"started a {kind} cloud update")
    else:
        log(f"backup check: cloud {'is updating now' if busy else 'last updated ' + (last.strftime('%a %-I:%M %p') if last else 'never')} — OK")


# ──────────────────────────────────────────────
# HEALTH: the Mac checks itself and the cloud, and writes the result to HEALTH.md (health_check.py).
# ──────────────────────────────────────────────
LIBRARIES = ["numpy", "pandas", "scipy", "requests", "rich", "cryptography", "sportsdataverse"]
STRIKES = 3                                       # a sync/code problem must repeat this many runs (1.5 h) to count


def mac_problems(code_line: str, sync_ok: bool, missing_libs: list, app_up: bool, strikes: dict) -> list:
    """Pure (testable). strikes: running counts from earlier runs (updated in place) so one blip — Wi-Fi drop,
    GitHub hiccup — doesn't raise an alert; a missing library or a stopped app counts at once."""
    p = []
    if missing_libs:
        p.append(f"the app's Python is missing {', '.join(missing_libs)} (game data can't load)")
    if not app_up:
        p.append("the Mac app isn't running (http://127.0.0.1:8765 doesn't answer)")
    for key, bad, msg in (("code", "skipped" in code_line, f"GitHub's code isn't reaching the Mac — {code_line}"),
                          ("sync", not sync_ok, "the Mac can't download the newest data from GitHub")):
        strikes[key] = strikes.get(key, 0) + 1 if bad else 0
        if strikes[key] >= STRIKES:
            p.append(msg)
    return p


def report_health(code_line: str, sync_ok: bool) -> None:
    import importlib.util
    import json
    import urllib.request
    missing = [m for m in LIBRARIES if importlib.util.find_spec(m) is None]
    try:
        urllib.request.urlopen("http://127.0.0.1:8765/", timeout=10)
        app_up = True
    except Exception:  # noqa: BLE001
        app_up = False
    sfile = LOG.parent / "health_strikes.json"
    try:
        strikes = json.loads(sfile.read_text())
    except (OSError, ValueError):
        strikes = {}
    problems = mac_problems(code_line, sync_ok, missing, app_up, strikes)
    try:
        sfile.write_text(json.dumps(strikes))
    except OSError:
        pass
    import health_check
    found = health_check.run(problems)
    log("health: " + ("; ".join(found) if found else "all OK") + " (HEALTH.md)")


def main() -> int:
    try:
        nudge_cloud()
    except Exception as e:  # noqa: BLE001 — offline / not signed in to gh: the cloud's own schedule still runs
        log(f"backup check skipped ({type(e).__name__}: {e})")
    try:
        code_line = update_code()
    except Exception as e:  # noqa: BLE001 — offline / git busy: try again next time
        code_line = f"code update skipped ({type(e).__name__}: {e})"
    log(code_line)
    try:
        changed = sync()
        when = git("log", "-1", "--format=%cd", "--date=format:%a %-I:%M %p", "FETCH_HEAD")
        log(f"updated {', '.join(changed)} (cloud copy from {when})" if changed else f"already up to date (cloud copy from {when})")
        ok = True
    except Exception as e:  # noqa: BLE001 — offline, GitHub down, …: try again next time
        log(f"couldn't sync ({type(e).__name__}: {e}) — will try again")
        ok = False
    try:
        report_health(code_line, ok)
    except Exception as e:  # noqa: BLE001 — offline: the next run reports
        log(f"health check skipped ({type(e).__name__}: {e})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
