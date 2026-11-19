"""
Builds the free, password-protected website (run on a schedule by GitHub — .github/workflows/update-site.yml).

    python3 publish.py --out site_out             # update everything, then write the encrypted website
    python3 publish.py --out site_out --no-update # just repackage what's already in the database
    python3 publish.py --should-run nightly|hourly # exit 0 if an update is worth running now (saves free minutes)

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
STATIC_PARTS = HERE / "gui" / "static_site.html"       # login screen + the bit that replaces the server
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
    out = gui.Collector(lambda msg: print(f"  … {msg}", flush=True))
    nba.predict_today(first_pass=True, teams=set(), ui=out)
    return gui.keep_todays_cards(out.result)


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
    parts = STATIC_PARTS.read_text()
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


def main(argv) -> int:
    if "--should-run" in argv:
        kind = argv[argv.index("--should-run") + 1]
        ok = should_run(kind)
        print(f"should run ({kind}): {'yes' if ok else 'no — no upcoming games today'}")
        return 0 if ok else 3
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else HERE / "site_out"
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
