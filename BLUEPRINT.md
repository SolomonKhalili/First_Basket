# NBA First Basket Scorer Bot — Project Blueprint

> **Last updated:** 2026-11-09 · **Owner:** Solomon · **Repo folder:** `~/Desktop/NBA`
> This file is the single source of truth for project state. Update it at the end of every work session (see the checklist at the bottom).

---

## 0. AI CONTEXT — read this first (paste/upload this file with the project)

**Goal.** Each game day, *before tip-off*, predict which **starter** will score the first **made field goal** (free throws excluded; regulation only) in every NBA game, output a probability per player, and compare to sportsbook prices to look for value.

**Where it stands (one paragraph).** Data ingestion, a lineup/injury pipeline, and a calibrated *pre-game* model are working. The pre-game model is **weak by design** (top-1 ≈ 15% vs 10% random; honest ceiling in public sources ≈ 17–18%), so it is a **shortlist generator, not a proven betting edge**. The original model (`model.txt`, "62.8% accuracy") is **invalid for pre-game use** — it learned from first-10-plays features that already contain the first basket (label leakage). Do not trust or cite its numbers. Odds/EV is **not built**: the FanDuel scraper is blocked by PerimeterX captcha (we do not bypass it) and needs a licensed odds API instead.

**Hard rules / decisions already made**
- Only the **two most recent seasons** (2025-26 + current 2026-27) are used; rosters/roles change too fast for older data.
- Train on **regular season only**; evaluate with a **forward test** (train 2025-26 → test 2026-27 so far). Never evaluate on random splits.
- Pre-game features must be knowable **before tip**. Never use `early_*` (first-10-plays) or `tip_winner` for pre-game prediction.
- Every lineup is labeled **CONFIRMED / EXPECTED / PROJECTED**; never present a projected lineup as official.
- Don't bypass bot protection (PerimeterX) to scrape sportsbooks.
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
NBA.com live box score ─────┼─► starters.py ─► resolve starting five (CONFIRMED > EXPECTED > PROJECTED)
ESPN starter flags ─────────┘                          ▲
ESPN injuries feed ───────────────────────────────────┘  (removes Out/Doubtful, flags Day-to-Day)
                                   │
