# NBA First Basket Scorer Bot

Predicts which **starter** will score the **first basket** — the game's first points, a made field goal **or a made free throw** (regulation only, the sportsbook rule) — in each NBA game, using free data (ESPN via `sportsdataverse`, plus NBA.com's public lineup feeds). It runs **before tip-off**, shows the lineup it used (confirmed / expected / projected), and prints a probability and fair odds for each player.

> **Read this first.** The pre-game model is deliberately modest: tested week by week on the 198 games of the current season, its top pick scored first **19.7%** of the time (random = 10%; simply picking the starter with the most minutes = 16.7%). That is a *shortlist generator*, not a proven betting edge. Sportsbook favorites are priced around 14–17% implied with a large margin, so treat picks accordingly. Not betting advice; gamble responsibly and only if you're of legal age where you live.

Project status, decisions, known issues and the to-do list live in [`BLUEPRINT.md`](BLUEPRINT.md).

---

## What it does

For each game today it:

1. Finds the **starting five** for both teams, in this order of trust:
   `CONFIRMED` (NBA.com daily lineups feed, then NBA.com box score) →
   `EXPECTED` (NBA.com lists a lineup but it isn't confirmed yet) →
   `PROJECTED` (each team's usual starters — start rate over the last 15 games, the last 3 weighted most — with anyone ruled Out/Doubtful swapped for their expected replacement and any open spot filled by the next player in line, and a missing center replaced by a center when the coach's own history doesn't say otherwise; Questionable and Day-to-Day players stay in, flagged, with an "if he sits" replacement; current rosters make traded players count for their new team).
   Replacements are picked from who started during the player's current absence, and the lineup is rebuilt from the current injury report on every run: a player who is cleared goes straight back in. Official `CONFIRMED` lineups are never changed.
2. Pulls the **ESPN injury report** (league feed plus each game's own report) and warns about any listed starter who's hurt.
3. Scores the 10 starters with a pre-game model and prints the top 5 with a probability, **fair American odds**, and each team's chance to win the opening tip.

Every game is labeled with the lineup type it used. Use `--confirmed-only` to skip anything that isn't official.

---

## Runs by itself (nothing to start — Mac on or off)

The private website is the main way to use it: **https://solomonkhalili.github.io/first-basket/** (login required;
accounts only by the owner: `python3 site_users.py add NAME`). Everything below is free, with no card anywhere.

| What | How often | Done by |
|---|---|---|
| Lineups (Expected → Confirmed), injuries, picks re-run | Every 3 min, 9 AM → last tip-off | GitHub cloud job (`website_live/live-picks.yml`, `publish.py --live`) in the public website repo |
| Website republished | On any change (≤ every ~6 min, GitHub Pages' limit) | same job |
| **FanDuel + DraftKings odds** | **On page open, then every ~30 s** | the page: FanDuel through a free Cloudflare relay, DraftKings directly from the viewer's browser |
| Live scores, first baskets, "Our #N pick" | Instant | the page, straight from ESPN |
| Yesterday view (yesterday's picks, first scorer, our rank, final scores) | With each update | same job / the app |
| Results, record, CSV, spreadsheet | As each game ends (after the day's last tip-off) | same job |
| Retraining | Nightly ~4 AM ET | same job |
| Keeping the job switched on | Every 10 min | Cloudflare timer (backup: this Mac, every 30 min) |
| This Mac's NBA folder (newest code from GitHub + the cloud's data; never overwrites edits made on the Mac) | Every 30 min | `sync_from_cloud.py` (`launcher/com.firstbasket.sync.plist`) |
| Health check: writes `HEALTH.md` in this folder (✅ all good, or ⚠️ what stopped updating); Claude reads it at the start of every session | Every 30 min while the Mac is on | `health_check.py` (run by `sync_from_cloud.py`) |
| Desktop app | Always running, live odds every 30 s | `gui.py` (`launcher/com.firstbasket.app.plist`) |

The model, code and data stay in the private repo; the website only holds encrypted results that each friend's
password unlocks in their browser. Full details: [`BLUEPRINT.md`](BLUEPRINT.md) §4a.

## Quick start

```bash
cd ~/Desktop/NBA
python3 -m pip install -r requirements.txt

python3 nba.py init                 # create the database
python3 nba.py backfill 2025        # 2025-26 regular season  (~10 min)
python3 nba.py backfill 2026        # 2026-27 so far
python3 nba.py lineups              # store who started every game (~10 min, once)
python3 build_features.py           # build training matrices
python3 train_pregame.py            # train the pre-game model (writes pregame_model.json)

python3 nba.py report               # today's matchups, injuries, starters
python3 nba.py                      # today's picks (same as: python3 nba.py predict)
python3 nba.py knicks               # just one team's game (team name or code, any case)
python3 gui.py                      # the same picks in your web browser (cards, colours, FanDuel odds)
```

If you already have the database and `pregame_model.json`, you only need the last two commands.

Run the tests any time: `python3 -m pytest` (237 tests, all in the `tests/` folder).

---

## Commands

| Command | What it does |
|---------|--------------|
| `python3 nba.py init` | Create the database tables |
| `python3 nba.py backfill [year]` | Backfill a season's completed regular-season games (`2025` = 2025-26) |
| `python3 nba.py lineups` | Store starters/minutes for every game that lacks them (resumable; run once) |
| `python3 nba.py report [YYYYMMDD]` | Matchups, who's injured, who's starting (projected if NBA.com hasn't posted yet) |
| `python3 nba.py` | Shows today's games as a numbered list and tells you what to type: the game's number, a team name, or just Enter for all games |
| `python3 nba.py knicks` | Only that team's game today — team name (Knicks, Lakers, Trail Blazers, Sixers…) or code (NYK, LAL…), any case. Best run ~30 minutes before tip-off, when the lineup is confirmed |
| `python3 nba.py --all` | Every game, without asking |
| `python3 nba.py predict` | Brings everything up to date first (new games, lineups, CSV, player stats, training data, model), then predicts today's games not yet started |
| `python3 nba.py predict --watch [--interval 300]` | Recheck every N seconds; print each game when its lineup first appears, changes, or is confirmed; stops when all games are confirmed or tipped |
| `python3 nba.py predict --confirmed-only` | Skip any game without an official confirmed lineup |
| `python3 gui.py` | Today's picks in your web browser at http://127.0.0.1:8765 — tap games to choose, press **Get picks**; also shows the track record. Same model, data update and pick log as `predict`. Ctrl-C in the terminal to stop |
| `python3 nba.py predict --no-odds` | Predict without fetching FanDuel's First Basket prices |
| `python3 nba.py eval` | Real track record: every logged pick on finished games, by lineup type and model version; plus **paper betting** — $1 on every +EV FanDuel price, settled after the games (nothing is really bet) |
| `python3 build_features.py [--incremental]` | Build training matrices (`--incremental` adds only new games) |
| `python3 train_pregame.py [--incremental]` | Retrain the model and run the week-by-week test |
| `python3 tip_model.py` | Accuracy check of the tip-off model |

Before predicting, `predict` (and `report`, `eval`) continues from the saved data: it adds finished games since the last stored date with their first basket, tip-off players and lineups, retries recent gaps, **rewrites `first_basket_data.csv` from the database**, adds new games to the player stats, and — when there are new games — updates the training data and retrains the model. Nothing is re-downloaded from scratch.

### Reading the output

Each game is a box that opens with a headline — the **top 2** most likely first scorers and the **predicted
tip-off winner by name** (with his chance and the opposing jumper) and **each team's chance to score first** — followed by the top 5 picks (chance + a bar +
fair odds), both starting fives (injured players coloured), and any notes
(replacements, players back from injury). The box border and label show the lineup type:
✅ CONFIRMED (green) · 🟡 EXPECTED (yellow) · 🟠 PROJECTED (orange, our guess). A "How to read this" box at
the bottom explains the columns. Data-update chatter is hidden; problems appear in a yellow **Warnings** box.

NBA.com posts expected lineups in the early afternoon ET and confirms them ~30 min before tip. ESPN is not
used for lineups: it doesn't publish starters until a game starts.

*Fair odds* are the model's probability converted to American odds. A sportsbook price is only **+EV** if it is *longer* than the fair odds: at 13.6% (fair ≈ +634), +800 is value and +450 is not. Remember the model's probabilities carry real uncertainty.

---

## How the model works

A **two-stage model** that uses only information known before tip-off:

1. **Which team scores first?** From each team's chance to win the opening tip (plus home court).
2. **Which starter on that team?** A conditional logit over the team's 5 starters, using the player inputs below.

A player's chance = his team's chance × his share within the team. Inputs built for each starter:

| Feature | Meaning |
|---------|---------|
| `is_home` | Player's team is home |
| `fb_rate_shrunk` | Season first-basket rate, shrunk toward 10% for small samples |
| `L10_fb_shrunk` | Same over the last 10 games, shrunk harder |
| `shot_attempts_per36`, `true_shooting`, `minutes_per_game`, `points_per_game` | Season box-score profile (shot attempts = FGA + 0.44·FTA per 36 minutes) |
| `team_tipoff_win_pct` | How often the player's team has won the opening tip |
| `early_shot_shrunk` | His share of his team's first 5 shot attempts in earlier starts (the team's early first option) |
| `tip_win_prob` | Chance the player's team wins tonight's opening tip (`tip_model.py`: the expected jumper for each team — its regular jumper, else NBA.com's listed center, else its most experienced starter — their past tip results and home court; earlier games only) |

Trained on the **2025-26 regular season plus every 2026-27 game so far** (1,428 games). Only the two most recent seasons are used, because rosters and roles change too fast for older data.

### Results (walk-forward test, 198 games of 2026-27)

Each week of the current season is predicted by a model trained only on games before that week, the way it is used in real life.

| Metric | Value |
|--------|-------|
| Top pick scored first | **19.7%** (random = 10%; best simple rule, season first-basket rate = 17.7%) |
| Log-loss | **2.232** (uniform guess = 2.303; lower = more accurate chances) |
| Calibration | Checked on 1,129 games: the chances match how often players actually score first |
| Tip-off model | Calls the tip winner **63.6%** of the time (always picking the home team: 47.5%); names the player who actually jumps ~88% of the time |

With 198 games the margin of error is about ±5.5 points, so this is not statistically distinguishable from the best simple rule. Results are re-measured every time the model retrains.

### What we learned (from our own data)
- The team that wins the opening tip scores the first basket **66.3%** of the time (1,005 of 1,515 games); the home team wins the tip 52.8% (2025-26).
- The first points are a free throw in 8.9% of games (135 of 1,515).
- An earlier model reported ~60% accuracy, but it used features taken from the first plays of the game, which already contain the first basket. That number is invalid for pre-game use; the model has been deleted.
- Public sources put the honest pre-game ceiling around 17–18% (e.g. StatsBench: 17.7% over 724 games, no ROI or closing-line data). Recent-form windows (L5/L10) add little.

---

## Data sources

| Source | Used for | Cost |
|--------|----------|------|
| ESPN via `sportsdataverse` | Schedules, play-by-play, box scores, injury report | Free |
| ESPN game summary (`site.api.espn.com`) | Starters + positions on days NBA.com posts no lineups (e.g. 11/02/26) | Free |
| NBA.com daily lineups feed (`stats.nba.com/js/data/leaders/00_daily_lineups_YYYYMMDD.json`) | Expected/confirmed starters (appears during the game day) | Free |
| NBA.com live box score (`cdn.nba.com/static/json/liveData/...`) | Confirmed starters (backup) | Free |
| FanDuel sportsbook feed (public, no login) | First Basket prices; live price feed every ~30 s | Free |
| DraftKings sportsbook feed (public, no login) | "1st Points Scorer" prices, read live by each viewer's own browser every ~30 s (DraftKings refuses cloud servers but lets browsers read it); best price across both books | Free |
| GitHub (Actions + Pages) | Cloud updates and the private website | Free |
| Cloudflare Workers (free plan) | Live-odds relay + the timer that keeps the cloud job on | Free, no card |

These are unofficial public endpoints; they can change or be rate-limited. ESPN allows roughly 3 requests/sec (`SLEEP` in `nba.py`).

---

## Database

SQLite file `nba_first_basket.db`.

| Table | Columns |
|-------|---------|
| `games` | game_id, game_date (`YYYYMMDD`, Eastern Time), home_team, away_team, home_score, away_score, status (3 = final), tip_winner_team, tip_winner_player_id/name, tip_loser_player_id/name, created_at |
| `first_baskets` | game_id, scorer_player_id, scorer_name, scorer_team, shot_type, period, clock, x, y, created_at |
| `lineups` | game_id, team, player_id, player_name, is_starter, minutes (ESPN box scores) |
| `official_lineups` | game_date, team, player_id, player_name, position, status — NBA.com's starting lineups for every played day |
| `first_shots` | game_id, team, player_id, player_name, made, shot_type — each team's first field-goal attempt (recorded for research; not a model input) |
| `early_shots` | game_id, team, seq, player_id, player_name, made — each team's first 5 field-goal attempts (feeds the early-shot share input) |
| `pick_log` | every pick: game_id, game_date, logged_at, matchup, lineup_type, lineup_source, lineup_sig, model_id, pick player/team/probability, tip_home, all 10 probabilities (JSON) |
| `predictions` | legacy (before 2026-11-17): one overwritten pick per game; no longer used |

"First basket" = the first points of the game in periods 1–4: a made field goal or a made free throw. Every pick is kept in `pick_log`; a new row is added only when a game's lineup type, lineup or model version changes, so re-running `predict` never overwrites history.

---

## Files

```
NBA/
├── season_backtest.py      # fills this season's scorecard for games before live picks began
├── experiments/            # test scripts for ideas not in the model yet (safe to run, change nothing)
├── nba.py                  # CLI: init, backfill, lineups, report, predict, eval
├── display.py              # the prediction screen
├── starters.py             # lineups, injuries, projection, daily report
├── tip_model.py            # chance each team wins the opening tip
├── pregame_model.py        # features, probabilities, fair odds  (active model code)
├── pregame_model.json      # trained weights + test results       (active model)
├── train_pregame.py        # retrains pregame_model.json and runs the week-by-week test
├── build_features.py       # builds training matrices and player game logs
├── picks.py                # pick log + track record (`eval`)
├── tests/                  # 237 tests (python3 -m pytest)
├── first_basket_data.csv   # every past game: who scored first (auto-regenerated; don't edit by hand)
├── my_predictions.xlsx     # open this: Scorecard, Games (our picks vs results), Lineups (expected vs confirmed vs actual)
├── nba_first_basket.db     # SQLite database
├── player_game_logs.pkl    # per-player game logs (refreshed automatically)
├── X_train.npy, y_train.npy, groups.npy, game_ids.json, feature_names.json, meta.json
├── gui.py, gui/            # the app (also the website's page); gui/static_site.html = website login + data layer
├── iphone_check.py         # screenshots of the site as an iPhone sees it (Today + Yesterday)
├── publish.py              # builds the encrypted website; --live keeps it updated all evening
├── site_users.py           # website accounts
├── fanduel_odds.py, ev.py  # FanDuel prices + value (EV)
├── draftkings_odds.py      # DraftKings prices
├── sync_from_cloud.py      # Mac: take GitHub's newest code + the cloud's data; start the cloud job if it went quiet
├── website_live/           # the cloud job, the Cloudflare relay + timer
├── launcher/               # Mac launch agents + Desktop app
├── requirements.txt, README.md, BLUEPRINT.md
```

---

## Limitations

- **Pre-game accuracy is low.** Expect roughly 15–18%, not 60%. A single game is a one-event market; even a real edge loses for long stretches.
- **Lineups can change.** Anything not marked CONFIRMED may differ at tip; a bet on a player who doesn't start is typically voided. Run close to tip.
- **Projections are guesses** based on recent starts, the injury report and current rosters (about 4 of 5 starters right before NBA.com posts). They ignore rest days and matchups.
- **Odds: FanDuel + DraftKings.** Value uses whichever pays more. DraftKings may block automated reading at any time (then FanDuel only). Always confirm the price on the bet slip.
- **Website delays:** odds ~30 s, lineups/picks ~3–6 min (GitHub Pages allows ~10 publishes an hour).
- **Unofficial endpoints** (ESPN, NBA.com JSON) can break without notice.

---

## References

- Dickinson College, research on NBA tip-offs (27,000+ tip-offs, 2000–2021): the tip winner scores the first basket far more often than not.
- StatsBench, "First Basket Predictions: The Data Method": two-stage structure (tip, then scorer given possession); reports a 17.7% pre-game hit rate.

Many commercial first-basket sites publish "factor weights" and stability claims without backtests or sample sizes; this project doesn't rely on them.

## License

MIT — free to use, modify, distribute.
