"""
Pre-game first-basket model, TWO STAGES (adopted 2026-11-17):
  1. team:   P(home team scores first) = sigmoid(a + b * logit(P(home team wins the opening tip)))
  2. player: P(player | his team scores first) = softmax over that team's 5 starters (player inputs only)
  P(player scores first) = stage 1 for his team x stage 2.
The older one-stage model (softmax over all 10 starters with every input) is still supported for old model files.
Uses ONLY information known before tip-off, so training and live prediction match.
Weights live in pregame_model.json (written by train_pregame.py); the math here is shared by both.
"""
import json
import numpy as np

FEATURES = ["is_home", "fb_rate_shrunk", "L10_fb_shrunk", "shot_attempts_per36",
            "true_shooting", "minutes_per_game", "points_per_game", "team_tipoff_win_pct",
            "tip_win_prob", "early_shot_shrunk", "opp_pos_share", "vs_opp_rate"]
# opp_pos_share / vs_opp_rate (adopted 2026-11-18): tonight's opponent — see context_features.py for the tests.
HOME, TIP = FEATURES.index("is_home"), FEATURES.index("tip_win_prob")
PLAYER_FEATURES = [f for f in FEATURES if f not in ("is_home", "team_tipoff_win_pct", "tip_win_prob")]
PLAYER_IDX = [FEATURES.index(f) for f in PLAYER_FEATURES]
PRIOR = 0.10   # ~1 in 10 starters scores first
# Shrink strengths (imaginary games at the 10% prior). Tested 2026-11-17: with the L2 held at 0.001, M_SEASON = 100
# looked better (2026-27 21.7% / 2.2307), but the real training procedure (L2 chosen on 2025-26) then picked L2 = 0.3
# and scored 19.2% / 2.2378 — worse than 30 (20.7% / 2.2369). Kept at 30. The last-10 strength (10) tested flat.
M_SEASON, M_L10 = 30, 10
# Early-shot share (adopted 2026-11-17): his share of his team's first 5 shot attempts over earlier starts, shrunk
# toward 1 in 5 with 25 imaginary shots. Walk-forward log-loss improved in both periods (2026-27 2.2368 -> 2.2318,
# Jan-Apr 2026 2.2781 -> 2.2763) with the real training procedure.
EARLY_PRIOR, M_EARLY = 0.2, 25
# A first-shot-share input was tested on 2026-11-17 and NOT adopted (walk-forward log-loss 2.2462 vs 2.2453 without
# it): the team's most frequent first shooter takes only 24% of its first shots, barely above 1 in 5. The data is
# still recorded (first_shots table; first_shot_share / first_shot_starts columns in X_train.npy) for future models.

def shrunk(rate, n, m, prior=PRIOR):
    """Shrink a rate toward the league prior; small samples count for less."""
    return (rate * n + m * prior) / (n + m)


def build_row(p: dict, t: dict, is_home: int, tip_win_prob: float = 0.5) -> list:
    """p = compute_player_season_stats() dict, t = compute_team_stats_up_to_date() dict.
    tip_win_prob = P(this player's TEAM wins the opening tip), from tip_model.team_win_probs()."""
    gp = float(p.get("games_played", 0) or 0)
    row = [
        float(is_home),
        shrunk(p.get("first_basket_rate", 0.0), gp, M_SEASON),
        shrunk(p.get("L10_first_basket_rate", 0.0), min(gp, 10), M_L10),
        p.get("shot_attempts_per36", 0.0),
        p.get("true_shooting_pct", 0.0),
        p.get("minutes_per_game", 0.0),
        p.get("points_per_game", 0.0),
        t.get("team_tipoff_win_pct", 0.5),
        float(tip_win_prob),
        shrunk(p.get("early_shot_share", 0.0), p.get("early_shot_count", 0), M_EARLY, EARLY_PRIOR),
        float(p.get("opp_pos_share", 0.4)),     # context_features.Context.opp_pos_share
        float(p.get("vs_opp_rate", PRIOR)),     # context_features.Context.vs_opp_rate
    ]
    return row


