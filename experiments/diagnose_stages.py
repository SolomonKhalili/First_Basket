"""Experiment (read-only, 2026-11-18): where does the model lose accuracy? Week-by-week on 2026-27 and Jan–Apr 2026:
  stage 1 — how often the favoured team scores first, and its log-loss (coin flip = 0.6931)
  stage 2 — given the team that DID score first, how often our top player on that team was the scorer
  'perfect stage 1' — top-pick rate if we knew the team in advance (upper bound for stage-1 work)."""
import io, contextlib, os, sys
import numpy as np
from datetime import date, timedelta
os.chdir("/Users/solomonkhalili/Desktop/NBA"); sys.path.insert(0, ".")
src = open("train_pregame.py").read().replace("args = parser.parse_args()", "args = parser.parse_args([])")
ns = {"__name__": "x"}
with contextlib.redirect_stdout(io.StringIO()):
    exec(compile(src[:src.index("# ── L2 (regularisation strength)")], "t", "exec"), ns)
import pregame_model as pm
dates, season, Y, home, home_first, tip_home, PL = (ns[k] for k in ("dates", "season", "Y", "home", "home_first", "tip_home", "PL"))
for lo, hi, label in (("20261019", max(dates), "2026-27"), ("20260101", "20260412", "Jan–Apr 2026")):
    ws = date(int(lo[:4]), int(lo[4:6]), int(lo[6:])); s1, s1ll, s2, s2n, perf, n = 0, 0.0, 0, 0, 0, 0
    tip_right = 0
    while ws.strftime("%Y%m%d") <= hi:
        a_, b_ = ws.strftime("%Y%m%d"), (ws + timedelta(days=7)).strftime("%Y%m%d")
        te = np.where((season != "playoffs") & (dates >= a_) & (dates < b_) & (dates <= hi))[0]
        if len(te):
            tr = np.where((season != "playoffs") & (dates < a_))[0]
            team, w, mu, sd = ns["fit2"](tr, 1e-3)
            for k in te:
                ph = float(pm.home_scores_first(team, tip_home[k]))
                hf = bool(home_first[k])
                s1 += (ph > 0.5) == hf; s1ll += -np.log(ph if hf else 1 - ph); n += 1
                side = home[k] if hf else ~home[k]
                sc = ((PL[k] - mu) / sd) @ w
                idx = np.where(side)[0]
                s2 += Y[k, idx[sc[idx].argmax()]] == 1; s2n += 1
        ws += timedelta(days=7)
    print(f"{label}: {n} games")
    print(f"  stage 1  favoured team scored first {s1 / n:6.1%}   log-loss {s1ll / n:.4f} (coin flip 0.6931)")
    print(f"  stage 2  top player of the scoring team was the scorer {s2 / s2n:6.1%}  (random 1 in 5 = 20%)")
    print(f"  → if stage 1 were perfect, top pick would be ~{s2 / s2n:5.1%}; today it is ~{s1 / n * s2 / s2n + (1 - s1 / n) * 0:5.1%}+")
print("DONE")
