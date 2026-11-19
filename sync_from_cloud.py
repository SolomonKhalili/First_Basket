"""
Keeps this Mac's NBA folder up to date with the cloud copy (GitHub keeps the "live" data — see
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


def main() -> int:
    try:
        nudge_cloud()
    except Exception as e:  # noqa: BLE001 — offline / not signed in to gh: the cloud's own schedule still runs
        log(f"backup check skipped ({type(e).__name__}: {e})")
    try:
        changed = sync()
    except Exception as e:  # noqa: BLE001 — offline, GitHub down, …: try again next time
        log(f"couldn't sync ({type(e).__name__}: {e}) — will try again")
        return 1
    when = git("log", "-1", "--format=%cd", "--date=format:%a %-I:%M %p", "FETCH_HEAD")
    log(f"updated {', '.join(changed)} (cloud copy from {when})" if changed else f"already up to date (cloud copy from {when})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
