"""
NBA.com as a backup source for a finished game ESPN has no play-by-play for (ESPN answers "not found" for a few
2026 playoff games). Reads NBA.com's own play-by-play and box score and turns them into exactly what the rest of
the project expects from ESPN (nba.get_pbp actions, lineups rows), with ESPN player ids — matched by name from the
players already stored (starters.espn_ids_by_name) — so the game fits the training data like any other.

Checked against games ESPN does have: same first scorer, tip and first shots (tests/test_nba_com.py; 2026-11-19).
"""
import re
from typing import Optional

import starters as st

PBP = "https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_{gid}.json"
BOX = "https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{gid}.json"
NBA_TO_ESPN = {v: k for k, v in st.ESPN_TO_NBA.items()}


def espn_team(code: Optional[str]) -> str:
    return NBA_TO_ESPN.get(code or "", code or "")


def clock(iso: str) -> str:
    """'PT11M32.00S' -> '11:32' (ESPN's display clock)."""
    m = re.match(r"PT(\d+)M([\d.]+)S", iso or "")
    return f"{int(m.group(1))}:{int(float(m.group(2))):02d}" if m else ""


def _box(gid: str) -> Optional[dict]:
    try:
        return st._nba_json(BOX.format(gid=gid))["game"]
    except Exception:  # noqa: BLE001 — no such game / NBA.com refused
        return None


def playoff_game_id(season_year: int, day: str, home: str, away: str, rounds=(1, 2, 3, 4)) -> Optional[str]:
    """NBA.com's id for a play-in/playoff game (004YY00RSG: round R, series S, game G), found by trying the box
    scores until teams and date match. day: YYYYMMDD (US date); home/away: ESPN codes. One-off repairs only."""
    from datetime import datetime, timedelta, timezone
    want = {st.ESPN_TO_NBA.get(home, home), st.ESPN_TO_NBA.get(away, away)}
    for r in rounds:
        for s in range(8 if r == 1 else 4 if r == 2 else 2 if r == 3 else 1):
            gid1 = f"004{season_year % 100:02d}00{r}{s}1"
            b = _box(gid1)
            if not b or {b["homeTeam"]["teamTricode"], b["awayTeam"]["teamTricode"]} != want:
                continue
            for g in range(1, 8):
                b = _box(gid1[:-1] + str(g)) if g > 1 else b
                if not b:
                    break
                t = datetime.fromisoformat(b["gameTimeUTC"].replace("Z", "+00:00")) - timedelta(hours=4)
                if t.strftime("%Y%m%d") == day:
                    return gid1[:-1] + str(g)
    return None


def players(box: dict, id_map: dict) -> dict:
    """NBA personId -> {"id": ESPN id (or "nba<id>" if unknown), "name", "team" (ESPN code), "starter", "minutes"}."""
    out = {}
    for side in ("homeTeam", "awayTeam"):
        team = espn_team(box[side]["teamTricode"])
        for p in box[side].get("players", []):
            name = p.get("name") or f"{p.get('firstName', '')} {p.get('familyName', '')}".strip()
            mins = (p.get("statistics") or {}).get("minutes") or ""
            m = re.match(r"PT(\d+)M([\d.]+)S", mins)
            out[int(p["personId"])] = {"id": id_map.get(st.norm_name(name)) or f"nba{p['personId']}", "name": name,
                                       "team": team, "starter": str(p.get("starter")) == "1",
                                       "minutes": round(int(m.group(1)) + float(m.group(2)) / 60, 2) if m else 0.0}
    return out


