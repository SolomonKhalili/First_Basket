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
    top = c["picks"][0][2] if c["picks"] else 0
    for i, (name, team, p, fair) in enumerate(c["picks"], 1):
        bold = "bold" if i == 1 else ""
        picks.add_row(str(i), Text(name, style=bold), team, Text(f"{p:.1%}", style=bold),
                      _bar(p, top), Text(f"{fair:+d}", style=bold))

    (t1, p1), (t2, p2) = sorted(c["tip"].items(), key=lambda kv: -kv[1])
    tip = Text("Tip-off: ", style="dim")
    tip.append(f"{t1} {p1:.0%}", style="bold")
    tip.append(f" vs {t2} {p2:.0%}")
    j = c.get("jumpers") or {}
    if j.get(t1) or j.get(t2):
        tip.append(f"   jumpers: {j.get(t1) or 'unknown'} vs {j.get(t2) or 'unknown'}", style="dim")

    starters = Table.grid(padding=(0, 1))
    starters.add_column(style="bold", width=5)
    starters.add_column()
    starters.add_row(c["home"], _starter_line(c["home_starters"], c["injuries"]))
    starters.add_row(c["away"], _starter_line(c["away_starters"], c["injuries"]))
    body = [picks, Text(""), tip, Text(""), Text("Starters", style="dim"), starters]
    if c.get("notes"):
        body += [Text("")] + [Text(f"• {n}", style="dim") for n in c["notes"]]

    title = Text.assemble((c["matchup"], "bold"), ("   " + c.get("start", ""), "dim"))
    subtitle = Text.assemble((badge, colour), (f"  {meaning}", "dim"))
    if c.get("updated"):
        subtitle = Text.assemble(("🔄 UPDATED  ", "bold magenta"), subtitle)
    console.print(Panel(Group(*body), title=title, title_align="left", subtitle=subtitle,
                        subtitle_align="left", border_style=colour, padding=(0, 1), width=88))


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
    t.add_row("Tip-off", "Each team's chance to win the opening jump ball. The winner scores first "
                         "about 64% of the time.")
    t.add_row("Lineups", "✅ official   🟡 expected, can change   🟠 our guess")
    if any_projected:
        tip = Text("NBA.com usually posts lineups in the early afternoon (ET) and confirms them about "
                   "30 min before tip-off. Re-run later, or use ", style="dark_orange")
        tip.append("python3 nba.py predict --watch", style="bold dark_orange")
        tip.append(" to update automatically.", style="dark_orange")
        t.add_row("", tip)
    console.print(Panel(t, title="How to read this", title_align="left", box=box.ROUNDED,
                        border_style="dim", width=88))
