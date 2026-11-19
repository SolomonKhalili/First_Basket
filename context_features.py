"""
Two game-context inputs for step 2 of the model (adopted 2026-11-18 after the walk-forward tests in
experiments/combined_test.py: log-loss better in both periods — 2026-27 2.2392 -> 2.2365, Jan–Apr 2026
2.2763 -> 2.2727; top pick 19.2% -> 21.7% and 15.1% -> 14.9%):

  opp_pos_share   tonight's OPPONENT: the share of the first baskets it gave up (before tonight) that went to
                  players of this starter's position group (G / F / C), shrunk toward the league share with
                  K = 10 imaginary games. Unknown position -> 0.4 (as tested).
  vs_opp_rate     this starter's first-basket rate AGAINST tonight's opponent, this season only, shrunk toward
                  his own overall rate with M = 5 imaginary meetings (his overall rate itself shrunk toward 10%
                  with 30 imaginary starts).

Point-in-time: only games BEFORE the date asked about count, so training and game day see the same thing.
Built once from the database (Context), then cheap to query for every starter.
"""
import sqlite3
from bisect import bisect_left
from collections import defaultdict

import starters as st

K_POS, UNKNOWN_POS = 10, 0.4
M_VS, M_ALL, PRIOR = 5, 30, 0.1


def season_of(d: str) -> str:
    """'20261118' -> '2026-27' (seasons start in the autumn)."""
    y, m = int(d[:4]), int(d[4:6])
    start = y if m >= 8 else y - 1
    return f"{start}-{str(start + 1)[2:]}"


class Context:
    def __init__(self, con: sqlite3.Connection):
        info = {g: (d, h, a) for g, d, h, a in con.execute("SELECT game_id, game_date, home_team, away_team FROM games")}
        scorer = {g: (str(p), t) for g, p, t in con.execute(
            "SELECT game_id, scorer_player_id, scorer_team FROM first_baskets")}
        names = {str(p): n for p, n in con.execute("SELECT player_id, player_name FROM lineups")}
        self.slot = {}                                   # (date, team, norm name) -> G/F/C from NBA.com's lineup
        st.ensure_official_tables(con)
        for d, t, n, pos in con.execute("SELECT game_date, team, player_name, position FROM official_lineups"):
            g = st.position_group(pos)
            if g:
                self.slot[(d, t, st.norm_name(n))] = g
        # conceded first baskets by group: per team and league-wide, in date order
        events = []
        for gid, (pid, team) in scorer.items():
            if gid not in info:
                continue
            d, h, a = info[gid]
            grp = self.slot.get((d, team, st.norm_name(names.get(pid, ""))))
            if grp:
                events.append((d, a if team == h else h, grp))
        events.sort()
        self.team_dates, self.team_counts = defaultdict(list), defaultdict(list)   # running counts per team
        self.league_dates, self.league_counts = [], []
        run_t, run_l = defaultdict(lambda: {"G": 0, "F": 0, "C": 0}), {"G": 0, "F": 0, "C": 0}
        for d, opp, grp in events:
            run_t[opp][grp] += 1
            run_l[grp] += 1
            self.team_dates[opp].append(d)
            self.team_counts[opp].append(dict(run_t[opp]))
            self.league_dates.append(d)
            self.league_counts.append(dict(run_l))
        # every start: player -> [(date, opponent, scored first?)], in date order
        self.starts = defaultdict(list)
        for gid, team, pid in con.execute("SELECT game_id, team, player_id FROM lineups WHERE is_starter = 1"):
            if gid in info and gid in scorer:
                d, h, a = info[gid]
                self.starts[str(pid)].append((d, a if team == h else h, scorer[gid][0] == str(pid)))
        for v in self.starts.values():
            v.sort()

    @staticmethod
    def _before(dates, counts, d):
        i = bisect_left(dates, d)                       # events strictly before d
        return counts[i - 1] if i else {"G": 0, "F": 0, "C": 0}

    def opp_pos_share(self, opp: str, group, before: str) -> float:
        if group not in ("G", "F", "C"):
            return UNKNOWN_POS
        c = self._before(self.team_dates.get(opp, []), self.team_counts.get(opp, []), before)
        lg = self._before(self.league_dates, self.league_counts, before)
        tot_l = sum(lg.values())
        prior = lg[group] / tot_l if tot_l > 1 else 0.33
        return (c[group] + K_POS * prior) / (sum(c.values()) + K_POS)

    def vs_opp_rate(self, player_id, opp: str, before: str) -> float:
        prior = [x for x in self.starts.get(str(player_id), []) if x[0] < before]
        r_all = (sum(x[2] for x in prior) + M_ALL * PRIOR) / (len(prior) + M_ALL)
        now = season_of(before)
        vs = [x for x in prior if x[1] == opp and season_of(x[0]) == now]
        return (sum(x[2] for x in vs) + M_VS * r_all) / (len(vs) + M_VS)

    def group_that_night(self, d: str, team: str, name: str):
        """Training: his position group in NBA.com's lineup that night (None if NBA.com had no lineup)."""
        return self.slot.get((d, team, st.norm_name(name)))
