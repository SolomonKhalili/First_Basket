"""
How `nba.py predict` looks on screen (uses the `rich` library for colours, boxes and tables).
Only presentation lives here; nba.py works out the lineups and probabilities.
"""
from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console(highlight=False)

LINEUP_STYLE = {   # label -> (badge, colour, one-line meaning)
    "CONFIRMED": ("✅ CONFIRMED", "green", "official starters from NBA.com"),
    "EXPECTED": ("🟡 EXPECTED", "yellow", "NBA.com's expected starters — can still change"),
    "PROJECTED": ("🟠 PROJECTED", "dark_orange", "our guess — NBA.com hasn't posted this lineup yet"),
}
INJURY_STYLE = {"Out": "red", "Doubtful": "red", "Questionable": "dark_orange",
                "Day-To-Day": "yellow", "Day-to-Day": "yellow"}


def header(day_label: str, *lines: str):
    title = Text("🏀  FIRST BASKET PICKS", style="bold")
    body = Text.assemble(title, "\n", (day_label, "bold cyan"))
    for line in lines:
        if line:
            body.append("\n" + line, style="dim")
    console.print()
    console.print(Panel(body, box=box.HEAVY, width=88, padding=(0, 2)))


def schedule(games: list[dict]):
    """One line per game: time, matchup, state."""
    console.print(Text("Today's games", style="bold"))
    t = Table(box=None, show_header=False, padding=(0, 2, 0, 2))
    for g in games:
        state = {1: ("upcoming", "cyan"), 2: ("LIVE — already started", "red"),
                 3: ("final", "dim")}.get(g["status"], ("?", "dim"))
        t.add_row(Text(g.get("start", ""), style="dim"), f"{g['away_team']} @ {g['home_team']}",
                  Text(state[0], style=state[1]))
    console.print(t)


def _bar(p: float, top: float, width: int = 12) -> Text:
    n = max(1, round(width * p / top)) if top > 0 else 0
    return Text("█" * n + "░" * (width - n), style="green" if p == top else "grey50")


def _starter_line(players: list[dict], injuries: dict) -> Text:
    line = Text()
    for i, p in enumerate(players):
        if i:
            line.append(" · ", style="dim")
        status = injuries.get(p["name"])
        line.append(p["name"], style=INJURY_STYLE.get(status, ""))
        if status:
            line.append(f" ({status})", style=INJURY_STYLE.get(status, "yellow"))
        if p.get("replaces"):
            line.append(f" ↳ in for {p['replaces'].title()}", style="dim")
    return line


