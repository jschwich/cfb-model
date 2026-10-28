"""Public betting splits and "sharp side" flags.

Every run reads ScoresAndOdds' consensus page, which shows for each game the
share of BETS (tickets) and the share of MONEY (handle) on each side of the
spread, total and moneyline. Those numbers are saved to
data/snapshots/<year>/splits.csv every run, so the track record can grade the
flags on finished games using only what was known before kickoff.

Two signals, both INFO ONLY (they never change the model's numbers):

  Reverse line move   A side has most of the bets, but the line has moved the
                      OTHER way since it opened. Books moved against the
                      crowd, which usually means respected money is on the
                      other side.
  Money vs tickets    One side has a minority of the bets but most of the
                      money: fewer, bigger bets are on that side.

Each flag names the side the signal points to (the "sharp side"). Thresholds
live in config.py under "Public betting".
"""
import difflib
import json
import os
import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd
import requests

import config

ROOT = Path(__file__).resolve().parent.parent
URL = "https://www.scoresandodds.com/ncaaf/consensus-picks"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
TYPES = ["Reverse line move", "Money vs tickets"]

# ScoresAndOdds spelling -> CollegeFootballData spelling, where they differ.
ALIASES = {"appalachian state": "app state", "miami fl": "miami", "miami oh": "miami oh",
           "louisiana monroe": "ul monroe", "ul lafayette": "louisiana", "louisiana lafayette": "louisiana",
           "southern mississippi": "southern miss", "hawaii": "hawai i", "massachusetts": "umass",
           "connecticut": "uconn", "central florida": "ucf", "texas san antonio": "utsa",
           "sam houston state": "sam houston", "fiu": "florida international"}


def _cfg(name, default):
    return getattr(config, name, default)


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return ALIASES.get(s, s)


# ---------------------------------------------------------------- fetch & parse

def parse(page):
    """One row per game and market: side a = away (or Over), side b = home (or Under)."""
    rows, seen = [], set()
    for card in page.split('<div class="trend-card consensus consensus-table-')[1:]:
        m = re.match(r"(moneyline|spread|total)--", card)
        ev = re.search(r'data-event="ncaaf/(\d+)"', card)
        names = re.findall(r'<span class="team-name">\s*<span>(.*?)</span>', card)[:2]
        when = re.search(r'data-value="([^"]+)"', card)
        pct = [int(x) for x in re.findall(r'class="percentage-[ab]" style="width:\s*(\d+)%', card)[:4]]
        if not (m and ev and len(names) == 2 and when and len(pct) == 4):
            continue
        key = (ev.group(1), m.group(1))
        if key in seen:
            continue
        seen.add(key)
        labels = [re.sub(r"<[^>]+>|\s+", " ", x).strip()
                  for x in re.findall(r"<strong>(.*?)</strong>", card, re.S)[:2]]
        line = np.nan
        if labels:
            x = re.search(r"\(([ou]?)([+-]?\d+(?:\.\d+)?)\)", labels[0])
            if x:
                line = float(x.group(2))
        rows.append({"event": key[0], "market": key[1], "away_name": names[0], "home_name": names[1],
                     "start": pd.Timestamp(when.group(1)), "line_a": line,
                     "bets_a": pct[0], "bets_b": pct[1], "money_a": pct[2], "money_b": pct[3]})
    return pd.DataFrame(rows)


