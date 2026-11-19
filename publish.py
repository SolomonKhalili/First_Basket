"""
Builds the free, password-protected website (run on a schedule by GitHub — .github/workflows/update-site.yml).

    python3 publish.py --out site_out             # update everything, then write the encrypted website
    python3 publish.py --out site_out --no-update # just repackage what's already in the database
    python3 publish.py --should-run nightly|hourly # exit 0 if an update is worth running now (saves free minutes)
    python3 publish.py --live --out site_out      # game night: keep updating until every game has tipped off

Live mode (run by the public website repo's workflow, which has unlimited free minutes): every few minutes it
re-checks FanDuel's odds, NBA.com's lineups (Expected -> Confirmed) and injuries, re-runs the picks and republishes
the moment anything changes (at most every ~6 minutes — GitHub Pages' limit is about 10 publishes an hour). After
the last tip-off it stays on and files each result as its game ends (Data tabs, record, spreadsheet the same night),
then stops. Its public log only says "updated" / "no change": details go to a private log.

What it publishes: ONLY results — tonight's picks (chances, fair odds, FanDuel prices, value), the scoreboard, the
track record and the four Data tabs. Never the model, the training data or the code that makes picks.

How the lock works: the results are encrypted with a random data key (AES-256-GCM). Each friend's password
unlocks a copy of that key (PBKDF2-SHA256, 600,000 rounds); passwords themselves are never stored anywhere.
Accounts are managed with site_users.py. The data key comes from the SITE_DATA_KEY environment variable
(a GitHub secret) or ~/.config/first-basket/site_data_key on your Mac.
"""
import base64
import gzip
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
SITE_USERS = HERE / "site_users.json"
KEY_FILE = Path.home() / ".config" / "first-basket" / "site_data_key"
STATIC_PARTS = HERE / "gui" / "static_site.html"
RELAY_FILE = HERE / "website_live" / "relay_url.txt"    # the live-odds relay's address (setup_odds_relay.sh)       # login screen + the bit that replaces the server
ET = ZoneInfo("America/New_York")


# ──────────────────────────────────────────────
# KEY + ENCRYPTION
# ──────────────────────────────────────────────
def data_key() -> bytes:
    """The 32-byte data key: SITE_DATA_KEY (base64) or the key file on this Mac."""
    raw = os.environ.get("SITE_DATA_KEY") or (KEY_FILE.read_text().strip() if KEY_FILE.exists() else "")
    if not raw:
        raise SystemExit("No data key: set SITE_DATA_KEY or create it with: python3 site_users.py init")
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise SystemExit("The data key must be 32 bytes (base64).")
    return key


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def encrypt_bundle(bundle: dict, key: bytes) -> dict:
    """gzip(JSON) encrypted with AES-256-GCM under `key`."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = os.urandom(12)
    plain = gzip.compress(json.dumps(bundle, separators=(",", ":"), default=_jsonable).encode(), compresslevel=9)
    return {"iv": b64(iv), "ct": b64(AESGCM(key).encrypt(iv, plain, None))}


def decrypt_bundle(enc: dict, key: bytes) -> dict:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    plain = AESGCM(key).decrypt(base64.b64decode(enc["iv"]), base64.b64decode(enc["ct"]), None)
    return json.loads(gzip.decompress(plain))


def _jsonable(o):
    return o.item() if hasattr(o, "item") else str(o)


# ──────────────────────────────────────────────
# WHAT TO PUBLISH
# ──────────────────────────────────────────────
def run_update() -> dict:
    """The same update as the "Get picks" button: sync data (results, lineups, injuries, retrain) + today's picks."""
    import gui
    import nba
    import starters as st
    st._FEED_CACHE.clear(); st._SB_CACHE.clear()
    nba._SEEN.clear(); nba._DONE.clear()          # every pass is a full fresh look (odds and lineups move)
    out = gui.Collector(lambda msg: print(f"  … {msg}", flush=True))
    nba.predict_today(first_pass=True, teams=set(), ui=out)
    return gui.keep_todays_cards(gui.add_logged_cards(out.result))


def build_bundle(result) -> dict:
    """Everything the website shows, in the same shapes the app's own server sends."""
    import gui
    board = gui.LiveBoard()
    board.refresh()
    now = datetime.now(ET)
    return {
        "published": now.isoformat(timespec="seconds"),
        "version": str(int(time.time())),
        "status": {"running": False, "step": "", "error": "", "finished_at": now.strftime("%-I:%M %p"),
                   "result": result},
        "board": board.data,
        "record": gui.track_record(),
        "data": {tab: gui.data_tab(tab) for tab in gui.DATA_TABS},
    }


