// First Basket — live FanDuel odds relay (a free Cloudflare Worker).
// Browsers can't read FanDuel's odds from another website, so the website asks here and this asks FanDuel's small
// live-price feed (what FanDuel's own site polls; the same one fanduel_odds.fetch_prices reads — public prices, no
// login, no bets). Only that feed, only GET /prices?ids=<market ids>, only for our website. Everyone asking within
// 20 seconds shares one FanDuel request, which keeps us far inside Cloudflare's free plan and easy on FanDuel.
const PRICES = "https://smp.ny.sportsbook.fanduel.com/api/sports/fixedodds/readonly/v1/getMarketPrices";
const SITE = "https://solomonkhalili.github.io";
const CACHE_SECONDS = 20;

export default {
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
