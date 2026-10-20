"""Pulls data from CollegeFootballData (CFBD) and FEI (bcftoys.com).

Every run fetches 3 things that change during the season: games, betting
lines, and per-game advanced stats. Slower-moving inputs (last year's SP+,
team talent, returning production, team and stadium locations) are saved in
data/cache/ the first time and reused, so a normal run uses about 3 API calls.

For testing without the internet, set CFBD_FIXTURE_DIR to a folder of JSON
files named like the endpoints (games.json, lines.json, ...).
"""
import datetime as dt
import json
import os
import re
from pathlib import Path

import pandas as pd
import requests

API = "https://api.collegefootballdata.com"
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"

_calls = 0


def detect_year(today=None):
    """College football seasons start in late August and end in January."""
    today = today or dt.date.today()
    return today.year if today.month >= 8 else today.year - 1


def _get(path, params, fixture_name):
    """One CFBD request (or fixture file read)."""
    global _calls
    fx = os.environ.get("CFBD_FIXTURE_DIR")
    if fx:
        f = Path(fx) / f"{fixture_name}.json"
        return json.loads(f.read_text()) if f.exists() else []
    key = os.environ.get("CFBD_API_KEY", "").strip()
    if not key:
        raise SystemExit("CFBD_API_KEY is not set. Add it as a repository secret (see README step 3).")
    _calls += 1
    r = requests.get(API + path, params=params, timeout=60,
                     headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    if r.status_code == 401:
        raise SystemExit("CFBD rejected the API key (401). Check the CFBD_API_KEY secret.")
    r.raise_for_status()
    return r.json()


def _cached(name, path, params, fixture_name=None):
    """Fetch once and keep in data/cache/; reuse on later runs."""
    f = CACHE / f"{name}.json"
    if f.exists() and not os.environ.get("CFBD_FIXTURE_DIR"):
        return json.loads(f.read_text())
    try:
        out = _get(path, params, fixture_name or name.rsplit("_", 1)[0])
    except SystemExit:
        raise
    except Exception as e:  # a missing optional input shouldn't stop the model
        print(f"  warning: could not load {name}: {e}")
        return []
    if out and not os.environ.get("CFBD_FIXTURE_DIR"):
        CACHE.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(out))
    return out


def _pick(d, *keys, default=None):
    """CFBD v2 uses camelCase, v1 used snake_case. Accept either."""
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return default


