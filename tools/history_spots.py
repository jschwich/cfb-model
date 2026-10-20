"""Grade the situational spots on past seasons and save data/history/spots.json
(shown on the dashboard next to this season). Run offline after tuning:
    python tools/history_spots.py
"""
import os, sys, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from multiprocessing import Pool
import pandas as pd

def one(y):
    os.environ["CFBD_FIXTURE_DIR"] = str(ROOT / "data" / "history" / str(y))
    from cfbmodel import data as D
    from cfbmodel.value import backtest
    import config
    d = D.load_all(y); bt = backtest(d)
    bt = bt[bt["wk"] >= config.BACKTEST_FIRST_WEEK]
    rows = []
    for _, r in bt.dropna(subset=["market_spread"]).iterrows():
        cover = r["actual_margin"] + r["market_spread"]
        if cover == 0: continue
        for f in r["flags"]:
            team_cov = (cover > 0) == (f["team"] == r["home"])
            against = f["type"] != "Rest edge"
            rows.append({"year": y, "type": f["type"], "worked": float(team_cov != against),
                         "conflict_pick": r["spread_pick"] != "" and f["team"] == r["spread_pick"] if against else False})
    # value picks: ATS with and without a conflicting spot
    vp = bt[bt["spread_tier"] != ""].dropna(subset=["ats_win"])
    vrows = [{"year": y, "conflict": bool(r["flag_conflict"]), "win": r["ats_win"]} for _, r in vp.iterrows()]
    return rows, vrows

if __name__ != "__main__":
    raise SystemExit
YEARS = [2022, 2023, 2024, 2025]
with Pool(2) as p:
    res = p.map(one, YEARS)
df = pd.DataFrame([r for a, _ in res for r in a]); v = pd.DataFrame([r for _, b in res for r in b])
g = df.groupby("type")["worked"].agg(["mean", "count"]); g["w"] = (g["mean"] * g["count"]).round().astype(int); g["l"] = g["count"] - g["w"]
print(g.round(3).to_string())
print(df.groupby(["type", "year"])["worked"].mean().unstack().round(2).to_string())
print("value picks:", v.groupby("conflict")["win"].agg(["mean", "count"]).round(3).to_string())

import json
out = {"years": f"{YEARS[0]}–{YEARS[-1]}",
       "spots": {t: {"w": int(r["w"]), "l": int(r["l"])} for t, r in g.iterrows()},
       "conflict": {str(k): {"w": int(round(r["mean"] * r["count"])), "n": int(r["count"])}
                    for k, r in v.groupby("conflict")["win"].agg(["mean", "count"]).iterrows()}}
(ROOT / "data" / "history" / "spots.json").write_text(json.dumps(out, indent=1))
print("saved data/history/spots.json")
