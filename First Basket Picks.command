#!/bin/zsh
# First Basket Picks — double-click to open the app.
#   • Already running?  Just opens it in your browser.
#   • Not running?      Starts it, keeps the Mac awake, and (if sharing is set up) opens your ngrok link for friends.
#   • If the app or the link ever stops by itself, it is restarted within a few seconds.
# Keep the Terminal window open while you (or your friends) use the app; close it or press Ctrl-C to stop everything.
#
# Sharing is set up when launcher/ngrok_url.txt holds your fixed ngrok link and at least one account exists
# (python3 gui.py add-user NAME --owner). Without accounts the link is never started.

printf '\e]0;First Basket Picks\a'                       # Terminal window title

# Go to the NBA folder (works even when started from the Desktop app or a shortcut)
here="$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")"
cd "$here" || { echo "Couldn't find the NBA folder at: $here"; read -k1 "?Press any key to close."; exit 1; }

PORT=8765
URL="http://127.0.0.1:$PORT"

if curl -s -o /dev/null -m 2 "$URL/api/status"; then
  echo "🏀 First Basket Picks is already running — opening it in your browser."
  open "$URL"
  exit 0
fi

if ! command -v python3 >/dev/null; then
  echo "python3 isn't installed or isn't on the PATH, so the app can't start."
  read -k1 "?Press any key to close."
  exit 1
fi

# ── Stop everything together when this window closes or Ctrl-C is pressed ──
pids=()
cleanup() {
  trap - INT TERM HUP EXIT
  echo; echo "Stopping First Basket Picks…"
  for p in $pids; do pkill -P $p 2>/dev/null; kill $p 2>/dev/null; done
  exit 0
}
trap cleanup INT TERM HUP EXIT

# ── Keep the Mac awake while this runs (sleep would take the app and the link offline) ──
caffeinate -is -w $$ &
pids+=($!)

# ── The app, restarted automatically if it ever stops ──
(
  while true; do
    python3 gui.py --no-open
    echo "⚠️  The app stopped — restarting in 5 seconds…"
    sleep 5
  done
) &
pids+=($!)

echo "🏀 Starting First Basket Picks…"
for i in {1..30}; do curl -s -o /dev/null -m 1 "$URL/api/status" && break; sleep 1; done
open "$URL"

# ── The sharing link (ngrok), restarted automatically if the connection drops ──
SHARE_URL="$(tr -d ' \n\r' < launcher/ngrok_url.txt 2>/dev/null)"
has_accounts=$(python3 -c "import accounts; print(1 if accounts.enabled() else 0)" 2>/dev/null)
if [ -n "$SHARE_URL" ] && command -v ngrok >/dev/null; then
  if [ "$has_accounts" = "1" ]; then
    (
      while true; do
        ngrok http "$PORT" --url "$SHARE_URL" --log launcher/ngrok.log --log-level warn >/dev/null 2>&1
        echo "⚠️  The sharing link dropped — reconnecting in 10 seconds…"
        sleep 10
      done
    ) &
    pids+=($!)
    echo "🔗 Friends' link: $SHARE_URL   (they log in with the account you gave them)"
  else
    echo "🔒 Sharing link NOT started: create your account first:  python3 gui.py add-user YOURNAME --owner"
  fi
fi

echo "   Keep this window open while the app is in use. To stop everything: close this window or press Ctrl-C."
echo "   Your Mac will stay awake while it runs (it can still sleep if you close the lid on battery)."
wait
