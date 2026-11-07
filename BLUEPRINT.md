# NBA First Basket Scorer Bot — Project Blueprint

**Status:** ✅ **CORE PIPELINE COMPLETE** — Data → Features → Model → Production all working

---

## ✅ Already Done (Phases 1–4 Complete)

| Component | Status | Details |
|-----------|--------|---------|
| **Database schema** | ✅ Complete | 3 tables: `games`, `first_baskets`, `predictions` |
| **ESPN data ingestion** | ✅ Complete | `sportsdataverse` integration, PBP parsing, tip/first-basket extraction |
| **Backfill script** | ✅ Complete | `nba.py backfill` — regular season only, checkpointing, CSV export |
| **Heuristic predictor** | ✅ Complete | Tip-winner early-possession leader (~55% claimed) |
| **Evaluation** | ✅ Complete | `nba.py eval` — accuracy reporting |
| **Feature engineering** | ✅ Complete | `build_features.py` — 10 starters/game, early PBP features, checkpointing |
| **Model training** | ✅ Complete | `train_model.py` — LightGBM Ranker, NDCG@1=0.63, Top-1=62.8% |
| **Production integration** | ✅ Complete | `nba.py predict` — model inference, auto-catchup, pre-game + live modes |
| **README** | ✅ Complete | Research-backed documentation with 10-factor weights, correlations, methodology |

---

## 📊 Current Data State (as of 2026-11-06)

| Season | Code | Games in DB | Status |
|--------|------|-------------|--------|
| 2025-26 | `2025` | 642 | ✅ Complete regular season (Oct 21 – Nov 6) |
| 2026-27 | `2026` | 0 (pre-game) | 🔄 Live target — auto-catchup on `predict` |

**Training data:** 642 games → 6,420 samples (10 starters × 642 games), 4 features, 10% positive rate

---

## 🎯 Model Performance (Phase 3 Results)

| Metric | Value | Target | Status |
|--------|-------|--------|--------|
| Test NDCG@1 | **0.6282** | >0.25 | ✅ PASS |
| Test NDCG@3 | 0.7823 | — | — |
| Test NDCG@5 | 0.7978 | — | — |
| Test Top-1 Accuracy | **62.8%** | >55% | ✅ PASS |

**Feature Importance (gain):**
1. `early_shots`: 6,390
2. `early_touches`: 1,314
3. `tip_winner`: 171
4. `is_home`: 33

---

## 🚀 Daily Production Workflow

```bash
cd ~/Desktop/NBA
python nba.py predict    # Morning: auto-catchup + model predictions for pre-game/live
python nba.py eval       # Night: check accuracy on completed games
```

**Auto-catchup:** Every `predict` run fetches missing games from last DB date → yesterday.

---

## 📁 File Map

```
NBA/
├── nba.py                      # Main CLI — ingestion, model predict, eval, auto-catchup
├── build_features.py           # Feature engineering (checkpointed, parallel)
├── train_model.py              # LightGBM Ranker training + evaluation
├── view_data.py                # DB viewer + CSV export
├── requirements.txt            # Dependencies
├── README.md                   # Full documentation
├── BLUEPRINT.md                # This file
├── nba_first_basket.db         # SQLite (games, first_baskets, predictions)
├── first_basket_data.csv       # Auto-export (11 cols)
├── build_features_checkpoint.npz  # Feature build resume point
├── X_train.npy                 # Training features (6420, 4)
├── y_train.npy                 # Labels (6420,)
├── groups.npy                  # Game group sizes (642,)
├── feature_names.json          # ['is_home','tip_winner','early_touches','early_shots']
├── meta.json                   # Build metadata
├── model.txt                   # Trained LightGBM model (best_iter=25)
└── model_meta.json             # Model metadata + metrics
```

---

## 🎯 Next Enhancements (Phase 5+)

### Feature Expansion (in `build_features.py`)
| Feature | Description | Source |
|---------|-------------|--------|
| `first_basket_rate` | Season-to-date first basket % | DB aggregation |
| `first_shot_rate` | Season-to-date first shot attempt % | DB aggregation |
| `team_tipoff_win_pct` | Team's tipoff win rate | DB aggregation |
| `DVP_rank` | Defensive vs Position rank | ESPN stats |
| `L10_first_basket_rate` | Last 10 games form | Rolling window |
| `usage_rate` | Offensive usage % | Boxscores |

### Model Improvements
- [ ] Isotonic/Platt calibration for true probabilities
- [ ] Incremental re-training (weekly)
- [ ] SHAP feature importance analysis
- [ ] Hyperparameter tuning (Optuna)

### Odds & EV Layer (Phase 5)
| Component | Status | Notes |
|-----------|--------|-------|
| Sportsbook API | 🔲 Not started | Need to choose: Pinnacle, BetMGM, FanDuel, etc. |
| Line shopping | 🔲 Not started | Compare ≥4 books |
| Implied probability calc | 🔲 Not started | `1 / decimal_odds` |
| EV calculation | 🔲 Not started | `model_prob * odds - 1` |
| Kelly/bankroll sizing | 🔲 Not started | 0.5–1% unit, fractional Kelly |
| Bet logging | 🔲 Not started | Track ROI, CLV, hit rate |

---

## 🔄 Maintenance Schedule

| Frequency | Task |
|-----------|------|
| Daily (game days) | `python nba.py predict` (morning), `python nba.py eval` (night) |
| Weekly | Re-train model with latest games (incremental) |
| Monthly | Refresh season stats in feature builder, validate feature drift |
| Season start | Full re-train on all historical data |

---

## ❓ Open Decisions

1. **Playoffs in training?** Currently excluded (`season_type == 2`). Add `season_type in (2,3)` if wanted.
2. **Feature set scope:** Start minimal (current 4 numeric), expand incrementally, validate each addition.
3. **Validation strategy:** Time-series split (2025-26 H1 train, 2025-26 H2 validate, 2026-27 test).
4. **Odds source:** Which sportsbook API? (Phase 5 decision)
5. **Pre-game tip prediction:** Add tipoff win probability model for true pre-game confidence.