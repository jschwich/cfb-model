"""Download game-time weather for past seasons into data/history/<year>/weather.json.gz.

Runs from the "Pull history" workflow (only = weather). Uses Open-Meteo's free
historical API: one request per week of games, about 20 per season.
"""
import argparse
import gzip
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cfbmodel import data as D, weather as W  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("years", nargs="+", type=int)
    a = ap.parse_args()
    venues = W.venue_table(D.read_fixture(ROOT / "data" / "history", "venues"))
    for y in a.years:
        folder = ROOT / "data" / "history" / str(y)
        os.environ["CFBD_FIXTURE_DIR"] = str(folder)
        d = D.load_all(y)
        g = d["games"]
        g = g[(g["home"].isin(d["fbs"]) | g["away"].isin(d["fbs"])) & g["completed"]]
        wx = W.for_games(g[["id", "start", "venue_id"]], venues, past=True)
        rows = wx.reset_index().to_dict("records")
        with gzip.open(folder / "weather.json.gz", "wt") as f:
            json.dump(rows, f, default=float)
        print(f"{y}: weather for {len(rows)} of {len(g)} games")


if __name__ == "__main__":
    main()
