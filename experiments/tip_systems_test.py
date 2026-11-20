"""Experiment (read-only, 2026-11-19): tip-off rating systems from public projects (research_notes/.../opensource_tipoff.md)
vs ours. Ours = 50/50 blend of the shrunk-rate log5 model and Elo (K=24, home 15). Each candidate replaces the Elo half
(or the rate half for the opponent-adjusted model). Point-in-time: ratings only from tips before each game.
Same 1,238 games as the Elo adoption test; log-loss decides, better in BOTH seasons to adopt."""
import math, os, sqlite3, sys
import numpy as np
from scipy.optimize import minimize
os.chdir("/Users/solomonkhalili/Desktop/NBA"); sys.path.insert(0, ".")
import tip_model as tm, starters as st

con = sqlite3.connect("nba_first_basket.db")
src = tm._tips(con, True)
tips = con.execute(f"""SELECT game_date, home_team, tip_winner_team, tip_winner_player_id, tip_loser_player_id FROM {src}
    WHERE tip_winner_team IS NOT NULL AND COALESCE(tip_winner_player_id,'') != '' AND COALESCE(tip_loser_player_id,'') != ''
    ORDER BY game_date, game_id""").fetchall()
games = con.execute("""SELECT game_id, game_date, home_team, away_team, tip_winner_team FROM games
    WHERE tip_winner_team IS NOT NULL AND game_date >= '20251201' ORDER BY game_date""").fetchall()
lg = lambda p: math.log(p / (1 - p)); sg = lambda z: 1 / (1 + math.exp(-z))

# the live call's two halves for every test game (jumpers as the live model picks them)
rows = []
tm_blend = tm.ELO_BLEND
for gid, d, h, a, w in games:
    hs, as_ = tm.starters_for_game(con, gid, h), tm.starters_for_game(con, gid, a)
    hc, ac = st.listed_center(con, d, h), st.listed_center(con, d, a)
    tm.ELO_BLEND = 0.0; p_rate = tm.home_win_prob(con, h, a, d, hs, as_, hc, ac, True)
    tm.ELO_BLEND = tm_blend; p_live = tm.home_win_prob(con, h, a, d, hs, as_, hc, ac, True)
    jh = str(tm.team_jumper(con, h, d, hs, hc, extended=True) or ""); ja = str(tm.team_jumper(con, a, d, as_, ac, extended=True) or "")
    rows.append((d, w == h, p_rate, p_live, jh, ja))
print(len(rows), "games", flush=True)

def score(ps):
    out = {}
    for (d, hw, *_), p in zip(rows, ps):
        p = min(max(p, 1e-6), 1 - 1e-6); s = out.setdefault("2026-27" if d >= "20260801" else "2025-26", [0, 0, 0.0])
        s[0] += (p > .5) == hw; s[1] += 1; s[2] -= math.log(p if hw else 1 - p)
    return {k: (round(r / n * 100, 1), round(ll / n, 5)) for k, (r, n, ll) in sorted(out.items())}

def run_system(update, predict):
    """Walk the tips in date order; before each test game's date, predict P(home jumper wins) from the system."""
    out, i = [], 0
    for d, hw, p_rate, p_live, jh, ja in rows:
        while i < len(tips) and tips[i][0] < d:
            update(*tips[i]); i += 1
        out.append(predict(jh, ja, d))
    return out
blend = lambda p_rate, p_sys: sg(0.5 * lg(p_rate) + 0.5 * lg(min(max(p_sys, 1e-6), 1 - 1e-6)))

print("LIVE (rate + Elo K24):", score([r[3] for r in rows]), flush=True)

# ── Elo with a new-season pull toward 1500 (F) ──
def elo(regress):
    R, last = {}, {}
    def season(dd): return dd[:4] if dd[4:6] >= "08" else str(int(dd[:4]) - 1)
    def get(p, dd):
        r = R.get(p, 1500.0)
        if p in last and season(last[p]) != season(dd): r = 1500 + regress * (r - 1500)
        return r
    def upd(dd, th, tw, wp, lp):
        rw, rl = get(wp, dd), get(lp, dd); adj = 15 if tw == th else -15
        e = 1 / (1 + 10 ** ((rl - rw - adj) / 400)); R[wp], R[lp] = rw + 24 * (1 - e), rl - 24 * (1 - e); last[wp] = last[lp] = dd
    def pred(jh, ja, dd): return 1 / (1 + 10 ** ((get(ja, dd) - get(jh, dd) - 15) / 400))
    return run_system(upd, pred)