def _games(raw):
    rows = []
    for g in raw:
        stype = _pick(g, "seasonType", "season_type", default="regular")
        rows.append({
            "id": _pick(g, "id"),
            "week": int(_pick(g, "week", default=0)),
            "season_type": stype,
            "start": pd.to_datetime(_pick(g, "startDate", "start_date"), utc=True, errors="coerce"),
            "completed": bool(_pick(g, "completed", default=False)),
            "neutral": bool(_pick(g, "neutralSite", "neutral_site", default=False)),
            "conf_game": bool(_pick(g, "conferenceGame", "conference_game", default=False)),
            "venue_id": _pick(g, "venueId", "venue_id"),
            "home": _pick(g, "homeTeam", "home_team"),
            "away": _pick(g, "awayTeam", "away_team"),
            "home_class": (_pick(g, "homeClassification", "home_division", default="") or "").lower(),
            "away_class": (_pick(g, "awayClassification", "away_division", default="") or "").lower(),
            "home_conf": _pick(g, "homeConference", "home_conference", default=""),
            "away_conf": _pick(g, "awayConference", "away_conference", default=""),
            "home_pts": _pick(g, "homePoints", "home_points"),
            "away_pts": _pick(g, "awayPoints", "away_points"),
            "home_ls": _pick(g, "homeLineScores", "home_line_scores"),
            "away_ls": _pick(g, "awayLineScores", "away_line_scores"),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df[df["season_type"].isin(["regular", "postseason"])].copy()
    df["home_pts"] = pd.to_numeric(df["home_pts"], errors="coerce")
    df["away_pts"] = pd.to_numeric(df["away_pts"], errors="coerce")
    df["completed"] = df["completed"] & df["home_pts"].notna() & df["away_pts"].notna()
    # One running week number: bowls/playoffs come after the last regular week.
    last_reg = int(df.loc[df["season_type"] == "regular", "week"].max() or 0)
    df["wk"] = df["week"].where(df["season_type"] == "regular", last_reg + df["week"])
    return df.sort_values(["start", "id"]).reset_index(drop=True)


def _lines(raw):
    rows = []
    for g in raw:
        for ln in _pick(g, "lines", default=[]) or []:
            rows.append({
                "game_id": _pick(g, "id"),
                "provider": _pick(ln, "provider", default=""),
                "spread": pd.to_numeric(_pick(ln, "spread"), errors="coerce"),
                "spread_open": pd.to_numeric(_pick(ln, "spreadOpen", "spread_open"), errors="coerce"),
                "total": pd.to_numeric(_pick(ln, "overUnder", "over_under"), errors="coerce"),
            })
    return pd.DataFrame(rows, columns=["game_id", "provider", "spread", "spread_open", "total"])


def _adv(raw):
    rows = []
    for g in raw:
        off = _pick(g, "offense", default={}) or {}
        rows.append({
            "game_id": _pick(g, "gameId", "game_id"),
            "team": _pick(g, "team"),
            "opponent": _pick(g, "opponent"),
            "plays": pd.to_numeric(_pick(off, "plays"), errors="coerce"),
            "ppa": pd.to_numeric(_pick(off, "ppa"), errors="coerce"),
            "sr": pd.to_numeric(_pick(off, "successRate", "success_rate"), errors="coerce"),
        })
    df = pd.DataFrame(rows, columns=["game_id", "team", "opponent", "plays", "ppa", "sr"])
    return df.dropna(subset=["plays", "ppa", "sr"]).query("plays > 0")


def _locations(teams_raw, venues_raw):
    teams = {}
    for t in teams_raw:
        loc = _pick(t, "location", default={}) or {}
        teams[_pick(t, "school")] = {
            "conference": _pick(t, "conference", default=""),
            "lat": _pick(loc, "latitude", default=None) or _pick(_pick(loc, "location", default={}), "y"),
            "lon": _pick(loc, "longitude", default=None) or _pick(_pick(loc, "location", default={}), "x"),
            "tz": _pick(loc, "timezone"),
            "venue_id": _pick(loc, "id", "venueId", "venue_id"),
        }
    venues = {}
    for v in venues_raw:
        venues[_pick(v, "id")] = {
            "lat": _pick(v, "latitude", default=None) or _pick(_pick(v, "location", default={}), "y"),
            "lon": _pick(v, "longitude", default=None) or _pick(_pick(v, "location", default={}), "x"),
            "tz": _pick(v, "timezone"),
        }
    return teams, venues


def load_all(year):
    global _calls
    _calls = 0
    games = _games(_get("/games", {"year": year, "seasonType": "both"}, "games"))
    lines = _lines(_get("/lines", {"year": year, "seasonType": "both"}, "lines"))
    adv = _adv(_get("/stats/game/advanced", {"year": year, "excludeGarbageTime": "true"}, "adv"))

    teams_raw = _cached(f"teams_{year}", "/teams/fbs", {"year": year}, "teams")
    venues_raw = _cached("venues_all", "/venues", {}, "venues")
    sp_prev = _cached(f"sp_{year - 1}", "/ratings/sp", {"year": year - 1}, "sp")
    talent = _cached(f"talent_{year}", "/talent", {"year": year}, "talent")
    returning = _cached(f"returning_{year}", "/player/returning", {"year": year}, "returning")

    teams, venues = _locations(teams_raw, venues_raw)
    fbs = set(teams) or set(games.loc[games["home_class"] == "fbs", "home"]) | \
        set(games.loc[games["away_class"] == "fbs", "away"])

    sp = pd.DataFrame([{
        "team": _pick(s, "team"),
        "sp": pd.to_numeric(_pick(s, "rating"), errors="coerce"),
        "sp_off": pd.to_numeric(_pick(_pick(s, "offense", default={}) or {}, "rating"), errors="coerce"),
        "sp_def": pd.to_numeric(_pick(_pick(s, "defense", default={}) or {}, "rating"), errors="coerce"),
    } for s in sp_prev if _pick(s, "team") not in (None, "nationalAverages")])
    tal = pd.DataFrame([{"team": _pick(t, "team", "school"),
                         "talent": pd.to_numeric(_pick(t, "talent"), errors="coerce")} for t in talent])
    ret = pd.DataFrame([{"team": _pick(t, "team"),
                         "ret_ppa": pd.to_numeric(_pick(t, "percentPPA", "percent_ppa"), errors="coerce")}
                        for t in returning])
    return {"year": year, "games": games, "lines": lines, "adv": adv, "fbs": fbs,
            "teams": teams, "venues": venues, "sp": sp, "talent": tal, "returning": ret,
            "api_calls": _calls}


_ALIASES = {"miamifl": "miami", "appstate": "appalachianstate", "floridainternational": "fiu",
            "massachusetts": "umass", "northcarolinastate": "ncstate", "samhoustonstate": "samhouston",
            "southernmississippi": "southernmiss", "connecticut": "uconn",
            "louisianamonroe": "ulmonroe", "ulm": "ulmonroe", "louisianalafayette": "louisiana",
            "ullafayette": "louisiana", "texassanantonio": "utsa", "centralflorida": "ucf",
            "brighamyoung": "byu", "southerncalifornia": "usc", "louisianastate": "lsu",
            "texaschristian": "tcu", "southernmethodist": "smu", "texaselpaso": "utep",
            "nevadalasvegas": "unlv", "alabamabirmingham": "uab", "mississippi": "olemiss",
            "pittsburgh": "pitt", "miamiohio": "miamioh", "kennesaw": "kennesawstate",
            "middletennesseestate": "middletennessee", "westernkentuckyu": "westernkentucky"}


def norm_name(s):
    """One spelling per school, so CFBD and FEI names line up."""
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = s.replace("&", "and")
    s = re.sub(r"\bst\.?$", "state", s.strip())
    k = re.sub(r"[^a-z0-9]", "", s)
    return _ALIASES.get(k, k)


def _warn(msg):
    print(f"  warning: {msg}")
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::warning title=Model note::{msg}")


def _parse_fei(page):
    """bcftoys splits the ratings across several tables, and the header markup
    varies. Read each row directly: Rk | Team | Rec | FBS rec | FEI | ..."""
    import html as _html
    rec = re.compile(r"^\d{1,2}-\d{1,2}$")
    num = re.compile(r"^-?\d*\.\d+$|^-?\d+$")
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.S | re.I):
        cells = [_html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.S | re.I)]
        cells = [c for c in cells if c != ""]  # drop blank spacer columns
        if len(cells) >= 5 and cells[0].isdigit() and rec.match(cells[2]) and num.match(cells[4]):
            rows.append({"team": cells[1], "fei": cells[4]})
    if len(rows) < 50:
        return None
    return pd.DataFrame(rows).drop_duplicates("team")


