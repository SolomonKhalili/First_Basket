# NBA First Basket Scorer Bot — Project Blueprint

> **Last updated:** 2026-11-17 (full project review; fixed stale data, the `predict` crash, and the lineup/injury gaps; `predict` now syncs everything first; corrected status claims that weren't true; remaining to-dos in §7) · **Owner:** Solomon · **Folder:** `~/Desktop/NBA`
> This file is the single source of truth for project state. Update it at the end of every work session (see the checklist at the bottom).

---

## 0. AI CONTEXT — read this first (paste/upload this file with the project)

**Goal.** Each game day, *before tip-off*, predict which **starter** will score the first **made field goal** (free throws excluded; regulation only) in every NBA game, output a probability per player, and compare to sportsbook prices to look for value.

**Where it stands (one paragraph).** Data ingestion, a lineup/injury pipeline, and a calibrated *pre-game* model are working. The pre-game model is **weak by design** (top-1 ≈ 15% vs 10% random; honest ceiling in public sources ≈ 17–18%), so it is a **shortlist generator, not a proven betting edge**. The original model (`model.txt`, "62.8% accuracy") is **invalid for pre-game use** — it learned from first-10-plays features that already contain the first basket (label leakage). Do not trust or cite its numbers. Odds/EV is **not built**.

**Review findings (2026-11-17) — read before trusting any number:**
- With the rebuilt tip feature (2026-11-17) the active model picks the first scorer **18.2%** of the time on 198 forward-test games, vs 16.7% for the "most minutes per game" rule. That gain is **within noise** (log-loss better by 0.0062 ± 0.0095), so it's promising, not proven.
- Auto-retrain (`build_features.py --incremental` + `train_pregame.py --incremental`) only ever fits the 2025-26 season, so it **never learns from 2026-27 games**.
- Fixed today: player stats and lineups were stuck at Nov 8 (now refresh automatically), and `predict` crashed on games that had already tipped off.
**Hard rules / decisions already made**
- Only the **two most recent seasons** (2025-26 + current 2026-27) are used; rosters/roles change too fast for older data.
- Train on **regular season only**; evaluate with a **forward test** (train 2025-26 → test 2026-27 so far). Never evaluate on random splits.
- Pre-game features must be knowable **before tip**. Never use `early_*` (first-10-plays) or `tip_winner` for pre-game prediction.
- Every lineup is labeled **CONFIRMED / EXPECTED / PROJECTED**; never present a projected lineup as official.
- Work on copies of data; keep outputs honest (report forward-test numbers, not in-sample).
- **Keep this file current.** Whenever something is finished or a new to-do appears, update §4/§6/§7 and add to the §10 session log in the same session. This file is Solomon's tracker *and* the instructions handed to any AI working on the project.

**Environment.** Python 3.10 (macOS). Data via `sportsdataverse` (ESPN) + NBA.com public JSON feeds. SQLite DB. The AI assistant's cloud sandbox **cannot reach ESPN / NBA.com**; anything live must be run by Solomon locally and pasted back.

---

## 1. How the system works (data flow)

```
ESPN (sportsdataverse) ──► nba.py backfill/catchup ──► nba_first_basket.db
                                   │                      ├ games, first_baskets, predictions
                                   └─ box score ─────────►└ lineups (starter flag, minutes per player per game)

NBA.com daily lineups feed ─┐
NBA.com live box score ─────┴─► starters.py ─► resolve starting five (CONFIRMED > EXPECTED > PROJECTED)
                                                       ▲
ESPN injuries feed ───────────────────────────────────┘  (removes Out/Doubtful, flags Day-to-Day)
                                   │
player_game_logs.pkl + team stats ─► pregame_model.py ─► P(first basket) per starter ─► fair odds
```

**Pre-game model** (`pregame_model.json`, trained by `train_pregame.py`): conditional logit (softmax over the 10 starters), 9 features: `is_home, fb_rate_shrunk, L10_fb_shrunk, usage_rate, true_shooting, minutes_per_game, points_per_game, team_tipoff_win_pct, tip_win_prob`. Rates are shrunk toward 10% (prior) so small samples count less. Strongest weights: `usage_rate`, `fb_rate_shrunk`. Note: `usage_rate` is really shot attempts per 36 min (not true usage). `tip_win_prob` = P(player's team wins the opening tip) from `tip_model.py` (rebuilt 2026-11-17; weight +0.071, now the 3rd-strongest feature).

**Lineup source order** (in `predict`): NBA.com daily lineups feed with status `Confirmed` → NBA.com live box score → NBA.com `Expected` (injury-adjusted) → our projection (usual starters minus injured). ESPN is **not** a lineup source: checked 2026-11-17, ESPN's pre-game data has no players at all (starters appear only after tip-off), so that step was removed. Starters are the feed's players that carry a `position` tag.

---

## 2. Current data state (as of 2026-11-17)

| Item | State |
|------|-------|
| Games in DB | 1,524 (Oct 21 2025 – Nov 16 2026); 1,515 with a labeled first basket; 9 without (ingest misses, never retried) |
| `first_baskets` table | 1,515 rows, all matched to a game ✅ (72 orphan rows — preseason + All-Star games — removed 2026-11-17; `predict` now drops any new ones automatically) |
| 2025-26 regular season | Complete: 1,230 labeled games (Oct 21 – Apr 12); every team 82–83 games |
| 2025-26 post-season | Partial (87 labeled games after Apr 12; playoffs incomplete). **Excluded from training.** |
| 2026-27 season | In progress: 198 labeled games (Oct 20 – Nov 16). Used as the **forward test set** |
| `lineups` table | 1,517 games, through Nov 16 ✅ (7 old games fail with HTTP 403 — harmless) |
| `player_game_logs.pkl` | Through Nov 16 ✅ — now refreshed automatically by `predict` (was stuck at Nov 8 until 2026-11-17) |
| Training matrices | `X_train.npy` (15,150×18), `y_train.npy`, `groups.npy` (1,515 games, 10 starters each). Rows for Nov 9–16 games were built with stale (Nov 8) player stats |
| `tip_winner_player_id` / `tip_loser_player_id` | 1,514/1,515 games populated in DB + `first_basket_data.csv` (auto-synced on catchup) |
| `games.status` column | Empty (NULL) for every game — not filled by ingest |

**Key measured facts (from our own data)**
- The team that wins the tip scores the first basket **64.4%** of the time (792 / 1,230). Home wins the tip 52.8%.
- The first scorer is on the home team 52.9% of the time.
- Random pick among 10 starters = 10%.
- **Tip-off model (rebuilt 2026-11-17, point-in-time):** calls the tip winner **63.6%** of the time on 2026-27 (126/198; always-home = 47.5%) and 61.0% on 2025-26 (log-loss 0.637 vs 0.693 coin flip). The old "70.1%" was in-sample and not real.

---

## 3. Model results

| Model | Scenario | Top-1 | Log-loss (uniform = 2.3026) |
|-------|----------|-------|------------------------------|
| **Pre-game conditional logit (ACTIVE, rebuilt tip feature)** | Forward test, **198 games** of 2026-27 (2026-11-17) | **18.2%** | **2.2497** |
| Pre-game conditional logit (broken tip feature) | same 198 games | 16.7% | 2.2559 |
| ↳ Baseline: highest minutes per game | same 198 games | **16.7%** | — |
| ↳ Baseline: season first-basket rate | same 198 games | 15.7% | — |
| ↳ Baseline: `usage_rate` (shots per 36) | same 198 games | 15.2% | — |
| ↳ Baseline: points per game | same 198 games | 13.6% | — |
| ↳ Baseline: L10 first-basket rate | same 198 games | 13.1% | — |
| Pre-game conditional logit (+tip_win_prob, as trained) | Forward test, 139 games of 2026-27 | 15.1% | 2.2633 |
| Pre-game conditional logit (previous) | Forward test, 125 games of 2026-27 | 15.2% | 2.268 |
| — | `nba.py eval` (7 games, mixed model versions) | 42.9% (3/7) — **noise, don't cite** | — |
| Season first-basket-rate alone | same 125 games | 16.0% | — |
| LightGBM ranker, pre-game features only | same 125 games (5 seeds) | 8.8–16.0% (mean 13.8%) | not calibrated |
| Legacy `model.txt` (leaky) | with in-game features | ~60% | — *(invalid pre-game)* |
| Legacy `model.txt` | in-game features zeroed (true pre-game) | **10.7%** | — |

Read this honestly: with 198 test games the 95% interval is ≈ ±5 points, so 15–17% is **not statistically distinguishable** from 10–20%, and the model does **not** beat the "most minutes" rule. Every future model change must be compared against that rule, not just against 10% random. Predicted probabilities were roughly calibrated (top-pick mean 14.9% vs 15.2% hit rate). Public sources report a ≈17.7% pre-game hit rate (StatsBench, 724 games, 2024-25) and call it the ceiling. Sportsbook favorites are ~+400 to +800 (14–17% implied) with ~20% board margin, so **no demonstrated edge**.

---

## 4. Components — status

| Component | File | Status |
|-----------|------|--------|
| DB schema (`games, first_baskets, predictions, lineups`) | `nba.py init` | ✅ (init fixed to match real schema) |
| ESPN ingestion, backfill, auto-catchup | `nba.py` | ✅ populates `tip_winner_player_id`; **catchup stores lineups since 2026-11-17** (it didn't before). ⚠️ games whose PBP fetch fails are never retried |
| Player game logs refresh | `build_features.build_player_game_logs()` | ✅ **fixed 2026-11-17** — incremental: loads the cache and fetches box scores only for new games; called by `predict` every run |
| Historical tip player backfill | `nba.py backfill_tip_players` | ✅ targeted, fast (populates `tip_winner_player_id` + `tip_winner_player_name`; rewrites CSV) |
| Lineup backfill | `nba.py lineups` | ✅ done once (re-run only if table is wiped) |
| Lineup resolution + injury logic | `starters.py`, `nba.py` | ✅ 47 unit tests passing; the review's lineup gaps fixed 2026-11-17 (see §10). Projects a full five for all 30 teams on real data. Remaining: nickname aliases, traded players (§6) |
| Injury report (ESPN feed) | `starters.py` | ✅ `predict` and `report` (projection view) now also fetch each game's ESPN summary, which carries Doubtful/Questionable (31 extra entries on 11-17). The NBA.com-feed view of `report` still uses the league feed only |
| Daily report (matchups + lineups + injuries) | `nba.py report` | ✅ works; falls back to projections when NBA.com file isn't published |
| Pre-game model + training | `pregame_model.py`, `train_pregame.py`, `pregame_model.json` | ✅ retrained 2026-11-17 with the rebuilt tip feature: 18.2% vs 16.7% for the "most minutes" rule — ahead, but within noise (§3) |
| Watch mode (recheck, print on change/confirm) | `nba.py predict --watch` | ✅ built, tested with simulated feeds |
| Screen output | `display.py` (uses `rich`) | ✅ 2026-11-17: one box per game (top 5 with bars + fair odds, tip-off chances + jumpers, starters with injuries coloured, notes), coloured by lineup type; data-update chatter hidden; warnings collected in one box; "How to read this" legend. Game times shown in ET. 4 tests in `test_display.py` |
| **Fully automated predict workflow** | `nba.py predict` (also `report`, `eval`) | ✅ **`sync_data()` runs first, continuing from saved data (nothing rebuilt from scratch):** catchup from the last stored date (re-checks that date, skips games already labeled) + tip + lineups → retry recent (14-day) games missing a first basket or lineup → drop orphan first baskets → rewrite CSV → add new games to player logs → if the DB has labeled games newer than the training matrices: `build_features --incremental` + `train_pregame --incremental`. Then injuries → lineups → predict; games already tipped off are skipped. Ran end to end on real games 2026-11-17 |
| Auto-retrain | `nba.py _retrain_if_needed` | ✅ trigger fixed 2026-11-17: compares the matrices' last game date (`game_ids.json`) to the DB, so any new labeled game triggers it (old 10-game threshold + `last_retrain.txt` removed); a failed step warns and keeps the old model. ⚠️ still only refits 2025-26 (never learns 2026-27) and overwrites `pregame_model.json` with no "is it better?" check |
| Accuracy check | `nba.py eval` | ⚠️ mixes model versions; `INSERT OR REPLACE` overwrites earlier picks; only 16 predictions stored |
| **Incremental feature building** | `build_features.py --incremental` | ✅ loads existing matrices, finds last date, only computes new games, appends to arrays |
| **Warm-start model training** | `train_pregame.py --incremental` | ✅ loads previous model, warm-starts L-BFGS-B from weights, uses previous L2, skips grid search |
| Feature engineering (legacy) | `build_features.py` | ✅ checkpointing enables incremental rebuild; leaky `early_*` only in `X_train.npy` |
| Legacy model + in-game branch | `train_model.py`, `model.txt` | 🗑️ `model.txt`/`model_meta.json` deleted earlier; **`load_model()` and the in-game branch actually removed from `nba.py` on 2026-11-17** (the 11-09 entry said so, but the code was still there and crashed) |
| Odds / EV / bankroll / bet logging | — | 🔲 not started |
| Tip-off win model | `tip_model.py` (used by `train_pregame.py`, `build_features.py`, `nba.py`) | ✅ **rebuilt 2026-11-17**: real jumpers (who jumped most in the team's last 10 games, must be starting), win rates from earlier games only (shrunk to 50%), log5 + home court; home + away add to 1. 63.6% tip winners on 2026-27. `python3 tip_model.py` prints the check. `tipoff_model.json` is no longer used |
| First-shot-share feature (from play-by-play) | — | 🔲 not started |

---

## 5. Daily workflow

```bash
cd ~/Desktop/NBA
python3 nba.py predict                # one command: syncs everything that's missing (DB, lineups, CSV, player logs, training data, model) → injuries → lineups → predict
python3 nba.py predict --watch        # leave open: projections → EXPECTED → CONFIRMED, rechecks every 5 min
python3 nba.py predict --confirmed-only   # only games with an official CONFIRMED lineup
python3 nba.py report                 # matchups, injuries, starters (or projections)
python3 nba.py eval                   # night: accuracy on finished games
pytest test_starters.py test_tip_model.py test_display.py   # sanity check after changing code (expect 56 passed)
```

Output per game (a box, see `display.py`): top 5 starters, probability, **fair American odds**, tip-off chances, starters. Compare to a sportsbook price: it is only +EV if the book's price is *longer* than the fair odds (e.g., model 13.6% = fair ≈ +635 → +800 is value, +450 is not). Run ~30 min before tip; NBA.com posts official lineups then.

---

## 6. Known issues / unverified assumptions

- ✅ **Confirmed status string — verified 2026-11-17:** NBA.com's daily files for 11-15 and 11-16 list every starter as exactly `"Confirmed"` (90/90 and 80/80), so `parse_daily_game` is right.
- ℹ️ **When the NBA.com daily file appears:** 11-17's file existed at 1:11 PM ET (stamped 1:06 PM, all `Expected`); the next day's file is 404 until then. Before it appears, `predict` must use our projection (🟠 PROJECTED).
- ℹ️ **How good the projection is** (checked 2026-11-17 against NBA.com's confirmed starters for 11-15 and 11-16, 34 team-lineups): 146/170 starters right (**86%**), all five right for 14/34 teams. Simpler "last game's five" scored 80% / 11/34, so the projection stays. Misses are mostly the 5th starter / centre.
- ❓ **NBA.com box score 403** before lineups are submitted is treated as "not posted yet" (assumption).
- ⚠️ **Out-but-returning-today.** ESPN sometimes lists players `Out` with est. return = today (e.g., Doncic 11-09). They are removed in projections but flagged "may actually play".
- ⚠️ **ESPN game dates are UTC**, so late games can carry tomorrow's date; the feed lookup tries today's ET date first.
- ⚠️ **ID matching.** NBA.com ↔ ESPN player IDs are matched by normalized name via the `lineups` table. Unmatched players (new/rookies) are flagged and scored with default stats.
- ⚠️ `predictions` uses `INSERT OR REPLACE` per game, so re-running overwrites earlier picks. `model_version` encodes the lineup source (`…_nba_confirmed`, `…_nba_expected_unconfirmed`, `…_projected_usual_starters`), but history of earlier runs is lost.
- ℹ️ 9 games have no first-basket record; 7 old games lack lineups (HTTP 403). Immaterial.

**Found in the 2026-11-17 review (not fixed yet — each has a to-do in §7):**
- ✅ ~~`tip_win_prob` was broken three ways~~ — **fixed 2026-11-17** (wrong player — the max-minutes starter jumped only 10.1% of the time; away players got the home team's chance; always 0.5 live + leakage). The `tip_win_prob` column stored in `X_train.npy` for old rows still holds the old values; it isn't used (`train_pregame.py` recomputes the feature from the DB).
- ⚠️ **Lineup gaps still open** (the other review findings were fixed 2026-11-17 — see §10):
  - Nickname pairs (Nic/Nicolas Claxton, Herb/Herbert Jones) still don't match between NBA.com and ESPN names.
  - A traded player counts for his old team until he plays a game for the new one (team is learned from box scores).
  - A cleared regular starter is brought back only when the injury report loaded; if the feed fails, he stays out (on purpose — no report means we can't tell "cleared" from "still hurt").
- ⚠️ **Data hygiene:** `games.status` is never filled; ESPN dates are UTC (late games stored under the next day). (Fixed 2026-11-17: orphan rows removed; catchup re-checks the last stored date; recent games missing a label or lineup are retried. The 9 old unlabeled games and 7 old 403 lineups are permanent and no longer retried.)
- ⚠️ **Misleading features:** `first_shot_rate` is just 2 × `first_basket_rate`; `usage_rate` is shots per 36 min, not usage; `team_tipoff_win_pct` counts games with no recorded tip as losses.
- ⚠️ **Git:** all of the 2026-11-17 work is committed (`8bd440a`), including `tip_model.py`, `game_ids.json`, `display.py` and the tests. Still tracked but shouldn't be: `.pyc` and `.DS_Store`. The DB, `.pkl` and `.npy` are tracked too (a decision to make). `tipoff_model.json` is untracked and unused. 8 old worktrees in `.claude/worktrees/` (`fanduel-px-fix` has 14 uncommitted changes).

---

## 7. To-do (prioritized)

**P0 — correctness / trust**
- [x] ~~Verify the CONFIRMED status string~~ — **done 2026-11-17** (exactly "Confirmed").
- [ ] Pin down when NBA.com first posts the daily lineups file (seen by 1:06 PM ET on 11-17); check `predict --watch` against NBA.com's lineups page during a full evening.
- [ ] Improve the projection's 5th starter (86% of starters right, all five right for only 14/34 teams).
- [x] ~~Auto-refresh `player_game_logs.pkl` so features aren't stale~~ — **actually done 2026-11-17**: `build_player_game_logs()` is now incremental and `predict` calls it every run. (The 11-09 entry claimed `update_player_game_logs()`, but it never existed; logs were stuck at Nov 8.)
- [x] ~~Catchup stores lineups for new games~~ — **done 2026-11-17** (`st.store_game_lineup` in `catchup_missing_games`); Nov 9–16 gap filled with `nba.py lineups`.
- [x] ~~Remove the leaky legacy model + in-game branch from `nba.py`~~ — **actually done 2026-11-17**: `load_model()`, `softmax` and the in-game branch removed; games that already tipped off are now skipped instead of crashing. Also fixed the same crash in `nba.py test`.
- [x] ~~**Lineups:** an Out starter with no replacement must not count as a complete lineup~~ — **done 2026-11-17**: the projection is marked incomplete and `predict` skips the game, naming the player.
- [x] ~~**Lineups:** run injury substitution on EXPECTED NBA.com lineups too~~ — **done 2026-11-17**: swaps are printed under the game; CONFIRMED lineups are still never changed.
- [x] ~~**Lineups:** keep a cleared regular starter in the five even if he missed the whole last-5 window~~ — **done 2026-11-17** (flagged as returning).
- [x] ~~**Lineups:** pick replacements from the *current* absence first, then the whole season~~ — **done 2026-11-17**.
- [x] ~~**Lineups:** two Questionable starters get the same "if he sits" player~~ — **done 2026-11-17**.
- [x] ~~**Injuries:** pass today's ESPN game IDs to `fetch_injury_report()`~~ — **done 2026-11-17** (`predict` + `report` projection view).
- [x] ~~**Names:** treat "P.J." = "PJ" in `norm_name`~~ — **done 2026-11-17** (no two players in the DB merge).
- [x] ~~`returnDate` with a time part fails the "returns today" check~~ — **done 2026-11-17**.
- [ ] **Names:** add a small nickname alias table (Nic/Nicolas, Herb/Herbert) — only once a real mismatch shows up in `predict` ("no history found").
- [ ] Log every pick (with lineup status + timestamp) to a table *without overwriting*, so we can measure accuracy by CONFIRMED vs EXPECTED vs PROJECTED. Make `eval` group by model version.
- [x] ~~Catchup: re-check the last stored date and retry games that have no first basket~~ — **done 2026-11-17** (`sync_data()`; retries cover the last 14 days).
- [x] ~~Clean up the 72 orphan `first_baskets` rows~~ — **done 2026-11-17** (they were preseason + All-Star games).
- [x] ~~One command keeps everything up to date~~ — **done 2026-11-17**: `predict`/`report`/`eval` run `sync_data()` first.
- [ ] Fill or drop the empty `games.status` column; store game dates in ET, not UTC.
- [x] ~~**Git:** commit `tip_model.py`, `game_ids.json`~~ — done 2026-11-17 (`8bd440a`).
- [ ] **Git:** stop tracking `.pyc`/`.DS_Store`; decide whether the DB/`.pkl`/`.npy` belong in git; remove the 8 old worktrees (check `fanduel-px-fix` first).
- [x] ~~Fix `README.md`~~ — rewritten 2026-11-09 (accurate methodology, honest results, new commands). `requirements.txt` now includes `scipy`.

**P1 — model quality (the only realistic path to an edge)**
- [x] ~~**Redo the tip feature**~~ — **done 2026-11-17**: real jumpers, past games only, home + away add to 1; retrained → 18.2% top-1 / log-loss 2.2497 on 198 test games (was 16.7% / 2.2559). 5 new tests in `test_tip_model.py`.
- [ ] **Two-stage model (Phase 3):** use P(team wins tip) × 64% to get P(team scores first), then pick the player within the team — the tip feature is now trustworthy enough to try this.
- [ ] **Fix auto-retrain:** train on a rolling window that includes 2026-27 games; only replace `pregame_model.json` if the new model does better on held-out games. (Counting new games by date — done 2026-11-17.)
- [ ] **Beat the "most minutes" baseline** (16.7% on 198 games) — report it next to every new model result.
- [ ] Rebuild training matrices so Nov 9–16 rows use fresh player stats (they were built from Nov 8 logs).
- [ ] Clean up misleading features: drop `first_shot_rate` (2 × fb rate), rename `usage_rate`, count no-tip games as unknown in `team_tipoff_win_pct`.
- [x] **Incremental feature building:** `build_features.py --incremental` loads existing matrices, finds last date, only computes new games, appends to arrays.
- [x] **Warm-start model training:** `train_pregame.py --incremental` loads previous model, warm-starts L-BFGS-B from weights, uses previous L2, skips grid search.
- [x] **Auto-retrain uses incremental pipeline:** `nba.py predict` passes `--incremental` to both build and train whenever the DB has labeled games newer than the training matrices (2026-11-17; was a 10-game threshold).
- [x] **CSV always in sync:** `sync_data()` rewrites `first_basket_data.csv` from the DB on every `predict`/`report`/`eval` run.
- [x] **Fixed catchup KeyError 'y':** `fb['y']` → `fb.get('y')` in `ingest_game()`.
- [x] **Fixed checkpoint system:** atomic writes with `os.replace`, clears old checkpoints in incremental mode, fixed `start_idx` overwrite bug.
- [ ] **First-shot share:** each starter's share of their team's first shot attempt, from play-by-play.
- [ ] Rebound branch (who gets the board if the first shot misses); starter position/role; opponent defense vs position.
- [ ] Combine: P(team scores first) × P(player | team scores first); calibrate; re-run the forward test.
- [ ] Weekly forward test as 2026-27 games accumulate (±6 points now; shrinks with sample).

**P2 — odds layer (only after the model shows signal)**
- [ ] Vig removal, EV, closing-line value, fractional Kelly (0.5–1% unit), bet log with ROI/CLV/hit rate.

**P3 — nice to have**
- [ ] Official NBA injury report (PDF) as a second source; ESPN's feed can lag.
- [ ] Optuna / SHAP / isotonic calibration once there's more data.

---

## 8. File map

```
NBA/
├── nba.py                  # CLI: init, backfill, backfill_tip_players, lineups, report, predict [--watch|--confirmed-only], eval
├── display.py              # how `predict` looks on screen (rich boxes/tables)
├── test_display.py         # pytest suite for display.py [4 tests]
├── starters.py             # lineups (NBA.com/ESPN), injuries, projection, daily report  [47 tests]
├── test_starters.py        # pytest suite for starters.py
├── pregame_model.py        # feature builder + probabilities + fair odds   (ACTIVE model code)
├── pregame_model.json      # trained pre-game weights                       (ACTIVE model)
├── train_pregame.py        # retrains pregame_model.json (needs scipy)
├── build_features.py       # feature builder with checkpointing (incremental); also builds/refreshes player_game_logs.pkl; leaky early_* only in X_train.npy
├── tip_model.py            # tip-off model: P(team wins tip) from earlier games only
├── test_tip_model.py       # pytest suite for tip_model.py [5 tests]
├── game_ids.json           # game order of the training matrices
├── train_model.py          # legacy LightGBM trainer (retired)
├── ~~model.txt / model_meta.json~~   # **removed** — legacy LightGBM model (leaky, invalid pre-game)
├── view_data.py            # DB viewer + CSV export
├── nba_first_basket.db     # SQLite: games, first_baskets, predictions, lineups (+ unused player_season_stats)
├── player_game_logs.pkl    # per-player game logs cache (✅ auto-refreshed by predict since 2026-11-17)
├── X_train.npy, y_train.npy, groups.npy, feature_names.json, meta.json   # training matrices
├── first_basket_data.csv   # export, rewritten from the DB on every predict/report/eval run: 13 columns (game_start … clock); empty `x,y` removed 2026-11-17 (still in the DB table, always empty)
├── requirements.txt (now includes scipy), README.md (rewritten 2026-11-09), BLUEPRINT.md (this file)
```

---

## 9. Research notes (what's solid vs not)

- **Solid / consistent with our data:** tip winner scores first ≈ 64% (Dickinson, 27k tip-offs; our 64.4%); two-stage structure P(team scores first) × P(player | team) is the standard approach.
- **Not validated:** HeatCheck/PropsEdge/SportsCommand-style "10-factor weights" and "% stability" claims — commercial sites, no backtests or sample sizes. Recent-form windows (L5/L10) carry little signal in our data.
- **Ceiling:** StatsBench reports 17.7% pre-game hit rate over 724 games (2024-25), no ROI/CLV. Breakeven at +600 is 14.3%; board margin ≈ 20%; losing streaks are long even with a real edge (10 straight losses at +700 ≈ 26%).

---

## 10. Session log (newest first — add an entry every session)

### 2026-11-17 (full review + incremental "save-file" training live)
- **Full project review** (model, training, `nba.py`, `starters.py`, data). Findings recorded in §3, §4, §6 and §7. Main ones: the model ties the "most minutes" rule (16.7% on 198 games); `tip_win_prob` is broken; auto-retrain never learns from 2026-27; several lineup/injury gaps.
- **Corrected false claims from the 11-09 entries**: `update_player_game_logs()` never existed (logs were stuck at Nov 8); the in-game branch and `load_model()` were still in `nba.py`; catchup did not store lineups; the tip-off model is not working.
- **Fixed stale data**: `build_features.build_player_game_logs()` is now incremental, and `predict` calls it every run → logs now through Nov 16 (59 games added). Catchup now stores each new game's lineup; ran `nba.py lineups` → lineups through Nov 16 (59/66 stored; the 7 failures are the old 403 games).
- **Fixed `predict` crash**: removed the in-game branch (`tip[0]` on a dict → crash; also used the deleted `model.txt`). Games that already tipped off are now skipped. Fixed the same crash in `nba.py test`.
- **One command keeps everything current (`sync_data()` in `nba.py`)**: `predict`, `report` and `eval` first continue from saved data — catchup from the last stored date (re-checked, labeled games skipped) with tip + lineups, retry recent games missing a label/lineup, drop orphan first baskets, rewrite the CSV every run, add new games to player logs, and run incremental build + train only when the DB has labeled games newer than the training matrices. Removed the 10-game threshold and `last_retrain.txt`. Deleted the 72 orphan first-basket rows (preseason + All-Star).
- **Verified**: 40 tests pass; full `python3 nba.py predict` ran end to end on today's 5 real games: sync re-checked Nov 16, found everything current, CSV = 1,515 games through Nov 16, training data up to date, predictions printed.
- **Incremental "save-file" training live**:
  - **Incremental feature building**: `build_features.py --incremental` loads existing matrices, finds last processed date, only computes features for new games, appends to arrays (saves ~90% feature build time).
  - **Warm-start model training**: `train_pregame.py --incremental` loads previous `pregame_model.json`, warm-starts L-BFGS-B from previous weights, uses previous L2, skips grid search.
  - **Auto-retrain pipeline**: `nba.py predict` catches up missing games, exports CSV, and runs incremental build + train (`--incremental`) — now triggered by any labeled game newer than the training matrices (see `sync_data()` above).
  - **Catchup fixes**: Fixed `KeyError: 'y'` in `ingest_game()` (`fb['y']` → `fb.get('y')`), fixed missing closing parenthesis in SQL execution, added traceback for debugging.
  - **Checkpoint system hardened**: Atomic writes with `os.replace`, clears old checkpoints in incremental mode, fixed `start_idx` overwrite bug (incremental `start_idx` no longer wiped by checkpoint load).
  - **CSV auto-sync**: `first_basket_data.csv` is rewritten from the DB on every `predict`/`report`/`eval` run (moved from `catchup_missing_games()` into `sync_data()`).
  - **Full rebuild completed**: restored 1,515-game training matrix (15,150 samples, 18 features) after earlier bug corrupted data.
- **Model continues from save file**: no more full retrain from scratch — the matrices are appended to and training warm-starts from the previous weights. Note: the training window is still the 2025-26 season only, so new 2026-27 games don't change the weights yet (to-do in §7 P1).
- **Fixed lineup/injury gaps** (7 new tests, written first and seen failing; 47 pass):
  - Out starter with no replacement → projection marked incomplete; `predict` skips the game and names him.
  - EXPECTED NBA.com lineups now get injury substitution (swaps printed); CONFIRMED lineups untouched.
  - Regular starter who missed the whole last-5 window and is no longer on a loaded injury report → back in the five, flagged as returning. Not done if the report failed to load or he's still Out.
  - Replacement chosen from his *current* absence first (an October fill-in no longer wins), then the whole season.
  - Two Questionable starters get different "if he sits" players.
  - `norm_name` joins initials ("P.J." = "PJ"); checked: no two players in the DB merge.
  - `returnDate` with a time part now works in the "returns today" check.
  - `predict` and `report` fetch each game's ESPN injury summary (Doubtful/Questionable): 31 extra entries today.
  - Verified on real data: all 30 teams project a complete five; today's 5 games predicted end to end.
- **Rebuilt the tip feature** (`tip_model.py` rewritten; 5 new tests written first and seen failing; 52 tests pass):
  - Jumper = who actually jumped most for the team in its last 10 games (and is starting tonight) — not the minutes leader.
  - Win rates and home-court edge use only games before the game date (no leakage); home + away add to 1.
  - Point-in-time check: 63.6% tip winners on 2026-27 (always-home 47.5%), 61.0% on 2025-26.
  - `pregame_model.build_row` now takes `tip_win_prob`; `train_pregame.py`, `build_features.py`, `nba.py` compute it once per game. Old helpers + `tipoff_model.json` dependency removed.
  - Retrained: forward test **18.2%** top-1, log-loss **2.2497** (was 16.7% / 2.2559 on the same 198 games). Paired check: log-loss better by 0.0062 ± 0.0095 → promising, not statistically proven yet. `tip_win_prob` weight +0.071 (was +0.016).
- **CSV cleanup**: removed the `x`/`y` columns from `first_basket_data.csv` (empty in all 1,515 games — the ESPN parser never captured shot location; nothing read them). `view_data.py`'s "Export to CSV" now calls `nba.export_csv`, so it no longer overwrites the CSV with a shorter format that dropped the tip-off columns. Rewrote the CSV: 1,515 games, 13 columns.
- **Lineup source fixed**: checked live — ESPN publishes no starters before tip-off (its pre-game data has no player list), so the ESPN step in `predict` could never work; removed it. NBA.com works: Expected lineups by ~1 PM ET, Confirmed ~30 min before tip; verified the status string is exactly "Confirmed". Measured our fallback projection against real starters (86% of starters right; beats "last game's five" at 80%). Game dates for today's games now use the ET date (ESPN's UTC date put evening games on tomorrow).
- **New screen output** (`display.py`, `rich`): one coloured box per game with the top 5 picks (bars + fair odds), tip-off chances and jumpers, both starting fives (injuries coloured), notes; data-update chatter hidden behind a spinner; warnings in one box; a "How to read this" legend that tells you to re-run or use `--watch` when a lineup is only projected. Removed the fake 45% heuristic used when the model file was missing (now shows a warning instead). 4 new tests; 56 pass. Ran on today's 5 real games.
- **Committed** all of today's work to `main` as `8bd440a`.
- **Next**: fix the auto-retrain training window (learn from 2026-27 games), then try the two-stage model.

### 2026-11-09 (Phase 1 complete — tip_win_prob integrated, forward test 15.1% on 139 games)
- **`tip_model.py`**: 155 centers, shrunk rates (prior=50%, K=10), Bradley-Terry + HCA → 70.1% in-sample tip accuracy. Saved `tipoff_model.json`.
- **`build_features.py`**: rebuilt matrices with `tip_win_prob` → 18 features, 14,560 samples across 1,456 games.
- **`train_pregame.py`**: retrained with tip feature → forward test **15.1% top-1**, log-loss **2.2633** (was 2.268). `tip_win_prob` weight +0.016 — marginal log-loss gain, no top-1 delta.
- **`nba.py eval`**: latest 7 games → 3/7 correct (42.9%); small sample.
- **Fixed `get_pbp`/`tip_jumpball`**: now correctly extracts both jumpball participants from ESPN PBP.
- **Added `render_game_card`** to `nba.py` for game card display (all 40 tests pass).
- **Added `_retrain_if_needed`** to `nba.py` — auto-retrains on `predict` when 10+ new games since last retrain.
- **Backfill**: `tip_loser` columns populated for 1,455/1,456 games (1 missing jumpball in PBP).
- **Next**: Phase 2 — First-shot share from PBP (team's 1st FGA in Q1).

### 2026-11-09 (tip_model.py trained + build_features.py rebuilt with tip_win_prob)
- **Ran `tip_model.py`**: 155 centers with tip history; shrunk rates with prior=50%, K=10; Bradley-Terry model with home court advantage; **70.1% in-sample accuracy** predicting tip winner (1,019/1,454). Saved `tipoff_model.json`.
- **Ran `build_features.py`**: rebuilt training matrices with `tip_win_prob` as 18th feature (was 17). 14,560 samples × 18 features across 1,456 games.
- **Fixed `get_pbp` / `tip_jumpball`**: now correctly extracts both jumpball participants from ESPN PBP (`participants.0` = winner, `participants.1` = loser).
- **Backfill complete**: `tip_loser_player_id` / `tip_loser_player_name` populated for 1,455/1,456 games (1 game missing jumpball in PBP).
- **Next**: `train_pregame.py` → `nba.py eval` for forward test with new tip feature.

### 2026-11-09 (tip_win_prob feature integrated — Phase 1 tip-off model code complete)
- **Built `tip_model.py`**: computes shrunk tip win rates per center, builds Bradley-Terry style tip-off model with home court advantage, persists to `tipoff_model.json`
- **Updated `pregame_model.py`**: added `tip_win_prob` as 9th feature in `FEATURES`; added `_load_tip_model()`, `predict_tip_win_prob()`, `get_tip_win_prob_for_player()` helpers; extended `build_row()` signature with `game_id`, `player_team`, `home_team`, `away_team` to compute tip win probability from starting centers
- **Updated `build_features.py`**: added `tip_win_prob` computation in feature loop (loads `tipoff_model.json`, queries starting centers from `lineups` table); added to `NUMERIC_FEATURES` list
- **Updated `train_pregame.py`**: pre-loads game info (home/away teams) from DB; passes `game_id`, `player_team`, `home_team`, `away_team` to `build_row()` for tip_win_prob computation
- **Updated `nba.py`**: `predict` command now passes game context to `build_row()` for live predictions
- **Next**: run `tip_model.py` → `build_features.py` → `train_pregame.py` → `nba.py eval`

### 2026-11-09 (tip_winner_player_name column + CSV export + backfill populates name)
- **Added `tip_winner_player_name` column** to `games` table — stores the actual player name (not just ID) who won the opening tip
- **Updated `tip_winner()`** to return `(team, player_id, player_name)` tuple from PBP jumpball play
- **Updated `ingest_game()` and `backfill_tip_players()`** to store all three tip fields in DB
- **Added `export_csv()`** and rewrote `backfill_tip_players()` to regenerate `first_basket_data.csv` with `tip_winner_player_name` column
- **Registered `backfill_tip_players` CLI command** — populates both ID and name for missing tip winner data
- **Fixed all `tip` usages** in `predict_today()` to use `tip_team` (string) instead of tuple
- This enables Phase 1 tip-off model (roadmap §7B) by providing player identity for historical tip win rates

### 2026-11-09 (auto-retrain + backfill_tip_players + full predict automation)
- **Full automation in `nba.py predict`**: catchup → `update_player_game_logs()` → `auto_retrain_if_needed()` (runs `build_features.py` + `train_pregame.py` if new games) → injuries → lineups → predict. No manual steps ever.
- **Added `nba.py backfill_tip_players`**: targeted backfill for `tip_winner_player_id` — only fetches PBP for games with first_basket but NULL tip_winner_player_id (fast, ~seconds vs ~60 min full backfill).
- **Removed legacy leaky model**: deleted `model.txt`/`model_meta.json`; stripped `load_model()` + in-game branch from `nba.py`; in-game predictions now use heuristic only.
- **`build_features.py` rebuild complete**: 1,456 games, 14,560 samples, 17 features; fixed `--` minutes parsing bug that silently skipped games.
- **40 pytest tests passing** (9 new for injury substitution logic).
- Updated BLUEPRINT.md to reflect automation, removed stale issues, marked to-dos done.

### 2026-11-09
- Added rule: BLUEPRINT.md is updated every time something is done or a new to-do appears (see §0).
- Built NBA.com daily-lineups source with Expected/Confirmed status; added injury-adjusted usual-starters fallback; `--watch` and `--confirmed-only` modes.
- Added `nba.py report` (matchups + injuries + starters). Injury parser fixed to ESPN's real format (team, reason, est. return); warning for "Out but est. return today".
- Feed fetched once per run; 404 = "not published yet". Tests: 23 passed on Solomon's machine; 24th test (returning-today warning) added afterward — confirm `24 passed` after copying the latest files.
- First `report` run worked on real data (8 games). NBA.com daily file not yet published at 2 AM.
- Rewrote this blueprint.
- Rewrote `README.md` (removed unverified "10-factor weights" and old methodology; added commands, honest results, lineup labels, limitations). Added `scipy` to `requirements.txt`.

### 2026-11-07
- Audited project: found **label leakage** in legacy model (`early_shots`/`early_touches` from first 10 plays). True pre-game accuracy 10.7%; blueprint numbers invalid.
- Confirmed data covers two seasons; 2025-26 regular season complete, playoffs partial; defined forward test.
- Researched methods (tip effect, two-stage model, public ceiling ≈17.7%).
- Found/fixed starter bugs: date-string comparison dropped all 2026 games → stale Dec-2025 lineups; ESPN pre-game starter flag unreliable; "Day-to-Day" wrongly treated as out.
- Backfilled `lineups` table (1,399/1,406). Trained and shipped pre-game conditional-logit model (forward test top-1 15.2%, log-loss 2.268).

---

## 11. End-of-day update checklist

1. Add a dated entry to §10 (what changed, what you ran, results).
2. Move finished items in §7 to ✅ and note the date; add new items discovered.
3. Update §2 (data counts) and §3 (any new model numbers — always forward-test, never in-sample).
4. Update §6: delete assumptions you verified; add new gotchas.
5. Bump "Last updated" at the top. Run `pytest test_starters.py`.

---

## 7B. Model Improvement Roadmap (step-by-step)

**Goal:** Push pre-game top-1 from 15.2% → 17%+ (honest ceiling) by building the missing levers.

### Phase 1: Tip-Off Model (biggest single lever — 64% of first baskets follow tip)

| Step | Task | File(s) | Status |
|------|------|---------|--------|
| 1.1 | Add `tip_winner_player_id` to `games` table (already done via backfill) | DB schema | ✅ |
| 1.2 | Extract center matchup per game: identify the two jumpball participants from PBP | `tip_model.py` `tip_jumpball()` | ✅ |
| 1.3 | Compute historical tip win rate per center (career + season) with shrinkage | `tip_model.py` `compute_center_tip_rates()` | ✅ |
| 1.4 | Build P(team wins tip) = f(center_A_tip_pct, center_B_tip_pct, home_court) | `tip_model.py` `build_tip_model()` | ✅ |
| 1.5 | Tip rates from earlier games only (no saved file) | `tip_model.py` | ✅ 2026-11-17 — 63.6% point-in-time on 2026-27 |
| 1.6 | Integrate into pre-game features: `tip_win_prob` as 9th feature | `pregame_model.py`, `build_features.py`, `train_pregame.py`, `nba.py` | ✅ redone 2026-11-17 (real jumpers, home + away add to 1, works live) |
| 1.7 | Retrain pre-game model with tip feature; forward-test | `train_pregame.py` | ✅ 18.2% on 198 games, log-loss 2.2497 (was 16.7% / 2.2559) |

**Data source:** `tip_winner_player_id` and `tip_loser_player_id` now in `games` table for 1,455/1,456 games. PBP has jumpball participants for each game (1 game missing jumpball in PBP — BOS @ MEM 2025-11-12).

---

### Phase 2: First-Shot Share (from play-by-play)

| Step | Task | File(s) | Status |
|------|------|---------|--------|
| 2.1 | Parse PBP for each game: find team's first FGA in Q1, credit shooter | `build_features.py` / `first_shot.py` | 🔲 |
| 2.2 | Compute per-starter first-shot share (season + L10) with shrinkage | `first_shot.py` | 🔲 |
| 2.3 | Add `first_shot_share_shrunk` as feature to pre-game model | `pregame_model.py`, `train_pregame.py` | 🔲 |
| 2.4 | Retrain + forward-test | `train_pregame.py` | 🔲 |

**Data source:** PBP already fetched during `build_features.py` (early game features). Reuse or extend.

---

### Phase 3: Two-Stage Architecture (P(team scores first) × P(player | team))

| Step | Task | File(s) | Status |
|------|------|---------|--------|
| 3.1 | Train team-level model: P(team scores first) using tip_win_prob, home/away, team pace, opponent defense | `team_first_model.py` | 🔲 |
| 3.2 | Train player-level model: P(player | team scores first) using usage, first-shot-share, position, matchup | `player_given_team_model.py` | 🔲 |
| 3.3 | Combine: joint probability = P(team) × P(player | team) | `pregame_model.py` | 🔲 |
| 3.4 | Calibrate jointly (isotonic / Platt) | `train_pregame.py` | 🔲 |
| 3.5 | Forward-test vs current single-model baseline | `train_pregame.py` | 🔲 |

---

### Phase 4: Position/Role + Opponent Defense

| Step | Task | File(s) | Status |
|------|------|---------|--------|
| 4.1 | Store position per starter (from NBA.com daily feed — already in `lineups` table) | `starters.py` | 🔲 |
| 4.2 | Compute opponent defense vs position (e.g., opponent FG% allowed to PG/SG/SF/PF/C) | `build_features.py` | 🔲 |
| 4.3 | Add position-specific features + opponent defense vs position to model | `pregame_model.py` | 🔲 |
| 4.4 | Retrain + forward-test | `train_pregame.py` | 🔲 |

---

### Phase 5: Calibration & Validation

| Step | Task | File(s) | Status |
|------|------|---------|--------|
| 5.1 | Isotonic calibration on forward-test set (2026-27 games) | `train_pregame.py` | 🔲 |
| 5.2 | Weekly automated forward-test as season accumulates | `nba.py eval` / new script | 🔲 |
| 5.3 | Track accuracy by lineup status (CONFIRMED vs EXPECTED vs PROJECTED) | `nba.py` + new predictions_history table | 🔲 |

---

**Execution order:** Phase 1 → 2 → 3 → 4 → 5. Each phase produces a measurable forward-test delta. Stop if a phase doesn't move the needle.

