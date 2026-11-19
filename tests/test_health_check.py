"""The health check raises an alert only for real problems — and the Mac's report doesn't cry wolf over one blip."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from health_check import find_problems
from sync_from_cloud import mac_problems

ET = ZoneInfo("America/New_York")
at = lambda h, m=0: datetime(2026, 11, 19, h, m, tzinfo=ET)  # noqa: E731


def healthy(now):
    return {"site_status": 200, "site_data_ok": True, "relay_status": 200,
            "timer_text": "nothing to do · start permission: OK", "state_saved": at(4, 1),
            "live_runs": [{"created": now - timedelta(minutes=15), "status": "completed", "conclusion": "success"}],
            "mac": {"at": now - timedelta(minutes=20), "problems": []}}


def test_all_good():
    for h in (7, 12, 17, 21):
        assert find_problems(at(h, 30), healthy(at(h, 30))) == []


def test_website_down():
    f = healthy(at(12)) | {"site_status": 404}
    assert "website isn't opening" in find_problems(at(12), f)[0]
    f = healthy(at(12)) | {"site_data_ok": False}
    assert "unreadable" in find_problems(at(12), f)[0]


def test_relay_and_timer():
    assert "relay" in find_problems(at(12), healthy(at(12)) | {"relay_status": 500})[0]
    assert "timer" in find_problems(at(12), healthy(at(12)) | {"timer_text": "start permission: MISSING"})[0]


def test_nightly_missed():
    f = healthy(at(7, 30)) | {"state_saved": at(4, 1) - timedelta(days=1)}
    assert "nightly" in find_problems(at(7, 30), f)[0]
    assert find_problems(at(5), f | {"live_runs": []}) == []          # too early to tell


def test_live_job_failed_or_stuck():
    now = at(17, 30)
    failed = healthy(now) | {"live_runs": [{"created": now - timedelta(minutes=5), "status": "completed",
                                            "conclusion": "failure"}]}
    assert "failed" in find_problems(now, failed)[0]
    stuck = healthy(now) | {"live_runs": [{"created": now - timedelta(hours=3), "status": "completed",
                                           "conclusion": "success"}]}
    assert "hasn't started" in find_problems(now, stuck)[0]
    assert find_problems(at(7, 30), healthy(at(7, 30)) | {"live_runs": []}) == []   # nothing runs overnight
    running = healthy(now) | {"live_runs": [{"created": now - timedelta(hours=3), "status": "in_progress",
                                             "conclusion": ""}]}
    assert "hasn't started" in find_problems(now, running)[0]   # a 3-hour live run is fine only if new ones queue


def test_mac_problems_pass_through_but_old_reports_and_off_macs_dont():
    now = at(12)
    f = healthy(now) | {"mac": {"at": now - timedelta(hours=1), "problems": ["the Mac app isn't running"]}}
    assert find_problems(now, f) == ["Mac: the Mac app isn't running"]
    f["mac"]["at"] = now - timedelta(days=3)                    # stale report: Mac's just been off
    assert find_problems(now, f) == []
    assert find_problems(now, healthy(now) | {"mac": None}) == []


def test_mac_one_blip_is_not_a_problem():
    strikes = {}
    for _ in range(2):
        assert mac_problems("code update skipped (offline)", False, [], True, strikes) == []
    p = mac_problems("code update skipped (offline)", False, [], True, strikes)
    assert len(p) == 2 and "can't download" in p[1]
    assert mac_problems("code already up to date", True, [], True, strikes) == []  # recovered: counts reset
    assert strikes == {"code": 0, "sync": 0}


def test_mac_missing_library_or_app_counts_at_once():
    p = mac_problems("code already up to date", True, ["numpy"], False, {})
    assert "missing numpy" in p[0] and "isn't running" in p[1]


def test_cant_reach_github_is_reported_not_mistaken_for_stuck():
    now = at(17, 30)
    p = find_problems(now, healthy(now) | {"live_runs": None})
    assert len(p) == 1 and "signed in" in p[0]


def test_health_file_shows_problems_then_records_the_fix(tmp_path, monkeypatch):
    import health_check
    monkeypatch.setattr(health_check, "OUT", tmp_path / "HEALTH.md")
    health_check.write_report(["The website isn't opening (got 404)."], at(12))
    text = (tmp_path / "HEALTH.md").read_text()
    assert text.startswith("# ⚠️ PROBLEMS") and "website isn't opening" in text.split("## Recent")[1]
    health_check.write_report(["The website isn't opening (got 404)."], at(12, 30))   # same problem: no new line
    assert (tmp_path / "HEALTH.md").read_text().count("website isn't opening") == 2   # once at the top, once in the history
    health_check.write_report([], at(13))
    text = (tmp_path / "HEALTH.md").read_text()
    assert text.startswith("# ✅ All good") and "✅ fixed: The website" in text