player_game_logs.pkl + team stats ─► pregame_model.py ─► P(first basket) per starter ─► fair odds
```

**Pre-game model** (`pregame_model.json`, trained by `train_pregame.py`): conditional logit (softmax over the 10 starters), 8 features: `is_home, fb_rate_shrunk, L10_fb_shrunk, usage_rate, true_shooting, minutes_per_game, points_per_game, team_tipoff_win_pct`. Rates are shrunk toward 10% (prior) so small samples count less. Strongest weights: `usage_rate`, `fb_rate_shrunk`.

**Lineup source order** (in `predict`): NBA.com daily lineups feed with status `Confirmed` → NBA.com live box score → ESPN starter flags → NBA.com `Expected` → usual starters (last 5 games) minus injured. Starters are the feed's players that carry a `position` tag.

---

## 2. Current data state (as of 2026-11-09)

| Item | State |
|------|-------|
| Games in DB | 1,451 (1,442 with a labeled first basket; 9 have no first-basket record — ingest misses) |
| 2025-26 regular season | Complete: 1,230 labeled games (Oct 21 – Apr 12); every team 82–83 games |
| 2025-26 post-season | Partial (~87 labeled games after Apr 12; playoffs incomplete). **Excluded from training.** |
| 2026-27 season | In progress: 125 labeled games (Oct 20 – Nov 6). Used as the **forward test set** |
| `lineups` table | ~1,440 games backfilled (7 late-season/playoff games failed with HTTP 403 — harmless) |
| Training matrices | `X_train.npy` (14,420×17), `y_train.npy`, `groups.npy` (1,442 games, 10 starters each) |

**Key measured facts (from our own data)**
- The team that wins the tip scores the first basket **64.4%** of the time (792 / 1,230). Home wins the tip 52.8%.
- Random pick among 10 starters = 10%.

---

## 3. Model results

| Model | Scenario | Top-1 | Log-loss (uniform = 2.3026) |
|-------|----------|-------|------------------------------|
| **Pre-game conditional logit (ACTIVE)** | Forward test, 125 games of 2026-27 | **15.2%** | **2.268** |
| Season first-basket-rate alone | same 125 games | 16.0% | — |
| LightGBM ranker, pre-game features only | same 125 games (5 seeds) | 8.8–16.0% (mean 13.8%) | not calibrated |
| Legacy `model.txt` (leaky) | with in-game features | ~60% | — *(invalid pre-game)* |
| Legacy `model.txt` | in-game features zeroed (true pre-game) | **10.7%** | — |

Read this honestly: with 125 test games the 95% interval is ≈ ±6 points, so 15% is **not statistically distinguishable** from 10–20%. Predicted probabilities were roughly calibrated (top-pick mean 14.9% vs 15.2% hit rate). Public sources report a ≈17.7% pre-game hit rate (StatsBench, 724 games, 2024-25) and call it the ceiling. Sportsbook favorites are ~+400 to +800 (14–17% implied) with ~20% board margin, so **no demonstrated edge**.

---

## 4. Components — status

| Component | File | Status |
|-----------|------|--------|
| DB schema (`games, first_baskets, predictions, lineups`) | `nba.py init` | ✅ (init fixed to match real schema) |
| ESPN ingestion, backfill, auto-catchup | `nba.py` | ✅ (catchup also stores lineups) |
| Lineup backfill | `nba.py lineups` | ✅ done once (re-run only if table is wiped) |
| Lineup resolution + injury logic | `starters.py` | ✅ built, 24 unit tests passing; live behavior partly **unverified** (see §6) |
| Injury report (ESPN feed) | `starters.py` | ✅ parsed from a real capture (team, status, reason, est. return) |
| Daily report (matchups + lineups + injuries) | `nba.py report` | ✅ works; falls back to projections when NBA.com file isn't published |
| Pre-game model + training | `pregame_model.py`, `train_pregame.py`, `pregame_model.json` | ✅ |
| Watch mode (recheck, print on change/confirm) | `nba.py predict --watch` | ✅ built, tested with simulated feeds |
| Feature engineering (legacy) | `build_features.py` | ⚠️ still builds leaky `early_*` features; only used to produce `X_train.npy` |
| Legacy model + training | `train_model.py`, `model.txt` | ⚠️ retired for pre-game; still loaded by `nba.py` (only affects already-started games) |
| FanDuel odds scraper | `fanduel_scraper.py`, `get_fanduel_first_basket_odds` | ❌ blocked by PerimeterX captcha; also mis-assigns odds by sort order. **Do not use.** |
| Odds / EV / bankroll / bet logging | — | 🔲 not started |
| Tip-off win model | — | 🔲 not started (biggest expected lever) |
| First-shot-share feature (from play-by-play) | — | 🔲 not started |

---

## 5. Daily workflow

```bash
cd ~/Desktop/NBA
python3 nba.py report                 # matchups, who's injured, who's starting (or projected)
python3 nba.py predict --watch        # leave open: projections → EXPECTED → CONFIRMED, prints picks as lineups change
python3 nba.py predict                # one-shot version
python3 nba.py predict --confirmed-only   # only games with an official CONFIRMED lineup
python3 nba.py eval                   # night: accuracy on finished games
pytest test_starters.py               # sanity check after changing code (expect 24 passed)
```

Output per game: top 5 starters, probability, **fair American odds**. Compare to a sportsbook price: it is only +EV if the book's price is *longer* than the fair odds (e.g., model 13.6% = fair ≈ +635 → +800 is value, +450 is not). Run ~30 min before tip; NBA.com posts official lineups then.

---

## 6. Known issues / unverified assumptions

- ❓ **Confirmed status string.** NBA.com daily feed showed `lineupStatus: "Expected"`. We *assume* the confirmed value is `"Confirmed"` (case-insensitive). **Verify the first time a game is confirmed**; if the string differs, change `parse_daily_game` in `starters.py`.
- ❓ **When the NBA.com daily file appears.** `…/00_daily_lineups_YYYYMMDD.json` returned 200 on Sat 11-07 (entries stamped 17:49) but 404 at 1:48 AM on 11-09. Record the time it first appears.
- ❓ **NBA.com box score 403** before lineups are submitted is treated as "not posted yet" (assumption).
- ⚠️ **`player_game_logs.pkl` is not auto-refreshed.** Player rates/usage come from this cache (built once by `build_features.py`). It gets stale every day. *Top fix priority* (see §7).
- ⚠️ **Out-but-returning-today.** ESPN sometimes lists players `Out` with est. return = today (e.g., Doncic 11-09). They are removed in projections but flagged "may actually play".
- ⚠️ **ESPN game dates are UTC**, so late games can carry tomorrow's date; the feed lookup tries today's ET date first.
- ⚠️ **ID matching.** NBA.com ↔ ESPN player IDs are matched by normalized name via the `lineups` table. Unmatched players (new/rookies) are flagged and scored with default stats.
- ⚠️ `predictions` uses `INSERT OR REPLACE` per game, so re-running overwrites earlier picks. `model_version` encodes the lineup source (`…_nba_confirmed`, `…_nba_expected_unconfirmed`, `…_projected_usual_starters`), but history of earlier runs is lost.
- ⚠️ In-game branch of `predict` (games already started) still uses the leaky legacy model; those picks are not meaningful pre-tip.
- ℹ️ 9 games have no first-basket record; 7 old games lack lineups (HTTP 403). Immaterial.

---

## 7. To-do (prioritized)

**P0 — correctness / trust**
- [ ] Verify live: NBA.com daily file publish time; what the CONFIRMED status string is; that `report`/`predict --watch` match what NBA.com's lineups page shows.
- [ ] Auto-refresh `player_game_logs.pkl` (incrementally from stored box scores) so features aren't stale.
- [ ] Remove/relabel the leaky legacy model in `nba.py` (drop `load_model()` and the in-game branch or clearly mark it).
- [ ] Log every pick (with lineup status + timestamp) to a table *without overwriting*, so we can measure accuracy by CONFIRMED vs EXPECTED vs PROJECTED.
- [x] ~~Fix `README.md`~~ — rewritten 2026-11-09 (accurate methodology, honest results, new commands). `requirements.txt` now includes `scipy`.

**P1 — model quality (the only realistic path to an edge)**
- [ ] **Tip-off model:** P(team wins tip) from the two centers' tip history (64% of first baskets follow the tip).
- [ ] **First-shot share:** each starter's share of their team's first shot attempt, from play-by-play.
- [ ] Rebound branch (who gets the board if the first shot misses); starter position/role; opponent defense vs position.
- [ ] Combine: P(team scores first) × P(player | team scores first); calibrate; re-run the forward test.
- [ ] Weekly forward test as 2026-27 games accumulate (±6 points now; shrinks with sample).

**P2 — odds layer (only after the model shows signal)**
- [ ] Choose a **licensed/official odds API** (FanDuel scraping is blocked; do not bypass). Match players by name, not by sort order.
- [ ] Vig removal, EV, closing-line value, fractional Kelly (0.5–1% unit), bet log with ROI/CLV/hit rate.

**P3 — nice to have**
- [ ] Official NBA injury report (PDF) as a second source; ESPN's feed can lag.
- [ ] Optuna / SHAP / isotonic calibration once there's more data.

---

## 8. File map

```
NBA/
├── nba.py                  # CLI: init, backfill, lineups, report, predict [--watch|--confirmed-only|--projected|--odds], eval
├── starters.py             # lineups (NBA.com/ESPN), injuries, projection, daily report  [24 tests]
├── test_starters.py        # pytest suite for starters.py
├── pregame_model.py        # feature builder + probabilities + fair odds   (ACTIVE model code)
├── pregame_model.json      # trained pre-game weights                       (ACTIVE model)
├── train_pregame.py        # retrains pregame_model.json (needs scipy)
├── build_features.py       # legacy feature builder (leaky early_* features)
├── train_model.py          # legacy LightGBM trainer (retired)
├── model.txt / model_meta.json   # legacy LightGBM model + metrics (retired, leaky)
├── fanduel_scraper.py      # blocked by captcha; unused
├── view_data.py            # DB viewer + CSV export
├── nba_first_basket.db     # SQLite: games, first_baskets, predictions, lineups (+ unused player_season_stats)
├── player_game_logs.pkl    # per-player game logs cache (⚠️ not auto-refreshed)
├── X_train.npy, y_train.npy, groups.npy, feature_names.json, meta.json   # training matrices
├── first_basket_data.csv   # export
├── requirements.txt (now includes scipy), README.md (rewritten 2026-11-09), BLUEPRINT.md (this file)
```

---

## 9. Research notes (what's solid vs not)

- **Solid / consistent with our data:** tip winner scores first ≈ 64% (Dickinson, 27k tip-offs; our 64.4%); two-stage structure P(team scores first) × P(player | team) is the standard approach.
- **Not validated:** HeatCheck/PropsEdge/SportsCommand-style "10-factor weights" and "% stability" claims — commercial sites, no backtests or sample sizes. Recent-form windows (L5/L10) carry little signal in our data.
- **Ceiling:** StatsBench reports 17.7% pre-game hit rate over 724 games (2024-25), no ROI/CLV. Breakeven at +600 is 14.3%; board margin ≈ 20%; losing streaks are long even with a real edge (10 straight losses at +700 ≈ 26%).

---

## 10. Session log (newest first — add an entry every session)

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
- FanDuel scraper confirmed blocked by captcha; decided not to bypass.

---

## 11. End-of-day update checklist

1. Add a dated entry to §10 (what changed, what you ran, results).
2. Move finished items in §7 to ✅ and note the date; add new items discovered.
3. Update §2 (data counts) and §3 (any new model numbers — always forward-test, never in-sample).
4. Update §6: delete assumptions you verified; add new gotchas.
5. Bump "Last updated" at the top. Run `pytest test_starters.py`.
