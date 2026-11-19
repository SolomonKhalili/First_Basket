# NBA First Basket Scorer — Project Blueprint

> **Last updated:** 2026-11-19 midday (big day: bets tracker + parlays, best bets, DraftKings via ESPN, field-goal model for DraftKings, us-vs-sportsbooks scoreboards, odds logging opening→closing, auto-sync every minute — see "Where we left off" in §0) · **Owner:** Solomon · **Folder:** `~/Desktop/NBA`
>
> Single source of truth for the project's state: what works, how well, what is known to be wrong, and
> what comes next. Update it in the same session as any change (checklist in §11). Every number here was
> measured on the project's own data; anything unverified is marked as such.

---

## 0. Summary — read this first

**Goal.** Before each NBA game, estimate for each of the 10 starters the probability that he scores the
game's **first points — a made field goal or a made free throw** (regulation only; the sportsbook "first
basket" rule, owner's decision 2026-11-17), show the top picks with fair odds,
and — later — compare those odds with sportsbook prices.

**Status (2026-11-17).**
- The full pipeline works from one command, `python3 nba.py` (same as `python3 nba.py predict`): it brings all data up to date, resolves
  tonight's starting lineups, scores every starter and prints one card per game.
- **Model performance (graded 2026-11-19 with the live formula):** #1 pick right **20.0%** this season (43/215),
  **15.3%** last season (190/1,238, Nov 1 → Finals), **16.0%** all time (random 10%, "most minutes" ≈ 14%, published
  StatsBench model 17.7%). Last season is lower mainly because early-season weeks had little to learn from. Against
  **DraftKings' closing odds** (307 games, field-goal model, same 10 starters): we were closer 155, DraftKings 152;
  log-loss 2.2401 vs 2.2487 — about even with the book's final line.
- **Tip-off model:** calls the opening-tip winner **63.6%** of the time this season (always-home 47.5%).
- No open fixes. Owner's workflow: run `python3 nba.py` about 30 minutes before tip-off — it shows today's games as a numbered list and asks which one (type the number, a team name, or Enter for all); or name the team directly, e.g. `python3 nba.py knicks`.
- Each game card names the **top 2 picks** and the **predicted tip-off winner by player**. This is a
  **shortlist tool, not a proven betting edge.**
- **Odds + value (built 2026-11-18/19):** FanDuel (first POINTS, free throws count) judged with the main model;
  DraftKings (first FIELD GOAL only — owner + web check 11/19) judged with a separate **field-goal model**. Paper
  betting, closing-line value ("does the sportsbook agree"), us-vs-sportsbooks scoreboards and a **real bet log
  (My bets, straight + parlays)** are built. Kelly stakes are not.

