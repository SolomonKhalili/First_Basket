#!/bin/bash
# One-time setup: live FanDuel odds on the website (the free Cloudflare relay, website_live/odds-relay.js).
#   1. Sign in to Cloudflare (a browser window opens: create a free account if you don't have one — no card —
#      then click "Allow"). You type your own email and password there.
#   2. Puts the relay on Cloudflare (free plan: 100,000 requests a day; we use ~2%).
#   3. Checks it can reach FanDuel, saves its address, and the website switches to live odds by itself.
set -euo pipefail
cd "$(dirname "$0")"
W="npx -y wrangler@4.155.0"

echo "1/3  Sign in to Cloudflare in the browser window that opens (free account, no card)…"
$W login

echo "2/3  Putting the relay on Cloudflare…"
out=$($W deploy website_live/odds-relay.js --name first-basket-odds --compatibility-date 2026-11-01 2>&1 | tee /dev/stderr)
url=$(printf '%s\n' "$out" | grep -Eo 'https://first-basket-odds\.[A-Za-z0-9.-]+\.workers\.dev' | head -1)
[ -n "$url" ] || { echo "Couldn't find the relay's address in the output above — send it to Claude."; exit 1; }

echo "3/3  Checking the relay reaches FanDuel…"
code=$(curl -s -o /dev/null -w '%{http_code}' -H "Origin: https://solomonkhalili.github.io" "$url/prices?ids=1.0")
[ "$code" = "200" ] || { echo "The relay is up ($url) but FanDuel answered $code — send this to Claude."; exit 1; }
printf '%s\n' "$url" > website_live/relay_url.txt
git add website_live/relay_url.txt
git commit -q -m "Live odds relay address" --only website_live/relay_url.txt
git push -q origin HEAD:main

echo
echo "✅ Done: $url"
echo "   The website switches to live FanDuel odds within a few minutes (odds re-read every ~30 s while open)."
