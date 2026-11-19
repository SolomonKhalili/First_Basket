"""
Keeps this Mac's NBA folder up to date with the cloud copy (GitHub keeps the "live" data — see
.github/workflows/update-site.yml). Runs by itself every 30 minutes and at login (launcher/com.firstbasket.sync.plist);
you can also run it any time:

    python3 sync_from_cloud.py

It only DOWNLOADS: the newest database, first_basket_data.csv, my_predictions.xlsx and model files from the private
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
         "y_train.npy", "groups.npy", "game_ids.json", "meta.json", "feature_names.json", "player_game_logs.pkl"]
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


def main() -> int:
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
