// First Basket — live FanDuel odds relay (a free Cloudflare Worker).
// Browsers can't read FanDuel's odds from another website, so the website asks here and this asks FanDuel's small
// live-price feed (what FanDuel's own site polls; the same one fanduel_odds.fetch_prices reads — public prices, no
// login, no bets). Only that feed, only GET /prices?ids=<market ids>, only for our website. Everyone asking within
// 20 seconds shares one FanDuel request, which keeps us far inside Cloudflare's free plan and easy on FanDuel.
const PRICES = "https://smp.ny.sportsbook.fanduel.com/api/sports/fixedodds/readonly/v1/getMarketPrices";
const SITE = "https://solomonkhalili.github.io";
const CACHE_SECONDS = 20;

// ── Timer: every 10 minutes (wrangler.jsonc "crons"), make sure the website's cloud job is running ──────────
// GitHub's own timer can start late, and the Mac backup only works while the Mac is on. With a GitHub key that can
// only start that one job (secret GH_TOKEN, set in Cloudflare), this starts it whenever nothing is running:
//   9 AM – midnight ET: the live job (it stops by itself when no games are left)   4–9 AM ET: the nightly update.
const GH = "https://api.github.com/repos/SolomonKhalili/first-basket/actions/workflows/live-picks.yml";
async function keepJobRunning(env) {
  if (!env.GH_TOKEN) return "no GH_TOKEN";
  const h = { Authorization: `Bearer ${env.GH_TOKEN}`, Accept: "application/vnd.github+json",
              "User-Agent": "first-basket-odds", "X-GitHub-Api-Version": "2022-11-28" };
  const r = await fetch(`${GH}/runs?per_page=10`, { headers: h });
  if (!r.ok) return `GitHub ${r.status}`;
  const runs = (await r.json()).workflow_runs || [];
  if (runs.some((x) => ["queued", "in_progress", "waiting", "pending", "requested"].includes(x.status))) return "running";
  const now = new Date();
  const et = (d, o) => Number(new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour12: false, ...o }).format(d));
  const hour = et(now, { hour: "numeric" }) % 24, day = et(now, { day: "numeric" });
  const last = runs.length ? new Date(Math.max(...runs.map((x) => Date.parse(x.created_at)))) : null;
  const ageMin = last ? (now - last) / 60000 : Infinity;
  let kind = null;
  if (hour >= 9 && ageMin > 25) kind = "live";
  else if (hour >= 4 && hour < 9 && (!last || et(last, { day: "numeric" }) !== day || et(last, { hour: "numeric" }) % 24 < 4)) kind = "once";
  if (!kind) return "nothing to do";
  const d = await fetch(`${GH}/dispatches`, { method: "POST", headers: { ...h, "Content-Type": "application/json" },
                                              body: JSON.stringify({ ref: "main", inputs: { kind } }) });
  return d.ok ? `started ${kind}` : `start failed ${d.status}`;
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(keepJobRunning(env).then((m) => console.log("timer:", m)));
  },

  async fetch(request, env, ctx) {
    const cors = { "Access-Control-Allow-Origin": SITE, "Vary": "Origin" };
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: { ...cors, "Access-Control-Allow-Methods": "GET" } });
    const url = new URL(request.url);
    if (request.method !== "GET" || url.pathname !== "/prices") return new Response('{"error":"not found"}', { status: 404, headers: cors });
    const ids = [...new Set((url.searchParams.get("ids") || "").split(",").filter((i) => /^[0-9.]{1,40}$/.test(i)))].sort().slice(0, 20);
    if (!ids.length) return new Response("[]", { headers: { ...cors, "Content-Type": "application/json" } });
    const key = new Request(`https://relay.cache/prices?ids=${ids.join(",")}`);      // one shared answer per 20 s
    let res = await caches.default.match(key);
    if (!res) {
      const up = await fetch(PRICES, { method: "POST", body: JSON.stringify({ marketIds: ids }),
        headers: { "Content-Type": "application/json", Accept: "application/json", "User-Agent": "Mozilla/5.0" } });
      if (!up.ok) return new Response(JSON.stringify({ error: `FanDuel answered ${up.status}` }), { status: 502, headers: cors });
      res = new Response(await up.arrayBuffer(), { headers: { "Content-Type": "application/json", "Cache-Control": `max-age=${CACHE_SECONDS}` } });
      ctx.waitUntil(caches.default.put(key, res.clone()));
    }
    return new Response(res.body, { headers: { ...cors, "Content-Type": "application/json", "Cache-Control": "no-store" } });
  },
};