def fetch():
    """Splits for every game on the page, or None if it can't be read."""
    r = requests.get(URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    df = parse(r.text)
    return df if len(df) else None


# ---------------------------------------------------------------- matching

def _team_names(year):
    """CFBD school -> set of normalized spellings (school, alternates, abbreviation)."""
    out = {}
    f = ROOT / "data" / "cache" / f"teams_{year}.json"
    try:
        raw = json.loads(f.read_text())
        raw = raw["rows"] if isinstance(raw, dict) else raw
        for t in raw:
            names = {t.get("school")} | set(t.get("alternateNames") or []) | {t.get("abbreviation")}
            out[t["school"]] = {_norm(n) for n in names if n}
    except Exception:
        pass
    return out


def _sim(sao, cfbd, known):
    a = _norm(sao)
    if a == _norm(cfbd) or a in known.get(cfbd, ()):
        return 1.0
    return difflib.SequenceMatcher(None, a, _norm(cfbd)).ratio()


def match(sp, games, year):
    """Attach CFBD game_id to each splits row. Sides are flipped when the page
    lists the teams the other way round (neutral sites)."""
    known = _team_names(year)
    pairs = {}
    for ev, grp in sp.groupby("event"):
        r = grp.iloc[0]
        near = games[(games["start"] - r["start"]).abs() <= pd.Timedelta(hours=18)] \
            if games["start"].notna().any() else games
        best = (0, None, False)
        for _, g in near.iterrows():
            s = _sim(r["away_name"], g["away"], known) + _sim(r["home_name"], g["home"], known)
            f = _sim(r["away_name"], g["home"], known) + _sim(r["home_name"], g["away"], known)
            if s > best[0]:
                best = (s, g["game_id"], False)
            if f > best[0]:
                best = (f, g["game_id"], True)
        if best[0] >= 1.6:
            pairs[ev] = best[1:]
    sp = sp[sp["event"].isin(pairs)].copy()
    sp["game_id"] = sp["event"].map(lambda e: pairs[e][0])
    flip = sp["event"].map(lambda e: pairs[e][1]) & (sp["market"] != "total")
    for a, b in (("bets_a", "bets_b"), ("money_a", "money_b")):
        sp.loc[flip, [a, b]] = sp.loc[flip, [b, a]].values
    sp.loc[flip, "line_a"] = -sp.loc[flip, "line_a"]
    return sp


# ---------------------------------------------------------------- flags

def signals(market, bets_a, bets_b, money_a, money_b, move):
    """Sharp-side signals for one market.

    market: "spread" (a = away, b = home) or "total" (a = Over, b = Under).
    move:   line now minus line at open. Spreads are the home team's number, so
            + means the line moved toward the away team; totals + means it went up.
    Returns a list of {type, side ("a"/"b"), text}.
    """
    out = []
    if any(pd.isna(x) for x in (bets_a, bets_b, money_a, money_b)):
        return out
    pub = _cfg("SHARP_PUBLIC_PCT", 65)
    gap = _cfg("SHARP_MONEY_GAP", 15)
    need = _cfg("SHARP_SPREAD_MOVE", 0.5) if market == "spread" else _cfg("SHARP_TOTAL_MOVE", 1.5)
    # How far the line moved toward side a (away team / Over), in points.
    # (spread: home's number going up = toward the away team; total going up = toward the Over)
    toward_a = np.nan if move is None or pd.isna(move) else move

    for side, bets, other in (("a", bets_a, "b"), ("b", bets_b, "a")):
        if bets < pub or pd.isna(toward_a):
            continue
        toward = toward_a if side == "a" else -toward_a   # + = moved toward the public side
        if toward <= -need:
            out.append({"type": "Reverse line move", "side": other,
                        "text": f"{bets:.0f}% of bets on {{{side}}}, but the line moved {abs(toward):g} toward {{{other}}}"})
    for side, bets, money in (("a", bets_a, money_a), ("b", bets_b, money_b)):
        # Fewer tickets but most of the money. A side with most of BOTH is
        # usually just big public bettors (VSiN 2022-25: sides with 75%+ of
        # the handle covered 47%), so that doesn't count.
        if money - bets >= gap and money > 50 and bets < 50:
            out.append({"type": "Money vs tickets", "side": side,
                        "text": f"{{{side}}} has {money:.0f}% of the money on {bets:.0f}% of bets"})
    return out


def _side_label(market, side, home, away, line_home):
    if market == "total":
        return "Over" if side == "a" else "Under"
    team = away if side == "a" else home
    if pd.isna(line_home):
        return team
    ln = -line_home if side == "a" else line_home
    return f"{team} {'PK' if abs(ln) < 0.25 else f'{ln:+g}'}"


def describe(r, market):
    """Flags for one game row (works on live games and saved snapshot rows).

    Returns (side_text, side_key, list of types, detail text); side_key is the
    team name or Over/Under the signals point to, or "" when none/conflicting.
    """
    pre = "sp" if market == "spread" else "tot"
    sig = signals(market, r.get(f"{pre}_bets_a"), r.get(f"{pre}_bets_b"),
                  r.get(f"{pre}_money_a"), r.get(f"{pre}_money_b"),
                  r.get("spread_move" if market == "spread" else "total_move"))
    if not sig:
        return "", "", [], ""
    sides = {s["side"] for s in sig}
    home, away = r["home"], r["away"]
    names = {"a": away, "b": home} if market == "spread" else {"a": "Over", "b": "Under"}
    detail = "; ".join(f"{s['type']}: " + s["text"].format(a=names["a"], b=names["b"]) for s in sig)
    if len(sides) > 1:
        return "Mixed signals", "", [s["type"] for s in sig], detail
    side = sides.pop()
    line = r.get("market_spread") if market == "spread" else r.get("market_total")
    label = _side_label(market, side, home, away, line)
    if market == "total" and not pd.isna(line):
        label += f" {line:g}"
    key = names[side]
    return label, key, [s["type"] for s in sig], detail


# ---------------------------------------------------------------- live run

COLS = ["sp_bets_a", "sp_bets_b", "sp_money_a", "sp_money_b",
        "tot_bets_a", "tot_bets_b", "tot_money_a", "tot_money_b",
        "ml_bets_a", "ml_bets_b", "ml_money_a", "ml_money_b"]


def attach(games, year):
    """Add splits columns and sharp-side flags to the upcoming slate, and save a snapshot."""
    games = games.copy()
    for c in COLS:
        games[c] = np.nan
    for m in ("spread", "total"):
        for c in ("side", "key", "types", "text", "agree"):
            games[f"sharp_{m}_{c}"] = ""
    if not _cfg("USE_SPLITS", True) or not len(games):
        return games, "off"
    fixture = bool(os.environ.get("CFBD_FIXTURE_DIR"))
    try:
        sp = None if fixture else fetch()
    except Exception as e:
        print(f"  warning: betting splits unavailable ({type(e).__name__}); running without them")
        sp = None
    if sp is None:
        return games, "unavailable"
    live = games[~games.get("final", pd.Series(False, index=games.index)).astype(bool)]
    sp = match(sp, live, year)
    pre = {"spread": "sp", "total": "tot", "moneyline": "ml"}
    idx = {gid: i for i, gid in zip(games.index, games["game_id"])}
    for _, s in sp.iterrows():
        i = idx.get(s["game_id"])
        if i is None:
            continue
        p = pre[s["market"]]
        for k in ("bets_a", "bets_b", "money_a", "money_b"):
            games.at[i, f"{p}_{k}"] = s[k]
    for i, r in games.iterrows():
        if pd.isna(r["sp_bets_a"]) and pd.isna(r["tot_bets_a"]):
            continue
        for m in ("spread", "total"):
            label, key, types, detail = describe(r, m)
            games.at[i, f"sharp_{m}_side"] = label
            games.at[i, f"sharp_{m}_key"] = key
            games.at[i, f"sharp_{m}_types"] = ", ".join(types)
            games.at[i, f"sharp_{m}_text"] = detail
            pick = r.get("spread_pick") if m == "spread" else r.get("total_pick")
            if key and pick:
                games.at[i, f"sharp_{m}_agree"] = "yes" if key == pick else "no"
    n = int(games["sp_bets_a"].notna().sum())
    if not fixture:
        _save(games, year)
    print(f"  splits: {n} games matched, {int((games['sharp_spread_key'] != '').sum())} spread and "
          f"{int((games['sharp_total_key'] != '').sum())} total sharp-side flags")
    return games, f"{n} games"


SNAP_COLS = ["taken", "game_id", "wk", "start", "home", "away", "market_spread", "open_spread",
             "spread_move", "market_total", "open_total", "total_move", "spread_pick", "spread_tier",
             "total_pick", "total_tier"] + COLS


def _save(games, year):
    f = ROOT / "data" / "snapshots" / str(year) / "splits.csv"
    f.parent.mkdir(parents=True, exist_ok=True)
    live = games[games["sp_bets_a"].notna() | games["tot_bets_a"].notna()]
    if "final" in live:
        live = live[~live["final"].astype(bool)]
    if not len(live):
        return
    snap = live.assign(taken=pd.Timestamp.now(tz="UTC").floor("min"))
    snap = snap[[c for c in SNAP_COLS if c in snap]]
    snap.to_csv(f, mode="a", header=not f.exists(), index=False)


# ---------------------------------------------------------------- grading

def graded(data, year):
    """Each finished game's last pre-kickoff snapshot, with how every sharp
    flag and model value pick did against the line at that moment."""
    f = ROOT / "data" / "snapshots" / str(year) / "splits.csv"
    if not f.exists():
        return pd.DataFrame()
    s = pd.read_csv(f)
    for c in ("taken", "start"):
        s[c] = pd.to_datetime(s[c], utc=True, errors="coerce")
    s = s[s["taken"] < s["start"]].sort_values("taken").groupby("game_id").tail(1)
    g = data["games"]
    fin = g[g["completed"]].set_index("id")[["home_pts", "away_pts"]]
    s = s[s["game_id"].isin(fin.index)].join(fin, on="game_id")
    rows = []
    for _, r in s.iterrows():
        margin = r["home_pts"] - r["away_pts"]
        total = r["home_pts"] + r["away_pts"]
        for m in ("spread", "total"):
            line = r["market_spread"] if m == "spread" else r["market_total"]
            if pd.isna(line):
                continue
            label, key, types, _ = describe(r, m)
            if m == "spread":
                c = margin + line                     # > 0: home covered
                res = {r["home"]: np.sign(c), r["away"]: -np.sign(c)}
            else:
                c = total - line
                res = {"Over": np.sign(c), "Under": -np.sign(c)}
            pick = r["spread_pick"] if m == "spread" else r["total_pick"]
            tier = r["spread_tier"] if m == "spread" else r["total_tier"]
            pick = pick if isinstance(pick, str) else ""
            tier = tier if isinstance(tier, str) else ""
            rows.append({"game_id": r["game_id"], "wk": r["wk"], "market": m, "key": key,
                         "types": types, "sharp_res": res.get(key, np.nan) if key else np.nan,
                         "pick": pick, "tier": tier,
                         "pick_res": res.get(pick, np.nan) if pick else np.nan})
    return pd.DataFrame(rows)


def record(data, year):
    """Win-loss by signal type, and the model's value picks split by whether
    the sharp signals agreed."""
    G = graded(data, year)
    if not len(G):
        return None

    def wl(x):
        x = x.dropna()
        x = x[x != 0]
        return {"w": int((x > 0).sum()), "l": int((x < 0).sum())}
    by_type = []
    for m in ("spread", "total"):
        gm = G[G["market"] == m]
        for t in TYPES:
            sel = gm[gm["types"].map(lambda ts: t in ts)]
            by_type.append({"market": m, "type": t, **wl(sel["sharp_res"])})
        by_type.append({"market": m, "type": "Any (one clear side)", **wl(gm["sharp_res"])})
    val = G[G["tier"] != ""]
    agree = val[(val["key"] != "") & (val["key"] == val["pick"])]
    against = val[(val["key"] != "") & (val["key"] != val["pick"])]
    none = val[val["key"] == ""]
    return {"games": int(G["game_id"].nunique()), "by_type": by_type,
            "model": {"Sharps agreed": wl(agree["pick_res"]), "Sharps on the other side": wl(against["pick_res"]),
                      "No sharp signal": wl(none["pick_res"])}}
