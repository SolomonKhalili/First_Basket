import numpy as np
import pytest
import pregame_model as pm


def rows(tip_home=0.7):
    """5 home + 5 away starters; player k has fb-rate 0.05 + 0.01 k."""
    out = []
    for k in range(10):
        p = {"first_basket_rate": 0.05 + 0.01 * k, "games_played": 40, "minutes_per_game": 30}
        is_home = 1 if k < 5 else 0
        out.append(pm.build_row(p, {}, is_home, tip_win_prob=tip_home if is_home else 1 - tip_home))
    return out


def two_stage_model(a=0.0, b=1.0):
    w = np.zeros(len(pm.PLAYER_FEATURES)); w[pm.PLAYER_FEATURES.index("fb_rate_shrunk")] = 1.0
    return {"type": "two_stage", "team": {"a": a, "b": b}, "w": w.tolist(),
            "mu": [0.0] * len(pm.PLAYER_FEATURES), "sd": [0.01] * len(pm.PLAYER_FEATURES)}


def test_two_stage_chances_add_up_and_split_by_team():
    m = two_stage_model()
    p = pm.two_stage(m["team"], m["w"], m["mu"], m["sd"], np.array(rows(tip_home=0.7), dtype=float))
    assert p.sum() == pytest.approx(1.0)
    assert p[:5].sum() == pytest.approx(0.7)          # a=0, b=1: P(home scores first) = P(home wins tip)
    assert p[5:].sum() == pytest.approx(0.3)


def test_live_chances_mix_in_the_simple_rule():
    m = two_stage_model()
    R = np.array(rows(tip_home=0.7), dtype=float)
    pure = pm.two_stage(m["team"], m["w"], m["mu"], m["sd"], R)
    fb = R[:, pm.FEATURES.index("fb_rate_shrunk")]
    p = pm.probabilities(m, rows(tip_home=0.7))
    assert p.sum() == pytest.approx(1.0)
    mixed = (1 - pm.SIMPLE_MIX) * pure + pm.SIMPLE_MIX * fb / fb.sum()
    home = R[:, pm.FEATURES.index("is_home")] > 0.5
    assert p == pytest.approx(pm.stretch_teams(mixed, home))          # then team chances stretched (TEAM_STRETCH)


def test_within_a_team_the_better_player_ranks_higher():
    p = pm.probabilities(two_stage_model(), rows())
    assert list(np.argsort(-p[:5])) == [4, 3, 2, 1, 0] and list(np.argsort(-p[5:])) == [4, 3, 2, 1, 0]


def test_home_scores_first_follows_the_tip():
    team = {"a": 0.1, "b": 0.8}
    assert pm.home_scores_first(team, 0.5) == pytest.approx(1 / (1 + np.exp(-0.1)))
    assert pm.home_scores_first(team, 0.8) > pm.home_scores_first(team, 0.5)


def test_old_one_stage_model_files_still_work():
    model = {"w": [0.0] * len(pm.FEATURES), "mu": [0.0] * len(pm.FEATURES), "sd": [1.0] * len(pm.FEATURES)}
    assert pm.probabilities(model, rows()) == pytest.approx([0.1] * 10)
