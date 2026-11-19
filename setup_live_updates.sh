#!/bin/bash
# One-time setup: turns on the live website job (website_live/live-picks.yml) in the public website repo.
#   1. makes a key the job uses to read the private code and save data (only GitHub ever sees it; deleted here)
#   2. stores that key + the website's data key as hidden secrets of SolomonKhalili/first-basket
#   3. adds the job to that repo and starts it
#   4. tells the old hourly timer (private repo) to stand down, so only one job saves data
set -euo pipefail
cd "$(dirname "$0")"
PRIV=SolomonKhalili/First_Basket
PUB=SolomonKhalili/first-basket
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

echo "1/4  Making the job's key…"
ssh-keygen -q -t ed25519 -N "" -C "first-basket live job" -f "$TMP/key"
gh api -X POST "repos/$PRIV/keys" -f title="live job (public website repo)" -f key="$(cat "$TMP/key.pub")" \
  -F read_only=false --jq '"     added to the private repo: " + .title' 

echo "2/4  Storing the hidden secrets…"
gh secret set PRIVATE_REPO_KEY -R "$PUB" < "$TMP/key"
gh secret set SITE_DATA_KEY -R "$PUB" < ~/.config/first-basket/site_data_key

echo "3/4  Adding the job…"
gh api -X PUT "repos/$PUB/contents/.github/workflows/live-picks.yml" -f message="Live picks job" \
  -f content="$(base64 < website_live/live-picks.yml | tr -d '\n')" \
  $(sha=$(gh api "repos/$PUB/contents/.github/workflows/live-picks.yml" --jq .sha 2>/dev/null) && echo "-f sha=$sha") \
  --jq '"     " + .content.path'
sleep 5
gh workflow run live-picks.yml -R "$PUB" -f kind=live && echo "     started"

echo "4/4  Standing down the old hourly timer…"
gh variable set LIVE_ENABLED -R "$PRIV" -b true

echo
echo "✅ Done. The website now keeps itself live on game nights. Watch it: https://github.com/$PUB/actions"