def game_card(c: dict):
    """c: matchup, start, label, updated, picks [(name, team, prob, fair)], tip {team: prob},
    jumpers {team: name}, home/away starters, injuries {name: status}, notes [str]."""
    badge, colour, meaning = LINEUP_STYLE[c["label"]]

    picks = Table(box=box.SIMPLE_HEAD, pad_edge=False, expand=True, show_edge=False)
    picks.add_column("#", justify="right", style="dim", width=2)
    picks.add_column("Player")
    picks.add_column("Team", style="dim")
    picks.add_column("Chance", justify="right")
    picks.add_column("", no_wrap=True)
    picks.add_column("Fair odds", justify="right")
    fanduel = c.get("fanduel")                     # {name: American odds}; None = odds not fetched
    if fanduel is not None:
        picks.add_column("FanDuel", justify="right")
    top = c["picks"][0][2] if c["picks"] else 0
    for i, (name, team, p, fair) in enumerate(c["picks"], 1):
        bold = "bold" if i == 1 else ""
        cells = [str(i), Text(name, style=bold), team, Text(f"{p:.1%}", style=bold),
                 _bar(p, top), Text(f"{fair:+d}", style=bold)]
        if fanduel is not None:
            cells.append(_fanduel_cell(fanduel.get(name), fair))
        picks.add_row(*cells)

    # Headline: the two most likely first scorers and the predicted tip winner (by player)
    headline = Table.grid(padding=(0, 1))
    headline.add_column(no_wrap=True)
    headline.add_column()
    top2 = Text()
    for i, (name, team, p, _) in enumerate(c["picks"][:2], 1):
        top2.append(("     " if i > 1 else "") + f"{i}. {name} ({team}) {p:.1%}", style="bold" if i == 1 else "")
    headline.add_row(Text("🎯 Top 2", style="bold"), top2)

    (t1, p1), (t2, _) = sorted(c["tip"].items(), key=lambda kv: -kv[1])
    j = c.get("jumpers") or {}
    tip = Text()
    tip.append(f"{j[t1]} ({t1}) to win the tip" if j.get(t1) else f"{t1} to win the tip", style="bold")
    tip.append(f" — {p1:.0%}")
    if not j.get(t1):
        tip.append("  (jumper not known yet)", style="dim")
    tip.append(f"  vs {j[t2]} ({t2})" if j.get(t2) else f"  vs {t2} jumper not known yet", style="dim")
    headline.add_row(Text("🏀 Tip-off", style="bold"), tip)
    if c.get("first_team"):
        (f1, q1), (f2, q2) = sorted(c["first_team"].items(), key=lambda kv: -kv[1])
        first = Text()
        first.append(f"{f1} {q1:.0%}", style="bold")
        first.append(f"  vs {f2} {q2:.0%}", style="dim")
        headline.add_row(Text("📈 Scores first", style="bold"), first)

    starters = Table.grid(padding=(0, 1))
    starters.add_column(style="bold", width=5)
    starters.add_column()
    starters.add_row(c["home"], _starter_line(c["home_starters"], c["injuries"]))
    starters.add_row(c["away"], _starter_line(c["away_starters"], c["injuries"]))
    body = [headline, Text(""), picks]
    if c.get("fanduel_note"):
        body.append(Text(c["fanduel_note"], style="dim"))
    body += [Text(""), Text("Starters", style="dim"), starters]
    if c.get("notes"):
        body += [Text("")] + [Text(f"• {n}", style="dim") for n in c["notes"]]

    title = Text.assemble((c["matchup"], "bold"), ("   " + c.get("start", ""), "dim"))
    subtitle = Text.assemble((badge, colour), (f"  {meaning}", "dim"))
    if c.get("updated"):
        subtitle = Text.assemble(("🔄 UPDATED  ", "bold magenta"), subtitle)
    console.print(Panel(Group(*body), title=title, title_align="left", subtitle=subtitle,
                        subtitle_align="left", border_style=colour, padding=(0, 1), width=88))


def _payout(american: int) -> float:
    """Profit per $1 staked at these American odds."""
    return american / 100 if american > 0 else 100 / -american


def _fanduel_cell(price, fair: int) -> Text:
    """FanDuel's price; green when it pays more than the fair odds (the model sees value), dim otherwise."""
    if price is None:
        return Text("–", style="dim")
    better = _payout(price) > _payout(fair)
    return Text(f"{price:+d}", style="bold green" if better else "dim")


def _rate(s: dict) -> str:
    return f"{s['hits']}/{s['games']} = {s['hits'] / s['games']:.1%}" if s["games"] else "—"


def track_record(s: dict, data_summary: str = "", recent: int = 15):
    """`nba.py eval`: real results of logged picks (picks.summary)."""
    header("TRACK RECORD", data_summary)
    if not s["final"]["games"]:
        console.print(Text("No finished games with a logged pick yet. Picks are logged every time you run "
                           "`python3 nba.py predict`; results appear here the day after the games.", style="dim"))
        return

    t = Table(box=box.SIMPLE_HEAD, show_edge=False, width=88)
    t.add_column("Picks")
    t.add_column("Games", justify="right")
    t.add_column("Top pick scored first", justify="right")
    t.add_column("Scorer in our top 3", justify="right")
    f = s["final"]
    t.add_row(Text("Final pick per game", style="bold"), str(f["games"]), Text(_rate(f), style="bold"),
              f"{f['top3']}/{f['games']} = {f['top3'] / f['games']:.1%}")
    for label in ("CONFIRMED", "EXPECTED", "PROJECTED"):
        if label in s["by_type"]:
            b = s["by_type"][label]
            badge, colour, _ = LINEUP_STYLE[label]
            t.add_row(Text(f"  {badge}", style=colour), str(b["games"]), _rate(b),
                      f"{b['top3']}/{b['games']} = {b['top3'] / b['games']:.1%}")
    console.print(t)

    if len(s["by_model"]) > 1:
        m = Table(box=box.SIMPLE_HEAD, show_edge=False, width=88, title="By model version", title_justify="left")
        m.add_column("Model (trained through – fingerprint)")
        m.add_column("Games", justify="right")
        m.add_column("Top pick scored first", justify="right")
        for k, b in sorted(s["by_model"].items()):
            m.add_row(k, str(b["games"]), _rate(b))
        console.print(m)

    g = Table(box=box.SIMPLE_HEAD, show_edge=False, width=88,
              title=f"Latest {min(recent, len(s['games']))} games", title_justify="left")
    for col, kw in (("", {"width": 2}), ("Date", {"style": "dim"}), ("Game", {}), ("Our pick", {}),
                    ("Chance", {"justify": "right"}), ("Actual first scorer", {}), ("Lineup", {"style": "dim"})):
        g.add_column(col, **kw)
    for r in s["games"][:recent]:
        d = r["game_date"] or ""
        g.add_row("✅" if r["hit"] else "❌", f"{d[4:6]}/{d[6:8]}", r["matchup"] or "", r["pick"],
                  f"{r['prob']:.1%}", r["actual"] or "", r["lineup_type"].title())
    console.print(g)
    console.print(Text("A random pick is right 10% of the time. Judge the model over many games — "
                       "even a good model is wrong on most nights.", style="dim"))


