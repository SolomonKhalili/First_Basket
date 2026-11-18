"""
Logins for sharing the GUI with friends.

    python3 gui.py add-user alex            # a friend: can view everything, can't run the model
    python3 gui.py add-user me --owner      # you: can also press "Get picks"
    python3 gui.py remove-user alex
    python3 gui.py users                    # list accounts

Accounts live in users.json next to this file. Passwords are never stored: only a salted PBKDF2-SHA256 hash
(240,000 rounds). As long as users.json has no accounts, the app works as before (this Mac only, no login).

Roles
  owner   everything, including running the model ("Get picks")
  viewer  picks, live scoreboard, Data tab, EV guide — read-only; never the model files or code
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

USERS_FILE = Path(os.environ.get("FB_USERS_FILE") or Path(__file__).parent / "users.json")   # env var: for testing
ROLES = ("owner", "viewer")
ITERATIONS = 240_000
SESSION_SECONDS = 7 * 24 * 3600          # stay logged in for a week
MAX_FAILS, LOCK_SECONDS = 5, 15 * 60     # 5 wrong passwords -> locked for 15 minutes
MIN_PASSWORD = 1                         # no length rules (owner's choice); longer is safer
NAME_RE = re.compile(r"^\S+$")           # any username without spaces, any length

_lock = threading.Lock()
SESSIONS: dict = {}                      # token -> {"user", "role", "expires"}
FAILS: dict = {}                         # "user:NAME" / "ip:ADDR" -> [failures, locked_until]
_DUMMY_SALT = "00" * 16


def _hash(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), ITERATIONS).hex()


# ──────────────────────────────────────────────
# ACCOUNTS (users.json)
# ──────────────────────────────────────────────
def load_users(path: Path | None = None) -> dict:
    path = path or USERS_FILE
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_users(users: dict, path: Path | None = None) -> None:
    path = path or USERS_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(users, indent=2))
    os.chmod(tmp, 0o600)                 # only your Mac account can read it
    tmp.replace(path)


def enabled(path: Path | None = None) -> bool:
    """Logins are on as soon as at least one account exists."""
    return bool(load_users(path))


def add_user(name: str, password: str, role: str = "viewer", path: Path | None = None) -> str:
    name = (name or "").strip().lower()
    if not NAME_RE.match(name):
        raise ValueError("username: no spaces")
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    if len(password or "") < MIN_PASSWORD:
        raise ValueError("password: can't be empty")
    users = load_users(path)
    salt = secrets.token_hex(16)
    users[name] = {"role": role, "salt": salt, "hash": _hash(password, salt), "created": time.strftime("%Y-%m-%d")}
    save_users(users, path)
    end_sessions_for(name)               # a password change logs out old sessions
    return name


def remove_user(name: str, path: Path | None = None) -> bool:
    users = load_users(path)
    if users.pop((name or "").strip().lower(), None) is None:
        return False
    save_users(users, path)
    end_sessions_for(name)
    return True


def verify(name: str, password: str, path: Path | None = None):
    """The user's role if the password is right, else None. Takes the same time for unknown users."""
    user = load_users(path).get((name or "").strip().lower())
    salt = user["salt"] if user else _DUMMY_SALT
    digest = _hash(password or "", salt)
    if user and hmac.compare_digest(digest, user["hash"]):
        return user["role"]
    return None


# ──────────────────────────────────────────────
# LOCKOUT after repeated wrong passwords
# ──────────────────────────────────────────────
def locked_for(*keys: str) -> int:
    """Seconds left before these keys may try again (0 = not locked)."""
    now = time.time()
    with _lock:
        return max([int(FAILS[k][1] - now) for k in keys if k in FAILS and FAILS[k][1] > now] or [0])


def record_failure(*keys: str) -> None:
    with _lock:
        for k in keys:
            n, _ = FAILS.get(k, [0, 0])
            n += 1
            FAILS[k] = [0, time.time() + LOCK_SECONDS] if n >= MAX_FAILS else [n, 0]


def clear_failures(*keys: str) -> None:
    with _lock:
        for k in keys:
            FAILS.pop(k, None)


# ──────────────────────────────────────────────
# SESSIONS (cookie token -> user)
# ──────────────────────────────────────────────
def new_session(user: str, role: str) -> str:
    token = secrets.token_urlsafe(32)
    with _lock:
        SESSIONS[token] = {"user": user, "role": role, "expires": time.time() + SESSION_SECONDS}
    return token


def session(token: str):
    if not token:
        return None
    with _lock:
        s = SESSIONS.get(token)
        if s and s["expires"] < time.time():
            SESSIONS.pop(token, None)
            s = None
    if s and s["user"] not in load_users():   # account removed since logging in
        end_session(token)
        return None
    return s


def end_session(token: str) -> None:
    with _lock:
        SESSIONS.pop(token, None)


def end_sessions_for(name: str) -> None:
    name = (name or "").strip().lower()
    with _lock:
        for t in [t for t, s in SESSIONS.items() if s["user"] == name]:
            SESSIONS.pop(t, None)