def build_site(enc: dict, users: dict, out: Path) -> None:
    """index.html (the app's page + login screen + offline data layer) and data.json (the locked results)."""
    out.mkdir(parents=True, exist_ok=True)
    page = (HERE / "gui" / "index.html").read_text()
    relay = RELAY_FILE.read_text().strip() if RELAY_FILE.exists() else ""
    parts = STATIC_PARTS.read_text().replace("__FB_ODDS_BASE__", relay.rstrip("/"))
    page = page.replace("<title>First Basket Picks</title>",
                        '<title>First Basket Picks</title>\n<meta name="robots" content="noindex, nofollow">', 1)
    page = page.replace("<body>", "<body>\n" + parts, 1)          # plain replace: the JS contains backslashes
    (out / "index.html").write_text(page)
    (out / "data.json").write_text(json.dumps({"v": 1, **enc, "users": users}, separators=(",", ":")))
    (out / ".nojekyll").write_text("")
    (out / "robots.txt").write_text("User-agent: *\nDisallow: /\n")


def load_site_users() -> dict:
    try:
        return json.loads(SITE_USERS.read_text())
    except (OSError, ValueError):
        return {}


# ──────────────────────────────────────────────
# SHOULD WE RUN NOW? (keeps GitHub's free minutes for when it matters)
# ──────────────────────────────────────────────
def should_run(kind: str) -> bool:
    """nightly: always. hourly: only if today has a game that hasn't started yet (picks can still change)."""
    if kind == "nightly":
        return True
    import requests
    d = datetime.now(ET).strftime("%Y%m%d")
    try:
        events = requests.get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
                              params={"dates": d}, timeout=20).json().get("events", [])
    except Exception:  # noqa: BLE001 — if ESPN can't be reached, run anyway (the update will report it)
        return True
    return any(e.get("status", {}).get("type", {}).get("state") == "pre" for e in events)


# ──────────────────────────────────────────────
# LIVE MODE: game night, every few minutes
# ──────────────────────────────────────────────
PUBLISH_GAP = 6 * 60          # GitHub Pages allows ~10 publishes an hour
MAX_RUN = 5 * 3600 + 30 * 60  # GitHub stops a job at 6 h; the next scheduled run picks up from here
FAST = SLOW = 3 * 60          # check lineups, injuries and odds every 3 minutes all day (not just near tip-off)
STATE_FILES = ["nba_first_basket.db", "first_basket_data.csv", "my_predictions.xlsx", "pregame_model.json",
               "X_train.npy", "y_train.npy", "groups.npy", "game_ids.json", "meta.json", "feature_names.json",
               "player_game_logs.pkl", "todays_cards.json", "run.log"]


def fingerprint(result) -> str:
    """What viewers would see change: each card's picks, chances, lineup status, tip, FanDuel prices + skipped games."""
    import hashlib
    keep = ("matchup", "label", "picks", "fanduel", "best_value", "tip", "jumpers", "home_starters", "away_starters",
            "injuries")
    view = [{k: c.get(k) for k in keep} for c in (result or {}).get("cards", [])]
    view.append(sorted(x.get("matchup", "") + x.get("reason", "") for x in (result or {}).get("skipped", [])))
    return hashlib.sha256(json.dumps(view, sort_keys=True, default=_jsonable).encode()).hexdigest()


def seconds_to_next_tip(result) -> float:
    """Seconds until the next game that hasn't started tips off (inf if none)."""
    now = datetime.now(ET).timestamp()
    tips = [datetime.fromisoformat(str(g["start_utc"]).replace("Z", "+00:00")).timestamp()
            for g in (result or {}).get("schedule", []) if g.get("status") == 1 and g.get("start_utc")]
    return min(tips) - now if tips else float("inf")


def _git(*args, cwd) -> None:
    import subprocess
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, timeout=180)


def push_dir(folder: Path, remote: str, branch: str, message: str) -> None:
    """Replace `branch` on `remote` with exactly this folder (one commit, no growing history)."""
    import shutil
    shutil.rmtree(folder / ".git", ignore_errors=True)
    who = ["-c", "user.name=First Basket bot", "-c", "user.email=bot@users.noreply.github.com"]
    _git("init", "-q", "-b", branch, cwd=folder)
    _git(*who, "add", "-A", cwd=folder)
    _git(*who, "commit", "-q", "-m", message, cwd=folder)
    _git("push", "-q", "--force", remote, f"HEAD:{branch}", cwd=folder)


def publish_site(out: Path, result) -> None:
    build_site(encrypt_bundle(build_bundle(result), data_key()), load_site_users(), out)
    remote = os.environ.get("SITE_REMOTE")
    if remote:
        push_dir(out, remote, os.environ.get("SITE_BRANCH", "gh-pages"), f"Picks {datetime.now(ET):%Y-%m-%d %H:%M}")


def save_state() -> None:
    """Keep the data for the next run (the private repo's "state" branch)."""
    import shutil
    remote = os.environ.get("STATE_REMOTE")
    if not remote:
        return
    folder = HERE / ".state_out"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir()
    for f in STATE_FILES:
        if (HERE / f).exists():
            shutil.copy2(HERE / f, folder / f)
    push_dir(folder, remote, "state", f"Saved data {datetime.now(ET):%Y-%m-%d %H:%M}")


