"""Grade your own picks (from the published Pick Log) against the model.

For each pick, the model's view is what it projected BEFORE that week's games,
so "agree" means the model would have liked the same side at your number.
Reads only the rows for config.PICKS_PERSON.
"""
import io
import os
import re

import numpy as np
import pandas as pd
import requests

import config
from .data import norm_name

_PERIOD = re.compile(r"^\s*(1Q|2Q|3Q|4Q|1H|2H)\s+", re.I)
_SPREAD = re.compile(r"\(\s*([+-]?\d+(?:\.\d+)?|PK|pk)\s*\)")
_OU = re.compile(r"\b([ou])\s*(\d+(?:\.\d+)?)\s*$", re.I)
_EXTRA = {"cal": "california", "usf": "southflorida", "ulm": "ulmonroe", "smu": "smu",
          "olddominion": "olddominion", "fau": "floridaatlantic", "wku": "westernkentucky",
          "niu": "northernillinois", "ecu": "eastcarolina", "utsa": "utsa", "uconn": "uconn"}


def _key(name):
    k = norm_name(name)
    return _EXTRA.get(k, k)


def fetch(url=None):
    """The Pick Log as a DataFrame of this person's picks."""
    path = os.environ.get("PICKS_CSV")
    url = url or getattr(config, "PICKS_CSV_URL", "")
    try:
        if path:
            text = open(path, encoding="utf-8").read()
        elif url:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            r.encoding = "utf-8"
            text = r.text
        else:
            return None
    except Exception as e:
        print(f"  warning: could not read picks ({type(e).__name__}: {e})")
        return None
    raw = pd.read_csv(io.StringIO(text), header=None, dtype=str, keep_default_na=False)
    hdr = next((i for i in range(min(len(raw), 15)) if "Person" in list(raw.iloc[i])), None)
    if hdr is None:
        print("  warning: picks sheet has no 'Person' header row")
        return None
    cols = [str(c).strip() for c in raw.iloc[hdr]]
    df = raw.iloc[hdr + 1:].copy()
    df.columns = cols
    pick_col = next(c for c in cols if c.lower().startswith("pick"))
    df = df.rename(columns={pick_col: "Pick"})
    me = getattr(config, "PICKS_PERSON", "Jerome")
    df = df[(df["Person"].str.strip() == me) & (df["Pick"].str.strip() != "")]
    keep = [c for c in ["Week", "Pick", "Result"] if c in df]
    return df[keep].reset_index(drop=True)


def parse(text):
    """'1H Ohio State (-7.5) vs. Iowa' -> dict(period, market, team, opp, line, side)."""
    t = text.strip()
    m = _PERIOD.match(t)
    period = m.group(1).upper() if m else "Game"
    t = t[m.end():] if m else t
    out = {"period": period, "market": None, "team": None, "opp": None, "line": None, "side": None}
    ou = _OU.search(t)
    if ou:
        out["side"] = "Over" if ou.group(1).lower() == "o" else "Under"
        out["line"] = float(ou.group(2))
        t = t[:ou.start()].strip()
    parts = [p.strip() for p in re.split(r"\s+(?:vs\.?|@|at)\s+", t, flags=re.I) if p.strip()]
    if " TT" in f" {t} " or t.endswith("TT") or re.search(r"\bTT\b", t):
        out["market"] = "Team total"
        tt = [p for p in parts if re.search(r"\bTT\b", p)]
        out["team"] = re.sub(r"\bTT\b", "", tt[0] if tt else parts[0]).strip()
        others = [re.sub(r"\bTT\b", "", p).strip() for p in parts if p not in tt]
        out["opp"] = others[0] if others else None
        return out if out["line"] is not None else None
    if ou:
        out["market"] = "Total"
        out["team"] = parts[0] if parts else None
        out["opp"] = parts[1] if len(parts) > 1 else None
        return out
    for i, p in enumerate(parts):
        s = _SPREAD.search(p)
        if s:
            v = s.group(1)
            out["market"] = "Spread"
            out["line"] = 0.0 if v.lower() == "pk" else float(v)
            out["team"] = _SPREAD.sub("", p).strip()
            rest = [q for j, q in enumerate(parts) if j != i]
            out["opp"] = _SPREAD.sub("", rest[0]).strip() if rest else None
            return out
    return None


def _week_num(w):
    m = re.search(r"(\d+)", str(w))
    return int(m.group(1)) if m else None