def load(path="pregame_model.json"):
    with open(path) as f:
        return json.load(f)


def logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def home_scores_first(team: dict, tip_home) -> np.ndarray:
    """Stage 1: P(home team scores first) from P(home team wins the tip). team = {'a': .., 'b': ..}."""
    return 1 / (1 + np.exp(-(team["a"] + team["b"] * logit(tip_home))))


def two_stage(team: dict, w, mu, sd, R: np.ndarray) -> np.ndarray:
    """R = one game's 10 feature rows (FEATURES order) -> P(each starter scores first), sums to 1."""
    home = R[:, HOME] > 0.5
    ph = float(home_scores_first(team, R[home, TIP][0] if home.any() else 0.5))
    idx = PLAYER_IDX[:len(w)]                     # an older model file with fewer inputs ignores the newer ones
    s = ((R[:, idx] - np.asarray(mu)) / np.asarray(sd)) @ np.asarray(w)
    out = np.zeros(len(R))
    for side, p_team in ((home, ph), (~home, 1 - ph)):
        if side.any():
            e = np.exp(s[side] - s[side].max())
            out[side] = p_team * e / e.sum()
    return out


# Mixed with the simple rule (adopted 2026-11-18, experiments/recency_and_mix_test.py): 80% model + 20% each starter's
# shrunk season first-basket rate (normalised over the 10). Walk-forward log-loss better in both periods:
# 2026-27 2.2365 -> 2.2347, Jan–Apr 2026 2.2727 -> 2.2649 (top pick 21.7% -> 21.2%, 14.9% -> 15.1%). 40% was worse
# for 2026-27. Re-check with the other inputs on 2026-12-08.
SIMPLE_MIX = 0.2

# Team chances were too cautious at the extremes (checked 2026-11-19 on 1,461 graded games: "63%" happened 73%,
# "37%" happened 27%). Stretch each team's chance on the log-odds scale: sigmoid(TEAM_STRETCH * logit), players keep
# their share inside the team. Learned on one season, checked on the other (1.45 / 1.35): player AND team log-loss
# better in both (2026-27 2.2371 -> 2.2360, Jan–Apr 2026 2.2646 -> 2.2636).
TEAM_STRETCH = 1.4


def stretch_teams(p: np.ndarray, is_home: np.ndarray, k: float = TEAM_STRETCH) -> np.ndarray:
    """Pure (testable). Players' chances -> same shares within each team, team totals stretched by k."""
    p = np.asarray(p, dtype=float)
    h = np.asarray(is_home, dtype=bool)
    ph = p[h].sum()
    if not (0 < ph < 1) or k == 1:
        return p
    ph2 = 1 / (1 + np.exp(-k * np.log(ph / (1 - ph))))
    q = p.copy()
    q[h] *= ph2 / ph
    q[~h] *= (1 - ph2) / (1 - ph)
    return q


def probabilities(model: dict, rows: list) -> np.ndarray:
    R = np.array(rows, dtype=float)
    if model.get("type") == "two_stage":
        p = two_stage(model["team"], model["w"], model["mu"], model["sd"], R)
        fb = R[:, FEATURES.index("fb_rate_shrunk")]
        p = (1 - SIMPLE_MIX) * p + SIMPLE_MIX * fb / fb.sum() if fb.sum() > 0 else p
        return stretch_teams(p, R[:, FEATURES.index("is_home")] > 0.5)
    R = R[:, :len(model["mu"])]
    Z = (R - np.array(model["mu"])) / np.array(model["sd"])          # older one-stage model files
    s = Z @ np.array(model["w"])
    e = np.exp(s - s.max())
    return e / e.sum()


def fair_american(p: float) -> int:
    """Model probability -> fair American odds."""
    return int(round(-100 * p / (1 - p))) if p >= 0.5 else int(round(100 * (1 - p) / p))
