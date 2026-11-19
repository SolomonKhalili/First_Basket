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
    season = c.get("season")                       # {name: (first baskets, games played)} this season
    if season is not None:
        picks.add_column("Season", justify="right", no_wrap=True)
    picks.add_column("Fair", justify="right")
    fanduel = c.get("fanduel")                     # {name: American odds}; None = odds not fetched
    evs = c.get("ev") or {}                        # {name: EV per $1} at the better book's price
    dk = c.get("draftkings") or {}                 # {name: American odds} at DraftKings
    best_book = c.get("best_book") or {}           # {name: "FanDuel" | "DraftKings"}
    if fanduel is not None:
        picks.add_column("FD" if dk else "FanDuel", justify="right", no_wrap=True)   # short labels when both fit
        if dk:
            picks.add_column("DK", justify="right", no_wrap=True)
        picks.add_column("EV/$1", justify="right")
    top = c["picks"][0][2] if c["picks"] else 0
    for i, (name, team, p, fair) in enumerate(c["picks"], 1):
        bold = "bold" if i == 1 else ""
        cells = [str(i), Text(name, style=bold), team, Text(f"{p:.1%}", style=bold), _bar(p, top, width=6)]
        if season is not None:
            cells.append(season_cell(season.get(name)))
        cells.append(Text(f"{fair:+d}", style=bold))
        if fanduel is not None:
            fd_p, dk_p = fanduel.get(name), dk.get(name)
            both = fd_p is not None and dk_p is not None and fd_p != dk_p     # ★ only when the books differ
            cells.append(_fanduel_cell(fd_p, fair, star=both and best_book.get(name) == "FanDuel", faded=both and best_book.get(name) != "FanDuel"))
            if dk:
                cells.append(_fanduel_cell(dk_p, fair, star=both and best_book.get(name) == "DraftKings", faded=both and best_book.get(name) != "DraftKings"))
            cells.append(ev_cell(evs.get(name)))
        picks.add_row(*cells)

    # Headline: the two most likely first scorers and the predicted tip winner (by player)
    headline = Table.grid(padding=(0, 1))
    headline.add_column(no_wrap=True)
    headline.add_column()
    top2 = Text()
    for i, (name, team, p, _) in enumerate(c["picks"][:2], 1):
        top2.append(("     " if i > 1 else "") + f"{i}. {name} ({team}) {p:.1%}", style="bold" if i == 1 else "")
    headline.add_row(Text("🎯 Top 2", style="bold"), top2)

    headline.add_row(Text("🏀 Tip-off", style="bold"), tip_line(c))
    if c.get("first_team"):
        ft = c["first_team"]                       # away first, like the tip-off line; favourite in bold
        fav = c["away"] if ft[c["away"]] > ft[c["home"]] else c["home"]
        first = Text()
        for i, team in enumerate((c["away"], c["home"])):
            if i:
                first.append("  vs  ", style="dim")
            first.append(f"{team} {ft[team]:.0%}", style="bold" if team == fav else "dim")
        headline.add_row(Text("📈 Scores first", style="bold"), first)

    starters = Table.grid(padding=(0, 1))
    starters.add_column(style="bold", width=5)
    starters.add_column()
    starters.add_row(c["home"], _starter_line(c["home_starters"], c["injuries"]))
    starters.add_row(c["away"], _starter_line(c["away_starters"], c["injuries"]))
    body = [headline, Text(""), picks]
    if c.get("fanduel_note"):
        body.append(Text(c["fanduel_note"], style="dim"))
    if fanduel:
        body.append(best_value_line(c.get("best_value")))
    body += [Text(""), Text("Starters", style="dim"), starters]
    if c.get("notes"):
        body += [Text("")] + [Text(f"• {n}", style="dim") for n in c["notes"]]

    title = Text.assemble((c["matchup"], "bold"), ("   " + c.get("start", ""), "dim"))
    subtitle = Text.assemble((badge, colour), (f"  {meaning}", "dim"))
    if c.get("updated"):
        subtitle = Text.assemble(("🔄 UPDATED  ", "bold magenta"), subtitle)
    console.print(Panel(Group(*body), title=title, title_align="left", subtitle=subtitle,
                        subtitle_align="left", border_style=colour, padding=(0, 1), width=88))


