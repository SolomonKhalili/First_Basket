"""The Mac's backup only starts a cloud update when GitHub's own schedule has gone quiet."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sync_from_cloud import backup_action

ET = ZoneInfo("America/New_York")
at = lambda h, m=0: datetime(2026, 11, 18, h, m, tzinfo=ET)  # noqa: E731


def test_hourly_when_stale():
    assert backup_action(at(20, 30), at(19, 0), busy=False) == "hourly"
    assert backup_action(at(20, 30), at(19, 45), busy=False) is None          # recent enough
    assert backup_action(at(20, 30), None, busy=False) == "hourly"


def test_never_while_a_run_is_going():
    assert backup_action(at(20, 30), at(12, 0), busy=True) is None


def test_nightly_in_the_morning():
    assert backup_action(at(6), at(23, 17) - timedelta(days=1), busy=False) == "nightly"
    assert backup_action(at(6), at(4, 9), busy=False) is None                 # the nightly already ran


def test_quiet_overnight():
    assert backup_action(at(2), at(23) - timedelta(days=1), busy=False) is None


def test_full_check_only_every_25_minutes(tmp_path):
    from sync_from_cloud import due_full_check
    f = tmp_path / "t"
    assert due_full_check(1e9, f) is True
    assert due_full_check(1e9 + 60, f) is False            # a minute later: just the quick sync
    assert due_full_check(1e9 + 26 * 60, f) is True


def test_sync_waits_while_the_app_is_updating(tmp_path):
    from sync_from_cloud import app_busy
    f = tmp_path / "busy"
    assert app_busy(1e9, f) is False                      # no sign
    f.write_text(str(1e9 - 60))
    assert app_busy(1e9, f) is True                       # updating for a minute: wait
    f.write_text(str(1e9 - 3600))
    assert app_busy(1e9, f) is False                      # an hour-old sign = left behind by a crash: ignore
