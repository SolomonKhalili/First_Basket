"""The Mac takes GitHub's newest code by itself, but never overwrites work in progress on the Mac."""
from sync_from_cloud import code_update_plan


def test_nothing_to_do_when_up_to_date():
    assert code_update_plan(True, False, set(), {"nba.py"}) == ("none",)


def test_updates_when_the_mac_has_no_edits():
    assert code_update_plan(False, False, {"starters.py", "BLUEPRINT.md"}, set()) == ("update", [])


def test_synced_data_files_are_reset_then_updated():
    plan = code_update_plan(False, False, {"starters.py", "nba_first_basket.db"}, {"nba_first_basket.db", "meta.json"})
    assert plan == ("update", ["nba_first_basket.db"])                 # only the clashing data file is reset


def test_edits_elsewhere_on_the_mac_dont_block():
    assert code_update_plan(False, False, {"starters.py"}, {"gui/index.html"}) == ("update", [])


def test_skips_when_a_code_file_was_edited_on_both():
    plan = code_update_plan(False, False, {"starters.py"}, {"starters.py"})
    assert plan[0] == "skip" and "starters.py" in plan[1]


def test_skips_when_the_mac_has_its_own_saved_changes():
    assert code_update_plan(False, True, {"starters.py"}, set())[0] == "skip"


def test_identical_unsaved_file_is_not_a_clash():
    plan = code_update_plan(False, False, {".claude/launch.json"}, {".claude/launch.json"},
                            frozenset({".claude/launch.json"}))
    assert plan == ("update", [".claude/launch.json"])
