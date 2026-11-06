"""How much does losing the starting QB cost, and how much of it shows up in the total?

Offline study on past seasons (no API calls once data/history/<year>/passing.json.gz
exists; the "QB study" workflow downloads it and runs this).

1. From per-game passing stats, find each team's established starter before every
   game: the QB with the most pass attempts in that season's earlier games, if he
   threw at least 60% of the team's passes and the team has played 3+ games.
2. A game is "starter out" when that QB threw fewer than 5 passes in it.
3. Using the walk-forward model (each week rated only on earlier games, the same
   as tools/tune.py), measure in those games how far the team's own points, the
   opponent's points, the margin and the total landed from the model, and from
   the closing line. All other games are the baseline.

The ratio (total miss) / (margin miss) is the share of a QB adjustment that
belongs on the total: config.ADJ_TOTAL_SHARE.

    python tools/qb_study.py                # 2022-2026
    python tools/qb_study.py --seasons 2024 2025
Writes docs/data/qb_study.json.
"""
import argparse
import gzip
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

MIN_SHARE = 0.60    # starter = 60%+ of the team's attempts so far
MIN_GAMES = 3       # ...over at least 3 earlier games
OUT_ATTEMPTS = 5    # starter "out" = fewer than 5 attempts in the game


def _attempts(stat):
    try:
        return int(str(stat).split("/")[1])
    except (IndexError, ValueError):
        return 0


def passing_rows(year):
    f = ROOT / "data" / "history" / str(year) / "passing.json.gz"
    if not f.exists():
        return None
    raw = json.load(gzip.open(f, "rt"))
    out = []
    for g in raw:
        gid = g.get("id")
        for t in g.get("teams", []):
            team = t.get("team") or t.get("school")
            for c in t.get("categories", []):
                if c.get("name") != "passing":
                    continue
                for ty in c.get("types", []):
                    if ty.get("name") != "C/ATT":
                        continue
                    for ath in ty.get("athletes", []):
                        out.append({"game_id": gid, "team": team, "qb": str(ath.get("id") or ath.get("name")),
                                    "name": ath.get("name"), "att": _attempts(ath.get("stat"))})
    return pd.DataFrame(out)


def starter_out_games(year, games):
    """{(game_id, team): n-th consecutive game without the starter}"""
    P = passing_rows(year)
    if P is None or not len(P):
        return {}, 0
    start = games.set_index("id")["start"]
    P = P[P["game_id"].isin(start.index)].copy()
    P["start"] = P["game_id"].map(start)
    out = {}
    for team, tg in P.groupby("team"):
        order = tg.groupby("game_id")["start"].first().sort_values()
        seen = defaultdict(int)       # attempts by QB in earlier games
        starter, streak = None, 0
        for i, gid in enumerate(order.index):
            this = tg[tg["game_id"] == gid].groupby("qb")["att"].sum()
            tot = sum(seen.values())
            if i >= MIN_GAMES and tot:
                qb, att = max(seen.items(), key=lambda kv: kv[1])
                if att / tot >= MIN_SHARE:
                    if starter != qb:
                        starter, streak = qb, 0
                    if this.get(qb, 0) < OUT_ATTEMPTS:
                        streak += 1
                        out[(gid, team)] = streak
                    else:
                        streak = 0
            for qb, a in this.items():
                seen[qb] += a
    return out, P["game_id"].nunique()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", nargs="*", type=int, default=[2022, 2023, 2024, 2025, 2026])
    a = ap.parse_args()
    import tune
    df, _ = tune.evaluate({}, a.seasons, procs=2)
    rows, found = [], {}
    for y in a.seasons:
        d = tune.load(y)
        outs, n = starter_out_games(y, d["games"])
        found[y] = {"games_with_passing": n, "starter_out_team_games": len(outs)}
        for r in df[df["year"] == y].itertuples():
            hp = (r.pred_total + r.pred_margin) / 2
            ap_ = (r.pred_total - r.pred_margin) / 2
            hs, as_ = (r.total + r.margin) / 2, (r.total - r.margin) / 2
            # market-implied points (spread is from the home side, negative = home favored)
            mh = (r.mkt_total - r.mkt_spread) / 2 if not np.isnan(r.mkt_total) and not np.isnan(r.mkt_spread) else np.nan
            ma = (r.mkt_total + r.mkt_spread) / 2 if not np.isnan(mh) else np.nan
            for team, opp, tp, op, ts, os_, tm, om in ((r.home, r.away, hp, ap_, hs, as_, mh, ma),
                                                       (r.away, r.home, ap_, hp, as_, hs, ma, mh)):
                rows.append({"year": y, "wk": r.wk, "team": team, "opp": opp,
                             "out_streak": outs.get((r.game_id, team), 0),
                             "own_miss": ts - tp, "opp_miss": os_ - op,       # actual minus model
                             "own_mkt": ts - tm, "opp_mkt": os_ - om})        # actual minus closing line
    R = pd.DataFrame(rows)
    if not R["out_streak"].gt(0).any():
        print("No starter-out games found. Run the QB study workflow (it downloads passing stats first).")
        return

    def summ(x):
        if not len(x):
            return {"n": 0}
        m = x["own_miss"] - x["opp_miss"]
        t = x["own_miss"] + x["opp_miss"]
        mk = x.dropna(subset=["own_mkt"])
        return {"n": int(len(x)),
                "own_pts_vs_model": round(float(x["own_miss"].mean()), 2),
                "opp_pts_vs_model": round(float(x["opp_miss"].mean()), 2),
                "margin_vs_model": round(float(m.mean()), 2),
                "total_vs_model": round(float(t.mean()), 2),
                "own_pts_vs_line": round(float(mk["own_mkt"].mean()), 2) if len(mk) else None,
                "margin_vs_line": round(float((mk["own_mkt"] - mk["opp_mkt"]).mean()), 2) if len(mk) else None,
                "total_vs_line": round(float((mk["own_mkt"] + mk["opp_mkt"]).mean()), 2) if len(mk) else None}

    base = summ(R[R["out_streak"] == 0])
    out = {"seasons": a.seasons, "coverage": found, "baseline": base,
           "starter_out_all": summ(R[R["out_streak"] > 0]),
           "first_game_out": summ(R[R["out_streak"] == 1]),
           "games_2_3": summ(R[R["out_streak"].between(2, 3)]),
           "games_4_plus": summ(R[R["out_streak"] >= 4])}
    # Effect = starter-out games minus baseline; share = total effect / margin effect.
    first2 = R[R["out_streak"].between(1, 2)]
    s = summ(first2)
    if not s["n"]:
        print("No starter-out games in the first two games of an absence.")
        return
    eff_m = s["margin_vs_model"] - base["margin_vs_model"]
    eff_t = s["total_vs_model"] - base["total_vs_model"]
    out["first_two_games"] = s
    out["qb_out_spread_pts"] = round(eff_m, 2)
    out["qb_out_total_pts"] = round(eff_t, 2)
    out["suggested_ADJ_TOTAL_SHARE"] = round(float(np.clip(eff_t / eff_m, 0, 1.5)), 2) if eff_m < -0.5 else None
    out["by_year"] = {int(y): summ(x[x["out_streak"].between(1, 2)]) for y, x in R.groupby("year")}
    (ROOT / "docs" / "data").mkdir(parents=True, exist_ok=True)
    (ROOT / "docs" / "data" / "qb_study.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