def load_fei(year, fbs=None):
    """FEI team ratings scraped from bcftoys.com. Returns None if unreachable."""
    fx = os.environ.get("CFBD_FIXTURE_DIR")
    try:
        if fx:
            f = Path(fx) / "fei.json"
            if not f.exists():
                return None
            df = pd.DataFrame(json.loads(f.read_text()))
        else:
            r = requests.get(f"https://www.bcftoys.com/{year}-fei", timeout=30,
                             headers={"User-Agent": "Mozilla/5.0 (cfb-model)"})
            r.raise_for_status()
            df = _parse_fei(r.text)
            if df is None:
                _warn(f"FEI table not found on bcftoys.com (page {len(r.text):,} chars, "
                      f"{len(re.findall('<tr', r.text, flags=re.I))} rows, "
                      f"title {re.search('<title>(.*?)</title>', r.text, re.S|re.I).group(1).strip()[:60] if re.search('<title>', r.text, re.I) else 'none'!r}); running without it")
                return None
        df["fei"] = pd.to_numeric(df["fei"], errors="coerce")
        df = df.dropna(subset=["fei"])
        df["key"] = df["team"].map(norm_name)
        if len(df) <= 50 and not fx:
            _warn(f"FEI page only had {len(df)} teams; running without it")
            return None
        return df[["team", "key", "fei"]].reset_index(drop=True)
    except Exception as e:
        _warn(f"FEI unavailable ({type(e).__name__}: {e}); running without it")
        return None


def api_calls():
    return _calls
