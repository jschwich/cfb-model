"""Walk-forward test of model settings on past seasons (offline, no API calls).

For each season and each week, the model is rated using only games played
before that week, then graded on that week's games against the closing line.

    python tools/tune.py                 # current settings, all saved seasons
    python tools/tune.py --set RIDGE_ALPHA_PER_GAME=1.5 PRIOR_FADE_GAMES=3

Needs data/history/<year>/ (from the "Pull history" workflow).
"""
import argparse
import ast
import json
import os
import sys
import warnings
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

import config  # noqa: E402

SEASONS = [2022, 2023, 2024, 2025]
FIRST_WEEK = 3
_DATA = {}


def load(year):
    if year not in _DATA:
        from cfbmodel import data as D
        os.environ["CFBD_FIXTURE_DIR"] = str(ROOT / "data" / "history" / str(year))
        _DATA[year] = D.load_all(year)
    return _DATA[year]


def apply(overrides):
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(getattr(config, k, None), dict):
            getattr(config, k).update(v)
        else:
            setattr(config, k, v)


def season_rows(args):
    year, overrides = args
    apply(overrides)
    from cfbmodel.ratings import rate, lookup
    from cfbmodel.value import market_lines, project
    d = load(year)
    g = d["games"]
    fbs = d["fbs"]
    done = g[g["completed"] & g["home"].isin(fbs) & g["away"].isin(fbs)]
    M = market_lines(d["lines"]).set_index("game_id")
    out = []
    for wk in sorted(done["wk"].unique()):
        if wk < FIRST_WEEK:
            continue
        R, meta = rate(d, fei=None, through_week=wk)
        for _, r in done[done["wk"] == wk].iterrows():
            hp, ap = project(r, R, meta)
            gap = lookup(R, r["home"])["rating"] - lookup(R, r["away"])["rating"]
            mk = M.loc[r["id"]] if r["id"] in M.index else None
            out.append({"year": year, "wk": wk, "pred_margin": hp - ap, "pred_total": hp + ap, "gap": gap,
                        "hfa": (hp - ap) - gap,
                        "margin": r["home_pts"] - r["away_pts"], "total": r["home_pts"] + r["away_pts"],
                        "mkt_spread": mk["spread"] if mk is not None else np.nan,
                        "mkt_total": mk["total"] if mk is not None else np.nan})
    return out


def evaluate(overrides=None, seasons=SEASONS, procs=2):
    overrides = overrides or {}
    with Pool(procs) as p:
        parts = p.map(season_rows, [(y, overrides) for y in seasons])
    df = pd.DataFrame([r for part in parts for r in part])
    return df, score(df)


def score(df):
    l = df.dropna(subset=["mkt_spread"])
    t = df.dropna(subset=["mkt_total"])
    slope = float(np.polyfit(df["pred_margin"], df["margin"], 1)[0])
    edge = (l["pred_margin"] + l["mkt_spread"])          # >0: model likes home vs. the line
    cover = l["margin"] + l["mkt_spread"]
    pick = (edge.abs() >= config.SPREAD_LEAN) & (cover != 0)
    ats = float(((edge[pick] > 0) == (cover[pick] > 0)).mean()) if pick.any() else np.nan
    tedge = t["pred_total"] - t["mkt_total"]
    tpick = (tedge.abs() >= config.TOTAL_LEAN) & (t["total"] != t["mkt_total"])
    ou = float(((tedge[tpick] > 0) == (t["total"][tpick] > t["mkt_total"][tpick])).mean()) if tpick.any() else np.nan
    return {"games": len(df), "spread_mae": float((df["pred_margin"] - df["margin"]).abs().mean()),
            "spread_mae_lined": float((l["pred_margin"] - l["margin"]).abs().mean()),
            "market_mae": float((-l["mkt_spread"] - l["margin"]).abs().mean()),
            "total_mae": float((t["pred_total"] - t["total"]).abs().mean()),
            "market_total_mae": float((t["mkt_total"] - t["total"]).abs().mean()),
            "slope": slope, "lean_ats": ats, "lean_n": int(pick.sum()),
            "lean_ou": ou, "lean_ou_n": int(tpick.sum())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", nargs="*", default=[], help="NAME=value overrides")
    ap.add_argument("--seasons", nargs="*", type=int, default=SEASONS)
    a = ap.parse_args()
    ov = {}
    for kv in a.set:
        k, v = kv.split("=", 1)
        ov[k] = ast.literal_eval(v)
    _, s = evaluate(ov, a.seasons)
    print(json.dumps({k: round(v, 4) if isinstance(v, float) else v for k, v in s.items()}, indent=1))


if __name__ == "__main__":
    main()
