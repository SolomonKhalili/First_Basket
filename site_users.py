"""
Website accounts for the free, password-protected site (see publish.py).

    python3 site_users.py add alex      # asks for alex's password (typed twice, not shown); updates the site
    python3 site_users.py remove alex   # alex can't log in any more; updates the site
    python3 site_users.py list
    python3 site_users.py init          # one-time: create the data key (done during setup)

Each account stores only a "wrapped" copy of the site's data key, locked with that person's password
(PBKDF2-SHA256, 600,000 rounds + AES-256-GCM). The password itself is never stored. Because the locked copies
are part of the public website, longer passwords are safer (no length rules are enforced).
After add/remove the change is uploaded to your private GitHub repo and the website is rebuilt automatically.
"""
import base64
import getpass
import json
import os
import re
import subprocess
import sys
import time

import publish

ITERATIONS = 600_000
MIN_PASSWORD = 1                      # no length rules (owner's choice, 2026-11-18); longer is safer
NAME_RE = re.compile(r"^\S+$")         # any username without spaces, any length
REPO = "SolomonKhalili/First_Basket"


def _kek(password: str, salt: bytes, iterations: int = ITERATIONS) -> bytes:
    import hashlib
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations, dklen=32)


def wrap(key: bytes, password: str) -> dict:
    """The data key locked with this password (what the browser unlocks at login)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, iv = os.urandom(16), os.urandom(12)
    return {"salt": publish.b64(salt), "iv": publish.b64(iv), "iter": ITERATIONS,
            "wk": publish.b64(AESGCM(_kek(password, salt)).encrypt(iv, key, None)),
            "created": time.strftime("%Y-%m-%d")}


def unwrap(entry: dict, password: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    kek = _kek(password, base64.b64decode(entry["salt"]), entry["iter"])
    return AESGCM(kek).decrypt(base64.b64decode(entry["iv"]), base64.b64decode(entry["wk"]), None)


def save(users: dict) -> None:
    publish.SITE_USERS.write_text(json.dumps(users, indent=2, sort_keys=True))


def upload_and_rebuild(message: str) -> None:
    """Commit site_users.json to the private repo and start a website rebuild (best effort; says what to do if not)."""
    here = publish.HERE
    try:
        subprocess.run(["git", "add", "site_users.json"], cwd=here, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", message, "--only", "site_users.json"], cwd=here, check=True,
                       capture_output=True)
        subprocess.run(["git", "push", "-q", "origin", "HEAD:main"], cwd=here, check=True, capture_output=True)
        subprocess.run(["gh", "workflow", "run", "update-site.yml", "-R", REPO, "-f", "kind=publish"], cwd=here,
                       check=True, capture_output=True)
        print("Uploaded. The website rebuilds in ~3 minutes.")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        err = getattr(e, "stderr", b"") or b""
        print(f"Saved here, but couldn't upload automatically ({err.decode(errors='replace').strip()[:200]}).")


def main(argv) -> int:
    if not argv or argv[0] not in ("add", "remove", "list", "init"):
        print(__doc__)
        return 1
    cmd = argv[0]
    if cmd == "init":
        if publish.KEY_FILE.exists():
            print(f"Data key already exists at {publish.KEY_FILE}")
            return 0
        publish.KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        publish.KEY_FILE.write_text(publish.b64(os.urandom(32)))
        os.chmod(publish.KEY_FILE, 0o600)
        print(f"Data key created at {publish.KEY_FILE} (only your Mac account can read it).")
        return 0
    users = publish.load_site_users()
    if cmd == "list":
        if not users:
            print("No website accounts yet.")
        for name, u in sorted(users.items()):
            print(f"  {name:20s} created {u.get('created', '?')}")
        return 0
    if len(argv) < 2:
        print(f"usage: python3 site_users.py {cmd} NAME")
        return 1
    name = argv[1].strip().lower()
    if cmd == "remove":
        if users.pop(name, None) is None:
            print(f"No account called {name}.")
            return 1
        save(users)
        print(f"Removed {name}.")
        upload_and_rebuild(f"Website: remove account {name}")
        return 0
    if not NAME_RE.match(name):
        print("Usernames can't contain spaces.")
        return 1
    key = publish.data_key()
    pw = getpass.getpass(f"Password for {name} (it won't show as you type): ")
    if len(pw) < MIN_PASSWORD:
        print("The password can't be empty. Nothing saved.")
        return 1
    if pw != getpass.getpass("Type it again: "):
        print("The two passwords don't match. Nothing saved.")
        return 1
    users[name] = wrap(key, pw)
    assert unwrap(users[name], pw) == key                       # sanity check before saving
    save(users)
    print(f"Saved website account: {name}.")
    upload_and_rebuild(f"Website: add account {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
