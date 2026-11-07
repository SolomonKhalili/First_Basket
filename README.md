# NBA First Basket Scorer Bot — 100% Free

Predicts the first **made field goal** scorer for NBA games (excludes free throws, overtime). Uses ESPN data via `sportsdataverse` (free, no API key).

---

## Research-Backed Methodology

### Data Sources
| Source | Purpose | Cost |
|--------|---------|------|
| **ESPN** (via `sportsdataverse`) | Play-by-play, box scores, tip-off data, starting lineups | Free |
| **NBA.com/stats** | Advanced tip-off metrics, player tracking | Free |
| **Basketball Reference** | Historical play-by-play validation | Free |
| **Sportsbook odds** (FanDuel, DraftKings, BetMGM, Caesars, bet365, Pinnacle) | Implied probability comparison, line shopping | Free |

### Time Span Requirements
| Purpose | Minimum | Recommended |
|---------|---------|-------------|
| **Stable pattern detection** | 30 games per team | — |
| **Model training** | 1 full season (~1,230 games) | 2 full seasons (~2,460 games) |
| **Validation** | 10-game windows | Half-season holdout (~400 games) |
| **Live prediction** | Current season play-by-play | Ongoing |

**Current DB coverage:** 2024-25 full season + 2025-26 full season (regular + playoffs) = ~2,460 games.

### Key Statistics & Features (Weighted by Empirical Predictive Value)

Based on industry research (HeatCheck HQ, StatsBench, SportsBettingPix, SportsCommand):

| Feature | Weight | Description |
|---------|--------|-------------|
| **First Basket Rate (season)** | 20% | Player's historical first-made-FG frequency |
| **First Shot Rate** | 18% | Player's first-shot-attempt rate in Q1 |
| **Recent Form (L10)** | 15% | First-basket/first-shot rates over last 10 games |
| **Team Tipoff Win %** | 12% | Team's season tip-off win rate (home/away split) |
| **DVP Matchup** | 10% | Defense vs. Position — how opponent defends player's position |
| **Team Rank (on team)** | 10% | Player's usage rank within starting 5 |
| **Tipoff Matchup (specific)** | 7% | Head-to-head jumper matchup history |
| **Home/Away Split** | 4% | Home: 52.3% vs Away: 47.7% first basket rate |
| **Game Total** | 3% | High totals (235+) → marginal 2.1pp uplift for top scorers |
| **Recency Trend (L5)** | 1% | 5-game trend adjustment |

### Non-Predictive Factors (Research-Confirmed)
- ❌ Season scoring averages
- ❌ Star power / name recognition
- ❌ Team offensive rank

### Core Correlations (Validated Across 2,000+ Games)
| Correlation | Value | Source |
|-------------|-------|--------|
| Tip winner scores first | 52–55% | StatsBench, SportsCommand |
| Top-quartile first-shot players | 24.8% FB rate | HeatCheck HQ |
| Mid-quartile first-shot players | 14.2% FB rate | HeatCheck HQ |
| Bottom-quartile first-shot players | 6.1% FB rate | HeatCheck HQ |
| 60%+ tip-win teams → opening possession FB | 61% | HeatCheck HQ |
| Top-quartile stability (H1→H2) | 73% | HeatCheck HQ |

### Model Architecture (Two-Stage)
1. **Stage 1 — Tip Prediction**: Estimate P(tip win) using jumper height, standing reach, matchup history, home/away
2. **Stage 2 — Scorer Given Possession**: Estimate P(player scores first \| team has ball) using first-touch rate, scripted play design, shot profile, position

**Final probability** = P(tip win) × P(scorer \| possession) + P(tip loss) × P(scorer \| opponent possession)

### Value Archetypes (Positive EV Profiles)
| Archetype | Profile | Typical Odds |
|-----------|---------|--------------|
| **Rolling Big** | Center on tip-winning team, high first-touch rate | +800 to +1500 |
| **Transition Wing** | Wing on fast-paced team, attacks in transition | +600 to +1000 |
| **Isolation Closer** | Guard with >40% first-possession usage | +700 to +1200 |

---

## Quick Start

```bash
cd ~/Desktop/NBA
pip install -r requirements.txt
python nba.py init
python nba.py backfill 2024    # 2024-25 full season (~10 min)
python nba.py backfill 2025    # 2025-26 full season (~10 min)
python build_features.py       # Build ML features (~5 min)
python nba.py predict          # Today's pre-game predictions
python nba.py eval             # Check accuracy after games finish
```

---

## Daily Routine

```bash
# Run each morning before games (checks today's schedule)
python nba.py predict
```

Outputs predictions for all pre-game matchups with confidence scores.

---

## Database Schema

| Table | Columns |
|-------|---------|
| `games` | game_id, game_date, home_team, away_team, tip_winner_team |
| `first_baskets` | game_id, scorer_player_id, scorer_name, scorer_team, shot_type, period, clock, x, y |
| `predictions` | game_id, predicted_scorer_id, predicted_scorer_name, confidence, model_version, features_json, created_at |

---

## Feature Engineering (`build_features.py`)

Builds player-level features for all 10 starters per game — **no data leakage** (all stats computed up to game date only).