- **Runs by itself, 24/7, free, Mac on or off (2026-11-18, owner's request).** A private, login-protected website
  (https://solomonkhalili.github.io/first-basket/) for the owner and friends: the cloud (GitHub Actions) re-checks
  lineups, injuries and odds every 3 minutes from 9 AM to the last tip-off, re-runs the picks and republishes;
  FanDuel odds on the page refresh every ~30 s through a free Cloudflare relay; results are filed as each game ends;
  the nightly update (4 AM ET) retrains. A Cloudflare timer keeps the job running; the Mac is only a backup. Details §4a.

**Where we left off — read this first in a new session.** *(Keep this block current at the end of every
session: §11 step 0. Full details of every change are in §10, newest first.)*

*Last session ended 2026-11-19, ~12:30 PM ET* (Mac desktop session, owner on the phone via Remote Control).

**Right now**
- **Everything is shipped and running; nothing half-built.** 249 tests pass. The Mac's NBA folder follows GitHub
  every minute (code + the cloud's data) — no manual pull needed any more.
- **To watch tonight (first real runs):** the 30-second FanDuel checks in the last 15 min before the 8 PM tip-offs
  (closing price + "Market closed" time in Odds moves), and after midnight the first overnight odds checks
  (`kind=odds`, "Market opened"). If either misbehaves, `HEALTH.md` or the Odds moves tab will show it.
- **DraftKings rules (don't forget):** its first-scorer bet pays on the first FIELD GOAL; its team market (ESPN calls
  it "First Team to Score") is "Team to Score 1st FG". FanDuel's First Basket and Team to Score First count free
  throws. Each book is judged with its own chances; DraftKings team prices are shown, not valued.
- **DraftKings blocks cloud servers on purpose — never work around it.** The cloud reads DraftKings through ESPN's
  public odds feed (measured 11/19: ~2–8 min behind); the Mac app and viewers' browsers add live prices.
- **Owner's working style (hard):** plain kid-simple words on the site and in replies; short answers; do only what
  was asked and ask before side quests; every change goes live everywhere at once (GitHub + website + relay + Mac);
  never let a failing test ship.

**Coming up (dated)**
- **Around the All-Star break:** re-test adding play-in/playoff/Finals games to training (tested 2026-11-19 on 215 games of 2026-27, walk-forward: without 20.9% / 2.2378, with 20.5% / 2.2362 — no clear gain, not adopted).
- **On/after 2026-12-08:** re-check the two inputs adopted 11/18 (opponent by position, head-to-head) and the 20%
  simple-rule mix — §7.1 item 4. Keep only what is still better in both test periods.
- **~2026-12-20 (150+ finished games with saved odds):** test blending in sportsbook odds
  (`experiments/market_blend_test.py`, §10 11/18 "accuracy pass"). Log-loss decides.
- **Around the All-Star break:** re-test adding 2024-25 tip-offs (`old_tips`, §7.1 item 5).

**What was done, and when** (one line each; details in §10)
- **11/07–11/09:** first version of the bot (several 11/09 "done" items turned out not to exist — fixed 11/17).
- **11/17:** full review and overhaul: two-stage model + early-shot share, tip-off model rebuilt with NBA.com's
  listed centre, calibration checked, CLI picks by game, 115 tests.
- **11/18 (day):** Mac app (`gui.py`) with EV guide, season records, Data view; Desktop app with icon; accounts;
  FanDuel odds + EV + paper betting; private login website; cloud updates every 3 min from 9 AM, nightly retrain
  4 AM; Cloudflare odds relay + timer.
- **11/18 (evening):** DraftKings odds read by each viewer's browser; iPhone-friendly odds table; first-basket
  pop-up; cards kept after tip-off with their last saved lines.
- **11/18 (late):** model accuracy pass — **adopted** opponent-by-position + head-to-head inputs and a 20%
  simple-rule mix; rejected recency weighting, team first-possession, jumper height, tree model.
- **11/18 (night):** DraftKings line log (`dk_odds_log`); timer's GitHub key fixed; results filed even after the last tip.
- **11/19 (12–1 AM):** Today / Yesterday switch; AWAY / HOME labels; orange missed-pick colours; iPhone and desktop
  layout fixes (`iphone_check.py`); paper betting explained for beginners.
- **11/19 (morning, cloud session):** docs caught up; ESPN fallback for lineup days NBA.com leaves blank (11/02/26);
  fixed a test that wrote real DraftKings lines into the database; this handoff section. Learned: NBA.com's early
  lineups before 11/17 are gone for good (don't chase); NBA.com's servers refuse cloud computers, ESPN doesn't.
- **11/19 (~7:45 AM):** the Mac's 30-min sync now also takes GitHub's newest code by itself (safe: never
  overwrites Mac edits). Tested on practice copies: update, no-op, clash skipped, identical untracked file replaced.
- **11/19 (7:30 AM – 12:30 PM, Mac session):** Mac jobs pinned to Python 3.14 (Homebrew's 3.15 lacked the libraries);
  HEALTH.md health check (no emails — owner's choice); My bets (per login, encrypted on the relay; straight +
  parlays); Tonight's best bets; old-gap repair (8 never-played games removed, 4 real playoff games restored from
  NBA.com, LAC@CHI filled); game types (playoffs filed, kept out of training); track record this/last/all-time with
  grades; grading fix (re-created picks now use the live 20% mix: 20.0% / 15.3% / 16.0%); playoffs-in-training and
  "lean on fb rate later" tested — not adopted; closing-line value + us-vs-FanDuel boxes; team to score first (both
  books); price ↓/↑ + Odds moves tab (every change, opening → closing, market opened/closed); DraftKings via ESPN
  (cloud, history for every past game); field-goal model for DraftKings (first_fg for all 1,533 games; better in both
  test periods); us-vs-DraftKings all season; times in the viewer's time zone; #1 gold / #2 blue; 30-second checks
  before tip-off + overnight checks; live job restarts itself with new code; Mac syncs every minute.

**Talking to the owner:** plain words, short answers, one clear next step. Not technical; got lost in git/branch
talk on 11/19 — keep git out of the conversation (hard rule 8) and just say what was saved. Owner wants
`README.md` / this file kept up to date without being asked.

**Hard rules (decisions already made).**
1. Use only the two most recent seasons (2025-26 and 2026-27); rosters and roles change too fast for older data.
2. Train on **regular-season games only** (2025-26 playoffs excluded).
3. Evaluate with a **walk-forward test** only: each week of 2026-27 is predicted by a model trained on games
   before that week. Never report in-sample numbers or random splits.
4. Pre-game features must be knowable before tip-off. Never use the `early_*` columns (first 10 plays) or the
   actual tip winner of the game being predicted.
5. Every lineup is labelled **CONFIRMED / EXPECTED / PROJECTED**; a projected lineup is never shown as official.
6. Data updates continue from saved data ("save file"); nothing is re-scraped from scratch.
7. Keep this file current and factual. Never mark something done without verifying it.
8. Don't discuss git with the owner (owner's decision, 2026-11-17). GitHub is used for hosting and automation
   since 2026-11-18 (private repo SolomonKhalili/First_Basket = code + data; public repo SolomonKhalili/first-basket
   = the encrypted website + the live job). Commits are made as part of that, not as a topic.
9. Free services only, **no credit card** anywhere (owner's decision, 2026-11-18). The model, code and data never
   leave the private repo unencrypted; the public website holds only encrypted results.
10. Friends' accounts only with the owner's approval; viewers can look, never run the model.

**Environment.** Python 3.14 on macOS. Data from ESPN (via `sportsdataverse`) and NBA.com public JSON feeds.
SQLite database. Screen output via `rich`. Tests via `pytest`.

**History note.** Several items recorded as done on 2026-11-09 were not actually implemented. They were found
and fixed in the 2026-11-17 review (§10). Treat any older claim that is not reflected in §2–§4 as unverified.

---

## 1. How the system works

### 1.1 Data flow

```
ESPN scoreboard + play-by-play ─► nba.py (catchup / backfill) ─► nba_first_basket.db
                                                                  ├ games         (teams, date, tip winner + loser)
ESPN box scores ────────────────► starters.store_game_lineup ───►├ first_baskets (first made FG per game)
                                                                  ├ lineups       (starters + minutes per game)
                                                                  ├ first_shots   (each team's first shot attempt)
                                                                  └ pick_log      (every pick, never overwritten)
NBA.com daily lineups (each played day) ─► starters.store_official_lineups ─► official_lineups (starters + positions)
ESPN box scores ────────────────► build_features.build_player_game_logs ─► player_game_logs.pkl
ESPN team rosters (teams playing today) ─► starters.fetch_rosters ─► current team of each player

NBA.com daily lineups feed ─┐
NBA.com live box score ─────┴─► starters.py ─► tonight's five per team (CONFIRMED > EXPECTED > PROJECTED)
ESPN injury feed + per-game injury summaries ─┘   (Out/Doubtful replaced; Questionable/Day-to-Day kept and flagged)

player stats + team stats + tip_model ─► pregame_model.py ─► P(first basket) per starter ─► display.py
```

### 1.2 What `nba.py predict` does (in order)

1. **Sync (`sync_data`)** — continues from saved data:
   - catch up finished games from the last stored date (that date is re-checked; already-labelled games are skipped),
     storing first basket, tip-off players and box-score lineups;
   - retry games from the last 14 days that are missing a first basket or a lineup;
   - store NBA.com's official starting lineups (with positions) for every played day not stored yet;
   - delete first-basket rows with no matching game (preseason / All-Star);
   - rewrite `first_basket_data.csv` from the database;
   - add box scores for new games to `player_game_logs.pkl`;
   - if any labelled game is not in the training matrices yet (whatever its date): `build_features.py
     --incremental` then `train_pregame.py --incremental` (the previous model is kept if the new one tests worse).
2. **Today's games** from ESPN (dates and times in Eastern Time). Games that have already started are skipped.
3. **Injuries, rosters and positions:** ESPN league feed plus each game's ESPN summary (the summaries carry
   Doubtful/Questionable); current ESPN rosters of tonight's teams (a traded player counts for his new team)
   with each player's position (G/F/C; NBA.com's lineup history fills gaps).
4. **Lineups**, most to least trustworthy:
   1. NBA.com daily lineups feed with every starter `Confirmed`;
   2. NBA.com live box score (published about 30 minutes before tip-off);
   3. NBA.com daily lineups feed `Expected`, with players ruled out since then replaced;
   4. **our projection**: each team's usual starters (start rate over its last 15 games, the last 3 weighted
      4×), Out/Doubtful players replaced by whoever started in their place during the current absence (a
      missing centre, failing that, by the next centre in line), and any open spot filled by the next player in line. Measured on 366 team-games of 2026-27 without injury
      information: 82.1% of starters right, all five 41.0%.
   ESPN is not used for lineups: its pre-game data contains no players (verified 2026-11-17).
5. **Score** the 10 starters, add the pick to `pick_log` (`picks.py`; never overwritten, no duplicates), and print
   one card per game (`display.py`).

### 1.3 The model

- **Type: two stages** (adopted 2026-11-17; `pregame_model.py`, trained by `train_pregame.py`, weights in
  `pregame_model.json`):
  1. **Team:** P(home team scores first) = sigmoid(a + b · logit(P(home team wins the opening tip))). Current:
     a = +0.128, b = +0.357.
  2. **Player:** conditional logit over the 5 starters of the team that scores first, player inputs only
     (`fb_rate_shrunk`, `L10_fb_shrunk`, `shot_attempts_per36`, `true_shooting`, `minutes_per_game`, `points_per_game`,
     `early_shot_shrunk`).
  P(player scores first) = stage 1 for his team × stage 2. The same functions run in training and on game day.
- **Inputs built per starter (9):** `is_home`, `fb_rate_shrunk` (season first-basket rate shrunk toward 10%),
  `L10_fb_shrunk`, `shot_attempts_per36` ((FGA + 0.44·FTA) per 36 minutes), `true_shooting`, `minutes_per_game`,
  `points_per_game`, `team_tipoff_win_pct` (not used by the two-stage model), `tip_win_prob` (chance the
  player's team wins tonight's opening tip, from `tip_model.py`; drives stage 1), `early_shot_shrunk` (his share
  of his team's first 5 shot attempts over earlier starts, shrunk toward 1 in 5 with 25 imaginary shots; added
  2026-11-17).
- **Training matrices** (`X_train.npy`) hold exactly the 9 stored inputs in `build_features.NUMERIC_FEATURES`;
  `tip_win_prob` is computed by `train_pregame.py` / `nba.py`. The leaky first-10-plays columns and other unused
  columns were removed on 2026-11-17.
- **Current stage-2 weights** (standardised): shot attempts +0.15, fb_rate +0.11, early-shot share +0.10,
  true_shooting +0.09, points −0.05, L10 −0.03, minutes +0.00. L2 = 0.001 (chosen on 2025-26 only).
- **Training data:** 1,428 regular-season games (2025-26: 1,230; 2026-27: 198), through 2026-11-16.
- **Player inputs** use only games before the game date, so this season's roles and minutes are already
  reflected in the inputs; training learns how much each input matters.

### 1.4 The tip-off model (`tip_model.py`)

- A team's **jumper** (nobody announces it before the game) is chosen from tonight's starters:
  1. a starter who took 6+ of the team's last 10 tips (its clear regular jumper);
  2. otherwise the starter **NBA.com lists at C** (available once NBA.com posts the lineup, ~1 PM ET);
  3. otherwise the starter who jumped most for the team in its last 10 games;
  4. otherwise the starter with the most opening tips in our data, for any team.
  Checked on all 396 team-games of 2026-27 against NBA.com's posted lineups: this names the actual jumper
  **88.1%** of the time (always the listed C: 86.9%; without positions: 85.1%). Before NBA.com posts (our
  projection), there is no listed C and steps 1, 3, 4 apply. Training uses the same rule: the listed C of every
  past game comes from the stored `official_lineups`.
- Each jumper's tip win rate uses only games before the game date, shrunk toward 50% (10 imaginary tips).
- P(home wins tip) = log5 of the two rates plus home-court advantage (also measured from earlier games only).
  Home and away probabilities always add to 1. Nothing is stored; it is recomputed from the database.
- `python3 tip_model.py` prints the point-in-time accuracy check.

---

## 2. Data state (2026-11-17)

| Item | State |
|------|-------|
| `games` | 1,524 games, 2025-10-21 → 2026-11-16. Dates are ESPN scoreboard dates (Eastern Time); status 3 (final) |
| Labelled games (`first_baskets`) | Every stored game has a label (2026-11-19: of the 9 old gaps, 8 were never played — 4 postponed, 4 unneeded playoff games — and were removed; LAC @ CHI 1/20/26 filled from ESPN's summary feed). `nba.repair_old_gaps` re-asks ESPN once a week about any older game without a label; `_played` stops postponed games being stored as finished; `get_pbp` falls back to the summary feed when the library's feed is empty |
| 2025-26 regular season | 1,230 labelled games (Oct 21 – Apr 12) — training data |
| 2025-26 playoffs | 87 labelled games (incomplete) — excluded from training |
| 2026-27 season | 198 labelled games (Oct 20 – Nov 16) — training data and walk-forward test |
| `lineups` (ESPN box scores) | 1,517 games through 2026-11-16. 7 old games unavailable (ESPN HTTP 403, permanent) |
| `official_lineups` (NBA.com) | 15,150 starters with positions for 239 played days (Oct 2025 → Nov 16 2026); every name matched to an ESPN player; 1,500 of 1,515 labelled games have a listed C for both teams. One day had no file. **11/02/26** (NBA.com posted no starters) is filled from ESPN's box scores (status `ESPN`, see §10 2026-11-19) once the update runs with that code |
| Tip-off players | Winner recorded for all 1,515 labelled games, loser for 1,513 |
| `player_game_logs.pkl` | 647 players, through 2026-11-16; refreshed by every `predict` run |
| Training matrices | `X_train.npy` 15,150 × 13 (1,515 games × 10 starters; stored inputs incl. early-shot share/count and 2 unused first-shot columns), game list in `game_ids.json` |
| `early_shots` | Each team's first 5 field-goal attempts in order (player, made/missed) for all 1,515 labelled games; recorded on every ingest |
| `first_shots` | Each team's first field-goal attempt (player, made/missed) for all 1,515 labelled games; recorded on every ingest |
| `pick_log` | Every pick `predict` makes (started 2026-11-17). One new row per game whenever the lineup type, the lineup or the model changes |
| `predictions` | Legacy: 22 overwritten picks from older model versions (before 2026-11-17). No longer written or read |
| `first_basket_data.csv` | 1,515 rows, 13 columns, rewritten from the database on every run (all past games: who scored first) |
| `my_predictions.xlsx` | Excel file (opens in Numbers/Excel), **this season only** (2026-27, `picks.SEASON_START`), three tabs. **Scorecard** tab (`picks.scorecard`): season line, finished games / re-created vs live / still to play today, then correct / out of / percent for our #1 pick, our #1 or #2 pick and the tip-off winner, plus "ALL OUR PREDICTIONS COMBINED". **Games** tab: one row per game, newest first, frozen shaded header with a filter button, set column widths — **Pick made** (Live = run before the game; Re-created = games before live logging, made afterwards week by week), **Lineup used** (re-created picks used the real starters, so they read Confirmed), our final pre-game pick #1/#2 with chances, predicted tip-off winner and chance, predicted team to score first and chance (shown, not counted), actual first scorer, how, actual tip winner, green YES / red NO / grey pending, and two running counts (all predictions; #1 pick). **Lineups** tab (`picks.lineup_rows`): one row per team per game, newest first — NBA.com expected starters (earlier in the day), NBA.com confirmed starters (near tip-off), actual starters (ESPN box score), and "Expected = actual?" / "Confirmed = actual?". Written by `spreadsheet.py` (no extra package). Rewritten on every run |

**Measured facts.**
- The team that wins the opening tip scores first **66.3%** of the time (819/1,230 = 66.6% in 2025-26; 126/198 = 63.6% in 2026-27). (Field goals only, before 2026-11-17: 64.4%.)
- The home team wins the tip 52.8% of the time in 2025-26 (51.9% across all 1,515 games) and scores first 53.8% of the time.
- The first points are a free throw in **135 of 1,515 games (8.9%)**.
- Random pick among 10 starters = 10%.

---

## 3. Model performance

### 3.1 Current (walk-forward, 198 games of 2026-27)

Each week of 2026-27 is predicted by a model trained only on games before that week.

| Approach | Top pick scored first | Log-loss (uniform = 2.3026) |
|----------|----------------------:|----------------------------:|
| **Active: two-stage + early-shot share** | **19.7%** (39/198) | **2.2318** |
| Two-stage without early-shot share (previous active) | 20.7% | 2.2369 |
| One-stage model | 19.7% | 2.2453 |
| One-stage, trained on 2025-26 only | 18.2% | 2.2468 |
| Baseline: season first-basket rate | 17.7% | — |
| Baseline: shot attempts per 36 min | 16.2% | — |
| Baseline: most minutes per game | 14.6% | — |
| Baseline: points per game | 14.6% | — |
| Baseline: last-10-games first-basket rate | 12.1% | — |
| Random | 10.0% | 2.3026 |

All numbers use the free-throws-included labels (since 2026-11-17). Results under the old field-goal-only labels
are in §3.3; they are not directly comparable.

**How to read this.**
- 95% interval on 198 games ≈ ±5.6 points: 20.7% is not statistically distinguishable from the best simple rule
  (17.7%).
- **Two-stage vs one-stage, game by game (198 games):** log-loss better by 0.0084 (bootstrap 95% range −0.0014 to
  +0.0186; better in 95% of resamples); top pick right in 4 games only the two-stage got vs 2 only the one-stage
  got. **Second check** (both trained on Oct–Dec 2025, tested on 733 games Jan–Apr 2026): a tie — 13.6% vs 13.6%,
  log-loss 2.2935 vs 2.2924 (difference −0.0011, noise). Adopted: clearly ahead on the current season, never
  meaningfully worse; the keep-previous check stays on.
- Adding the first-shot share to stage 2 gave 20.2% / 2.2365 (no meaningful change) — not used.
- **Early-shot share (adopted):** real training procedure, both periods — log-loss 2.2368 → 2.2318 (2026-27) and
  2.2781 → 2.2763 (Jan–Apr 2026); top pick −2 games this season, +3 games Jan–Apr. Probabilities improve in both.
- **Tested and rejected the same day:** free-throw attempts per 36 (log-loss worse in both periods); season
  shrink strength 100 instead of 30 (looked better with L2 fixed, but the real procedure then chose L2 0.3 and
  scored 19.2% / 2.2378 — worse; kept 30).
- **Head-to-head (2026-11-17, owner's request):**
  - *Tip-off, jumper vs jumper record* — rejected. Point-in-time on 1,216 games (Dec 2025 → now); the two predicted
    jumpers had met before in 39% (2025-26) / 49% (2026-27) of games. Blending their head-to-head record into the
    tip chance made log-loss worse at every strength (2025-26 0.6309 → 0.6317…0.6599; 2026-27 0.6299 → 0.6300…
    0.6534) and never called more tips right. The overall jumper rate already contains those meetings; the extra
    weight only adds noise from 1–3 tips.
  - *Tip-off, record against a certain team* — rejected (`experiments/tip_vs_team_test.py`, same 1,216 games).
    Tonight's jumper vs that team (whoever jumped for them), all meetings or this season only, and team vs team
    this season. Every version made 2025-26 worse (log-loss 0.6309 → 0.6329…0.6560). In 2026-27 only team-vs-team
    was a hair better (0.6299 → 0.6290, +2 tips called; history in just 20% of games). Not better in both seasons,
    so not used. (In 2025-26 "all" = "this season", because the data starts that season.)
- **Hand-set weights review (2026-11-17, owner: "would you weigh anything differently?")** — no change made.
  - *Tip-record trust* (`tip_model.K`, imaginary 50/50 tips per jumper; `experiments/tip_shrink_test.py`):
    log-loss 2025-26 / 2026-27 — K=3 0.6342/0.6411, K=5 0.6311/0.6344, **K=10 0.6309/0.6299 (current, best in
    both)**, K=20 0.6356/0.6315, K=40 0.6452/0.6391. Kept 10. (K=40 calls 5 more tips this season but its
    chances are badly off, and it is worse last season.)
  - *Recent games counting more in training* (weight 0.5^(age/half-life), both stages;
    `experiments/recency_test.py`). With the real L2 procedure, half-lives 30–480 days gave 2026-27 log-loss
    2.2307–2.2444 vs 2.2318, Jan–Apr 2026 2.2690–2.2710 vs 2.2763 — but weighting also made the procedure pick a
    larger L2 (0.1–0.3). Control (`--control`): no weighting at L2 0.1 → 2.2317 / 2.2712, i.e. the Jan–Apr gain
    comes from the L2 change; weighting alone (480 days, L2 0.001) → 2.2304 / 2.2761 — noise-sized. Rejected.
  - *Observation, not acted on:* L2 0.1 looks as good as 0.001 on 2026-27 and better on Jan–Apr 2026. Choosing
    L2 from test results would be peeking, so the fixed procedure stays; if the L2 procedure is ever revisited,
    use a week-by-week validation inside 2025-26 rather than reading the test periods.
  - *First basket, player vs tonight's opponent* — promising, not yet adopted. Input: his first-basket rate in
    earlier starts against this team, shrunk toward his overall rate (M imaginary games). Real L2 procedure, both
    periods. Current 20.2% / 2.2318 (2026-27), 15.1% / 2.2763 (Jan–Apr 2026). All meetings, M=5: 19.2% / 2.2391,
    15.1% / 2.2754. All meetings, M=15: 20.2% / 2.2428, 15.3% / 2.2741. **This season only (trade-aware), M=5:
    21.2% / 2.2289, 15.1% / 2.2754** — the only version better in both periods, but the gain is small (+2 games;
    log-loss −0.003 / −0.001) and it was the best of three versions tried. Players average only 0.8–1.1 earlier
    meetings with an opponent, so the signal is thin. Trades are why "this season only" works best: last season's
    meetings were often against a different roster.
- **Calibration (checked 2026-11-17 on 1,129 week-by-week games, Dec 2025 → Nov 2026): accurate.** Players given
  ≈8% scored first 7.9% of the time, ≈10% → 10.0%, ≈13% → 13.4%, ≈16% → 17.1%; top pick 16.0% vs average top-pick
  chance 14.7%. The 2026-27 gap (15.7% vs 20.7%) was mostly small-sample luck. A "sharpening" adjustment fitted on
  past weeks made later weeks slightly worse (2.2368 → 2.2381; reverse direction also worse), so none is applied:
  the probabilities and fair odds are used as they come.
- Published reference: ≈17.7% pre-game hit rate (StatsBench, 724 games, 2024-25; field-goal definition unclear),
  described as the ceiling. Sportsbook favourites are priced around +400 to +800 (14–17% implied) with ~20%
  margin, so **no edge is demonstrated**. Every future change must be compared against the best simple rule.

### 3.2 Tip-off model (point-in-time)

| Season | Tip winner called | Always-home | Log-loss (coin flip = 0.6931) |
|--------|------------------:|------------:|------------------------------:|
| 2026-27 | **63.6%** (126/198) | 47.5% | **0.6299** |
| 2025-26 (from Dec 1) | **62.3%** (634/1,018) | 53.9% | **0.6309** |

Progression on 2026-11-17 (2026-27 / 2025-26 log-loss): rebuilt model 0.6367 / 0.6339 → most-experienced-starter
fallback 0.6354 / 0.6323 → NBA.com's listed C in training too 0.6299 / 0.6309.

### 3.3 History (superseded; kept for the record)

| Date | Model / test | Top-1 | Log-loss | Note |
|------|--------------|------:|---------:|------|
| 2026-11-17 | One-stage model, free throws counted (before the two-stage switch) — walk-forward | 19.7% | 2.2453 | |
| 2026-11-17 | **Field-goal-only labels**, final model (official-C jumper rule) — walk-forward | 17.7% | 2.2520 | Baselines then: minutes 16.7%, fb rate 15.7% |
| 2026-11-17 | Before the official-C jumper rule in training (walk-forward) | 18.2% | 2.2519 | |
| 2026-11-17 | Trained 2025-26 + 2026-27, walk-forward, before the jumper fallback | 17.2% | 2.2516 | |
| 2026-11-17 | Rebuilt tip feature, trained 2025-26 only, L2 0.03; forward test 198 games | 18.2% | 2.2497 | Before walk-forward |
| 2026-11-17 | Model with broken tip feature, re-scored on 198 games | 16.7% | 2.2559 | Tied the minutes baseline |
| 2026-11-09 | With broken tip feature; forward test 139 games | 15.1% | 2.2633 | Tip feature was invalid |
| 2026-11-07 | First conditional logit; forward test 125 games | 15.2% | 2.268 | |
| 2026-11-07 | LightGBM ranker, pre-game features; 125 games, 5 seeds | 8.8–16.0% | — | Not calibrated; dropped |
| 2026-11-07 | Legacy `model.txt` with in-game features | ~60% | — | **Invalid** (label leakage) |
| 2026-11-07 | Legacy `model.txt`, in-game features removed | 10.7% | — | True pre-game skill |
| 2026-11-09 | Old tip model "70.1%" | — | — | **Invalid** (in-sample) |
| 2026-11-09 | `nba.py eval` 3/7 | 42.9% | — | Noise; mixed model versions |

---

## 4. Components

| Component | File(s) | Status |
|-----------|---------|--------|
| Database schema | `nba.py init` | ✅ |
| Ingestion: backfill + catchup (first basket, tip players, lineups) | `nba.py` | ✅ A scoring play or jump ball with no team is never guessed (no label → retried) |
| One-command sync before predicting | `nba.py sync_data` | ✅ Used by `predict`, `report`, `eval`; also stores NBA.com's official lineups per played day |
| Player game logs (incremental) | `build_features.build_player_game_logs` | ✅ Only fetches new games |
| Training matrices (incremental) | `build_features.py --incremental` | ✅ Adds every labelled game not yet included, whatever its date; refuses to mix column layouts |
| Model training + walk-forward test + keep-previous check | `train_pregame.py` | ✅ Trains the two-stage model and prints the one-stage score for comparison; exit code 3 = previous model kept |
| Pre-game model | `pregame_model.py`, `pregame_model.json` | ✅ Two-stage (team, then player): 20.7% top pick; ahead of the best simple rule (17.7%) but within noise (§3) |
| Tip-off model | `tip_model.py` | ✅ 63.6% of tip winners on 2026-27; jumper: regular jumper → NBA.com's listed C → recent jumper → most experienced starter (88.1% right); same rule in training and on game day |
| Lineups + injury logic | `starters.py` | ✅ Projection tuned (82.1% of starters, all five 41.0% on 2026-27 without injury info); open spots filled; current rosters for traded players; missing centres replaced by centres (backtested) |
| Official lineups store | `starters.store_official_lineups` | ✅ NBA.com starters + positions for every played day |
| Injury report | `starters.fetch_injury_report` | ✅ League feed + per-game summaries in `predict` and both `report` views |
| Prediction screen | `display.py` | ✅ One card per game: top 2 picks, the predicted tip winner by name, each team's chance to score first (two-stage model), top-5 table, starters with injuries; lineup type colour-coded; warnings box; legend |
| Watch mode | `nba.py --watch` | Optional, not part of the workflow (owner prefers running ~30 min before tip). Rechecks every 5 min |
| Team choice | `python3 nba.py` / `python3 nba.py knicks` | ✅ Plain run shows a numbered list of today's games (full team names) with instructions on what to type; answer with the game number, a team name, or Enter/Ctrl-D for all (wrong answers get a hint and the question again; `--all` skips the question); team names and nicknames (Knicks, Trail Blazers, Sixers, Cavs…) or codes, any case |
| Daily report | `nba.py report` | ✅ One box per game, injury warnings highlighted |
| Pick log + track record | `picks.py`, `nba.py eval` | ✅ Every pick kept; `eval` by lineup type and model version. First results after the 2026-11-17 games |
| CSV export | `nba.py export_csv` (rows from `nba.csv_rows`, also shown in the GUI's Data view) | ✅ 13 columns, one writer |
| Predictions spreadsheet | `picks.export_predictions` → `my_predictions.xlsx` (Scorecard + Games tabs) | ✅ Our pick vs what happened, per game; written after every `predict` and every sync |
| FanDuel odds + EV | `fanduel_odds.py`, `ev.py`; `predict`, `gui.py`, `eval` | ✅ 2026-11-18: prices, EV per $1, best value, `odds_log`, paper betting on +EV (≥ +$0.01 per $1) |
| Bankroll / stakes / real bet log / CLV | — | 🔲 Not started (deliberately) |
| First-shot data + share | `nba.first_shots`, `build_features.first_shot_share` | ✅ Recorded for every game; tested as a model input and not adopted (§7.1) |
| Early-shot data + share | `nba.early_shots`, `build_features.early_shot_share` | ✅ Each team's first 5 attempts recorded for every game; **used by the model** (stage 2) |

**Tests:** 203 passing in 16 files (`python3 -m pytest`, 2026-11-19). Earlier breakdown (2026-11-17): 115 passing — `test_starters.py` (45), `test_pipeline.py` (29: sync, retrain decision, incremental build incl. late-labelled games, label guards, tip-rate rule), `test_display.py` (10), `test_tip_model.py` (8), `test_picks.py` (19), `test_pregame_model.py` (4). The incremental-build test was checked by re-introducing the old zero-games bug: it fails, as it should.

### 4a. App, website and automation (2026-11-18)

| Component | File(s) | Status |
|-----------|---------|--------|
| Desktop app (GUI) | `gui.py`, `gui/index.html` | ✅ Picks cards, live scoreboard (ESPN), tip-off matchups, EV guide, **Yesterday / Today switch** (Yesterday tab on the left: yesterday's game tiles with the picks made before tip-off, last saved FanDuel line, actual first scorer, our rank for him and final score — `gui.day_cards`, `/api/yesterday`; the website gets the same from `publish._yesterday`), AWAY / HOME labels on every tile and card (left = away, right = home), paper betting explained for beginners (stat boxes, "how to read this", what each lost bet would have paid), missed-pick bars and tags in orange, Data view (Scorecard / Our picks / Lineups / All games with search, season and player filters); tap games to show only those; started games keep the picks made before tip-off ("Picked before tip-off", rebuilt from `pick_log` if needed); exact pick number of the first scorer ("Our #2 pick"); always dark |
| Live FanDuel odds on the page | `gui/index.html` (`refreshOdds`), `gui.py` `/api/fd/prices`, `fanduel_odds.fetch_prices` | ✅ Read on page open, then every 30 s, from FanDuel's small price feed (~7 KB/game) by market + selection id (`card.fd_market`, set by `nba._fanduel_card`); EV and best deal recomputed in the page; 20 s shared cache; backs off on errors. Prices matched FanDuel exactly in a live check |
| DraftKings odds | `draftkings_odds.py`, `nba._draftkings_prices`, `nba._best_prices`; page `bookCell` | ✅ 2026-11-18 (owner's decision): DraftKings' "1st Points Scorer" (subcategory 16305 — first points incl. free throws, same rule as FanDuel and the model), read from the JSON DraftKings' site loads, one request for every game. Cards show FanDuel + DraftKings; Value and Best deal use the better price and name the book. **DraftKings refuses cloud servers** (GitHub's job gets an HTTP error) **but allows any website to read its feed from a browser** (`Access-Control-Allow-Origin: *`), so the page reads DraftKings straight from each viewer's own phone/computer on open and every 30 s (`refreshDK` in gui/index.html) — no Mac, no server. Verified 2026-11-18 10:07 PM (both open games, ★ and best deal from DraftKings). A Mac → cloud fallback (`draftkings_odds.py --push`, branch `dk-prices`) was tried and removed (owner: nothing may depend on the Mac); the cloud still uses a pushed snapshot if one is ever under 10 min old. Paper betting / `odds_log` stay FanDuel-only. Live check: all 30 starters matched (accents included); prices differ by book (Dončić FD +330 / DK +365). DraftKings refuses requests without browser-style headers; the owner accepted that it may block or change this |
| Accounts (Mac app sharing) | `accounts.py` | ✅ Hashed passwords, owner/viewer; no length rules (owner's choice) |
| Private website | `publish.py`, `gui/static_site.html`, `site_users.py`, `site_users.json` | ✅ Same page as the app; results encrypted (AES-256-GCM), each friend's password unlocks the key in the browser; remember-me 30 days; view-only. Accounts: `python3 site_users.py add NAME` (solomon, bhass, simon) |
| Live cloud job | `website_live/live-picks.yml` (runs in the public repo: unlimited free minutes), `publish.py --live` | ✅ Every 3 min from 9 AM: full update + picks; republishes on any change (≤ ~every 6 min: GitHub Pages limit); after the last tip-off files each result as its game ends (`nba.file_finished_games`), then stops; re-reads accounts each check; public log says only "published" / "no change" (details in the private `run.log`) |
| Nightly update | same job, `kind=once` | ✅ ~4 AM ET: results, CSV, spreadsheet, retraining, republish |
| Odds relay + timer | `website_live/odds-relay.js`, `website_live/wrangler.jsonc` → Cloudflare Worker `first-basket-odds` (GitHub repo SolomonKhalili/first-basket-odds, auto-deployed) | ✅ Relay live (`first-basket-odds.solomonkhalili.workers.dev/prices`); only the price feed, only for the website. Timer every 10 min starts the live job if nothing is running and the last run is over 10 min old (was 25 until 2026-11-19: manual updates delayed the 9 AM start) (needs the `GH_TOKEN` secret, added 2026-11-18). ⏳ First automatic start not yet observed (due 2026-11-19 from 9 AM) |
| Today's cards memory | `gui.keep_todays_cards`, `todays_cards.json` | ✅ A card stays after tip-off; a card rebuilt from the log never replaces a saved one; matched by matchup |
| Mac sync + backup | `sync_from_cloud.py`, `launcher/com.firstbasket.sync.plist` | ✅ Every minute (was 30 min until 2026-11-19): **takes GitHub's newest code (`main`) when safe** (`update_code` / `code_update_plan`, 2026-11-19: never overwrites a file edited on the Mac that GitHub also changed, skips if the Mac has its own unsaved-to-GitHub commits, restarts the Mac app if any of its code changed); downloads the cloud's data into the NBA folder; starts the cloud job if it has gone quiet (only while the Mac is on) |
| Health check | `health_check.py` (run by `sync_from_cloud.py`), `HEALTH.md`, `.claude/settings.json` | ✅ 2026-11-19: every 30 min the Mac checks website, data file, Cloudflare relay + timer permission, this morning's nightly save, live job (last run OK, started within 90 min, 10 AM–midnight) and itself (libraries, app answering, code/data syncing — 3 misses in a row before those count) and writes `HEALTH.md` (status + problem history; no emails — user's choice). Claude reads it at every session start (SessionStart hook) |
| My bets (real bet tracker) | `bets.py`, `/api/bets` + `/api/results` (gui.py and gui/static_site.html), page `openBets` / `openBetDialog`, relay `/bets` | ✅ 2026-11-19 (owner's request): tap a player on Today's cards → pick FanDuel / DraftKings / Other (buttons show each book's price, ★ = better) → type the amount. Every website account — including new ones, no setup — has its own list on the Cloudflare relay (KV key `bets:<slot>`, slot = username, or "h-" + SHA-256 for names with other characters; no expiry), encrypted in the browser with the data key; writes need a write code from the data key (relay keeps its SHA-256). Settled on the page from the first scorer (bundle `results` for filed games, live board for tonight): profit, bets won, money bet, return. The Mac app uses the same relay (owner slot `solomon`). Backup: the live job copies every list into the database table `my_bets` every 30 min (saved with the data → Mac). Tested 2026-11-19 in Chromium + WebKit iPhone 15 (save, settle won/lost/waiting, delete, stranger write refused) |
| Tonight's best bets | page `bestBets` / `renderBestBets` (gui/index.html), above the cards | ✅ 2026-11-19 (owner's request): every starter in games not yet started whose better price (FanDuel unless closed, DraftKings) has EV ≥ VALUE_MIN, best first; top 5, "Show all N"; each row: player, game, tip time, chance, book + odds, ¢ per $1. Tap a row → My bets form with that book. Redraws as odds move (renderResult, updateCardsLive); hidden on Yesterday. Tested WebKit iPhone 15 |
| Track record: this season + overall | `picks.summary` (`final` = this season, `overall` = every graded game), page `renderRecord`, header line (`nba.predict_today`), `season_backtest.fill_last_season` | ✅ 2026-11-19 (owner's request): the panel shows this season's #1-pick rate (ring), a Last season (2025-26) box and an All time box, each graded. Overall adds all of last season from Nov 1, 2025 (`LAST_SEASON_FROM`; the first ~10 days had nothing earlier to learn from) through the Finals on June 13, 2026 — play-in, playoffs and Finals included (owner's request) — re-created week by week with models trained only on earlier games (pick_log `BACKTEST`, filled in by sync_data in the cloud; re-built automatically when `LAST_SEASON_PLAN` changes, table `backtest_meta`). Now: 18.6% (40/215) this season, 15.1% (220/1,453) overall (last season alone 14.5%: regular season from Nov 1 14.6%, playoffs 13.8%); random 10%. Each number gets a grade + reason (`rateVerdict`): Poor <12, Weak <15, Decent <17.7, Good <22, Excellent; benchmarks random 10%, most-minutes ~14%, StatsBench 17.7%, ceiling ~26–30%; ±range shown under 400 games. The spreadsheet stays this season only. The header line now uses the same numbers (it showed the model file's test_top1 before) |
| Live job picks up new code | `publish.newer_code` / `code_changed` in `publish.live` | ✅ 2026-11-19: each check compares GitHub's main with the code the run started with; if code or the page changed (not accounts / relay address / docs / tests) it saves the data and ends, and the queued run (GitHub's 20-min timer, or the Cloudflare timer) starts with the new code — so changes pushed during the day reach the website within minutes instead of after the run (up to ~6 h). It leaves `app/.restart`; the workflow's last step then starts a fresh live run itself (`actions: write`), so the hand-off doesn't wait for a timer. While waiting between checks it looks for new code every minute (`publish.nap`), and the job keeps downloaded packages between runs (setup-python `cache: pip`) |
| Season check + NBA.com backup | `nba.audit_season` (`SEASONS_TO_AUDIT`), `nba_com.py`, `nba.nba_com_backup`, table `game_types` | ✅ 2026-11-19: every day of 2025-26 (Oct 21 – Jun 13) compared once with ESPN's schedule; 4 real playoff games ESPN has no play-by-play for (DEN@MIN 4/30, CLE@DET 5/7 + 5/13, NY@CLE 5/23 — wrongly removed earlier the same day by the gap repair, which now never treats "not found" as "not played") were filed from NBA.com's play-by-play + box score, players matched to ESPN ids by name; checked identical to ESPN on 6 playoff games ESPN has (first scorer, clock, tip, starters, first shots). Every game now stores its type (2 regular, 5 play-in, 3 playoffs): results of play-in/playoff games are filed (they weren't), and training leaves them out by type in every season, not only by last season's dates. Last season now: 1,231 regular-season + 91 play-in/playoff games, all labelled |
| Parlays (My bets) | `bets.clean` (type parlay), `betClean` (static_site.html), page `setBetMode` / parlay slip `pSlip` / `parlayDlg`, My bets tabs `betTabs` | ✅ 2026-11-19 (owner's request): bet form has Straight bet / Parlay tabs; Parlay adds the player as a leg (one per game; same game swaps) to a slip bar at the bottom (kept per device until saved, today only). Review: pick FanDuel / DraftKings / Other — every leg re-prices at that book and the combined odds are worked out (editable), type the amount, save. Stored as one bet with legs (2–12). My bets has Straight bets / Parlays tabs, each with its own profit; a parlay wins only if every leg scores first (✓/✗ per leg, "1 of 3 hit" while waiting). Players in today's parlays get a 🎟 tag on the cards |
| Closing line value | `ev.clv_results`, `ev.implied`, page `clvBlock` (Track record panel, above paper betting) | ✅ 2026-11-19 (from outside notes, #4): for each player first called a good deal (EV ≥ VALUE_MIN) in a finished game, FanDuel's price then vs its last price before tip-off (odds_log). Shows % of good deals that got shorter, average move our way, and the same for all players as a yardstick; "too early" under 30 price moves. First reading (16 games): 73% of moved good-deal prices shortened (19 vs 7), +4.4% vs +1.3% for all players |
| Team to score first | `fanduel_odds.parse_team_first` / `fetch_team_first` (game page, 1st Quarter tab), `nba._team_first_odds`, page `teamFirstLine`, best bets | ✅ 2026-11-19 (outside notes #7): FanDuel's "Team to Score First" price per team next to our team chance on each card ("Scores first"), with a plain verdict; good team bets also appear in Tonight's best bets (not tappable to log yet). FanDuel only — DraftKings refuses servers |
| Us vs FanDuel (who guesses better) | `ev.market_results`, page `marketBlock` (Track record panel) | ✅ 2026-11-19 (outside notes #1): each finished game's last FanDuel board, its prices turned into chances with the book's cut removed (scaled to 100% over listed players); whoever gave the real first scorer the higher chance "was closer". Also log-loss of each. First reading (12 games): FanDuel closer 7, us 5; log-loss us 2.2254 vs FanDuel 2.1955. The blend test (market + model) stays scheduled for ~Dec 20 |
| Price moves through the day | `ev.opening_prices` (odds_log + dk_odds_log), card `opening`, page `priceMove` / `moveArrow`, best bets ⏰ | ✅ 2026-11-19 (owner's idea): under every FanDuel/DraftKings price a ↓ (payout dropped since the day's first price — book thinks he's more likely) or ↑; hover/tap shows opened → now. A good bet whose payout is dropping gets "⏰ bet soon" in Tonight's best bets. Legend line in "How to read this" |
| Odds moves tab + Mac DraftKings fetch | `ev.odds_moves` → Data tab "📈 Odds moves" (`gui.data_tab('odds')`); `gui.dk_relay` → `/api/dk/markets` | ✅ 2026-11-19 (owner's requests): one row per player per book per game — opening price, closing price, dropped/rose/same, times it changed — from both logs (odds_log, dk_odds_log), filter by move. The Mac app's page now gets DraftKings from the Mac's own server (like FanDuel via /api/fd; shared 20 s), falling back to the browser; the website still reads DraftKings in the viewer's browser |
| Odds logged all day, both books | live job from 6 AM (workflow cron + `kind=skip` before 6, timer `hour >= 6`); `gui.dk_logger` → `draftkings_odds.push_to_relay`; Odds moves "Every change (time)" | ✅ 2026-11-19 (owner's request: every change, opening to closing): FanDuel logged by the cloud every check from 6 AM until tip-off. DraftKings refuses GitHub AND Cloudflare (403 from the relay, checked 2026-11-19), so the Mac app reads DraftKings every 3 min (6 AM – midnight) and adds changes to the relay's shared log like a viewer's browser; viewers' browsers still add too. Since the same day the cloud also reads DraftKings via ESPN (see below), so DraftKings is logged even with the Mac off — every few hours from ESPN, every few minutes when the Mac or a viewer is on. The Odds moves tab lists every change with its time |
| Viewer's own time zone; #1/#2 highlight | page `localTime` / `tipTime` / `cardStart`; `ev._clock` sends UTC `{…Z}`; rows `pick1` / `pick2` | ✅ 2026-11-19 (owner's requests): game start times, tile times, best-bets times and the Odds moves timeline show in each viewer's own time zone (checked LA = PST, London = GMT). Our #1 pick's row is gold, #2 blue, in every game's table. DraftKings from the cloud: blocked for GitHub and Cloudflare; not worked around (their block is deliberate) — the relay's /dk-check test was removed; DraftKings is logged by the Mac app + viewers' browsers |
| DraftKings via ESPN (no Mac needed) | `draftkings_odds.fetch_via_espn` / `parse_espn_props` / `log_espn`; used by `nba._draftkings_prices` when DraftKings refuses (the cloud) | ✅ 2026-11-19: ESPN's public odds feed (provider 100 = DraftKings, `…/odds/100/propBets`, all pages) republishes DraftKings' "First Basket" prices with the OPENING price and when each changed. Matched DraftKings exactly when fresh; ESPN refreshes every few hours. The cloud now gets DraftKings for every game all day and logs it (opening price stamped 00:00:00 = "opening" in the timeline); the Mac app and viewers' browsers add DraftKings' live prices in between (a fresher Mac/browser snapshot wins per game) |
| Team to score first: both books, logged | `draftkings_odds.parse_espn_props` ("First Team to Score" + opening), `log_team_first` / table `team_first_log`, `nba._team_first_odds`, page `teamFirstLine` (tf-table) | ✅ 2026-11-19 (owner's request): each card shows FanDuel and DraftKings team prices (↓/↑ since opening); DraftKings' is its "Team to Score 1st FG" (field goals only — ESPN labels it "First Team to Score"; matched the DraftKings app exactly 2026-11-19), so it is shown labelled "(1st field goal)" and NOT used for value, which is FanDuel's first-points market only; FanDuel logged each check, DraftKings via ESPN with its official opening price; team rows ("GS — team scores first") in the Odds moves tab |
| Field-goal model for DraftKings | `nba.first_field_goal` / `fill_first_fg` (table `first_fg`), `train_pregame.py` → `pregame_model_fg.json`, `nba._best_value`, page `recompute` / `bestBets` / `bookCell` | ✅ 2026-11-19: DraftKings' player bet pays on the first FIELD GOAL (free throws don't count; owner + web check). Every game's first made field goal stored (136 free-throw-first games re-read from play-by-play; in 127 a different player); a second model trained on those labels with every retrain (better at the first field goal in both test periods: 2026-27 log-loss 2.2473→2.2466, Jan–Apr 2026 2.2718→2.2707, #1 pick 14.2%→14.7%). FanDuel prices are judged with the first-points chances, DraftKings' with the field-goal chances; the better deal gets the ★ and drives Value / best bets. Saved with the data and synced |
| DraftKings odds history | `draftkings_odds.backfill_history` (table `dk_history`), run by `nba.sync_data` (all remaining games in one go) | ✅ 2026-11-19 (owner's request): ESPN's feed keeps DraftKings' opening and closing first-basket (and team 1st-FG) prices for finished games — every game this season, some of last season (e.g. Jan 15 yes; Dec 1, Mar 10, Apr 12 no). Stored in dk_odds_log / team_first_log stamped 00:00:00 (opening) and 23:59:59 (closing). FanDuel history isn't available anywhere public (ours starts 2026-11-18). "Beat Vegas" = compare against the CLOSING price |
| Mac copy ~minutes behind the server | `publish.SAVE_EVERY` (live job saves the data when the DB changed, at most every 10 min), launch agent every 60 s, `sync_from_cloud.due_full_check` | ✅ 2026-11-19 (owner: "we're making changes every couple minutes"): was save every 30 min + Mac sync every 30 min (up to ~1 h behind). Now the Mac checks every minute for new code and new data (downloads only what changed); the backup nudge + health check still every ~25–30 min. Mac app/NBA folder ≤ ~10 min behind the server |
| Exact opening + closing | `publish.watch` / `quick_odds` (last 15 min before each tip: FanDuel prices every 30 s, close time saved), `publish.odds_only` (workflow kind `odds`, cron every 30 min midnight–6 AM), table `market_times` (`ev.note_market`), Odds moves "Market opened" / "Market closed" | ✅ 2026-11-19 (owner's request): the closing price is within ~30 s of FanDuel closing and the close time is recorded; overnight checks catch when FanDuel first posts a market (within ~30 min) with its true opening price (overnight rows have no model chance: prob 0, ev 0, so they never count as value). DraftKings: opening price exact from ESPN, open/close times not known |
| Us vs DraftKings, whole season | `season_backtest.fill_fg_grades` (table `fg_grades`, run by sync_data when training grows), `ev.dk_market_results`, page `marketBlock` (both books) | ✅ 2026-11-19 (owner's request): the field-goal model's honest week-by-week picks for 1,453 past games vs DraftKings' CLOSING odds (ESPN history), both judged over the same 10 actual starters (DraftKings' board also lists players who didn't start). First reading, 307 games: we were closer 155, DraftKings 152; log-loss us 2.2401 vs DraftKings 2.2487 (random 2.3026). The box shows DraftKings (all season) and FanDuel (since Nov 18) in plain words |
| Card layout: books side by side + team section | page `bookTable`, `teamSection` | ✅ 2026-11-19 (owner: "separate sections (columns) per sportsbook"): one table per card with a FanDuel column group (Chance = first basket incl. free throws, Odds) and a DraftKings group (Chance = first FIELD GOAL, Odds); green odds = good bet, ↓/↑ since opening; a one-line key above. "🏀 First team to score" box underneath with the same columns (teams as rows; DraftKings chance = field-goal chances summed by team). The old "Scores first" bar was removed (same info). (Tabs version of the same day replaced.) |
| 📈 Odds page | `ev.odds_track` (bundle `odds_track`, `/api/odds_track`), page `openOdds` / `oddsCell` / `spark` | ✅ 2026-11-19 (owner's request): a 📈 Odds button; each of today's games, every player (and team to score first) with a FanDuel box and a DraftKings box: opened → now, ↓/↑, number of changes, a step chart of the chance over the day (up = more likely), biggest movers first; the cards' live prices are added as "now". Same day: ESPN's DraftKings price is only logged when newer than the last logged one (it lags DraftKings; logging it after a live Mac/browser price made prices look like they flipped back and forth — 11/19's early history still has some of those false flips) |
| Missing price: not offered / not posted / couldn't load | `nba.book_status` → card `book_status`, page `missing` | ✅ 2026-11-19 (owner: Edey not on DraftKings today): instead of "–", a missing price says "not offered" (we read the book; it doesn't offer him), "not posted yet" (the game's market isn't up) or "couldn't load" (we couldn't read the book) — on the player table, the team box and the Odds page |
| Mac app always on | `launcher/com.firstbasket.app.plist`, Desktop "First Basket Picks.app" | ✅ Starts at login, restarted if it stops. Runs on `/opt/homebrew/bin/python3.14` (2026-11-19: Homebrew's `python3` moved to 3.15 without the project's libraries and game data stopped loading) |
| One-time setup scripts | `setup_live_updates.sh`, `setup_odds_relay.sh` | ✅ Both run 2026-11-18 (the relay ended up deployed from the Cloudflare dashboard via GitHub instead) |
| Old private-repo workflow | `.github/workflows/update-site.yml` | Stands down on its timer (`LIVE_ENABLED=true`); still runs if started by hand; publishes to `gh-pages` |

---

## 5. Daily use

**Nothing needs to be run** (2026-11-18): the website and the Mac app update themselves (§4a). Open the website or
the Desktop app. Add a friend: `python3 site_users.py add NAME` (they type their password). Optional by hand:

```bash
cd ~/Desktop/NBA
python3 nba.py              # numbered list of today's games; type a number, a team name, or Enter for all
python3 nba.py knicks       # just that team's game (team name or code, any case; several allowed)
python3 nba.py --all        # every game, without asking
python3 nba.py report       # matchups, injuries, starters
python3 nba.py eval         # real track record of every logged pick
python3 nba.py help         # all commands
python3 -m pytest           # expect 182 passed
```

**When to run.** About **30 minutes before the game's tip-off**: NBA.com has confirmed the lineup (✅), so the
prediction uses the official starters. NBA.com posts *expected* lineups in the early afternoon ET (seen at 1:06 PM
on 2026-11-17); before that, every lineup is our projection (🟠). `my_predictions.xlsx` records every pick, and the
next run fills in the results.

**Reading fair odds.** A sportsbook price is only worth considering if it is longer than the fair odds
(e.g. fair +500 → a book price of +700 is value, +400 is not). The model's probabilities carry real uncertainty.

---

## 6. Known limitations and open questions

**Accuracy and evaluation**
- The pick log started on 2026-11-17, so there is no real-world track record yet; it needs a few weeks of games
  before the numbers mean much (same ±5-point noise as the walk-forward test).
- 198 test games are too few to separate 15–20% models; the walk-forward interval narrows as the season goes on.

**Lineups**
- Our projection (before NBA.com posts, ~1 PM ET) is right for about 4 of 5 starters; all five for about 41% of
  teams without injury information. **With the day's injury report** (live check 2026-11-17, compared with
  NBA.com's expected lineups at 2:21 PM): 48/50 starters (96%), all five for 8/10 teams; injury swaps such as
  Duncan Robinson → Kevin Huerter (DET) matched NBA.com. Run in the afternoon or use `--watch` for official lineups.
- **Position source (2026-11-17):** NBA.com's lineup history now comes first, ESPN's roster label only for players
  NBA.com never listed (the two disagree for ~21% of players; ESPN calls 27 regular NBA.com centres "F", e.g.
  Sandro Mamukelashvili — C in 12 of 13 NBA.com starts). Backtest replacements: Jan–Apr 2026 39.6% → 40.2%,
  2026-27 55.7% → 56.7%. Today's morning guess vs NBA.com: **50/50 starters** (LAL fixed: Kessler → Mamukelashvili).
- **Positions in replacements (2026-11-17):** forcing the same position for every missing starter made
  replacements worse in a 1,641 team-game backtest (coaches often swap a guard for a forward). Only centres use
  position, and only after the coach's own history: backtest replacements 39.5% → 39.6% (Jan–Apr 2026) and
  55.2% → 55.7% (2026-27) — a small gain; it fixed UTAH on 2026-11-17 (Hayes out → Mo Bamba, as NBA.com listed).
  The LAL Kessler case was then fixed by the position-source change above.

**Tip-off**
- Nobody announces who takes the opening tip; the jumper rule is right about 88% of the time. When the jumper has
  few recorded tips (e.g. a backup centre), his rate is shrunk toward 50% and the call is close to a coin flip.

**Hosting and automation (2026-11-18)**
- Website delay: odds ~30 s (relay); lineups/injuries/picks ~3–6 min (3-min checks, Pages allows ~10 publishes an
  hour); scores and first baskets instant (ESPN in the browser).
- GitHub's own timer in the new public repo had not fired by 9 PM on its first night; the Cloudflare timer (every
  10 min) and the Mac backup cover it. Confirm on 2026-11-19 that a run starts by itself after 9 AM.
- `GH_TOKEN` (fine-grained GitHub token, Actions read/write on `first-basket` only) expires on the date chosen at
  creation; when GitHub emails about it, make a new one and replace the Cloudflare secret. The token was also pasted
  in chat once; the owner chose to keep it (small scope).
- **DraftKings** refuses cloud servers; on the website it's read by each viewer's own browser (allowed by its feed).
  Not yet seen from a mobile-data connection or another state. If DraftKings ever blocks it, cards show FanDuel only. It may
  stop working without notice; the app then falls back to FanDuel only (note on the page). Fanatics has no web odds
  (app only), so it can't be added this way.
- NBA.com's `cdn.nba.com` scoreboard refuses cloud servers (403); the daily lineups feed supplies the game id, and a
  refusal now gives one note per run, not one per game.
- Cloudflare free plan: 100,000 requests a day (expected use ~2%); over the limit the relay stops until midnight UTC
  and the page falls back to the cloud's odds. No card on file, so nothing can be charged.

**Data**
- The old `predictions` table (22 picks, 3/15 = 20.0% on finished games) mixes several older model versions,
  including the broken tip feature; it is kept untouched for reference and is no longer used.

**Verified / resolved 2026-11-17 (no longer open)**
- Name matching: all 15,150 NBA.com starter names in `official_lineups` match an ESPN player — no nickname
  alias table needed.
- Wrong/misleading inputs, late-labelled games, unguarded labels, traded players, the fifth-starter guess, the
  `report` injury source and the training/game-day jumper difference were all fixed (§10).
- NBA.com's confirmed status string is exactly `"Confirmed"` (all 170 starters on 11-15 and 11-16).
- Stored game dates are Eastern-Time scoreboard dates; the UTC problem affected only today's games in
  `predict` and is fixed.

---

## 7. Roadmap

### 7.1 Next (in priority order)

Owner's order: **fix everything first, then build forward.**

**Fixes remaining**
- None. (The planned live evening check of `--watch` was dropped with watch mode from the workflow; the
  "Confirmed" status handling is verified on NBA.com's files for 11-15 and 11-16.)

**Build forward (next)**
1. ~~**First-shot share**~~ — **tested 2026-11-17, not adopted.** Each team's first field-goal attempt is now
   recorded for every game (`first_shots`; backfilled for all 1,515 games). The first scorer was his team's first
   shooter in 70.4% of games, but the first shooter is close to random from game to game: a team's most frequent
   first shooter takes only 24.1% of its first shots (random among 5 starters = 20%). Walk-forward: without
   19.7% / 2.2453, with the stat 19.2% / 2.2462, with stat × tip chance 20.2% / 2.2453 (noise). The data and the
   `first_shot_share` / `first_shot_starts` matrix columns are kept for future models; the model does not use them.
2. ~~**Two-stage model**~~ — **adopted 2026-11-17** (20.7% / 2.2369 vs 19.7% / 2.2453; §3.1).
3. ~~Smarter step 1~~ — tested 2026-11-17, not adopted (team "scored first" history / tip conversion: 2026-27
   58.1% → 60.6% but Jan–Apr 2026 55.1% → 54.3%; log-loss change ≤ 0.0003 there).
4. ~~Matchups + head-to-head~~ — **adopted 2026-11-18 (late)**, see §10 "accuracy pass": opponent by position
   (`opp_pos_share`) and first basket vs tonight's opponent, this season only (`vs_opp_rate`), both in
   `context_features.py` and live in `pregame_model.py`. **Re-check on/after 2026-12-08** with
   `python3 experiments/combined_test.py`: keep them only if still better in both test periods (their weights are
   small, −0.07). The 20% simple-rule mix (`SIMPLE_MIX`) is re-checked the same day. Tip-off head-to-head stays
   rejected. (Earlier 11/17 notes said "not added yet"; superseded.)
5. ~~Accuracy ideas (2026-11-17)~~: early-shot share **adopted**; free-throw rate and shrink-strength tuning
   rejected (§3.1). Remaining idea: add the 2024-25 season (≈1,230 more games) — **decided 2026-11-17: not now**; keep
   2025-26 + this season. Tested 2026-11-17 for tips only: more tip calls right, but first-basket picks worse —
   not used; `old_tips` kept for a re-test around the All-Star break (§10).
6. Then the odds layer (§7.3), once `eval` has a few weeks of real results.

**Next (2026-11-18)**
- Verify on 2026-11-19 that the live job starts by itself (Cloudflare timer / GitHub timer) and the website shows
  the green "FanDuel odds live" line; confirm tonight's results were filed by the job, not the 4 AM run.
- Renew `GH_TOKEN` before it expires.

**Done (2026-11-17)**
- ✅ Keep every pick (`pick_log`, new `eval`).
- ✅ Code health: shared stat code; engine tests; unused code/files removed; `games.status` filled; new `report` screen.
- ✅ Model inputs: `usage_rate` renamed `shot_attempts_per36`; tip rate ignores games without a recorded tip;
  9 unused/leaky columns removed from the matrices.
- ✅ Late-labelled games reach training; label guards for plays without a team.
- ✅ NBA.com's official lineups stored for every played day; training and game day use the same jumper rule.
- ✅ `report` uses per-game injury summaries; current rosters for traded players; name matching verified.
- ✅ Projection tuned (+4.9 points of starters, +12 points all-five on 2026-27); open spots filled.
- ✅ Free throws count as the first basket: all 1,524 games re-checked, 126 labels changed, matrices rebuilt offline, model retrained.
- ✅ Position-aware replacements, backtested: centres only, after the coach's history (all-position version rejected).
- ✅ Two-stage model adopted (team, then player).

### 7.2 Later

- Second injury source (official NBA injury report PDF).
- Re-check calibration once the season has more games (it was accurate on 1,129 games on 2026-11-17).

### 7.3 Odds layer

- ✅ 2026-11-18: EV per $1 from the model's chance and FanDuel's price (`ev.py`); prices saved per predict run
  (`odds_log`, unchanged snapshots skipped); paper betting = $1 on every +EV price in each finished game's
  last snapshot, settled against `first_baskets` (`ev.paper_results`).
- Still to do (only after the paper results hold up over a few hundred bets): closing-line value,
  fractional-Kelly stakes (0.5–1% of bankroll), and a real bet log with ROI, CLV and hit rate.

### 7.4 Model roadmap

| Phase | Content | Status |
|-------|---------|--------|
| 1. Tip-off model | Jumpers from play-by-play; point-in-time tip rates; `tip_win_prob` feature; retrain | ✅ 2026-11-17 |
| 1b. Learn the current season | Train on 2025-26 + 2026-27; walk-forward test; keep-previous check | ✅ 2026-11-17 (no measurable gain yet) |
| 1c. Tip-off with official lineups | NBA.com's listed C in training and on game day | ✅ 2026-11-17 (tip log-loss 0.6354 → 0.6299) |
| 2. First-shot share | Parse first FGA per team; shrunk per-starter share; add feature; walk-forward test | ✗ tested 2026-11-17, no gain (first shooter ≈ random); data kept |
| 3. Two-stage model | Team-scores-first model × player-given-team model; compare | ✅ 2026-11-17 (calibration still open) |
| 4. Position and opponent defence | Position per starter; opponent defence vs position | 🔲 |
| 5. Calibration and tracking | Calibration check; accuracy by lineup type | ✅ calibration checked — accurate, no adjustment (2026-11-17) · ✅ weekly walk-forward test · ✅ pick log by lineup type |

Each phase must beat both the previous model and the minutes baseline in the walk-forward test; stop a phase
that does not.

---

## 8. File map

```
NBA/
├── season_backtest.py      # re-creates this season's picks before live logging began (week by week)
├── experiments/            # read-only re-test scripts (head-to-head, matchups) — change nothing
├── nba.py                  # CLI: init, backfill, backfill_tip_players, lineups, report,
│                           #      predict [--watch | --confirmed-only], eval, test
├── display.py              # prediction screen (rich)
├── starters.py             # lineups (NBA.com), injuries, projection, daily report
├── tip_model.py            # P(team wins the opening tip), point-in-time
├── picks.py                # pick log + track-record summary
├── pregame_model.py        # feature row, two-stage probabilities, fair odds
├── context_features.py     # opponent-by-position + head-to-head inputs (point-in-time)
├── pregame_model.json      # active model weights + walk-forward results
├── train_pregame.py        # training, walk-forward test, keep-previous check
├── build_features.py       # training matrices (incremental) + player game logs
├── tests/                  # 203 tests in 16 files (python3 -m pytest)
├── nba_first_basket.db     # SQLite: games, first_baskets, lineups, official_lineups, first_shots, early_shots, pick_log (+ legacy predictions)
├── player_game_logs.pkl    # per-player game logs
├── X_train.npy, y_train.npy, groups.npy, game_ids.json, feature_names.json, meta.json   # training matrices
├── first_basket_data.csv   # export of all labelled games (13 columns)
├── my_predictions.xlsx     # Scorecard tab + Games tab: our picks vs what actually happened
├── gui.py, gui/            # desktop app server + page (gui/index.html); gui/static_site.html = website login + data layer
├── iphone_check.py         # iPhone-style screenshots (WebKit) of the Today and Yesterday views → iphone_shots/
├── accounts.py             # app accounts (owner/viewer)
├── publish.py              # builds the encrypted website; --live = game-night loop, --once = one update
├── site_users.py, site_users.json   # website accounts (wrapped keys only, no passwords)
├── fanduel_odds.py, ev.py  # FanDuel prices (pages + live price feed); EV, value, odds log, paper betting
├── draftkings_odds.py      # DraftKings "1st Points Scorer" prices (one request for every game)
├── sync_from_cloud.py      # Mac: download the cloud's data every 30 min + start the cloud job if it went quiet
├── launcher/               # Mac launch agents (sync, app), Desktop app, icon
├── website_live/           # live-picks.yml (public-repo job), odds-relay.js + wrangler.jsonc (Cloudflare), relay_url.txt
├── setup_live_updates.sh, setup_odds_relay.sh   # one-time setup (both done)
├── todays_cards.json       # today's picks cards (kept after tip-off)
├── .github/workflows/update-site.yml   # older private-repo workflow (stands down; manual fallback)
├── spreadsheet.py          # tiny .xlsx writer/reader (tabs, frozen header, widths, colours) — no extra package
├── requirements.txt        # only what the code uses: sportsdataverse, numpy, pandas, scipy, requests, rich (+ pytest, ruff)
├── README.md
└── BLUEPRINT.md            # this file
```

---

## 9. Research notes

- **Solid / consistent with our data:** the tip winner scores first ≈ 64% (Dickinson, 27k tip-offs; ours 64.4% for field goals, 66.3% with free throws);
  the two-stage structure P(team scores first) × P(player | team) is the standard approach.
- **Not validated:** commercial "10-factor weights" and "% stability" claims (HeatCheck, PropsEdge,
  SportsCommand) — no backtests or sample sizes. Recent-form windows (L5/L10) carry little signal in our data.
- **Ceiling and economics:** StatsBench reports a 17.7% pre-game hit rate over 724 games (2024-25), without ROI
  or CLV. Break-even at +600 is 14.3%; board margin ≈ 20%; losing streaks are long even with a real edge
  (10 straight losses at +700 ≈ 26% likely).

---

## 10. Change log (newest first)

### 2026-11-19 (morning, later) — the Mac takes GitHub's code by itself

- **Owner's request:** everything done from the phone or a cloud session must update the NBA folder on the Mac.
  `sync_from_cloud.py` (every 30 min) now first runs `update_code()`: fetch `main`, fast-forward when safe, then the
  usual data download. `code_update_plan` (pure, `tests/test_code_update.py`, 7 tests) decides: up to date → nothing;
  Mac has commits GitHub lacks → skip; a code file edited on the Mac (tracked or untracked) that GitHub also
  changed, with different content → skip and log; data files (`FILES`) and identical files are reset first (the
  data download replaces them right after). If `gui.py` or `gui/` changed, the Mac app is restarted
  (`launchctl kickstart -k gui/<uid>/com.firstbasket.app`). Tested end-to-end on practice repositories (a fake
  GitHub + fake Mac): update, repeat = no-op, clash skipped with the Mac's edit kept, identical untracked file
  (like the Mac's `.claude/launch.json`) replaced. **Starts working after the Mac gets this file once.**
- **Verified:** 203 tests pass.

### 2026-11-19 (morning) — ESPN fills days NBA.com left blank; a test no longer writes to the real database

- **ESPN fallback for official lineups** (`starters.espn_fallback_rows`, `parse_espn_starters`, called from
  `store_official_lineups`): for each played game whose teams NBA.com's daily file doesn't list, the starters come
  from ESPN's game summary (`site.api.espn.com`, reachable from the cloud — NBA.com's box scores are not), stored in
  `official_lineups` with status **`ESPN`**. ESPN gives usual positions, not that night's slot, so it can show two
  centres (CLE: Allen and Mobley) or none (it calls Wembanyama, Gafford, Sabonis F). The tip-off model needs one per
  team: the starter NBA.com listed at C most often **in the 120 days before** (all-time picked Herbert Jones over this
  season's Matković), else ESPN's C, is C; any other ESPN "C" becomes F. Dry run on a copy for **11/02/26**: all 15
  games, 150 starters, every player matched to an ESPN id, a centre for 29 of 30 teams (TOR started none: the
  model's backup rule applies). The real fill happens on the next cloud update after this code is on `main`
  (`nba.py` re-checks empty days for 30 days, i.e. until 12/02/26). The Lineups tab still says NBA.com posted
  nothing that day (`picks.lineup_rows` treats ESPN-only days as empty).
- **Not recoverable:** NBA.com's early (Expected) lineups before 11/17/26 — it overwrites them with the final list.
- **Test bug fixed:** `tests/test_live_mode.py` ran `publish.live` with the real relay address, so every test run
  pulled real DraftKings lines into the real `nba_first_basket.db` (70 rows each time). The test now points the
  relay at an empty temp folder; the database is unchanged after `python3 -m pytest`.
- **Verified:** 196 tests pass (4 new).

### 2026-11-19 (overnight) — Yesterday view, home/away labels, layout fixes

- **Today / Yesterday switch** (`gui.day_cards`, `/api/yesterday`, `publish._yesterday`): every game we picked
  yesterday, rebuilt from `pick_log` (picks made before tip-off, last saved FanDuel line), with who scored first
  (from `first_baskets`, or ESPN's play-by-play if not filed yet), our rank for him and ESPN's final score.
  Yesterday gets the same tile grid, section header and "our pick" line as Today; Yesterday tab on the left.
- **AWAY / HOME labels** on every tile and card, bigger and brighter; hint line "left team = away, right = home".
- **Colours:** missed-pick bars and tags orange instead of grey; green / yellow result bars share the orange one's border.
- **Paper betting explained for beginners:** what it is, three stat boxes ($1 bets / won / paid back), a fold-out
  "New to betting? How to read this", and for each lost bet what it would have paid.
- **Layout:** scores stay on one line on iPhones; wider desktop tiles so names, scores and pick tags aren't cut off.
  New `iphone_check.py` takes WebKit iPhone screenshots (Today + Yesterday) to check this.
- Data line shows the year ("through 11/18/2026").
- **Verified:** 192 tests pass (2026-11-19).

### 2026-11-18 (night) — DraftKings logging, saved lines, timer fixed

- **DraftKings log:** viewers' browsers send the DraftKings lines they read (only when a line changes) to the relay
  (`POST /dk-log`, Cloudflare KV namespace `first-basket-dk`, binding `DKLOG`, kept 14 days); each cloud check pulls
  them (`draftkings_odds.pull_relay_log`) into **`dk_odds_log`** (next to FanDuel's `odds_log`, for the beat-Vegas
  checks) and into the snapshot that puts DraftKings on the cards — so everyone keeps the last DK line after tip-off.
  Each browser also keeps the last line it saw (localStorage). Write path tested with a fake KV; live writes start with
  the next slate.
- **Started games keep their last saved FanDuel line** (rebuilt cards read `odds_log`; newer rebuilds replace older).
- **Timer fixed:** the GH_TOKEN only had read access ("public repositories"); now `first-basket` with Actions read and
  write. `/timer-check` on the relay reports what the timer would do and whether it may start jobs.
- **Results filing fixed:** the cloud job's "worth running?" check now also counts games in progress and finished
  games whose results aren't filed (it used to quit once every game had tipped off).

### 2026-11-18 (latest) — simple-rule mix adopted; recency weighting rejected

- **Adopted — 20% simple rule** (`pregame_model.SIMPLE_MIX`, `experiments/recency_and_mix_test.py`): live chances =
  80% model + 20% each starter's shrunk season first-basket rate (normalised). Log-loss 2026-27 2.2365 → 2.2347,
  Jan–Apr 2026 2.2727 → 2.2649 (both better); top pick 21.7% → 21.2%, 14.9% → 15.1%. 40% hurt 2026-27. Applied in
  `probabilities()` (live picks); the training script's own walk-forward numbers stay unmixed. Re-check 2026-12-08.
- **Rejected — weighting recent games more** (half-life 90/180/365 days): log-loss changes < 0.002, top pick worse.
- **Started saving the injury report** (`injury_log`, `starters.save_injury_report`, every predict run in the cloud):
  one row per player per status per day. Enables a later test (~mid-January, 6–8 weeks of data): do starters who were
  Questionable / just returned from injury score first less often?

### 2026-11-18 (later) — where accuracy is lost, and what didn't help

- **Diagnosis** (`experiments/diagnose_stages.py`): stage 1 (right TEAM scores first) is only 58.6% (2026-27) /
  54.6% (Jan–Apr 2026) — barely above a coin flip; stage 2 (right player given the team) 30.0% / 26.2% (random 20%).
  With a perfect stage 1 the top pick would hit ~26–30%. Stage 1 is the big gap — and it is the tip-off.
- **Rejected — team first-possession rates in stage 1** (`stage1_team_test.py`): conversion after winning / stealing
  after losing the tip, K = 10/30/60: better in 2026-27, worse in Jan–Apr in every version (noise).
- **Rejected — jumper height** (`tip_height_test.py`, heights from ESPN in `player_heights.json`): as an extra term it
  calls more tips right (+2–3 points) but log-loss gets worse (overconfident); as the starting guess for jumpers with few
  tips it helps Jan–Apr (0.6227 → 0.6189) but hurts 2026-27 (0.6277 → 0.6321). Kept the data for later.
- **Rejected — gradient-boosted trees for stage 2** (`stage2_trees_test.py`, XGBoost pairwise ranking): worse in both
  periods at every depth (2026-27 21.7% → 18.7%); ~1,400 games are too few for trees. Blend 50/50 also worse.
- **Conclusion:** the model is close to what this data supports (tip-off ≈ 63% right is the ceiling of tip records).
  The next real lever is the **sportsbook blend** (~2026-12-20, `market_blend_test.py`), then the **2026-12-08**
  re-check of the opponent inputs.

### 2026-11-18 (late) — accuracy pass: two new model inputs adopted

Tested everything on the list the honest way (week by week, only earlier games; L2 chosen on 2025-26):
- **Adopted — opponent by position + head-to-head** (`context_features.py`, step 2): `experiments/combined_test.py`
  log-loss 2026-27 2.2392 → **2.2365**, Jan–Apr 2026 2.2763 → **2.2727** (both better); top pick 19.2% → **21.7%** and
  15.1% → 14.9%. Retrained model: walk-forward 203 games **21.7% / 2.2364** (was 19.2% / 2.2393). Both weights came out
  small and negative (−0.07): corrections next to the season rate rather than strong signals — **re-check 2026-12-08**.
- **Not changed — tip-off jumper rule**: names the actual jumper 95.6% (2026-27) / 92.6% (Jan–Apr) against box-score
  starters; no variant beat it in both periods (`experiments/jumper_rules_test.py`).
- **Not testable yet — blend with sportsbook odds** (`experiments/market_blend_test.py`): odds saved since 11-18 only;
  decide with 150+ finished games (~2026-12-20). Log-loss decides.
- Live track record: 8 logged games (too few). `pytest.ini` now limits tests to `tests/` (experiments were being
  imported as tests). 187 tests.

### 2026-11-18 — app, private website, fully automatic live updates

**Built (owner's requests, in order):** browser app (`gui.py`) with EV guide, season records, Data view and player
search; Desktop app with icon; accounts; folder cleanup; FanDuel odds + EV (§7.3). Then the free private website
(encrypted static site on GitHub Pages; HF Spaces needed a paid plan and Oracle a card — both dropped) and full
automation:
- **Live cloud job** moved to the public website repo (unlimited free minutes): checks every 3 min from 9 AM,
  republishes on change, files results as games end, nightly retrain. Old hourly private workflow stands down.
- **Live FanDuel odds** every ~30 s: browsers can't read FanDuel from another site (CORS — tested: FanDuel only answers
  its own origin), so the Mac app passes the request through (`/api/fd/prices`) and the website uses a free
  Cloudflare Worker. Uses FanDuel's live price feed (~7 KB/game instead of a 680 KB page).
- **Always on without the Mac:** Cloudflare timer (every 10 min) starts the job if idle (GitHub's timer never fired on
  the first night). Mac launch agents: sync + backup every 30 min, app always running.
- **Page fixes:** one large Clear selection button; selected games glow with a "✓ Selected" tag and the rest fade;
  always dark (phones in light mode showed the light theme); tapping a started game shows its pre-tip-off picks
  (rebuilt from `pick_log`; cards matched by matchup — the model's cards carry no game id, which first caused
  duplicates); the first scorer's exact spot ("Our #2 pick", "✗ Our #6 pick") instead of "in our top 3"; NBA.com 403
  reported once per run.
- **Data:** results filed only after the day's last tip-off (new results retrain the model; picks must not change
  mid-evening). Accounts: bhass, simon added.
- **Verified:** 182 tests pass; full viewer walk-through of a local copy of the website (login, wrong password,
  remember-me, logout, tapping, EV guide, all Data tabs and filters, phone width, no console errors); public files
  hold no names, model or code; live odds in the app matched FanDuel exactly and refreshed every 30 s; relay answers
  only the website; website carries the relay address and FanDuel ids.
- **Later the same night:** DraftKings added next to FanDuel (best price + book on every card); phone layout of the
  odds table fixed (FD/DK labels, rank and Fair hidden on narrow screens, swipe if still too wide). 186 tests.
- **Not yet verified:** the Cloudflare timer's first automatic start (2026-11-19); the logged-in website showing the
  green live-odds line (owner to look).

### 2026-11-17 — full review and overhaul

**Review.** Audited model, training, `nba.py`, `starters.py` and data. Found that several 2026-11-09 entries
were not true in the code: `update_player_game_logs()` never existed (player stats and lineups were stuck at
Nov 8), `load_model()` and the in-game branch were still in `nba.py` (and crashed), catchup did not store
lineups, and the tip-off feature was broken.

**Data and automation**
- `sync_data()` added: `predict`, `report` and `eval` bring everything up to date first (§1.2).
- Player game logs made incremental; catchup stores lineups; recent missing labels/lineups retried; catchup
  re-checks the last stored date.
- Deleted 72 orphan first-basket rows (preseason and All-Star games, confirmed via ESPN).
- `build_features.py --incremental` fixed: it sliced the game list but looped from the old offset, so it
  appended **zero** games on every run. A first fix replaced the matrices with only the new games; caught by the
  row-count check, restored from backup, fixed with an explicit `appending` flag. Verified 1,456 + 59 = 1,515 games.
- Rebuilt the Nov 9–16 training rows with fresh player stats.
- CSV: removed the always-empty `x`/`y` columns; `view_data.py` now uses the same export (it previously wrote a
  shorter CSV without tip columns).
- Removed FanDuel scraping code, captured pages and the `undetected-chromedriver` dependency.

**Prediction**
- Removed the crashing in-game branch; games that have tipped off are skipped.
- Removed the ESPN lineup step (ESPN publishes no starters before tip-off). Verified NBA.com's `"Confirmed"`
  string. Today's games use the Eastern-Time date.
- Lineup fixes (7 tests): unresolved Out player → lineup incomplete; injuries applied to EXPECTED lineups;
  cleared regular starters return; replacements from the current absence first; distinct "if he sits" players;
  "P.J." = "PJ"; return dates with a time part.
- Injury report now includes per-game summaries (Doubtful/Questionable).
- New screen output (`display.py`, 4 tests); removed the fake 45% fallback used when the model file was missing.

**Model**
- Tip-off model rebuilt (5 tests): real jumpers, point-in-time rates, home + away add to 1 → 63.6% of 2026-27
  tip winners. The old feature used the minutes leader (the actual jumper only 10.1% of the time), gave away
  players the home team's chance, and was always 0.5 at prediction time.
- Training now uses 2025-26 + 2026-27 with a walk-forward test and a keep-previous check (exit 3). Result: 17.2%
  top-1 / log-loss 2.2516 — the same log-loss as training on 2025-26 only; weighting this season more made it worse.
- Tested: normal retrain, keep-previous path (file untouched), no-new-games build; 56 tests pass; `predict` ran
  end to end on the 5 games of 2026-11-17.

**Pick log (keep every pick)**
- New `picks.py` and `pick_log` table: every pick is stored with time, lineup type, lineup, model version
  (trained-through date + weights fingerprint), tip-off chance and all 10 probabilities. Never overwritten; a
  new row only when the lineup type, lineup or model changes (5 tests, written first).
- `eval` rewritten (new screen, quiet sync): final pick per game, top-3 rate, split by lineup type and model.
- Verified: first `predict` logged today's 5 picks, a second run logged nothing new, `eval` shows the empty-state
  message until tonight's results arrive. 63 tests pass.

**Code health**
- One copy of the stat calculations: `nba.py` now uses `build_features.compute_player_season_stats` /
  `compute_team_stats_up_to_date` (the training versions; both gave identical numbers on 400 sampled cases).
- Removed unused code: 4 functions in `nba.py` (incl. the old `render_game_card`), the legacy ESPN lineup chain
  and 7 other unused helpers in `starters.py`, unused variables/imports; deleted `train_model.py` (retired) and
  `tipoff_model.json` (old tip model). 9 tests that only covered removed code were removed; the accent-matching
  test was kept and now runs on the live projection. `ruff` reports no problems.
- Database: `games.status` filled with 3 (final) for all 1,524 games and set on insert; dropped the empty
  `player_season_stats` table.
- New `test_pipeline.py` (7 tests, temporary database, no internet): sync retries/orphans/CSV, all four retrain
  outcomes, incremental append and no-op. Re-introducing the old zero-games bug makes the append test fail.
- `report` now uses the new screen (quiet sync, one box per game, readable date; 1 test).
- Backups of all code and the database before the clean-up were kept outside the project.
- Verified: 62 tests pass; `predict` (5 games, no duplicate picks), `report` and `eval` run.

**Prediction screen: top 2 + tip winner by name**
- Each card now opens with a headline: "🎯 Top 2" (the two most likely first scorers with their chances) and
  "🏀 Tip-off: <player> (<team>) to win the tip — <chance> vs <opposing jumper>". Top-5 table kept below. 2 tests.
- Tonight one team's usual jumper (LAL: Walker Kessler, all of their last 10 tips) wasn't starting, so the card
  could only say "jumper not known yet". Added a fallback in `tip_model.team_jumper`: the starter with the most
  opening tips before the game date jumps instead (1 test, seen failing first). Unknown jumpers in 2026-27
  team-games: 28/396 → 4/396. Tip log-loss improved in both seasons; tip winners called 63.6% → 63.1% (−1 game)
  on 2026-27 and 61.0% → 61.4% on 2025-26.
- Retrained (walk-forward): 18.2% top-1 / log-loss 2.2519 (was 17.2% / 2.2516) — unchanged within noise. Today's
  picks were re-logged under the new model version (expected). 65 tests pass.

**Jumper: use NBA.com's official lineup first**
- Owner's point: once NBA.com posts the lineup (~1 PM ET), use that instead of guessing. NBA.com lists each
  starter's position but not who takes the tip, so the listed C is the official signal.
- Fetched NBA.com's lineup files for all 28 days of 2026-27 (396 team-games) and compared rules against who
  actually jumped: our previous rule 85.1%, always the listed C 86.9%, **listed C unless another starter took
  6+ of the team's last 10 tips 88.1%** (adopted; same score with an 8-of-10 threshold; 4 rules compared).
  NBA.com listed no C in 30 of the 396 team-games; the older steps cover those.
- `tip_model.team_jumper` takes `listed_center`; `predict` passes NBA.com's C for each team. Tonight's 5 jumpers
  were unchanged (they already matched the listed centres). 1 test (seen failing first); 66 tests pass.
- Known gap: training rows have no listed C (fix list item 9).

**Fix-everything pass (owner: "whatever needs to be fixed or improved, let's do")**
- **Model inputs:** `usage_rate` → `shot_attempts_per36` (true per-36 value; the old column was the same number ÷100,
  never capped — checked max 0.39); team tip rate ignores games without a recorded tip (0 such games today);
  removed 9 unused or leaky matrix columns (`tip_winner`, `early_touches`, `early_shots`, `first_shot_rate`,
  `L5_first_basket_rate`, `home_fb_pct`, `away_fb_pct`, `team_games_played`, stale `tip_win_prob`) and the
  first-10-plays download. Existing matrices converted in place (backup kept); retrain gave identical results.
  `build_features --incremental` now refuses to append to matrices with a different column layout.
- **Late-labelled games:** the incremental build adds every labelled game missing from `game_ids.json`, whatever
  its date; the retrain trigger counts those too; the walk-forward test no longer assumes date order (3 tests).
- **Label guards:** a play/jump ball with no team id is no longer assigned to the away team; such a first basket
  leaves the game unlabelled (retried) and the tip team is stored as unknown (3 tests). A real game still labels
  correctly (PHX @ DEN 11-16: Miles Bridges).
- **Official lineups (#9):** new `official_lineups` table filled from NBA.com's daily files for all 239 played
  days (15,150 starters, all names matched); sync adds each new day. Training and the tip check now use each
  game's listed C, like game day. Tip log-loss improved in both seasons (2026-27 0.6354 → 0.6299, 63.6% called;
  2025-26 0.6323 → 0.6309, 62.3%). First-basket model unchanged within noise (17.7%, log-loss 2.2520). 2 tests.
- **Report:** the NBA.com view now includes per-game injury summaries (31 entries today; one conflict resolved).
- **Names / trades:** 0 of 15,150 official names unmatched → no alias table needed. Current ESPN rosters for
  tonight's teams override the box-score team, so a traded player counts for his new team at once (2 tests).
- **Projection tuning:** settings chosen on Jan–Apr 2026 (1,466 team-games), confirmed on 2026-27 (366):
  recent window 3 games weighted 4×, 15-game window. Starters right 77.2% → 80.4%; plus filling any open spot
  with the next player in line → **82.1%**, all five 29.0% → **41.0%**, a full five for 366/366 (no injury info).
  Five tests that assumed the old 5-game window were updated (same behaviour, new window; one gap — a traded
  starter leaving only 4 — fixed by the open-spot fill).
- Verified: 76 tests pass, `ruff` clean, `predict` / `report` / `eval` / incremental build all run on real data.

**Lineup checks (owner's questions)**
- Official lineup fetch verified live at 2:26 PM ET: all 5 games read as `Expected` from NBA.com's file
  (updated 2:21 PM); "Confirmed" status already verified on the 11-15/16 files; box scores correctly "not
  posted yet". The live Expected → Confirmed switch remains to be watched tonight.
- Morning-guess accuracy with today's injury report: 48/50 starters, 8/10 full fives vs NBA.com's expected
  lineups. Both misses came from position-blind replacements → new fix-list item 1.

**Free throws count as the first basket (owner's correction)**
- `first_basket` now treats any made basket in regulation — field goal or free throw — as the first basket.
- `build_features.get_game_starters` reads the saved `lineups` first (checked identical to ESPN on 3 games), so
  rebuilding the matrices needs no downloads; `sync_first_basket_flags` keeps player-log flags equal to the
  current labels. 78 tests pass.
- Re-check of all 1,524 games finished (read-only, no errors):
  in **135 of 1,515 games (8.9%) the first points were a free throw**; the first scorer changes in **126** games
  (in the other 9 the free-throw shooter was also the first field-goal scorer); 1,389 unchanged; the same 9 old
  games still have no play-by-play; every new first scorer is a starter, so no game drops out of training.
- Applied with the owner's go-ahead (backup first): 126 labels updated; 252 player-log flags re-synced;
  CSV rewritten (1,515 rows); training matrices rebuilt from the saved lineups in 4 seconds with no downloads;
  retrained (L2 re-chosen on 2025-26: 0.001). Walk-forward on 2026-27: **19.7% top-1, log-loss 2.2453** (best simple
  rule 17.7%); tip-winner team scores first 66.3%. 78 tests pass; `predict` runs (e.g. Wembanyama 25.0%).

**Position-aware injury replacements**
- Positions: `starters.position_group` (G/F/C), `player_groups` (ESPN roster position, else the group NBA.com
  listed him at most as a starter); `fetch_rosters(..., positions=)` collects roster positions (525 players).
- Backtest on every past team-game with a missing regular starter (1,304 Jan–Apr 2026 + 337 2026-27), each
  missing starter marked Out, compared with NBA.com's confirmed starters. Replacements right:
  no positions 39.5% / 55.2%; same position first for everyone 33.9% / 42.8% (rejected); centres only, first
  39.0% / 55.2%; all positions after the coach's history 37.5% / 51.7%; **centres only, after the coach's
  history 39.6% / 55.7% (adopted)**. Starters right 77.4% / 88.2%, all five 26.5% / 54.9%.
- Today: 49/50 starters vs NBA.com's expected lineups (was 48/50); UTAH fixed (Mo Bamba).
- `predict` and both `report` views pass the positions; 3 tests; 81 tests pass; `ruff` clean.

**First-shot feature (built, tested, not adopted)**
- `get_pbp` keeps ESPN's `shootingPlay`; `first_shots()` = each team's first field-goal attempt in Q1 (made or
  missed; free throws excluded; plays without team/player skipped); stored in `first_shots` on every ingest;
  sync retries recent games missing it. Backfilled all 1,524 games (run visibly in a terminal tab at the
  owner's request; resumable).
- `build_first_shot_index` / `first_shot_share`: a starter's share of his earlier starts in which he took the
  first shot (point-in-time). 7 tests, written first.
- Facts: first shot made 46.2%; first shooter always a starter; the first scorer was his team's first shooter in
  70.4% of games; but a team's most frequent first shooter takes only 24.1% of its first shots.
- Walk-forward (2026-27, 198 games): without 19.7% / 2.2453; with the shrunk share 19.2% / 2.2462; with share ×
  tip chance 20.2% / 2.2453; "highest first-shot share" alone 14.6%. Not adopted — model reverted and retrained to
  19.7% / 2.2453 (backup kept). Data and two matrix columns kept. 86 tests pass.

**Two-stage model (adopted)**
- Experiment first (read-only, run visibly in the terminal): stage 1 logistic regression of "home scored first" on
  logit(P(home wins tip)); stage 2 conditional logit over the scoring team's 5 starters (player inputs only).
  Walk-forward 2026-27: 20.7% / 2.2369 vs one-stage 19.7% / 2.2453; with first-shot share in stage 2 20.2% / 2.2365.
  Stage 1 alone calls the team that scores first 58.6% of the time.
- Significance: game-by-game bootstrap — better in 95% of resamples (range −0.0014 to +0.0186). Second check on
  Jan–Apr 2026 (733 games, trained Oct–Dec): tie (−0.0011). Adopted with that caveat.
- Implementation: `pregame_model.two_stage` / `home_scores_first` / `probabilities` (type "two_stage"; old one-stage
  files still load); `train_pregame.py` trains the two-stage model, prints the one-stage comparison, keeps the
  keep-previous check; `--incremental` warm-starts stage 2. One-stage model backed up. 4 new tests; 90 pass;
  `predict` runs on it.

**Calibration check (no change)**
- Week-by-week two-stage predictions for every game Dec 2025 → Nov 2026 (1,129 games, each week trained only on
  earlier games; run visibly in the terminal). Chances match outcomes in every bin with enough data; the only gap
  is the >20% bin (23 player-games: 22.0% → 30.4%), too small to act on. Sharpening P^t fitted on past weeks did
  not improve later weeks (t = 0.95 → 2026-27 log-loss 2.2368 → 2.2381). Decision: no calibration layer.

**Card: team to score first**
- *(Still shown; owner only dropped its counter — see "Team to score first: no counter".)* Each card shows "📈 Scores first: <team> x% vs <team> y%" — stage 1 of the two-stage model (tip-off chance + home
  court). Example 2026-11-17: NY 54% to win the tip, yet TOR (home) 52% to score first. Legend updated (its tip
  line now says the tip winner scores first about 66% of the time, the free-throw-inclusive figure); 1 test.
- Watch mode started in its own terminal tab for the 2026-11-17 evening games (the live Expected → Confirmed check).

**Three improvement tests (owner: "whenever you're ready")**
- Smarter step 1 (team history): not adopted — helps 2026-27, hurts Jan–Apr 2026 (§7.1).
- Lakers backup-centre gap: cause = ESPN's roster calls Mamukelashvili "F". `starters.player_groups(prefer=
  "official")` now trusts NBA.com's lineup history first (default). Backtested on 1,641 team-games: better in both
  periods. Today 50/50 starters.
- Matchups (opponent's conceded first baskets by position): mixed — better probabilities, worse top pick; not
  adopted yet, re-test later (§7.1).
- All three run visibly in the terminal; 91 tests pass.

**Head-to-head tests (owner: "a formula for head-to-head … for the tip-off and for first basket")**
- Both run visibly in the terminal, honest week-by-week / point-in-time. Tip-off jumper-vs-jumper: worse at every
  strength, rejected. First basket vs tonight's opponent: this-season-only version slightly better in both periods
  (2026-27 20.2% → 21.2%, log-loss 2.2318 → 2.2289; Jan–Apr 2026 15.1% → 15.1%, 2.2763 → 2.2754); small, thin data
  (≈1 earlier meeting per player). Decision: not in the model yet — re-test on or after 2026-12-08 (§7.1).
- Owner follow-up: "tip off against a certain team?" — tested three ways (jumper vs team, all / this season;
  team vs team): worse in 2025-26 every time, at best +2 tips in 2026-27. Rejected (§3.1).
- **Outside research (2026-11-17, owner: "research what else we can do")** — what published first-basket models
  use, compared with ours. Sources are betting-industry articles (HeatCheck HQ, StatsBench, Legal Sports Report,
  Legion Report); none publish an independently checked model, so their weights are opinions, not evidence.
  - *Already in our model:* two stages (tip → team, then player) — the same structure StatsBench describes;
    tip-off strength; season and last-10 first-basket rate; early-shot share (their "first-shot rate", called
    the strongest predictor); shot volume/efficiency; home court (they report 52.3% home vs 47.7% away; our
    stage 1 has a home term). Tip winner's team scores first: Legion Report 68% (360 games), ours 66.3%.
  - *Benchmark:* StatsBench reports a 17.7% top-pick hit rate on 724 games of 2024-25; ours is 19.7–20.2% week by
    week on 2026-27 (different seasons, so only a rough comparison).
  - *Ideas ranked for us:* (1) add the 2024-25 season — more history per player, most likely real gain;
    (2) opponent defence by position — industry weights it ~10%; our test was mixed, re-test with the head-to-head
    idea on/after 2026-12-08; (3) offensive rebounders — only 56 of 1,515 first baskets (3.7%) were putbacks/tips
    and our player logs have no rebounds, so low ceiling, low priority; (4) game total / betting lines — reported
    +2.1 points at most and needs odds data (excluded by the owner), skip.
  - *Rule check — settled:* the project targets **FanDuel only**, whose "first basket" counts made free throws
    (DraftKings/BetMGM sell "first field goal", which does not). Our labels include free throws (135 of 1,515 first
    baskets, 8.9%), so the target matches FanDuel.
  - ⚠️ *Market availability:* Front Office Sports (2026-10-16) reports an NBA letter to its sportsbook partners
    (FanDuel included) that prohibits individual-player "first-of" markets, including who scores the first points,
    "this season"; prediction markets are not covered. **Owner checked the FanDuel app on 2026-11-17: the
    first basket prop is still offered.** Re-check if it disappears. "First team to score" is a team market.
- **2024-25 tips now used for the tip-off call only (owner: "if it helps then use it only for tip off")**:
  `tip_model` functions take `extended=False` (default); `extended=True` reads `games` + `old_tips` (2024-25
  opening tips, only games not already in `games`). Used ONLY for what the owner sees: the 🏀 Tip-off line
  (jumper + chance) on each card, and the tip-off columns/scorecard line in `my_predictions.xlsx` (`nba.py`
  `show_home`; `season_backtest.py` re-created picks). The first-basket model still gets the normal tip chance,
  exactly as trained and tested. Proof: re-creating the season gives the #1 pick **39 of 198, unchanged**, while
  tip-off calls go **126 → 133 of 198 (64% → 67%)**; for Jazz @ Clippers the model's tip input is identical before
  and after (0.6408). A card's tip-off % can now differ a little from the tip chance inside the first-basket
  model (e.g. 63% shown vs 64% used) — by design. LaMelo 16.8% → 17.7% tonight came from Minnesota/Portland's
  confirmed lineup (Avdija, Shannon Jr. out; Cody Williams, Jayson Kent in), not from this change.
  Also: `pick_log` now stores chances at full precision, so the spreadsheet always shows exactly what the screen
  shows (Garland 16.05% had shown as 16.1% in the file and 16.0% on screen). 1 new test; 115 pass.
- **2024-25 opening tips tested (owner: "include it just for tip-off winner? only scrape that stat")**:
  downloaded ONLY the opening tip of every 2024-25 regular-season game (1,236 games, 1,229 with both jumpers) with
  the program's own readers, into a separate table `old_tips` (+ `old_tips_days`, resumable) — never into `games`,
  so nothing uses it unless chosen. Database backed up first. Scripts: `experiments/tips_2024_25.py` (download),
  `tips_2024_25_test.py` (tip calls), `tips_2024_25_fb_test.py` (first-basket model); both tests use a temporary
  copy of the database.
  - *Tip-off calls* (point-in-time, Dec 2025 →): 2025-26 62.3% → **65.1%** (+29 tips), log-loss 0.6309 → 0.6283;
    2026-27 63.6% → **67.2%** (+7), but log-loss 0.6299 → **0.6344** (chances too confident). More calls right in
    both seasons; chances better in one, worse in the other.
  - *First-basket model* (real L2 procedure, week by week): 2026-27 20.2% / 2.2318 → **19.2% / 2.2348** (worse);
    Jan–Apr 2026 15.1% / 2.2763 → 14.2% / 2.2719 (top pick worse, chances better). **Not better → not used in the
    first-basket model.** Used for the tip-off call only (next entry).
- **Full check of my_predictions.xlsx (owner: "make sure everything is working smoothly")** — no problems found:
  Scorecard totals = Games tab counts (39/198, 73/198, 126/198, 238/594); every YES/NO re-derived from the names
  and teams in its row; running counts re-added from opening night (newest finished row = season totals); #1
  chance ≥ #2 chance and tip chance ≥ 50% on every row; all 198 finished games in the database appear once, with
  the database's first scorer; newest first, this season only; every #1/#2 pick and predicted jumper is in that
  team's starting five (actual starters for re-created picks, NBA.com's list for live ones); Lineups tab has 2 rows
  per game, no empty cells, same games as the Games tab; the .xlsx zip and all 8 XML parts parse cleanly. Then a
  full `python3 nba.py --all` run: file rewritten, and NBA.com's first Confirmed list of the night (one game) was
  saved alongside the 50 Expected entries.
- **Training date range (owner: "are you sure we should train on 2024-25 … a lot of trades"; "what's a good
  reference range?")** — decision: **keep the current range** (2025-26 + this season, ≈13 months, Oct 2025 →).
  Reasons: player inputs follow the player across trades, but his role can change, so older seasons describe a
  different situation; today's recency test found that weighting recent games more did not help within the
  current 13 months — so last season is not hurting, but it is no evidence that 2024-25 would help; the tip model
  already uses every tip on record and is the part most likely to gain from older data (a jumper's skill does not
  change with his team). 2024-25 stays an *optional test* (not a plan): only if a walk-forward test shows a gain,
  and most sensibly for the tip model alone; revisit around the All-Star break, when this season has ≈850 games.
- **"No final lineup to check" for old games explained (owner: "how does that make sense?")**: only one date —
  **11/02/26** (15 games, 30 team rows). NBA.com's daily lineup file for that day lists the games with **empty
  player lists** (checked directly: HTTP 200, `players: []`), so NBA.com never published lineups that day; it is a
  gap in their data, not ours, and cannot be filled. The cells now say "NBA.com posted no pre-game lineup this day
  (who actually started is in the next column)" / "Can't check: NBA.com posted no lineup". The actual starters
  (ESPN box score) are known for all 396 past team-games, 11/02 included. Sync now also re-fetches recent days
  (last 14) whose lineup file came back empty, in case of a failed download or a late post. Final lineup vs who
  started this season: 366 YES, 0 NO, 30 can't check (11/02), 10 waiting (tonight). 1 new test; 114 pass.
- **Lineups tab made specific, no empty cells (owner: "is expected = actual referring to the projected lineup
  before confirmation? … a lot of empty cells")**: headers now say exactly what each list is — "NBA.com EARLY lineup
  (marked 'Expected', posted before it is confirmed)", "NBA.com FINAL lineup (marked 'Confirmed', near tip-off)",
  "Who ACTUALLY started (box score after the game)", "Did the EARLY/FINAL lineup match who started?". Every empty
  cell now gives the reason: early lineups before 11/17/26 "Not recorded (we started saving early lineups on
  11/17/26)" — NBA.com's daily file is overwritten with the confirmed list, so past early lineups cannot be
  recovered; today's final lineup "Not confirmed yet (NBA.com confirms ~30 min before tip-off)"; "Game not played
  yet"; "Waiting for the game"; "No early lineup to check". Date/Game/Team frozen on the left. Note: our own
  PROJECTED lineup (used only when NBA.com has posted nothing) is shown on the Games tab as "Lineup used".
  1 new test (and a check that no cell is empty); 113 pass.
- **Games tab: prediction → actual → right? (owner: "I want related fields to be right next to each other")**:
  the actual first scorer now sits between our #1 pick and "#1 pick right?" (Date, Game | Our #1 pick, #1 chance,
  Who actually scored first, #1 pick right? | #2 pick, #2 chance, #1 or #2 right?, how scored | tip prediction,
  chance, actual tip winner, tip call right? | team, chance, team call right? | pick made, lineup | running).
  Also found why the owner still saw the old layout: Numbers had three stale copies open (re-opening an open
  file only brings the old window forward); closed them and opened the new file. Tests pin the order; 112 pass.
- **Games tab reordered (owner: "I have to scroll all the way across to see if the number one pick … was
  correct")**: each prediction now sits next to its result — Date, Game | #1 pick, chance, **#1 pick right?** |
  #2 pick, chance, #1 or #2 right? | actual scorer, how | predicted tip winner, chance, actual tip winner, tip
  call right? | team to score first, chance, team call right? | pick made, lineup used | running counts. Shorter
  headers ("#1 pick right?", "#1 chance"…), narrower columns, and Date + Game stay frozen on the left while
  scrolling (`spreadsheet` `freeze_col`). Checked in Numbers. Tests check the result sits 2 columns after its
  pick and that both freezes are set; 112 pass.
- **Expected vs confirmed starters kept separately (owner: "we need to keep track of the projected starters and
  the confirmed starters")**: new table `lineup_snapshots` (game_date, team, status, player…, seen_at) keeps
  NBA.com's **Expected** list and its **Confirmed** list apart — the latest list per status replaces the previous
  one (e.g. an injury swap), but Confirmed never erases Expected. Saved on every `predict` run
  (`starters.save_seen_lineups`, from the feeds fetched that run) and by the day-after sync
  (`store_official_lineups` → `store_lineup_snapshot`). New **Lineups** tab in `my_predictions.xlsx` shows
  expected / confirmed / actual starters per team, PG first and C last, with YES/NO matches (by ESPN id, or by
  accent-free name). History: before 2026-11-17 only the day-after Confirmed list was saved, so earlier rows show
  "—" for Expected. Finding: NBA.com's confirmed five matched the box-score starters in **366 of 366** team-games
  this season — the reason to run ~30 minutes before tip-off. Today's 10 Expected lists stored. 1 new test; 112 pass.
- **"Pick made" column (owner: "it says re-created … why don't you just use the actual lineup?")**: the re-created
  picks already used the real starting fives; "Re-created" was about WHEN the pick was made, but it sat in the
  "Lineup used" column. Now the Games tab has a separate **Pick made** column (Live / Re-created), and re-created
  rows show Lineup used = **Confirmed**. The Scorecard tab ends with a two-line explanation of Live vs Re-created.
  Header row made taller for the wrapped titles; checked in Numbers. 111 pass.
- **Spreadsheet is now an Excel file with two tabs (owner: "the CSV still looks funky" → "whichever looks the most
  organized and easiest to read")**: checked how Numbers really shows the CSV (exported to PDF and looked at it):
  a CSV holds one table, so Numbers made the SCORECARD line the header row and left the real column names
  mid-sheet. Now `my_predictions.xlsx` has a **Scorecard** tab and a **Games** tab — frozen shaded header with a
  filter button, set column widths, centred numbers, green YES / red NO / grey pending. New `spreadsheet.py`
  writes it with Python's own zip support (no new package; requirements unchanged). `picks.export_predictions`
  replaces `export_predictions_csv`; `CSV_COLUMNS`/`CSV_LABELS` → `COLUMNS`/`LABELS` (+ `WIDTHS`). Checked in
  Numbers again after the change: both tabs read cleanly. Old `my_predictions.csv` moved to the scratch backup.
  Also fixed a test whose #1-pick running-count check had slipped into a comment. 5 new/updated tests; 111 pass.
- **Newest games on top again (owner agreed, after asking which is better)**: oldest-first read like a calendar,
  but the daily use is checking last night's results and today's picks — newest first puts them right under the
  scorecard instead of ~1,000 rows down by April. Running counts still count from opening night, so the newest
  finished row (today: PHX @ DEN, 11/16) shows the season total, matching the scorecard (238 of 594; 39 of 198).
  Test renamed to check newest-first order; 109 pass.
- **Scorecard layout fixed (owner's Numbers screenshot: "the spacing looks off")**: the scorecard and the game
  table share columns, and a plain CSV cannot set column widths — the long one-line summary stretched column B and
  the labels sat in the narrow Date column. Now the scorecard starts in column C (the wide "Lineup used" column):
  summary split into short lines ("SCORECARD — 2026-27 season", "198 finished games since 10/20", "198 re-created
  week by week, 0 live picks", "5 games still to play today"), labels in C, Correct / Out of / Percent in D–F.
  Tests check it starts in column C and no line is wider than that column's header. 109 pass. (An .xlsx with
  set widths was considered; not chosen — it needs a new package and a new file type.)
- **Spreadsheet order flipped (owner: "it should be the other way around")**: `my_predictions.csv` rows now run
  oldest first — 10/20 (opening night) at the top, today's games at the bottom; running counts add up top-down,
  so the last finished row matches the scorecard (238 of 594, 39 of 198 today). 1 new test; 109 pass.
- **Season-wide scorecard (owner: "this scorecard should be for this season only … not just for today")**:
  live picks only started being saved on 2026-11-17, so new `season_backtest.py` re-creates a pick for every
  earlier 2026-27 game the honest way — week by week, each week predicted by a model trained only on earlier
  games, point-in-time inputs, tip chance/jumpers from earlier tips, the starters who actually started (what the
  confirmed lineup shows before tip-off). Stored in `pick_log` as `BACKTEST` and shown as "Re-created
  (week-by-week test)"; games with a live pick are skipped; re-running replaces old BACKTEST rows. Result: 198
  picks, #1 pick right 39 times (19.7% — exactly the model's official week-by-week score, so the re-creation
  matches). `my_predictions.csv` now covers this season only and the first line reads e.g. "2026-27 season: 198
  finished games since 10/20 (198 re-created week by week, 0 live picks) · 5 still to play today" (owner asked
  for "still to play today"). Database backed up first (scratch `backup/before_season_backtest`). 2 new tests; 108 pass.
- **Team to score first: no counter (owner: "keep team to score first, but I don't need a counter for it")**:
  still on every card ("📈 Scores first") and in `my_predictions.csv` (prediction, chance, and "Did we call the
  team that scored first?" per game), but left out of the scorecard and of the running "all predictions" count,
  which = #1 pick + top 2 + tip-off (`picks.CHECKS`). (Briefly removed everywhere, then restored, the same day.)
  Tests check it is shown but not counted; 106 pass.
- **Running counts in my_predictions.csv (owner: "a running count … correct compared to total predictions", and
  the same for the #1 pick only):** two new last columns on every game row — "Running count: all our predictions
  correct so far" and "Running count: #1 pick correct so far", e.g. "9 of 16 (56%)". Counted oldest game first, so
  the newest finished game shows the total; unplayed games are blank. "All predictions" = every YES/NO answer
  (#1 pick, top 2, tip-off — team to score first later taken out of the count; an unknown tip winner is not counted). The scorecard gained an
  "ALL OUR PREDICTIONS COMBINED" line with the same total. 1 new test; 106 pass.
- **Scorecard in my_predictions.csv (owner: "out of how many total predictions how many we got correct")**:
  the file now opens with a SCORECARD block — "Our #1 pick scored first", "Our #1 or #2 pick scored first",
  "We called the tip-off winner", "We called the team that scored first", each as correct / out of / percent —
  then a blank line, then one row per game. Only finished games count (pending and unknown tip winners are left
  out); the first line says how many games are finished, since when, and how many are still to play.
  2 new tests (incl. the start date across New Year); 105 pass. Today: 0 finished, 5 to play (picks log started today).
- **requirements.txt cleaned (2026-11-17):** checked against every import. Removed unused nba_api, lightgbm,
  apscheduler, python-dotenv, black, and the direct scikit-learn/xgboost/polars lines (sportsdataverse installs
  what it needs itself; the old `polars>=0.19` no longer matched its `polars>=2,<3`). Kept sportsdataverse,
  numpy, pandas, scipy, requests, rich, plus pytest and ruff for checks. `pip install --dry-run` resolves cleanly.
- **Spreadsheet headers in plain English (owner: "team right is very general")**: `my_predictions.csv` columns
  now read e.g. "Our #1 pick to score first", "Predicted team to score first", "Did we call the team that scored
  first?" (`picks.CSV_LABELS`; code keys unchanged). Rebuilt the file. 1 new test; 103 pass.
- FanDuel-only confirmed (free throws count — matches our labels). News found: NBA has told partner sportsbooks
  to drop player "first-of" markets this season — owner checked: FanDuel still offers first basket (§3.1).
- Hand-set weights review: tip-record trust K=10 is already the best setting (both seasons); recency weighting
  in training rejected after a control showed the apparent gain came from a different L2, not the weighting.
  No model change (§3.1).
- Owner asked if Paul Reed starts for DET today: NBA.com's expected lineup (17:52 ET) is Jenkins, Huerter,
  Cunningham, Thompson, Collins (C); Reed is healthy but came off the bench in DET's last 3 games. The program
  already uses NBA.com's list, so Collins is the predicted jumper.
- New `experiments/` folder (read-only test scripts, safe to run any time; they change nothing):
  `h2h_tip_test.py`, `tip_vs_team_test.py`, `tip_shrink_test.py`, `recency_test.py`, `h2h_fb_test.py`,
  `matchups_test.py`. No model code changed; 102 tests pass.

**Accuracy push (owner: "anything else to improve accuracy")**
- Free-throw attempts per 36 as a stage-2 input: rejected (log-loss worse in both periods).
- Season shrink strength: 100 looked best with L2 fixed at 0.001 (21.7% / 2.2307), but with the real training
  procedure the model chose L2 0.3 and scored 19.2% / 2.2378 — worse than 30. Reverted (note in pregame_model.py).
  Lesson recorded: experiments must use the real L2 selection.
- Early-shot share: new `early_shots` table (each team's first 5 attempts; `nba.early_shots`/`store_early_shots`,
  stored on every ingest; sync retries recent gaps); backfilled all games visibly in a terminal tab;
  `build_early_shot_index` / `early_shot_share` (point-in-time). Tested with the real procedure → adopted:
  log-loss 2.2369 → 2.2318 on 2026-27 (top pick 20.7% → 19.7%), better on Jan–Apr 2026 too. 3 tests.
- Watch mode restarted on the new model. 94 tests pass, `ruff` clean.

**Predictions spreadsheet + simpler command (owner's request)**
- `my_predictions.csv`: one row per game — our final pre-game pick #1/#2 with chances, predicted tip winner (player)
  and chance, team to score first and chance, then the actual first scorer, how he scored, the actual tip winner,
  and YES/NO/pending for pick 1, top 2, tip and team. Written after every `predict` and on every sync (so the
  next day's run fills in results). `pick_log` gained `home_jumper` / `away_jumper` (added automatically to the
  existing table); today's 35 picks were back-filled with their jumpers. 2 tests.
- Plain `python3 nba.py` (or with only options, e.g. `--watch`) now runs today's picks; `python3 nba.py help`
  lists the other commands. 96 tests pass.
- Folder tidy-up (engine/, data/, tests/ sub-folders) postponed until after tonight's watch-mode check, so the
  running watch tab is not disturbed.

**Simpler workflow: no watch mode, one-game view**
- Owner found watch mode unnecessary: run the program ~30 minutes before the game instead. Watch mode stopped
  and removed from the workflow (the option still exists).
- `python3 nba.py LAL` (any case; ESPN or NBA.com codes) shows only that team's game; `parse_teams` /
  `filter_games` in `nba.py`; 2 tests; 98 pass. Checked live: `python3 nba.py nyk` → NY @ TOR only.

**Team names + "which team?" question (owner's request)**
- `parse_teams` understands nicknames for all 30 teams (Knicks, Lakers, Hornets, Spurs, Thunder, Rockets, Wizards,
  Trail Blazers/Blazers, Sixers/76ers, Cavs, Mavs, Wolves…) as well as codes, in any case and with punctuation.
- Plain `python3 nba.py` in a terminal lists today's games and asks "Type a team (e.g. Knicks), or press Enter for
  all games"; an unknown name or a team without a game asks again; Ctrl-D/Ctrl-C at the question = all games;
  `--all` skips the question; scripts/pipes (no terminal) are never asked. 3 tests; 101 pass. Checked by simulating
  typing "spurs" → only DET @ SA.

**Self-explaining game question (owner: "it needs to be simple to use and not assume I know the commands")**
- The question now shows a numbered list with full team names ("3.  Pistons @ Spurs   8:00 PM ET") and spells out
  the three possible answers: the number next to the game (with an example, "1 = Knicks @ Raptors"), a team
  name, or Enter for all games.
- Wrong answers explain themselves ("There's no game 9 — pick a number from 1 to 5", "Didn't recognise …",
  "Celtics not playing today") and ask again. New `TEAM_LABEL` (code → display name, all 30 teams).
  1 new test; 102 pass. Checked live: typing "2" → only Trail Blazers @ Timberwolves.

**Version control:** git/GitHub declared out of scope by the owner (2026-11-17); no further git work is planned.

### 2026-11-09
> ⚠️ Several items below were later found not to be implemented or not to work (see 2026-11-17).
- Tip-off model v1 (`tip_model.py`, `tipoff_model.json`), "70.1%" in-sample; `tip_win_prob` added as 9th feature;
  forward test 15.1% on 139 games. *(Feature later found broken; rebuilt 2026-11-17.)*
- `tip_winner_player_name` / `tip_loser_*` columns, `export_csv()`, `backfill_tip_players` command.
- Claimed: automated predict with `update_player_game_logs()` and auto-retrain; removal of `load_model()` and the
  in-game branch. *(Not true in the code; done for real 2026-11-17.)*
- NBA.com daily-lineups source with Expected/Confirmed; injury-adjusted projection; `--watch`,
  `--confirmed-only`, `report`; ESPN injury parser; README rewrite; `scipy` added.

### 2026-11-07
- Audit found label leakage in the legacy model (`early_shots`/`early_touches` from the first 10 plays): true
  pre-game accuracy 10.7%, earlier numbers invalid.
- Defined the forward test; researched methods (tip effect, two-stage model, ≈17.7% ceiling).
- Fixed starter bugs (date-string comparison dropping 2026 games; unreliable ESPN pre-game starter flag;
  Day-to-Day treated as out).
- Backfilled `lineups` (1,399/1,406); shipped the first conditional-logit model (15.2%, log-loss 2.268).

---

## 11. End-of-session checklist

0. Update **"Where we left off"** in §0: date/time the session ended, what's in progress, what's coming up (with
   dates), and one dated line in "What was done".
1. Add a dated entry to §10 (what changed, what was run, results).
2. Update §4 (component status) and §7 (tick off finished items, add new ones).
3. Update §2 (data counts) and §3 (new results — walk-forward only, never in-sample).
4. Update §6: remove verified assumptions, add new limitations.
5. Update "Last updated" at the top. Run the test command in §5 and record the count.
6. Update `README.md` whenever anything a user sees or runs changed (features, files, commands, test count) — same session, never later.