def tip_line(c: dict) -> Text:
    """Who jumps for each team, head to head, away first (same order as the matchup):
    'Myles Turner (MIL) 15%  vs  Bam Adebayo (MIA) 85%' — the favourite in bold with a green chance."""
    j = c.get("jumpers") or {}
    away, home = c["away"], c["home"]
    fav = away if c["tip"][away] > c["tip"][home] else home
    records = c.get("tip_record") or {}
    # with this season's tip records the line gets long: one jumper per line so a record never splits
    two_lines = any(r and r[1] for r in records.values())
    line = Text()
    for i, team in enumerate((away, home)):
        if i:
            line.append("\nvs  " if two_lines else "  vs  ", style="dim")
        is_fav = team == fav
        if j.get(team):
            line.append(f"{j[team]} ({team})", style="bold" if is_fav else "")
        else:
            line.append(f"{team} jumper not known yet", style="dim italic")
        line.append(f" {c['tip'][team]:.0%}", style="bold green" if is_fav else "dim")
        rec = (c.get("tip_record") or {}).get(team)
        if rec and rec[1]:                          # his real tips this season: won / taken
            line.append(f" (won {rec[0]}/{rec[1]})", style="dim")
    return line


def season_cell(rec) -> Text:
    """(first baskets, games played) this season -> '4/14'; '–' when he hasn't played yet this season."""
    if not rec or not rec[1]:
        return Text("–", style="dim")
    fb, gp = rec
    return Text(f"{fb}/{gp}", style="bold" if fb else "dim")


def money(x: float) -> str:
    """+0.14 -> '+$0.14', -0.2 -> '−$0.20', anything that rounds to zero -> '$0.00'."""
    if abs(x) < 0.005:
        return "$0.00"
    return f"{'+' if x > 0 else '−'}${abs(x):.2f}"


def ev_cell(v) -> Text:
    """EV per $1: green when positive (the price beats the model's chance), dim when negative."""
    if v is None:
        return Text("–", style="dim")
    import ev
    return Text(money(v), style="bold green" if ev.is_value(v) else "dim")


def best_value_line(b) -> Text:
    """The starter (of all 10) whose best price (FanDuel or DraftKings) has the highest EV, or a note that none is
    positive."""
    if not b:
        return Text("💰 No value at the sportsbooks' prices: every listed starter has negative EV.", style="dim")
    return Text.assemble(("💰 Best value  ", "bold"), (f"{b['name']} ({b['team']}) ", "bold"),
                         (f"{b['american']:+d}", "bold green"), (f" on {b['book']}" if b.get("book") else "", "bold"),
                         (f"  · model {b['prob']:.1%}", "dim"),
                         ("  · EV ", "dim"), (f"{money(b['ev'])} per $1", "bold green"))


def _payout(american: int) -> float:
    """Profit per $1 staked at these American odds."""
    return american / 100 if american > 0 else 100 / -american


def _fanduel_cell(price, fair: int, star: bool = False, faded: bool = False) -> Text:
    """A sportsbook price; green when it pays more than the fair odds (the model sees value), dim otherwise.
    star: the better of the two books for this player (★, gold); faded: the other book pays more."""
    if price is None:
        return Text("–", style="dim")
    better = _payout(price) > _payout(fair)
    if star:
        return Text(f"★{price:+d}", style="bold green" if better else "bold yellow")
    return Text(f"{price:+d}", style="dim" if faded or not better else "bold green")


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