def live(out: Path, once: bool = False) -> int:
    """Update -> publish if anything changed -> wait -> again, until every game has tipped off."""
    import traceback
    public = sys.stdout                                   # the public log: one short line per check, no details
    logf = HERE / "run.log"
    if logf.exists() and logf.stat().st_size > 2_000_000:   # keep the private log small
        logf.write_text("\n".join(logf.read_text(errors="replace").splitlines()[-5000:]) + "\n")
    log = open(logf, "a", buffering=1)                    # everything else: private (saved with the data)
    sys.stdout = sys.stderr = log
    say = lambda msg: print(f"{datetime.now(ET):%-I:%M %p}  {msg}", file=public, flush=True)  # noqa: E731
    if not load_site_users():
        say("no website accounts yet — nothing to publish")
        return 0
    started, last_pub, last_save, published_fp, fails = time.time(), 0.0, time.time(), None, 0
    day = datetime.now(ET).strftime("%Y%m%d")          # the NBA day this run looks after
    settings = lambda: json.dumps(load_site_users(), sort_keys=True) + (  # noqa: E731 — accounts + relay address
        RELAY_FILE.read_text() if RELAY_FILE.exists() else "")
    users_seen = settings()
    while True:
        print(f"\n=== check {datetime.now(ET):%Y-%m-%d %H:%M:%S} ===")
        if os.environ.get("STATE_REMOTE"):               # accounts added/removed since the run started
            try:
                _git("fetch", "-q", "--depth", "1", os.environ["STATE_REMOTE"], "main", cwd=HERE)
                _git("checkout", "FETCH_HEAD", "--", "site_users.json", cwd=HERE)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            try:                                         # the live-odds relay, once it's set up
                _git("checkout", "FETCH_HEAD", "--", "website_live/relay_url.txt", cwd=HERE)
            except Exception:  # noqa: BLE001 — not set up yet
                pass
            try:                                         # DraftKings prices from the Mac (DraftKings refuses clouds)
                _git("fetch", "-q", "--depth", "1", os.environ["STATE_REMOTE"], "dk-prices", cwd=HERE)
                _git("checkout", "FETCH_HEAD", "--", "dk_prices.json", cwd=HERE)
            except Exception:  # noqa: BLE001 — the Mac hasn't sent any (it's off): FanDuel only
                pass
        users_now = settings()
        if users_now != users_seen:
            users_seen, published_fp = users_now, None    # publish straight away with the new account list
        try:
            result = run_update()
            fails = 0
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            fails += 1
            say("check failed — trying again shortly")
            if fails >= 3:
                save_state()
                return 1
            time.sleep(FAST)
            continue
        fp = fingerprint(result)
        due = time.time() - last_pub >= PUBLISH_GAP
        if published_fp is None or (fp != published_fp and due) or time.time() - last_pub >= 1800:
            try:
                publish_site(out, result)
                published_fp, last_pub = fp, time.time()
                say(f"published ({len(result.get('cards', []))} games)")
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                say("publish failed — trying again next check")
        else:
            say("no change" if fp == published_fp else "changed — publishing at the next check")
        wait = seconds_to_next_tip(result)
        new_day = datetime.now(ET).strftime("%Y%m%d") != day
        unfinished = [g for g in result.get("schedule", []) if g.get("status") != 3]
        if wait == float("inf") and not once and not new_day:
            # every game has tipped off: file each result as its game ends, so the Data tabs, the record and the
            # spreadsheet update tonight (not at 4 AM). Not earlier: new results retrain the model.
            try:
                import nba
                with nba._quiet([]):
                    filed = nba.file_finished_games(day)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                filed = 0
            if filed:
                try:
                    publish_site(out, result)
                    last_pub = time.time()
                    say(f"filed {filed} finished game{'s' * (filed != 1)} — Data tabs updated")
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
        all_done = wait == float("inf") and not unfinished
        if once or new_day or all_done or time.time() - started > MAX_RUN:
            if fp != published_fp:
                publish_site(out, result)
                say("published final update")
            save_state()
            say("done — every game is over and filed" if all_done else "done for this run")
            return 0
        if time.time() - last_save > 1800:
            save_state()
            last_save = time.time()
        time.sleep(FAST if wait < 100 * 60 else SLOW if wait != float("inf") else 10 * 60)


def main(argv) -> int:
    if "--should-run" in argv:
        kind = argv[argv.index("--should-run") + 1]
        ok = should_run(kind)
        print(f"should run ({kind}): {'yes' if ok else 'no — no upcoming games today'}")
        return 0 if ok else 3
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else HERE / "site_out"
    if "--live" in argv or "--once" in argv:
        return live(out, once="--once" in argv)
    users = load_site_users()
    result = None
    if "--no-update" not in argv:
        print("Updating data and picks…", flush=True)
        result = run_update()
    if not users:                       # data still updated (and saved by the job); nothing to publish yet
        print("No website accounts yet — website not published. Add one: python3 site_users.py add NAME")
        return 0
    key = data_key()
    print("Packaging results…", flush=True)
    bundle = build_bundle(result)
    build_site(encrypt_bundle(bundle, key), users, out)
    n_cards = len((result or {}).get("cards", []))
    print(f"Website written to {out} — {n_cards} game cards, {len(users)} account(s), "
          f"{(out / 'data.json').stat().st_size // 1024} KB locked data")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