### Features Extracted
| Category | Features |
|----------|----------|
| **Game Context** | game_id, is_home, tip_winner (team won tip), opponent |
| **Player Identity** | player_id, player_name, team, position, jersey |
| **Early Game PBP** | early_touches (first 10 plays), early_shots, first_play_type |
| **Season Stats** | (planned) first_basket_rate, first_shot_rate, usage_rate, DVP |

### Checkpointing
- Saves progress every 50 games to `build_features_checkpoint.npz`
- Resumes automatically on restart
- Parallel workers: `--workers N` (default 2, max 3 to respect ESPN rate limits)

### Output Files
| File | Description |
|------|-------------|
| `X_train.npy` | Feature matrix (numeric only) |
| `y_train.npy` | Labels (1 = scored first basket, 0 = didn't) |
| `groups.npy` | Group sizes per game (for ranking models) |
| `feature_names.json` | List of numeric feature names |
| `meta.json` | Metadata: sample count, feature count, positive rate, all feature names |

---

## Model Training (Planned)

```python
# Example: LightGBM ranker for first-basket prediction
import lightgbm as lgb
import numpy as np

X = np.load('X_train.npy')
y = np.load('y_train.npy')
groups = np.load('groups.npy')

model = lgb.LGBMRanker(
    objective='lambdarank',
    metric='ndcg',
    n_estimators=500,
    learning_rate=0.05,
    max_depth=5,
)
model.fit(X, y, group=groups)
```

**Target metric:** NDCG@1 (ranking the true first-basket scorer #1)

---

## Bankroll Management (Research-Based)

| Guideline | Value |
|-----------|-------|
| **Unit size** | 0.5%–1% of total bankroll per wager |
| **Max concurrent bets** | 3–5 games per slate |
| **Edge threshold** | Only bet when model probability > implied probability + 5% |
| **Line shopping** | Compare ≥4 books; bet once at best price |

---

## View / Export Data

```bash
# View all games with tip winner + first basket
python view_data.py

# Export to CSV (also done automatically during backfill)
python -c "
import sqlite3, csv
con = sqlite3.connect('nba_first_basket.db')
cur = con.cursor()
cur.execute('''
    SELECT g.game_date, g.game_id, g.home_team, g.away_team, g.tip_winner_team,
           fb.scorer_name, fb.scorer_team, fb.shot_type, fb.clock, fb.x, fb.y
    FROM games g
    JOIN first_baskets fb ON g.game_id = fb.game_id
    ORDER BY g.game_date
')
with open('first_basket_data.csv', 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow([d[0] for d in cur.description])
    w.writerows(cur.fetchall())
print('Exported to first_basket_data.csv')
"
```

---

## File Structure

```
~/Desktop/NBA/
├── nba.py                      # Main CLI: init, backfill, predict, eval, test
├── build_features.py           # ML feature engineering with checkpointing
├── view_data.py                # View/export database contents
├── requirements.txt            # Dependencies
├── README.md                   # This file
├── nba_first_basket.db         # SQLite database
├── first_basket_data.csv       # CSV export (auto-generated)
├── build_features_checkpoint.npz  # Feature build progress (auto)
├── X_train.npy                 # Training features (after build_features.py)
├── y_train.npy                 # Training labels
├── groups.npy                  # Game group sizes
├── feature_names.json          # Feature name mapping
└── meta.json                   # Build metadata
```

---

## Commands Reference

| Command | Description |
|---------|-------------|
| `python nba.py init` | Create database tables |
| `python nba.py backfill [year] [max_days]` | Backfill season (default: 2024, all days). `year=2024` → 2024-25 season |
| `python nba.py predict` | Today's pre-game predictions |
| `python nba.py eval` | Evaluate prediction accuracy |
| `python nba.py test [game_id]` | Test single game (default: 401909881) |
| `python build_features.py` | Build ML features (resumes from checkpoint) |
| `python build_features.py --workers 3` | Build with 3 parallel workers |
| `python view_data.py` | View table + optional CSV export |

---

## Limitations & Caveats

- **Preseason excluded** — only regular season (`season_type == 2`) and optionally playoffs (`season_type == 3`)
- **Rate limits** — ESPN allows ~3 req/sec; `SLEEP=0.2` in `nba.py`, `BASE_SLEEP=0.3` in `build_features.py`
- **Free throws excluded** — only made field goals in Q1-Q4 count as "first basket"
- **Overtime excluded** — first basket must occur in regulation
- **Lineup confirmation** — predictions assume confirmed starters; late scratches invalidate
- **Variance warning** — first basket is a single-possession market; even 25% models lose short-term

---

## Research References

- [StatsBench: First Basket NBA Betting Guide](https://blog.statsbench.com/first-basket-nba-betting-guide/) — Two-stage model, tip/scorer separation
- [HeatCheck HQ: 500-Prop Data Study 2026](https://heatcheckhq.io/blog/nba-first-basket-data-study-2026) — 10-factor composite, quartile stability
- [SportsBettingPix: NBA Opening Tip & First Basket Stats](https://www.sportsbettingpix.com/reports/nba-first-basket-opening-tip-report) — Daily updates from 2022-23
- [SportsCommand: First Basket Scorer Data-Driven Breakdown](https://blog.sportscommand.ai/first-basket-scorer-the-data-driven-breakdown-of-the-nba-s-most-volatile-prop-bet) — Five predictive factors, value archetypes
- [FirstBasketStats.com](https://firstbasketstats.com/) — Full season data, trends, team/player stats

---

## License

MIT — free to use, modify, distribute.