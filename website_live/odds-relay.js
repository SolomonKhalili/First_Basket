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
async function keepJobRunning(env, dryRun = false) {
  if (!env.GH_TOKEN) return "no GH_TOKEN";
  const h = { Authorization: `Bearer ${env.GH_TOKEN}`, Accept: "application/vnd.github+json",
              "User-Agent": "first-basket-odds", "X-GitHub-Api-Version": "2022-11-28" };
  const r = await fetch(`${GH}/runs?per_page=10`, { headers: h });
  if (!r.ok) return `GitHub ${r.status}`;
  const runs = (await r.json()).workflow_runs || [];
  let perm = "";
  if (dryRun) {                     // permission probe: start a job that doesn't exist (404 = allowed, 403 = not)
    const probe = await fetch(GH.replace("live-picks.yml", "no-such-job.yml") + "/dispatches",
      { method: "POST", headers: { ...h, "Content-Type": "application/json" }, body: JSON.stringify({ ref: "main" }) });
    perm = " · start permission: " + (probe.status === 404 ? "OK" : `MISSING (GitHub ${probe.status})`);
  }
  if (runs.some((x) => ["queued", "in_progress", "waiting", "pending", "requested"].includes(x.status))) return "running" + perm;
  const now = new Date();
  const et = (d, o) => Number(new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour12: false, ...o }).format(d));
  const hour = et(now, { hour: "numeric" }) % 24, day = et(now, { day: "numeric" });
  const last = runs.length ? new Date(Math.max(...runs.map((x) => Date.parse(x.created_at)))) : null;
  const ageMin = last ? (now - last) / 60000 : Infinity;
  let kind = null;
  if (hour >= 6 && ageMin > 10) kind = "live";   // from 6 AM (odds logged from opening); 10 min: a manual update must not hold it back
  else if (hour >= 4 && hour < 6 && (!last || et(last, { day: "numeric" }) !== day || et(last, { hour: "numeric" }) % 24 < 4)) kind = "once";
  if (!kind) return "nothing to do" + perm;
  if (dryRun) return `would start ${kind} (last run ${Math.round(ageMin)} min ago, ${hour}:00 ET)` + perm;
  const d = await fetch(`${GH}/dispatches`, { method: "POST", headers: { ...h, "Content-Type": "application/json" },
                                              body: JSON.stringify({ ref: "main", inputs: { kind } }) });
  return d.ok ? `started ${kind}` : `start failed ${d.status}`;
}

// ── DraftKings log: viewers' browsers send the DraftKings lines they read (DraftKings refuses servers, so only
// browsers can read it); kept per day in Cloudflare KV (binding DKLOG). The cloud job reads them back (GET) to log
// them next to FanDuel's and to keep them on the cards. Writes only when a line changed (free plan: 1,000/day).
const LOG_ORIGINS = new Set([SITE, "http://127.0.0.1:8765", "http://localhost:8765"]);
const etDay = () => new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date()).replaceAll("-", "");
async function dkLog(request, env) {
  const origin = request.headers.get("Origin") || "";
  const cors = { "Access-Control-Allow-Origin": LOG_ORIGINS.has(origin) ? origin : SITE, "Vary": "Origin",
                 "Access-Control-Allow-Methods": "GET, POST", "Access-Control-Allow-Headers": "Content-Type" };
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
  if (!env.DKLOG) return new Response('{"error":"storage not set up"}', { status: 503, headers: cors });
  const day = etDay();
  if (request.method === "GET") {
    const d = new URL(request.url).searchParams.get("day") || day;
    return new Response((await env.DKLOG.get(d)) || '{"games":{},"snapshots":[]}', { headers: { ...cors, "Content-Type": "application/json" } });
  }
  if (request.method !== "POST" || !LOG_ORIGINS.has(origin)) return new Response('{"error":"not allowed"}', { status: 403, headers: cors });
  let body;
  try { body = await request.json(); } catch (e) { return new Response('{"error":"bad json"}', { status: 400, headers: cors }); }
  const stored = JSON.parse((await env.DKLOG.get(day)) || '{"games":{},"snapshots":[]}');
  const now = Date.now(); let changed = 0;
  for (const g of (body.games || []).slice(0, 20)) {
    if (!/^[A-Z]{2,4}$/.test(g.away || "") || !/^[A-Z]{2,4}$/.test(g.home || "")) continue;
    const rows = (g.rows || []).slice(0, 30).filter((r) => typeof r.player === "string" && Number.isFinite(r.american))
      .map((r) => ({ player: r.player.slice(0, 60), team: String(r.team || "").slice(0, 4), american: Math.round(r.american) }));
    if (!rows.length) continue;
    const key = `${g.away}@${g.home}`;
    if (JSON.stringify(stored.games[key]?.rows) === JSON.stringify(rows)) continue;     // same line: nothing to write
    stored.games[key] = { at: now, rows };
    stored.snapshots.push({ at: now, key, rows });
    changed++;
  }
  if (changed) {
    stored.snapshots = stored.snapshots.slice(-3000);
    await env.DKLOG.put(day, JSON.stringify(stored), { expirationTtl: 60 * 60 * 24 * 14 });   // the cloud keeps the record
  }
  return new Response(JSON.stringify({ saved: changed }), { headers: { ...cors, "Content-Type": "application/json" } });
}