def paper_betting(r: dict, recent: int = 10):
    """`nba.py eval`: a flat $1 paper bet on every +EV FanDuel price, settled on finished games (ev.paper_results)."""
    console.print()
    console.print(Text("💰 Paper betting: $1 on every +EV FanDuel price", style="bold"))
    if not r["bets"]:
        console.print(Text("No settled bets yet. FanDuel prices are saved each time you run `python3 nba.py predict`; "
                           "a game counts here once it has finished. Nothing is ever really bet.", style="dim"))
        return
    colour = "green" if r["profit"] > 0 else "red"
    t = Table.grid(padding=(0, 2))
    t.add_column(style="dim")
    t.add_column()
    t.add_row("Bets", f"{r['bets']} on {r['games']} games · {r['wins']} won ({r['wins'] / r['bets']:.1%})")
    t.add_row("Result", Text.assemble((f"{money(r['profit'])} ", f"bold {colour}"),
                                      (f"on ${r['staked']:.0f} staked · return {r['roi']:+.1%}", colour)))
    t.add_row("EV said", f"{money(r['expected_profit'])} (what the model expected on these bets)")
    console.print(t)
    g = Table(box=box.SIMPLE_HEAD, show_edge=False, width=88, title=f"Latest {min(recent, r['bets'])} bets",
              title_justify="left")
    for col, kw in (("", {"width": 2}), ("Date", {"style": "dim"}), ("Game", {}), ("Bet", {}),
                    ("Price", {"justify": "right"}), ("EV", {"justify": "right"}), ("Scored first", {}),
                    ("Profit", {"justify": "right"})):
        g.add_column(col, **kw)
    for b in r["rows"][:recent]:
        d = b["game_date"] or ""
        g.add_row("✅" if b["won"] else "❌", f"{d[4:6]}/{d[6:8]}", b["matchup"] or "", b["player"],
                  f"{b['american']:+d}", money(b["ev"]), b["scorer"] or "",
                  Text(money(b["profit"]), style="green" if b["won"] else "dim"))
    console.print(g)
    console.print(Text("Long shots lose most nights, so this swings a lot. It takes a few hundred bets to tell a "
                       "real edge from luck.", style="dim"))


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
    t.add_row("Season", "His real record this season: scored the game's first basket in 4 of his 14 games = 4/14.")
    t.add_row("Fair", "Fair odds: the break-even price. A bet only makes sense if the sportsbook pays MORE "
                           "(e.g. +700 when fair is +500).")
    t.add_row("FanDuel / FD", "FanDuel's current First Basket price. Green = it pays more than the fair odds "
                         "(the model thinks the bet has value); grey = it pays less. Skip it with --no-odds.")
    t.add_row("EV/$1", "Expected value: the average profit per $1 if this bet were made many times, using the "
                       "model's chance (e.g. +$0.10 = you'd expect to win 10¢ per $1 in the long run). Green = "
                       "positive. It is only as good as the model; `nba.py eval` checks it on finished games.")
    t.add_row("DK", "DraftKings' price for the same bet (\"1st Points Scorer\": free throws count). ★ = the "
                            "better of the two books for that player (bet there); the other book's price is dimmed.")
    t.add_row("Best value", "The starter (of all 10, not only the top 5) with the highest EV at the better book's price.")
    t.add_row("Top 2", "The two players most likely to score the game's first basket.")
    t.add_row("Scores first", "Each team's chance to score the game's first points (from the tip-off and home court).")
    t.add_row("Tip-off", "Who jumps for each team (away first) and each one's chance to win the opening jump ball; "
                         "the favourite is in bold; (won 9/12) = tips he won this season. The jumper is the player who usually jumps for that team, "
                         "from past tips. The team that wins the tip scores first about 66% of the time.")
    t.add_row("Lineups", "✅ official   🟡 expected, can change   🟠 our guess")
    if any_projected:
        tip = Text("NBA.com usually posts lineups in the early afternoon (ET) and confirms them about "
                   "30 min before tip-off. Re-run later, or use ", style="dark_orange")
        tip.append("python3 nba.py predict --watch", style="bold dark_orange")
        tip.append(" to update automatically.", style="dark_orange")
        t.add_row("", tip)
    console.print(Panel(t, title="How to read this", title_align="left", box=box.ROUNDED,
                        border_style="dim", width=88))