for reg in (1.0, 0.8, 0.67, 0.5):
    print(f"Elo, new-season pull {reg}:", score([blend(r[2], p) for r, p in zip(rows, elo(reg))]), flush=True)

# ── Glicko-2 (B), one rating period per tip, RD grows with idle days ──
def glicko(tau=0.5, sigma0=0.06, rd0=350.0, home=15.0):
    S = {}                                   # p -> [mu, phi, sigma, last_date]
    from datetime import date
    def dd2(dd): return date(int(dd[:4]), int(dd[4:6]), int(dd[6:]))
    def get(p, dd):
        if p not in S: return [0.0, rd0 / 173.7178, sigma0, dd]
        mu, phi, sig, last = S[p]; idle = max((dd2(dd) - dd2(last)).days / 30.0, 0)    # idle months widen RD
        return [mu, min(math.sqrt(phi ** 2 + idle * sig ** 2), rd0 / 173.7178), sig, dd]
    g = lambda phi: 1 / math.sqrt(1 + 3 * phi ** 2 / math.pi ** 2)
    def one(mu, phi, sig, mu_o, phi_o, s):
        E = 1 / (1 + math.exp(-g(phi_o) * (mu - mu_o))); v = 1 / (g(phi_o) ** 2 * E * (1 - E)); delta = v * g(phi_o) * (s - E)
        a = math.log(sig ** 2); f = lambda x: (math.exp(x) * (delta ** 2 - phi ** 2 - v - math.exp(x))) / (2 * (phi ** 2 + v + math.exp(x)) ** 2) - (x - a) / tau ** 2
        A = a; B = math.log(delta ** 2 - phi ** 2 - v) if delta ** 2 > phi ** 2 + v else a - tau
        if B == a - tau:
            k = 1
            while f(a - k * tau) < 0: k += 1
            B = a - k * tau
        fA, fB = f(A), f(B)
        for _ in range(50):
            if abs(B - A) < 1e-6: break
            C = A + (A - B) * fA / (fB - fA); fC = f(C)
            if fC * fB <= 0: A, fA = B, fB
            else: fA /= 2
            B, fB = C, fC
        sig2 = math.exp(A / 2); phis = math.sqrt(phi ** 2 + sig2 ** 2); phi2 = 1 / math.sqrt(1 / phis ** 2 + 1 / v)
        return mu + phi2 ** 2 * g(phi_o) * (s - E), phi2, sig2
    h = home / 173.7178
    def upd(dd, th, tw, wp, lp):
        W, L = get(wp, dd), get(lp, dd); adj = h if tw == th else -h
        mw, pw, sw = one(W[0] + adj, W[1], W[2], L[0], L[1], 1.0); ml, pl, sl = one(L[0], L[1], L[2], W[0] + adj, W[1], 0.0)
        S[wp] = [mw - adj, pw, sw, dd]; S[lp] = [ml, pl, sl, dd]
    def pred(jh, ja, dd):
        H, A_ = get(jh, dd), get(ja, dd)
        return 1 / (1 + math.exp(-g(math.sqrt(H[1] ** 2 + A_[1] ** 2)) * (H[0] + h - A_[0])))
    return run_system(upd, pred)
for tau in (0.3, 0.5):
    print(f"Glicko-2 tau {tau}:", score([blend(r[2], p) for r, p in zip(rows, glicko(tau))]), flush=True)

