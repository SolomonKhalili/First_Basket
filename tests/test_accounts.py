"""Logins, roles and keeping the model private when the GUI is shared."""
import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

import accounts
import gui


@pytest.fixture
def users(tmp_path, monkeypatch):
    """A temporary users.json with one owner and one viewer; sessions and lockouts reset."""
    f = tmp_path / "users.json"
    monkeypatch.setattr(accounts, "USERS_FILE", f)
    monkeypatch.setattr(accounts, "SESSIONS", {})
    monkeypatch.setattr(accounts, "FAILS", {})
    accounts.add_user("me", "owner-pass-123", "owner")
    accounts.add_user("alex", "viewer-pass-123", "viewer")
    return f


# ── accounts.py ──────────────────────────────────────────────
def test_passwords_are_hashed_never_stored(users):
    text = users.read_text()
    assert "owner-pass-123" not in text and "viewer-pass-123" not in text
    stored = json.loads(text)
    assert stored["alex"]["role"] == "viewer" and len(stored["alex"]["hash"]) == 64
    assert stored["me"]["salt"] != stored["alex"]["salt"]                 # each account its own salt


def test_verify_and_roles(users):
    assert accounts.verify("me", "owner-pass-123") == "owner"
    assert accounts.verify("ALEX", "viewer-pass-123") == "viewer"         # usernames are case-insensitive
    assert accounts.verify("alex", "wrong-password") is None
    assert accounts.verify("nobody", "whatever-123") is None


def test_add_user_rules(users):
    # no length rules (owner's choice): any username without spaces, any non-empty password
    assert accounts.add_user("x", "a") == "x"
    assert accounts.add_user("a-really-long-username-with-no-limit-at-all", "b") == "a-really-long-username-with-no-limit-at-all"
    with pytest.raises(ValueError):
        accounts.add_user("two words", "pw")                              # spaces not allowed
    with pytest.raises(ValueError):
        accounts.add_user("sam", "")                                      # empty password
    with pytest.raises(ValueError):
        accounts.add_user("sam", "pw", role="admin")                      # unknown role


def test_lockout_after_five_wrong_tries(users):
    for _ in range(4):
        accounts.record_failure("user:alex")
    assert accounts.locked_for("user:alex") == 0
    accounts.record_failure("user:alex")
    assert accounts.locked_for("user:alex") > 0
    accounts.clear_failures("user:alex")
    assert accounts.locked_for("user:alex") == 0


def test_sessions_end_when_the_account_is_removed(users):
    token = accounts.new_session("alex", "viewer")
    assert accounts.session(token)["role"] == "viewer"
    accounts.remove_user("alex")
    assert accounts.session(token) is None


# ── the server ───────────────────────────────────────────────
@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(gui.JOB, "start", lambda teams: True)                # never really run the model
    srv = ThreadingHTTPServer(("127.0.0.1", 0), gui.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


def call(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    return r.status, dict(r.getheaders()), r.read()


def login(port, user, password):
    status, headers, _ = call(port, "POST", "/login", f"username={user}&password={password}",
                              {"Content-Type": "application/x-www-form-urlencoded"})
    cookie = headers.get("Set-Cookie", "").split(";")[0]
    return status, {"Cookie": cookie} if cookie else {}


def test_logged_out_visitors_go_to_the_login_page(users, server):
    status, headers, _ = call(server, "GET", "/")
    assert status == 303 and headers["Location"] == "/login"
    assert call(server, "GET", "/api/data?tab=picks")[0] == 401
    status, _, body = call(server, "GET", "/login")
    assert status == 200 and b"Log in" in body


def test_wrong_password_is_refused(users, server):
    status, cookie = login(server, "alex", "nope-nope-nope")
    assert status == 401 and not cookie


def test_viewer_sees_everything_but_cannot_run_the_model(users, server):
    status, cookie = login(server, "alex", "viewer-pass-123")
    assert status == 303 and cookie
    me = json.loads(call(server, "GET", "/api/me", headers=cookie)[2])
    assert me == {"user": "alex", "role": "viewer", "logins": True}
    assert call(server, "GET", "/", headers=cookie)[0] == 200
    assert call(server, "GET", "/api/status", headers=cookie)[0] == 200
    assert call(server, "POST", "/api/predict", "{}", {**cookie, "Content-Type": "application/json"})[0] == 403


def test_owner_can_run_the_model(users, server):
    _, cookie = login(server, "me", "owner-pass-123")
    assert call(server, "POST", "/api/predict", "{}", {**cookie, "Content-Type": "application/json"})[0] == 202


def test_model_files_and_code_are_never_served(users, server):
    _, cookie = login(server, "me", "owner-pass-123")                       # not even to the owner
    for path in ("/pregame_model.json", "/nba.py", "/gui.py", "/users.json", "/X_train.npy", "/nba_first_basket.db",
                 "/../pregame_model.json", "/gui/../pregame_model.json", "/%2e%2e/pregame_model.json", "/gui/login.html"):
        status, _, body = call(server, "GET", path, headers=cookie)
        assert status == 404, path
        assert b"stage1" not in body and b"import" not in body


def test_without_accounts_nothing_gets_in_through_a_sharing_link(tmp_path, monkeypatch, server):
    monkeypatch.setattr(accounts, "USERS_FILE", tmp_path / "none.json")    # no accounts
    assert call(server, "GET", "/api/status")[0] == 200                     # this Mac: works as before
    for h in ({"X-Forwarded-For": "1.2.3.4"}, {"Cf-Connecting-Ip": "1.2.3.4"}):
        assert call(server, "GET", "/", headers=h)[0] == 403                # through a tunnel: refused
        assert call(server, "GET", "/api/data?tab=picks", headers=h)[0] == 403


def test_auto_updater_runs_only_on_game_days_after_10am():
    from datetime import datetime
    upd = gui.AutoUpdater(minutes=30)
    upcoming, done = [{"status": 1}], [{"status": 3}]
    assert upd.due(datetime(2026, 11, 18, 14, 0), upcoming, now=1000)
    assert not upd.due(datetime(2026, 11, 18, 9, 0), upcoming, now=1000)     # too early
    assert not upd.due(datetime(2026, 11, 18, 14, 0), done, now=1000)        # nothing left to predict
    upd.last = 1000
    assert not upd.due(datetime(2026, 11, 18, 14, 10), upcoming, now=1000 + 600)
    assert upd.due(datetime(2026, 11, 18, 14, 40), upcoming, now=1000 + 1800)


def test_nightly_update_runs_once_at_4am_every_day():
    from datetime import datetime
    upd = gui.AutoUpdater()
    assert upd.nightly_due(datetime(2026, 11, 19, 4, 5))                     # 4 AM, not yet run today
    assert not upd.nightly_due(datetime(2026, 11, 19, 3, 59))
    assert not upd.nightly_due(datetime(2026, 11, 19, 5, 0))
    upd.last_nightly = datetime(2026, 11, 19).date()
    assert not upd.nightly_due(datetime(2026, 11, 19, 4, 30))               # already ran tonight
    assert upd.nightly_due(datetime(2026, 11, 20, 4, 0))                     # next night: runs again
