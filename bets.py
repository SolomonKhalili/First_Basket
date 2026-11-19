"""
My bets: the real bets each person places, so the page can show their own wins, losses and profit.

Where they live: the free Cloudflare relay (website_live/odds-relay.js, GET/PUT /bets?u=<user>), one list per website
account, encrypted with the website's data key (AES-256-GCM) before it leaves the browser or this Mac, so the relay
only ever holds scrambled text. Nothing depends on the Mac: the website reads and writes the relay itself
(gui/static_site.html); the Mac app does the same through this file (/api/bets in gui.py).

Backup: the cloud job copies every account's list into the database (table my_bets, backup_all), which is saved
with the data and reaches the Mac's NBA folder like everything else.

A bet: {"id", "at" (ms), "day" (YYYYMMDD, New York), "game_id", "away", "home", "player", "player_id", "team",
        "book" ("FanDuel" / "DraftKings" / other), "american" (e.g. 600), "stake" (dollars)}
The page settles each one itself from the game's first scorer (won / lost / waiting).
"""
import base64
import hashlib
import json
import os
import re
import time
from pathlib import Path

HERE = Path(__file__).parent
RELAY_FILE = HERE / "website_live" / "relay_url.txt"
USER_RE = re.compile(r"^[a-z0-9_.-]{1,40}$")
OWNER = "solomon"                  # the Mac app without logins is the owner's
MAX_BETS = 1500


def slot(user: str) -> str:
    """The relay's storage name for an account. Any website username works (they may use any characters but spaces):
    simple ones are used as they are, anything else becomes "h-" + a fingerprint of the name (static_site.html does the same)."""
    return user if USER_RE.match(user) else "h-" + hashlib.sha256(user.encode()).hexdigest()[:32]


def relay() -> str:
    return RELAY_FILE.read_text().strip().rstrip("/") if RELAY_FILE.exists() else ""


def write_code(key: bytes, user: str) -> str:
    """Proves to the relay that the writer is logged in (the browser computes the same: static_site.html)."""
    return hashlib.sha256(f"first-basket-bets:{base64.b64encode(key).decode()}:{user}".encode()).hexdigest()


def seal(bets: list, key: bytes) -> dict:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, json.dumps({"bets": bets}, separators=(",", ":")).encode(), None)
    return {"iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode()}


def unseal(blob, key: bytes) -> list:
    if not blob:
        return []
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    plain = AESGCM(key).decrypt(base64.b64decode(blob["iv"]), base64.b64decode(blob["ct"]), None)
    return json.loads(plain).get("bets", [])


def clean(b: dict) -> dict:
    """Pure (testable). A bet as sent by the page -> the stored shape; ValueError if it doesn't make sense."""
    try:
        american, stake = int(round(float(b["american"]))), round(float(b["stake"]), 2)
    except (KeyError, TypeError, ValueError):
        raise ValueError("odds and amount must be numbers") from None
    if not (american >= 100 or american <= -100):
        raise ValueError("odds look wrong (use American odds like +600 or -110)")
    if not (0 < stake <= 100_000):
        raise ValueError("amount must be more than $0")
    s = lambda k, n=60: str(b.get(k) or "")[:n]  # noqa: E731
    if not s("player") or not re.fullmatch(r"\d{8}", s("day", 8)):
        raise ValueError("missing player or day")
    return {"id": s("id", 40) or f"{int(time.time() * 1000)}", "at": int(b.get("at") or time.time() * 1000),
            "day": s("day", 8), "game_id": s("game_id", 20), "away": s("away", 5), "home": s("home", 5),
            "player": s("player"), "player_id": s("player_id", 20), "team": s("team", 5),
            "book": s("book", 20) or "FanDuel", "american": american, "stake": stake}


# ── talking to the relay ──────────────────────────────────────────────────────
def load(user: str, key: bytes | None = None) -> list:
    import requests
    from publish import data_key
    key = key or data_key()
    r = requests.get(f"{relay()}/bets", params={"u": slot(user)}, timeout=20)
    r.raise_for_status()
    return unseal(r.json(), key)


def save(user: str, bets: list, key: bytes | None = None) -> None:
    import requests
    from publish import data_key
    key = key or data_key()
    body = {**seal(bets[-MAX_BETS:], key), "w": write_code(key, user)}
    r = requests.put(f"{relay()}/bets", params={"u": slot(user)}, json=body, timeout=20,
                     headers={"Origin": "http://127.0.0.1:8765"})
    r.raise_for_status()


def add(user: str, bet: dict) -> list:
    bets = [b for b in load(user) if b.get("id") != bet.get("id")] + [clean(bet)]
    save(user, bets)
    return bets


def remove(user: str, bet_id: str) -> list:
    bets = [b for b in load(user) if b.get("id") != bet_id]
    save(user, bets)
    return bets


# ── for the page and the backup ───────────────────────────────────────────────
def results(con, limit: int = 1500) -> dict:
    """{game_id: [scorer id, scorer name]} for the most recent filed games — lets the page settle older bets."""
    rows = con.execute("SELECT game_id, scorer_player_id, scorer_name FROM first_baskets "
                       "ORDER BY game_id DESC LIMIT ?", (limit,)).fetchall()
    return {str(g): [str(i or ""), n or ""] for g, i, n in rows}


def backup_all(con, users) -> int:
    """Copy every account's bets into the database (table my_bets). Returns how many accounts were copied."""
    from publish import data_key
    key = data_key()
    con.execute("CREATE TABLE IF NOT EXISTS my_bets (user TEXT PRIMARY KEY, bets TEXT, updated TEXT)")
    n = 0
    for user in users:                                   # every account, whatever its name
        bets = load(user, key)
        if bets:
            con.execute("INSERT OR REPLACE INTO my_bets VALUES (?, ?, datetime('now'))", (user, json.dumps(bets)))
            n += 1
    con.commit()
    return n