# ── TrueSkill 1v1 (A) ──
def trueskill(home=0.0):
    mu0, s0 = 25.0, 25 / 3; beta, tau_ = s0 / 1.9, s0 / 100; S = {}
    from scipy.stats import norm
    def get(p): return S.get(p, [mu0, s0])
    def upd(dd, th, tw, wp, lp):
        (mw, sw), (ml, sl) = get(wp), get(lp); sw = math.sqrt(sw ** 2 + tau_ ** 2); sl = math.sqrt(sl ** 2 + tau_ ** 2)
        adj = home if tw == th else -home
        c = math.sqrt(2 * beta ** 2 + sw ** 2 + sl ** 2); t = (mw + adj - ml) / c
        vv = norm.pdf(t) / max(norm.cdf(t), 1e-12); ww = vv * (vv + t)
        S[wp] = [mw + sw ** 2 / c * vv, sw * math.sqrt(max(1 - sw ** 2 / c ** 2 * ww, 1e-6))]
        S[lp] = [ml - sl ** 2 / c * vv, sl * math.sqrt(max(1 - sl ** 2 / c ** 2 * ww, 1e-6))]
    def pred(jh, ja, dd):
        (mh, sh), (ma, sa) = get(jh), get(ja); c = math.sqrt(2 * beta ** 2 + sh ** 2 + sa ** 2)
        return float(norm.cdf((mh + home - ma) / c))
    return run_system(upd, pred)
for hm in (0.0, 0.3):
    print(f"TrueSkill home {hm}:", score([blend(r[2], p) for r, p in zip(rows, trueskill(hm))]), flush=True)

# ── Opponent-adjusted rate (G): penalised Bradley-Terry refit each test date, replaces the RATE half ──
def bt(prior_sd):
    out, cache = [], {}
    for d, hw, p_rate, p_live, jh, ja in rows:
        if d not in cache:
            past = [t for t in tips if t[0] < d]
            ids = sorted({p for t in past for p in (t[3], t[4])}); ix = {p: k for k, p in enumerate(ids)}
            W = np.array([ix[t[3]] for t in past]); L = np.array([ix[t[4]] for t in past])
            hs = np.array([1.0 if t[2] == t[1] else -1.0 for t in past])     # +1 when the winner was home
            def nll(v):
                th, hh = v[:-1], v[-1]; z = th[W] - th[L] + hh * hs; p = 1 / (1 + np.exp(-z))
                gr = np.zeros_like(v); r = (1 - p)
                np.add.at(gr, W, -r); np.add.at(gr, L, r); gr[-1] = -(r * hs).sum()
                gr[:-1] += th / prior_sd ** 2
                return -np.log(p).sum() + (th ** 2).sum() / (2 * prior_sd ** 2), gr
            v = minimize(nll, np.zeros(len(ids) + 1), jac=True, method="L-BFGS-B").x
            cache = {d: (dict(zip(ids, v[:-1])), v[-1])}
        th, hh = cache[d]
        out.append(sg(th.get(jh, 0.0) - th.get(ja, 0.0) + hh))
    return out
for sd in (0.3, 0.5):
    ps = bt(sd)
    print(f"Opponent-adjusted rate (prior sd {sd}) + Elo:", score([sg(0.5 * lg(p) + 0.5 * lg(min(max(e, 1e-6), 1 - 1e-6))) for p, e in zip(ps, elo(1.0))]), flush=True)

# RESULT (2026-11-19, 1,238 games; (right %, log-loss); lower log-loss is better). NOTHING ADOPTED.
#   live (rate + Elo K24) ............. 2025-26 64.3 / 0.62687   2026-27 67.4 / 0.62644   (pull 1.0 reproduces it exactly)
#   Elo new-season pull 0.8/0.67/0.5 .. 2025-26 ~0.6268 (flat)   2026-27 0.62756-0.63143 (worse)
#   Glicko-2 (tau 0.3 or 0.5) ......... 0.62692 / 0.62987        (worse both)
#   TrueSkill (home 0 / 0.3) .......... 0.62779 / 0.63209, 0.62631 / 0.63408 (2026-27 much worse)
#   opponent-adjusted rate sd 0.3 ..... 0.63190 / 0.62762        (worse both)
#   opponent-adjusted rate sd 0.5 ..... 0.62811 / 0.62539        (better 2026-27 only -> fails the both-seasons rule)
