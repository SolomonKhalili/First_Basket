

def test_clean_git_runs_once_a_day(tmp_path, monkeypatch):
    import sync_from_cloud as s
    calls = []
    monkeypatch.setattr(s, "git", lambda *a, **k: calls.append(a) or "")
    stamp = tmp_path / ".last_git_clean"
    assert s.clean_git(1_000_000.0, stamp) is True and calls[-1][0] == "gc"
    calls.clear()
    assert s.clean_git(1_000_000.0 + 3600, stamp) is False and calls == []        # an hour later: not again
    assert s.clean_git(1_000_000.0 + 25 * 3600, stamp) is True                    # next day: again
