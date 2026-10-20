"""Run the whole model: pull data -> rate teams -> project games -> write dashboard.

Usage:  python run.py            (current season)
        python run.py --year 2025
"""
import argparse
import sys

import pandas as pd

import config
from cfbmodel import data as D
from cfbmodel.ratings import rate
from cfbmodel.value import upcoming, backtest, summarize_backtest, fit_scale, apply_scale
from cfbmodel import report
from cfbmodel import picks as K
from cfbmodel import adjust as A


def _snapshot(year, data, R):
    """Save this week's outside ratings (FEI, SP+, FPI) and the model's own, so
    future tuning can test their weights using only what was known at the time."""
    import os
    from pathlib import Path
    if os.environ.get("CFBD_FIXTURE_DIR"):
        return  # test runs on saved data shouldn't write snapshots
    g = data["games"]
    soon = g["start"] > pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=3)
    todo = g[~g["completed"] & soon]   # ignore old games that were never played
    wk = int(todo["wk"].min()) if len(todo) else int(g["wk"].max()) + 1
    out = Path(__file__).resolve().parent / "data" / "snapshots" / str(year)
    out.mkdir(parents=True, exist_ok=True)
    cols = [c for c in ["data_rating", "prior_rating", "fei_rating", "sp_rating", "fpi_rating", "rating"] if c in R]
    R.loc[R["fbs"], cols].round(2).to_csv(out / f"before_week_{wk:02d}.csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=config.YEAR)
    ap.add_argument("--demo", action="store_true", help="label the page as sample data")
    a = ap.parse_args()
    year = a.year or D.detect_year()

    print(f"Loading {year} data...")
    data = D.load_all(year)
    print(f"  {len(data['games'])} games, {len(data['lines'])} lines, "
          f"{len(data['adv'])} team-game stat rows, {data['api_calls']} API calls")
    fei = D.load_fei(year) if config.USE_FEI else None
    print(f"  FEI: {'loaded ' + str(len(fei)) + ' teams' if fei is not None else 'not used'}")

    R, meta = rate(data, fei=fei)
    _snapshot(year, data, R)
    bt = backtest(data)
    meta["scale"] = fit_scale(bt)
    R = apply_scale(R, meta["scale"])
    g_all = data["games"]
    todo = g_all[~g_all["completed"] & (g_all["start"] > pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=3))]
    next_wk = int(todo["wk"].min()) if len(todo) else int(g_all["wk"].max()) + 1
    R = A.apply(R, next_wk)
    games, label = upcoming(data, R, meta)
    games = A.tag(games)
    print(f"Rated {int(R['fbs'].sum())} FBS teams; home field {meta['hfa']:.1f}; "
          f"calibration x{meta['scale']:.2f}; next slate: {label} ({len(games)} games)")

    bt_sum = summarize_backtest(bt)
    if bt_sum is not None:
        bt_sum["my_adj"] = A.record(bt[bt["wk"] >= 1])
    if bt_sum:
        print(f"Backtest: {bt_sum['games']} games; model miss {bt_sum['model_mae']:.1f} "
              f"vs market {bt_sum['market_mae']:.1f}")

    info = {"updated": pd.Timestamp.now(tz="America/New_York").strftime("%b %-d, %-I:%M %p ET"),
            "year": year, "fei": fei is not None}
    pk = K.fetch()
    if pk is not None and len(pk):
        P = K.match(pk, data["games"])
        proj = pd.concat([bt, games], ignore_index=True) if len(bt) else games
        G = K.grade(P, proj.drop_duplicates("game_id", keep="last"))
        info["picks"] = (G, K.summarize(G))
        s = info["picks"][1]
        if s:
            g = s["groups"]
            print(f"Your picks: {g['All picks']['w']}-{g['All picks']['l']}; model agreed "
                  f"{g['Model agreed']['w']}-{g['Model agreed']['l']}, disagreed "
                  f"{g['Model disagreed']['w']}-{g['Model disagreed']['l']}")
    if a.demo:
        info["demo_banner"] = "<div class=demo>SAMPLE DATA: made-up teams and scores for testing, not real projections</div>"
    report.write(R, meta, games, label, bt, bt_sum, info)

    if len(games):
        flagged = games[(games["spread_tier"] != "") | (games["total_tier"] != "")]
        print(f"\nValue plays ({len(flagged)}):")
        for _, r in flagged.iterrows():
            if r["spread_tier"]:
                print(f"  [{r['spread_tier']:6}] {r['spread_pick_text']:<32} edge {r['spread_edge']:.1f}")
            if r["total_tier"]:
                print(f"  [{r['total_tier']:6}] {r['away']} @ {r['home']} {r['total_pick_text']}  edge {r['total_edge']:.1f}")
    print("\nDashboard written to docs/index.html")


def _report_errors():
    """On GitHub, show any failure as a readable message on the run's page."""
    import os, traceback
    try:
        return main()
    except SystemExit as e:
        if e.code not in (None, 0) and os.environ.get("GITHUB_ACTIONS"):
            print(f"::error title=Model stopped::{e.code}")
        raise
    except Exception as e:
        if os.environ.get("GITHUB_ACTIONS"):
            where = traceback.extract_tb(e.__traceback__)[-1]
            print(f"::error title=Model crashed::{type(e).__name__}: {e} "
                  f"(at {os.path.basename(where.filename)} line {where.lineno})")
        raise


if __name__ == "__main__":
    sys.exit(_report_errors())