def match(picks, games):
    """Attach a CFBD game id to each pick (the sheet's week numbering may be offset)."""
    g = games.assign(hk=games["home"].map(_key), ak=games["away"].map(_key))
    rows = []
    for _, p in picks.iterrows():
        q = parse(p["Pick"])
        if q is None:
            rows.append({**p, "parsed": False})
            continue
        rows.append({**p, **q, "parsed": True, "sheet_week": _week_num(p.get("Week")),
                     "tk": _key(q["team"]) if q["team"] else None, "ok": _key(q["opp"]) if q["opp"] else None})
    P = pd.DataFrame(rows)
    if not len(P) or not P["parsed"].any():
        return P
    reg = g[g["season_type"] == "regular"]

    def pair(r):
        return reg[((reg["hk"] == r["tk"]) & (reg["ak"] == r["ok"])) |
                   ((reg["hk"] == r["ok"]) & (reg["ak"] == r["tk"]))]

    # Learn how the sheet's week numbers line up with CFBD's (Week 0 is often
    # CFBD week 1), from picks that name both teams.
    offs = {}
    for _, r in P[P["parsed"] & P["ok"].notna()].iterrows():
        hit = pair(r)
        if r["sheet_week"] is not None and len(hit):
            d = (hit["week"] - r["sheet_week"])
            d = d[d.abs() <= 2]
            if len(d):
                offs.setdefault(r["sheet_week"], []).append(int(d.iloc[(d.abs()).argmin()]))
    allo = [o for v in offs.values() for o in v]
    default = int(pd.Series(allo).mode().iloc[0]) if allo else 0
    week_off = {w: int(pd.Series(v).mode().iloc[0]) for w, v in offs.items()}

    ids = []
    for _, r in P.iterrows():
        gid = None
        if r.get("parsed") and r["sheet_week"] is not None:
            want = r["sheet_week"] + week_off.get(r["sheet_week"], default)
            if isinstance(r["ok"], str) and r["ok"]:
                c = pair(r)
                c = c[(c["week"] - want).abs() <= 1]
                if len(c):
                    gid = c.iloc[(c["week"] - want).abs().argmin()]["id"]
            else:
                c = reg[(reg["week"] == want) & ((reg["hk"] == r["tk"]) | (reg["ak"] == r["tk"]))]
                if len(c) == 1:
                    gid = c["id"].iloc[0]
        ids.append(gid)
    P["game_id"] = ids
    return P


def grade(P, proj):
    """Compare each pick with the model's pre-week projection.

    proj: one row per game with proj_home/away, model_spread/total and period columns.
    """
    if P is None or not len(P) or "game_id" not in P:
        return P
    J = proj.set_index("game_id")
    out = []
    for _, r in P.iterrows():
        row = dict(r)
        row.update(model_view=np.nan, model_edge=np.nan, agree=np.nan)
        gid = r.get("game_id")
        if gid is None or pd.isna(gid) or gid not in J.index:
            out.append(row)
            continue
        x = J.loc[gid]
        pk = {"Game": None, "1Q": "1q", "3Q": "3q", "1H": "1h", "2H": "2h"}.get(r["period"])
        if r["period"] in ("2Q", "4Q"):
            out.append(row)
            continue
        team_home = _key(x["home"]) == r["tk"]
        need = {"Spread": "model_spread" if pk is None else f"{pk}_spread",
                "Total": "model_total" if pk is None else f"{pk}_total",
                "Team total": ("tt" if pk is None else pk) + ("_home" if team_home else "_away")}[r["market"]]
        if need not in x or pd.isna(x[need]):
            out.append(row)  # no projection for this market that week (too early in the season)
            continue
        if r["market"] == "Spread":
            hs = x["model_spread"] if pk is None else x[f"{pk}_spread"]
            m_team = -hs if team_home else hs           # model margin for the picked team
            edge = m_team + r["line"]
            view = m_team
        elif r["market"] == "Total":
            view = x["model_total"] if pk is None else x[f"{pk}_total"]
            edge = (view - r["line"]) * (1 if r["side"] == "Over" else -1)
        else:  # team total
            col = ("tt" if pk is None else pk) + ("_home" if team_home else "_away")
            view = x[col]
            edge = (view - r["line"]) * (1 if r["side"] == "Over" else -1)
        row.update(model_view=view, model_edge=edge, agree=float(edge > 0))
        out.append(row)
    return pd.DataFrame(out)


def summarize(G):
    if G is None or not len(G) or "agree" not in G:
        return None
    done = G[G["Result"].isin(["Win", "Loss"]) & G["agree"].notna()]

    def rec(d):
        w, l = int((d["Result"] == "Win").sum()), int((d["Result"] == "Loss").sum())
        return {"w": w, "l": l, "pct": w / (w + l) if w + l else np.nan}

    groups = {"All picks": done, "Model agreed": done[done["agree"] == 1],
              "Model disagreed": done[done["agree"] == 0],
              "Model agreed by 3+ pts": done[done["model_edge"] >= 3],
              "Model disagreed by 3+ pts": done[done["model_edge"] <= -3]}
    by_market = {}
    for name, d in [("Full-game spreads", done[(done["market"] == "Spread") & (done["period"] == "Game")]),
                    ("Full-game totals", done[(done["market"] == "Total") & (done["period"] == "Game")]),
                    ("Team totals", done[done["market"] == "Team total"]),
                    ("Quarters & halves", done[(done["period"] != "Game") & (done["market"] != "Team total")])]:
        by_market[name] = {"agree": rec(d[d["agree"] == 1]), "disagree": rec(d[d["agree"] == 0])}
    return {"groups": {k: rec(v) for k, v in groups.items()}, "by_market": by_market,
            "unmatched": int(G["agree"].isna().sum()), "total": int(len(G))}