def report(body: str, data_summary: str = ""):
    """`nba.py report`: one box per game; injury warnings highlighted."""
    lines = body.strip("\n").split("\n")
    import re
    from datetime import datetime
    m = re.search(r"\d{8}", lines[0]) if lines else None
    day = datetime.strptime(m.group(), "%Y%m%d").strftime("%A, %B %-d, %Y") if m else ""
    header(f"DAILY REPORT  {day}".strip(), data_summary)
    blocks, current = [], []
    for line in lines[2:]:
        if line and not line.startswith(" ") and current:      # a new game starts at column 0
            blocks.append(current)
            current = []
        if line.strip():
            current.append(line)
    if current:
        blocks.append(current)
    for block in blocks:
        if not block[0].startswith(" ") and "@" in block[0]:
            body_text = Text()
            for i, l in enumerate(block[1:]):
                style = "yellow" if "⚠️" in l else ("bold" if l.strip().endswith("]") or l.strip().endswith(":") else "")
                body_text.append(("\n" if i else "") + l, style=style)
            console.print(Panel(body_text, title=Text(block[0].strip(), style="bold"), title_align="left",
                                border_style="cyan", padding=(0, 1), width=88))
        else:
            console.print(Text("\n".join(block), style="dim"))


def skipped(matchup: str, reason: str):
    console.print(Text.assemble(("  ⏳ ", ""), (matchup, "bold"), (f"  {reason}", "dim")))


def notes(lines: list[str]):
    if lines:
        console.print(Panel("\n".join(lines), title="Warnings", title_align="left",
                            border_style="yellow", width=88))


def legend(any_projected: bool):
    t = Table.grid(padding=(0, 2))
    t.add_column(style="bold", no_wrap=True)
    t.add_column()
    t.add_row("Chance", "The model's chance this player scores the game's first basket (random pick = 10%).")
    t.add_row("Fair odds", "The break-even price. A bet only makes sense if the sportsbook pays MORE "
                           "(e.g. +700 when fair is +500).")
    t.add_row("FanDuel", "FanDuel's current First Basket price. Green = it pays more than the fair odds "
                         "(the model thinks the bet has value); grey = it pays less. Skip it with --no-odds.")
    t.add_row("Top 2", "The two players most likely to score the game's first basket.")
    t.add_row("Scores first", "Each team's chance to score the game's first points (from the tip-off and home court).")
    t.add_row("Tip-off", "Who we expect to win the opening jump ball (the player who usually jumps for each "
                         "team, from their past tips). The team that wins it scores first about 66% of the time.")
    t.add_row("Lineups", "✅ official   🟡 expected, can change   🟠 our guess")
    if any_projected:
        tip = Text("NBA.com usually posts lineups in the early afternoon (ET) and confirms them about "
                   "30 min before tip-off. Re-run later, or use ", style="dark_orange")
        tip.append("python3 nba.py predict --watch", style="bold dark_orange")
        tip.append(" to update automatically.", style="dark_orange")
        t.add_row("", tip)
    console.print(Panel(t, title="How to read this", title_align="left", box=box.ROUNDED,
                        border_style="dim", width=88))
