#!/usr/bin/env python3
"""
FanDuel First Basket Scorer — Game Page Scraper
Uses content API to get game event IDs, then scrapes each game page.
"""

from __future__ import annotations
import asyncio
import json
import re
from dataclasses import dataclass, asdict
from typing import Optional

from playwright.async_api import async_playwright

FD_BASE = "https://sportsbook.fanduel.com"
FD_NAV_NBA = f"{FD_BASE}/navigation/nba"
FD_CONTENT_API = "https://api.sportsbook.fanduel.com/sbapi/content-managed-page"


@dataclass
class FDPlayerOdds:
    player_name: str
    team: str
    american_odds: int
    decimal_odds: float
    implied_prob: float


@dataclass
class FDGame:
    game_id: str
    home_team: str
    away_team: str
    start_time: str
    status: str
    players: list[FDPlayerOdds]


class FanDuelScraper:
    def __init__(self, headless: bool = True, timeout: int = 30000):
        self.headless = headless
        self.timeout = timeout
        self._browser = None
        self._context = None
        self._page = None
        self._ak = "FhMFpcPWXMeyZxOx"

    async def __aenter__(self):
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )
        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="America/New_York",
        )
        await self._context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {} };
        """)
        self._page = await self._context.new_page()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def _get_cookies(self):
        await self._page.goto(FD_NAV_NBA, wait_until="domcontentloaded", timeout=self.timeout)
        await self._page.wait_for_timeout(3000)
        cookies = await self._context.cookies()
        self._cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

    def _headers(self) -> dict:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Referer": FD_BASE + "/",
            "Origin": FD_BASE,
            "Cookie": self._cookie_header,
        }

    async def _fetch_content_api(self) -> dict:
        resp = await self._page.request.get(
            FD_CONTENT_API,
            params={
                "page": "CUSTOM",
                "customPageId": "nba",
                "pbHorizontal": "false",
                "_ak": self._ak,
                "timezone": "America/New_York",
            },
            headers=self._headers(),
        )
        if resp.status != 200:
            body = await resp.body()
            print(f"  ⚠️ Content API error {resp.status}: {body[:500]}")
            return {}
        return await resp.json()

    def _get_game_events(self, content: dict) -> list[dict]:
        """Extract game events with event IDs."""
        events = content.get('attachments', {}).get('events', {})
        games = []
        for event_id, ev in events.items():
            name = ev.get('name', '')
            # Only actual games (have @ or vs)
            if re.search(r'\s+@\s+|\s+vs\.?\s+', name, re.IGNORECASE):
                teams = self._parse_teams(name)
                if teams:
                    home, away = teams
                    games.append({
                        'event_id': str(event_id),
                        'home_team': home,
                        'away_team': away,
                        'event_name': name,
                        'start_time': ev.get('openDate', ''),
                    })
        return games

    async def _scrape_game_page(self, event_id: str, home: str, away: str) -> list[FDPlayerOdds]:
        """Navigate to game page and extract first basket odds."""
        # Try different URL patterns
        urls = [
            f"{FD_BASE}/event/{event_id}",
            f"{FD_BASE}/nba/event/{event_id}",
            f"{FD_BASE}/sports/nba/{event_id}",
        ]

        for url in urls:
            try:
                print(f"    🔗 Trying {url}")
                await self._page.goto(url, wait_until="domcontentloaded", timeout=15000)
                await self._page.wait_for_timeout(4000)

                # Click First Basket tab
                try:
                    await self._page.click('text=First Basket', timeout=5000)
                    await self._page.wait_for_timeout(2000)
                except Exception:
                    try:
                        await self._page.click('text=1st Basket', timeout=5000)
                        await self._page.wait_for_timeout(2000)
                    except Exception:
                        pass

                # Extract odds
                players = await self._page.evaluate(r"""
                    () => {
                        const results = [];
                        // Find all elements with odds
                        const all = document.querySelectorAll('*');
                        all.forEach(el => {
                            const text = el.innerText || '';
                            // Match +XXX pattern for first basket odds
                            const matches = text.match(/\+(\d{3,4})/g);
                            if (matches) {
                                const odds = parseInt(matches[0]);
                                if (odds >= 300 && odds <= 3000) {
                                    // Find player name nearby
                                    const nameMatch = text.match(/([A-Z][a-z]+\.?\s+[A-Z][a-z]+)/);
                                    if (nameMatch) {
                                        results.push({ name: nameMatch[1], odds });
                                    }
                                }
                            }
                        });
                        return results;
                    }
                """)

                if players:
                    fd_players = []
                    for p in players:
                        odds = p['odds']
                        decimal = odds / 100 + 1
                        implied = 1 / decimal
                        fd_players.append(FDPlayerOdds(
                            player_name=p['name'],
                            team=home,  # Simplified
                            american_odds=odds,
                            decimal_odds=round(decimal, 4),
                            implied_prob=round(implied, 4),
                        ))

                    # Deduplicate
                    seen = set()
                    unique = []
                    for p in fd_players:
                        key = p.player_name.lower()
                        if key not in seen:
                            seen.add(key)
                            unique.append(p)

                    unique.sort(key=lambda p: p.american_odds)
                    return unique[:12]

            except Exception as e:
                print(f"    ⚠️ {url} failed: {e}")
                continue

        return []

    async def get_todays_games(self) -> list[FDGame]:
        await self._get_cookies()

        # Get game events from content API
        content = await self._fetch_content_api()
        if not content:
            return []

        games = self._get_game_events(content)
        print(f"  📥 Found {len(games)} games from content API")

        results = []
        for g in games:
            print(f"  🎯 Scraping {g['away_team']} @ {g['home_team']} (event {g['event_id']})")
            players = await self._scrape_game_page(g['event_id'], g['home_team'], g['away_team'])
            if players:
                results.append(FDGame(
                    game_id=g['event_id'],
                    home_team=g['home_team'],
                    away_team=g['away_team'],
                    start_time=g['start_time'],
                    status="pre",
                    players=players,
                ))
                print(f"    ✅ Got {len(players)} players")

        return results

    def _parse_teams(self, event_name: str) -> Optional[tuple[str, str]]:
        patterns = [
            r"^(.+?)\s+@\s+(.+)$",
            r"^(.+?)\s+vs\.?\s+(.+)$",
            r"^([A-Z]{2,3})\s+@\s+([A-Z]{2,3})$",
        ]
        for pat in patterns:
            m = re.match(pat, event_name.strip(), re.IGNORECASE)
            if m:
                away = self._normalize_team(m.group(1).strip())
                home = self._normalize_team(m.group(2).strip())
                if home and away:
                    return home, away
        return None

    def _normalize_team(self, name: str) -> str:
        mapping = {
            "hawks": "ATL", "celtics": "BOS", "nets": "BKN", "hornets": "CHA",
            "bulls": "CHI", "cavaliers": "CLE", "mavericks": "DAL", "nuggets": "DEN",
            "pistons": "DET", "warriors": "GS", "rockets": "HOU", "pacers": "IND",
            "clippers": "LAC", "lakers": "LAL", "grizzlies": "MEM", "heat": "MIA",
            "bucks": "MIL", "timberwolves": "MIN", "pelicans": "NO", "knicks": "NY",
            "thunder": "OKC", "magic": "ORL", "76ers": "PHI", "sixers": "PHI",
            "suns": "PHX", "trail blazers": "POR", "blazers": "POR", "spurs": "SA",
            "kings": "SAC", "raptors": "TOR", "jazz": "UTAH", "wizards": "WSH",
            "atl": "ATL", "bos": "BOS", "bkn": "BKN", "cha": "CHA",
            "chi": "CHI", "cle": "CLE", "dal": "DAL", "den": "DEN",
            "det": "DET", "gs": "GS", "hou": "HOU", "ind": "IND",
            "lac": "LAC", "lal": "LAL", "mem": "MEM", "mia": "MIA",
            "mil": "MIL", "min": "MIN", "no": "NO", "ny": "NY",
            "okc": "OKC", "orl": "ORL", "phi": "PHI", "phx": "PHX",
            "por": "POR", "sa": "SA", "sac": "SAC", "tor": "TOR",
            "uta": "UTAH", "utah": "UTAH", "wsh": "WSH",
        }
        key = name.lower().replace(".", "").strip()
        return mapping.get(key, name.upper()[:3])

    def to_dict(self, games: list[FDGame]) -> list[dict]:
        return [asdict(g) for g in games]


async def scrape_fanduel_first_basket() -> list[dict]:
    async with FanDuelScraper(headless=True) as scraper:
        games = await scraper.get_todays_games()
        return scraper.to_dict(games)


if __name__ == "__main__":
    result = asyncio.run(scrape_fanduel_first_basket())
    print(json.dumps(result, indent=2))