"""Download past seasons from CollegeFootballData into data/history/<year>/.

Run from the "Pull history" workflow on GitHub (it has the API key).
About 9 API calls per season. The saved files let the model be tuned and
tested offline:  CFBD_FIXTURE_DIR=data/history/2024 python run.py --year 2024
"""
import argparse
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cfbmodel import data as D  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def save(folder, name, obj):
    folder.mkdir(parents=True, exist_ok=True)
    with gzip.open(folder / f"{name}.json.gz", "wt") as f:
        json.dump(obj, f, separators=(",", ":"))
    print(f"  {name}: {len(obj)} rows")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("years", nargs="+", type=int)
    a = ap.parse_args()
    for y in a.years:
        print(f"{y}:")
        out = ROOT / "data" / "history" / str(y)
        get = lambda path, params: D._get(path, params, "unused")  # noqa: E731
        save(out, "games", get("/games", {"year": y, "seasonType": "both"}))
        save(out, "lines", get("/lines", {"year": y, "seasonType": "both"}))
        save(out, "adv", get("/stats/game/advanced", {"year": y, "excludeGarbageTime": "true"}))
        save(out, "teams", get("/teams/fbs", {"year": y}))
        save(out, "sp", get("/ratings/sp", {"year": y - 1}))        # prior: last year's SP+
        save(out, "talent", get("/talent", {"year": y}))
        save(out, "returning", get("/player/returning", {"year": y}))
        save(out, "sp_final", get("/ratings/sp", {"year": y}))      # reference only (hindsight)
        save(out, "fpi_final", get("/ratings/fpi", {"year": y}))    # reference only (hindsight)
    if not (ROOT / "data" / "history" / "venues.json.gz").exists():
        save(ROOT / "data" / "history", "venues", D._get("/venues", {}, "unused"))
    print(f"API calls: {D._calls}")


if __name__ == "__main__":
    main()
