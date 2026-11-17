"""
Pre-game first-basket model: conditional logit (softmax over the 10 starters).
Uses ONLY information known before tip-off, so training and live prediction match.
Weights live in pregame_model.json (written by train_pregame.py).
"""
import json
import numpy as np

FEATURES = ["is_home", "fb_rate_shrunk", "L10_fb_shrunk", "usage_rate",
            "true_shooting", "minutes_per_game", "points_per_game", "team_tipoff_win_pct",
            "tip_win_prob"]
PRIOR = 0.10   # ~1 in 10 starters scores first
M_SEASON, M_L10 = 30, 10

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
        p.get("usage_rate", 0.0),
        p.get("true_shooting_pct", 0.0),
        p.get("minutes_per_game", 0.0),
        p.get("points_per_game", 0.0),
        t.get("team_tipoff_win_pct", 0.5),
        float(tip_win_prob),
    ]
    return row


def load(path="pregame_model.json"):
    with open(path) as f:
        return json.load(f)


def probabilities(model: dict, rows: list) -> np.ndarray:
    Z = (np.array(rows, dtype=float) - np.array(model["mu"])) / np.array(model["sd"])
    s = Z @ np.array(model["w"])
    e = np.exp(s - s.max())
    return e / e.sum()


def fair_american(p: float) -> int:
    """Model probability -> fair American odds."""
    return int(round(-100 * p / (1 - p))) if p >= 0.5 else int(round(100 * (1 - p) / p))
