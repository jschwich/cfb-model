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


def _with_finished(data, games, bt):
    """Keep this week's finished games on the board with the model's pre-kickoff
    numbers and the result. Each run saves the projections of games that haven't
    started; once a game is final, that saved copy is what's shown."""
    import json
    import os
    from pathlib import Path
    if not len(games):
        return games
    wk = int(games["wk"].iloc[0])
    year = data["year"]
    store = Path(__file__).resolve().parent / "data" / "cache" / f"slate_{year}.json"
    fixture = bool(os.environ.get("CFBD_FIXTURE_DIR"))
    saved = {}
    if store.exists() and not fixture:
        saved = {r["game_id"]: r for r in json.loads(store.read_text())}
    # save projections for games that haven't kicked off yet
    now = pd.Timestamp.now(tz="UTC")
    for _, r in games.iterrows():
        if pd.isna(r["start"]) or r["start"] > now:
            row = {k: (None if (not isinstance(v, (list, dict)) and pd.isna(v)) else v) for k, v in r.items()}
            saved[r["game_id"]] = row
    if not fixture:
        keep = {k: v for k, v in saved.items() if int(v.get("wk", 0)) >= wk - 1}  # last two weeks is plenty
        store.write_text(json.dumps(list(keep.values()), default=str))

    g = data["games"]
    done = g[(g["wk"] == wk) & g["completed"] & (g["home"].isin(data["fbs"]) | g["away"].isin(data["fbs"]))]
    rows = []
    btw = bt[bt["wk"] == wk].set_index("game_id") if len(bt) else pd.DataFrame()
    for _, x in done.iterrows():
        if x["id"] in saved:
            r = dict(saved[x["id"]])
            r["start"] = pd.to_datetime(r["start"], utc=True)
            r["source"] = "saved before kickoff"
        elif len(btw) and x["id"] in btw.index:
            r = btw.loc[x["id"]].to_dict()
            r["game_id"] = x["id"]
            r["source"] = "rebuilt from games before this week"
        else:
            continue
        r["home_pts"], r["away_pts"] = x["home_pts"], x["away_pts"]
        rows.append(r)
    out = games.assign(final=False)
    if rows:
        fin = pd.DataFrame(rows).assign(final=True)
        for c in ("flags",):
            if c in fin:
                fin[c] = fin[c].map(lambda v: v if isinstance(v, list) else [])
        out = pd.concat([fin, out], ignore_index=True)
    out = out.sort_values("start").reset_index(drop=True)

    def result(r):
        if not r["final"]:
            return pd.Series({"spread_result": "", "total_result": ""})
        m = r["home_pts"] - r["away_pts"]
        sr = tr = ""
        if r.get("spread_pick") and not pd.isna(r.get("market_spread")):
            c = m + r["market_spread"]
            sr = "P" if c == 0 else ("W" if (c > 0) == (r["spread_pick"] == r["home"]) else "L")
        if r.get("total_pick") and not pd.isna(r.get("market_total")):
            t = r["home_pts"] + r["away_pts"]
            tr = "P" if t == r["market_total"] else ("W" if (t > r["market_total"]) == (r["total_pick"] == "Over") else "L")
        return pd.Series({"spread_result": sr, "total_result": tr})
    out[["spread_result", "total_result"]] = out.apply(result, axis=1)
    return out


def _forecast(data, wk):
    """Fetch the wind forecast for the coming week's games and keep it, so later
    grading uses what was known before kickoff. Skipped in test runs."""
    import json
    import os
    from pathlib import Path
    from cfbmodel import weather as W
    if os.environ.get("CFBD_FIXTURE_DIR") or not getattr(config, "WIND_PTS_PER_MPH", 0):
        return
    g = data["games"]
    slate = g[(g["wk"] == wk) & ~g["completed"] & (g["home"].isin(data["fbs"]) | g["away"].isin(data["fbs"]))]
    if not len(slate):
        return
    venues = W.venue_table(D._cached("venues_all", "/venues", {}, "venues"))
    wx = W.for_games(slate[["id", "start", "venue_id"]], venues, past=False)
    if not len(wx):
        print("  weather: no forecast this run")
        return
    f = Path(__file__).resolve().parent / "data" / "cache" / f"weather_{data['year']}.json"
    rows = json.loads(f.read_text()) if f.exists() else []
    keep = {r["id"]: r for r in rows}
    for gid, r in wx.iterrows():
        keep[gid] = {"id": gid, **{k: (None if pd.isna(v) else v) for k, v in r.to_dict().items()}}
    f.write_text(json.dumps(list(keep.values()), default=float))
    data["games"] = data["games"].drop(columns=[c for c in ["wind", "gust", "temp", "precip", "dome"]
                                                 if c in data["games"]]).join(
        pd.DataFrame(list(keep.values())).set_index("id"), on="id")
    windy = wx[wx["wind"] >= config.WIND_START_MPH]
    print(f"  weather: forecast for {len(wx)} games, {len(windy)} at {config.WIND_START_MPH}+ mph")


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
    _forecast(data, next_wk)
    games, label = upcoming(data, R, meta)
    games = A.tag(games)
    games = _with_finished(data, games, bt)
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
        live = games[~games["final"]] if "final" in games else games
        flagged = live[(live["spread_tier"] != "") | (live["total_tier"] != "")]
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
