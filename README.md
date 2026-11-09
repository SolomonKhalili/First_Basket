# NBA First Basket Scorer Bot

Predicts which **starter** will score the first **made field goal** (free throws excluded, regulation only) in each NBA game, using free data (ESPN via `sportsdataverse`, plus NBA.com's public lineup feeds). It runs **before tip-off**, shows the lineup it used (confirmed / expected / projected), and prints a probability and fair odds for each player.

> **Read this first.** The pre-game model is deliberately modest: on 125 games from the current season it picked the first scorer **15.2%** of the time (random = 10%). That is a *shortlist generator*, not a proven betting edge. Sportsbook favorites are priced around 14–17% implied with a large margin, so treat picks accordingly. Not betting advice; gamble responsibly and only if you're of legal age where you live.

Project status, decisions, known issues and the to-do list live in [`BLUEPRINT.md`](BLUEPRINT.md).

---

## What it does

For each game today it:

1. Finds the **starting five** for both teams, in this order of trust:
   `CONFIRMED` (NBA.com daily lineups feed, then NBA.com box score, then ESPN) →
   `EXPECTED` (NBA.com lists a lineup but it isn't confirmed yet) →
   `PROJECTED` (each team's usual starters from the last 5 games, minus players ruled Out/Doubtful; Questionable and Day-to-Day players stay in and are flagged).
2. Pulls the **ESPN injury report** and warns about any listed starter who's hurt.
3. Scores the 10 starters with a pre-game model and prints the top 5 with a probability and **fair American odds**.

Every game is labeled with the lineup type it used. Use `--confirmed-only` to skip anything that isn't official.

---

## Quick start

```bash
cd ~/Desktop/NBA
pip install -r requirements.txt

python3 nba.py init                 # create the database
python3 nba.py backfill 2025        # 2025-26 regular season  (~10 min)
python3 nba.py backfill 2026        # 2026-27 so far
python3 nba.py lineups              # store who started every game (~10 min, once)
python3 build_features.py           # build training matrices
python3 train_pregame.py            # train the pre-game model (writes pregame_model.json)

python3 nba.py report               # today's matchups, injuries, starters
python3 nba.py predict --watch      # keep checking; print picks as lineups update/confirm
```

If you already have the database and `pregame_model.json`, you only need the last two commands.

Run tests any time: `pytest test_starters.py`.

---

## Commands

| Command | What it does |
|---------|--------------|
| `python3 nba.py init` | Create the database tables |
| `python3 nba.py backfill [year]` | Backfill a season's completed regular-season games (`2025` = 2025-26) |
| `python3 nba.py lineups` | Store starters/minutes for every game that lacks them (resumable; run once) |
| `python3 nba.py report [YYYYMMDD]` | Matchups, who's injured, who's starting (projected if NBA.com hasn't posted yet) |
| `python3 nba.py predict` | One-shot predictions for today's games not yet started |
| `python3 nba.py predict --watch [--interval 300]` | Recheck every N seconds; print each game when its lineup first appears, changes, or is confirmed; stops when all games are confirmed or tipped |
| `python3 nba.py predict --confirmed-only` | Skip any game without an official confirmed lineup |
| `python3 nba.py eval` | Accuracy of stored predictions on finished games |
| `python3 build_features.py` | Build training matrices (`X_train.npy`, `y_train.npy`, `groups.npy`) |
| `python3 train_pregame.py` | Retrain the pre-game model |
| `python3 view_data.py` | Browse games / export CSV |

`predict` also catches up any finished games since the last run (and stores their lineups) before predicting.

### Reading the output

```
BKN @ ATL   [pregame_clogit_nba_expected_unconfirmed]
  ⚠️  LINEUP EXPECTED, NOT CONFIRMED — may change before tip
  1. <player> (ATL) — 13.6%  fair +634
```

*Fair odds* are the model's probability converted to American odds. A sportsbook price is only **+EV** if it is *longer* than the fair odds: at 13.6% (fair ≈ +634), +800 is value and +450 is not. Remember the model's probabilities carry real uncertainty.

---

## How the model works

A **conditional logit** (softmax over the 10 starters). It uses only information known before tip-off:

| Feature | Meaning |
|---------|---------|
| `is_home` | Player's team is home |
| `fb_rate_shrunk` | Season first-basket rate, shrunk toward 10% for small samples |
| `L10_fb_shrunk` | Same over the last 10 games, shrunk harder |
| `usage_rate`, `true_shooting`, `minutes_per_game`, `points_per_game` | Season box-score profile |
| `team_tipoff_win_pct` | How often the player's team has won the opening tip |

Trained on the **2025-26 regular season** (1,230 games) and tested going forward on **2026-27 games** (125 so far). Only the two most recent seasons are used, because rosters and roles change too fast for older data.

### Results (forward test, 125 games)

| Metric | Value |
|--------|-------|
| Top-1 hit rate | **15.2%** (random = 10%) |
| Log-loss | **2.268** (uniform guess = 2.303) |
| Calibration | Predicted top-pick probability ≈ actual hit rate |

With 125 games the margin of error is about ±6 points, so this is not statistically distinguishable from 10–20%. Results will be re-measured as the season adds games.

### What we learned (from our own data)
- The team that wins the opening tip scores the first basket **64.4%** of the time (792 of 1,230 games); the home team wins the tip 52.8%.
- An earlier model reported ~60% accuracy, but it used features taken from the first plays of the game, which already contain the first basket. That number is invalid for pre-game use and the model is retired (`model.txt` is kept for reference only).
- Public sources put the honest pre-game ceiling around 17–18% (e.g. StatsBench: 17.7% over 724 games, no ROI or closing-line data). Recent-form windows (L5/L10) add little.

---

## Data sources

| Source | Used for | Cost |
|--------|----------|------|
| ESPN via `sportsdataverse` | Schedules, play-by-play, box scores, injury report | Free |
| NBA.com daily lineups feed (`stats.nba.com/js/data/leaders/00_daily_lineups_YYYYMMDD.json`) | Expected/confirmed starters (appears during the game day) | Free |
| NBA.com live box score (`cdn.nba.com/static/json/liveData/...`) | Confirmed starters (backup) | Free |

These are unofficial public endpoints; they can change or be rate-limited. ESPN allows roughly 3 requests/sec (`SLEEP` in `nba.py`).

---

## Database

SQLite file `nba_first_basket.db`.

| Table | Columns |
|-------|---------|
| `games` | game_id, game_date (`YYYYMMDD`), home_team, away_team, home_score, away_score, status, tip_winner_team, created_at |
| `first_baskets` | game_id, scorer_player_id, scorer_name, scorer_team, shot_type, period, clock, x, y, created_at |
| `lineups` | game_id, team, player_id, player_name, is_starter, minutes |
| `predictions` | game_id, predicted_scorer_id, predicted_scorer_name, confidence, model_version, features_json, created_at |

"First basket" = the first made field goal (not a free throw) in periods 1–4. `predictions.model_version` records which lineup type was used (`…_nba_confirmed`, `…_nba_expected_unconfirmed`, `…_projected_usual_starters`). Re-running `predict` replaces a game's stored prediction.

---

## Files

```
NBA/
├── nba.py                  # CLI: init, backfill, lineups, report, predict, eval
├── starters.py             # lineups, injuries, projection, daily report
├── test_starters.py        # tests for starters.py
├── pregame_model.py        # features, probabilities, fair odds  (active model code)
├── pregame_model.json      # trained weights                      (active model)
├── train_pregame.py        # retrains pregame_model.json
├── build_features.py       # builds training matrices (legacy feature set)
├── train_model.py          # legacy LightGBM trainer (retired)
├── model.txt, model_meta.json   # legacy model (retired; leaky)
├── view_data.py            # DB viewer / CSV export
├── nba_first_basket.db     # SQLite database
├── player_game_logs.pkl    # per-player game-log cache (not auto-refreshed yet)
├── X_train.npy, y_train.npy, groups.npy, feature_names.json, meta.json
├── requirements.txt, README.md, BLUEPRINT.md
```

---

## Limitations

- **Pre-game accuracy is low.** Expect ~15%, not 60%. A single game is a one-event market; even a real edge loses for long stretches.
- **Lineups can change.** Anything not marked CONFIRMED may differ at tip; a bet on a player who doesn't start is typically voided. Run close to tip.
- **Projections are guesses** based on recent starts and the injury report. They ignore rest days and matchups.
- **Player stats can be stale.** `player_game_logs.pkl` isn't refreshed automatically yet (tracked in `BLUEPRINT.md`).
- **No odds feed.** Sportsbook scraping is blocked by bot protection and is not supported; compare the printed fair odds to prices by hand.
- **Unofficial endpoints** (ESPN, NBA.com JSON) can break without notice.

---

## References

- Dickinson College, research on NBA tip-offs (27,000+ tip-offs, 2000–2021): the tip winner scores the first basket far more often than not.
- StatsBench, "First Basket Predictions: The Data Method": two-stage structure (tip, then scorer given possession); reports a 17.7% pre-game hit rate.

Many commercial first-basket sites publish "factor weights" and stability claims without backtests or sample sizes; this project doesn't rely on them.

## License

MIT — free to use, modify, distribute.