// ── My bets: each website account's own bet list, kept in KV (key "bets:<user>", never expires). The list is
// encrypted in the browser with the website's data key, so this only ever stores scrambled text. Only someone who
// is logged in can change it: the first save sets a write code (derived from the data key + user name) and later
// saves must send the same code ("betsw:<user>" keeps only its SHA-256).
const sha256hex = async (s) => [...new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s)))]
  .map((b) => b.toString(16).padStart(2, "0")).join("");
async function myBets(request, env) {
  const origin = request.headers.get("Origin") || "";
  const cors = { "Access-Control-Allow-Origin": LOG_ORIGINS.has(origin) ? origin : SITE, "Vary": "Origin",
                 "Access-Control-Allow-Methods": "GET, PUT", "Access-Control-Allow-Headers": "Content-Type" };
  const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { ...cors, "Content-Type": "application/json", "Cache-Control": "no-store" } });
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
  if (!env.DKLOG) return json({ error: "storage not set up" }, 503);
  const user = new URL(request.url).searchParams.get("u") || "";
  if (!/^[a-z0-9_.-]{1,40}$/.test(user)) return json({ error: "bad user" }, 400);
  if (request.method === "GET") return json(JSON.parse((await env.DKLOG.get(`bets:${user}`)) || "null"));
  if (request.method !== "PUT") return json({ error: "not allowed" }, 405);
  const text = await request.text();
  if (text.length > 200000) return json({ error: "too big" }, 413);
  let body;
  try { body = JSON.parse(text); } catch (e) { return json({ error: "bad json" }, 400); }
  if (typeof body.iv !== "string" || typeof body.ct !== "string" || typeof body.w !== "string" || body.w.length < 32) return json({ error: "bad body" }, 400);
  const want = await env.DKLOG.get(`betsw:${user}`), have = await sha256hex(body.w);
  if (want && want !== have) return json({ error: "not allowed" }, 403);
  if (!want) await env.DKLOG.put(`betsw:${user}`, have);
  await env.DKLOG.put(`bets:${user}`, JSON.stringify({ iv: body.iv, ct: body.ct, at: Date.now() }));
  return json({ saved: true });
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(keepJobRunning(env).then((m) => console.log("timer:", m)));
  },

  async fetch(request, env, ctx) {
    const cors = { "Access-Control-Allow-Origin": SITE, "Vary": "Origin" };
    if (new URL(request.url).pathname === "/dk-log") return dkLog(request, env);
    if (new URL(request.url).pathname === "/bets") return myBets(request, env);
    if (new URL(request.url).pathname === "/timer-check") {           // what the timer would do now (starts nothing)
      return new Response(await keepJobRunning(env, true), { headers: { "Content-Type": "text/plain" } });
    }
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