def to_actions(espn_gid: str, pbp_actions: list, who: dict) -> list:
    """Pure (testable). NBA.com play-by-play -> the action dicts nba.get_pbp makes from ESPN."""
    out = []
    for a in pbp_actions:
        kind = a.get("actionType", "")
        pid = int(a.get("personId") or 0)
        if kind == "jumpball":
            won, lost = int(a.get("jumpBallWonPersonId") or 0), int(a.get("jumpBallLostPersonId") or 0)
            w, lo = who.get(won, {}), who.get(lost, {})
            out.append({"gameId": espn_gid, "personId": w.get("id", ""), "playerName": w.get("name", ""),
                        "teamTricode": w.get("team", ""), "actionType": "jumpball", "shotType": "Jumpball",
                        "period": int(a.get("period") or 0), "clock": clock(a.get("clock")), "scoringPlay": False,
                        "shootingPlay": False, "tip_loser_player_id": lo.get("id", ""),
                        "tip_loser_player_name": lo.get("name", "")})
            continue
        shot = kind in ("2pt", "3pt")
        ft = kind == "freethrow"
        p = who.get(pid, {})
        desc = " ".join(x for x in (str(a.get("descriptor") or "").title(), str(a.get("subType") or "")) if x)
        out.append({"gameId": espn_gid, "personId": p.get("id", ""), "playerName": p.get("name", ""),
                    "teamTricode": espn_team(a.get("teamTricode")) if a.get("teamTricode") else "",
                    "actionType": "free_throw" if ft else kind.replace(" ", "_"),
                    "shotType": ("Free Throw - " + str(a.get("subType") or "")) if ft else (desc or kind),
                    "period": int(a.get("period") or 0), "clock": clock(a.get("clock")),
                    "scoringPlay": (shot or ft) and a.get("shotResult") == "Made",
                    "shootingPlay": shot or ft})
    return out


def game(espn_gid: str, nba_gid: str, con) -> Optional[dict]:
    """{"actions": [...], "lineups": [rows for starters.save_lineups], "home": .., "away": .., "home_score", "away_score"}
    from NBA.com, or None."""
    box = _box(nba_gid)
    if not box:
        return None
    try:
        pbp = st._nba_json(PBP.format(gid=nba_gid))["game"]["actions"]
    except Exception:  # noqa: BLE001
        return None
    who = players(box, st.espn_ids_by_name(con))
    rows = [(str(espn_gid), p["team"], p["id"], p["name"], int(p["starter"]), p["minutes"])
            for p in who.values() if p["starter"] or p["minutes"] > 0]
    return {"actions": to_actions(str(espn_gid), pbp, who), "lineups": rows,
            "home": espn_team(box["homeTeam"]["teamTricode"]), "away": espn_team(box["awayTeam"]["teamTricode"]),
            "home_score": box["homeTeam"].get("score"), "away_score": box["awayTeam"].get("score"),
            "unmatched": sorted(p["name"] for p in who.values() if p["id"].startswith("nba") and
                                (p["starter"] or p["minutes"] > 0))}


def box_logs(box: dict, espn_gid: str, game_date: str, lineups: list) -> dict:
    """Pure. NBA.com box score -> {ESPN player id: player-log entry} (the format of player_game_logs.pkl), for players
    who played. ESPN ids come from the game's stored lineup rows [game_id, team, id, name, starter, minutes].
    Added 2026-11-19: the 4 NBA.com-only playoff games had starters but no box-score lines."""
    ids = {st.norm_name(r[3]): str(r[2]) for r in lineups}
    out = {}
    for side in ("homeTeam", "awayTeam"):
        for p in box[side].get("players", []):
            s = p.get("statistics") or {}
            name = p.get("name") or f"{p.get('firstName', '')} {p.get('familyName', '')}".strip()
            m = re.match(r"PT(\d+)M([\d.]+)S", s.get("minutes") or "")
            mins = round(int(m.group(1)) + float(m.group(2)) / 60, 1) if m else 0.0
            pid = ids.get(st.norm_name(name))
            if not pid or mins <= 0:
                continue
            out[pid] = {"game_date": game_date, "game_id": espn_gid, "minutes": mins,
                        "fgm": int(s.get("fieldGoalsMade") or 0), "fga": int(s.get("fieldGoalsAttempted") or 0),
                        "fg3m": int(s.get("threePointersMade") or 0), "fg3a": int(s.get("threePointersAttempted") or 0),
                        "ftm": int(s.get("freeThrowsMade") or 0), "fta": int(s.get("freeThrowsAttempted") or 0),
                        "pts": int(s.get("points") or 0), "plus_minus": float(s.get("plusMinusPoints") or 0),
                        "first_basket": 0}
    return out
