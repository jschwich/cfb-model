"""Game-time weather from Open-Meteo (free, no key).

Wind is what matters for totals: strong wind cuts passing and kicking. For each
game we take the stadium's coordinates and average the wind over the first three
hours from kickoff. Domes are skipped (no weather inside).

Past games:     archive-api.open-meteo.com (used by tools/fetch_weather.py)
Upcoming games: api.open-meteo.com forecast (up to 16 days out)
"""
import time

import numpy as np
import pandas as pd
import requests

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"
HOURLY = "wind_speed_10m,wind_gusts_10m,temperature_2m,precipitation"


def venue_table(venues_raw):
    rows = []
    for v in venues_raw or []:
        lat, lon = v.get("latitude"), v.get("longitude")
        if lat is None or lon is None:
            loc = v.get("location") or {}
            lat, lon = loc.get("y"), loc.get("x")
        rows.append({"venue_id": v.get("id"), "lat": lat, "lon": lon, "dome": bool(v.get("dome"))})
    return pd.DataFrame(rows).dropna(subset=["lat", "lon"]).drop_duplicates("venue_id").set_index("venue_id")


def _request(url, games, extra, tries=6):
    """One multi-location call; returns {game_id: dict of game-window averages}."""
    params = {"latitude": ",".join(f"{x:.4f}" for x in games["lat"]),
              "longitude": ",".join(f"{x:.4f}" for x in games["lon"]),
              "hourly": HOURLY, "wind_speed_unit": "mph", "temperature_unit": "fahrenheit",
              "precipitation_unit": "inch", "timezone": "GMT", **extra}
    for i in range(tries):
        r = requests.get(url, params=params, timeout=90)
        if r.status_code == 429:
            print(f"    rate limited; waiting {65 * (i + 1)}s", flush=True)
            time.sleep(65 * (i + 1))
            continue
        r.raise_for_status()
        break
    else:
        r.raise_for_status()
    js = r.json()
    js = js if isinstance(js, list) else [js]
    out = {}
    for (gid, start), loc in zip(games[["id", "start"]].itertuples(index=False), js):
        h = loc.get("hourly") or {}
        if not h.get("time"):
            continue
        t = pd.to_datetime(h["time"], utc=True)
        k0 = start.floor("h")
        win = (t >= k0) & (t < k0 + pd.Timedelta(hours=3))
        if not win.any():
            continue

        def avg(name):
            vals = np.array(h.get(name) or [np.nan] * len(t), dtype=float)[win]
            return float(np.nanmean(vals)) if np.isfinite(vals).any() else np.nan
        out[gid] = {"wind": avg("wind_speed_10m"), "gust": avg("wind_gusts_10m"),
                    "temp": avg("temperature_2m"),
                    "precip": float(np.nansum(np.array(h.get("precipitation") or [0] * len(t), dtype=float)[win]))}
    return out


def for_games(games, venues, past, chunk=40, pause=12.0):
    """Weather for games (DataFrame with id, start, venue_id). Domes get wind 0."""
    g = games.dropna(subset=["start"]).copy()
    g = g.join(venues, on="venue_id")
    dome = g["dome"].fillna(False).astype(bool)
    res = {gid: {"wind": 0.0, "gust": 0.0, "temp": np.nan, "precip": 0.0, "dome": True}
           for gid in g.loc[dome, "id"]}
    todo = g[~dome].dropna(subset=["lat", "lon"])
    if past:
        # one call per week of games: a short date range keeps the request light
        todo = todo.assign(day=todo["start"].dt.floor("D"))
        groups = [grp for _, grp in todo.groupby(todo["start"].dt.isocalendar().week.astype(int)
                                                + 100 * todo["start"].dt.year)]
    else:
        groups = [todo]
    for grp in groups:
        for i in range(0, len(grp), chunk):
            part = grp.iloc[i:i + chunk]
            if past:
                extra = {"start_date": part["start"].min().strftime("%Y-%m-%d"),
                         "end_date": (part["start"].max() + pd.Timedelta(hours=4)).strftime("%Y-%m-%d")}
                url = ARCHIVE
            else:
                extra = {"forecast_days": 16}
                url = FORECAST
            try:
                res.update(_request(url, part, extra))
            except Exception as e:
                print(f"  warning: weather request failed ({type(e).__name__}: {e})")
            if past:
                time.sleep(pause)
    df = pd.DataFrame.from_dict(res, orient="index")
    if len(df):
        df.index.name = "id"
        df["dome"] = df.get("dome", False)
        df["dome"] = df["dome"].fillna(False).astype(bool)
    return df
