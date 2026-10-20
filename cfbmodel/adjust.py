"""Your manual team adjustments (config.MY_ADJUSTMENTS).

Each adjustment moves a team's rating by some points from a given week on
(optionally until a given week). Half goes to offense, half to defense, so
spreads move by the full amount and totals don't change.

The track record compares, on games already played, the model's line with
and without your adjustments, so you can see whether your reads add value.
"""
import numpy as np
import pandas as pd

import config


def _items():
    out = []
    for a in getattr(config, "MY_ADJUSTMENTS", []) or []:
        a = list(a) + [None] * (5 - len(a))
        team, pts, start, note, end = a[:5]
        if team is None or pts is None:
            continue
        out.append({"team": str(team), "pts": float(pts), "start": int(start or 1),
                    "end": int(end) if end not in (None, "") else None, "note": note or ""})
    return out


def active(team, wk):
    """Total points of adjustment for a team in a given (running) week, with notes."""
    pts, notes = 0.0, []
    for a in _items():
        if a["team"] == team and wk >= a["start"] and (a["end"] is None or wk <= a["end"]):
            pts += a["pts"]
            notes.append(a)
    return pts, notes


def apply(R, wk):
    """Ratings with adjustments that are active in week `wk`."""
    R = R.copy()
    R["my_adj"] = 0.0
    for team in {a["team"] for a in _items()}:
        if team not in R.index:
            print(f"  warning: adjustment for '{team}' doesn't match a team name; skipped")
            continue
        pts, _ = active(team, wk)
        if pts:
            R.loc[team, ["off", "def"]] += pts / 2
            R.loc[team, "rating"] += pts
            R.loc[team, "my_adj"] = pts
    return R


def tag(games):
    """Mark slate games that include an adjustment."""
    if games is None or not len(games):
        return games
    games = games.copy()
    txt = []
    for _, g in games.iterrows():
        bits = []
        for t in (g["away"], g["home"]):
            pts, notes = active(t, g["wk"])
            if pts:
                why = "; ".join(n["note"] for n in notes if n["note"])
                bits.append(f"{t} {pts:+g}" + (f" ({why})" if why else ""))
        txt.append(", ".join(bits))
    games["my_adj_text"] = txt
    return games


def record(bt):
    """On played games: was the model closer with or without your adjustments?"""
    if bt is None or not len(bt) or not _items():
        return None
    rows = []
    for _, r in bt.iterrows():
        ha, _ = active(r["home"], r["wk"])
        aa, _ = active(r["away"], r["wk"])
        d = ha - aa
        if not d:
            continue
        base = -r["model_spread"]
        adj = base + d
        actual = r["home_pts"] - r["away_pts"]
        row = {"wk": r["wk"], "game": f"{r['away']} @ {r['home']}", "shift": d,
               "err_without": abs(base - actual), "err_with": abs(adj - actual)}
        sp = r.get("market_spread")
        if sp is not None and not pd.isna(sp):
            cover = actual + sp
            if cover != 0:  # did the side your adjustment pushed toward cover?
                row["your_side_covered"] = float((cover > 0) == (d > 0))
        rows.append(row)
    if not rows:
        return {"games": 0}
    df = pd.DataFrame(rows)
    c = df["your_side_covered"].dropna() if "your_side_covered" in df else pd.Series(dtype=float)
    return {"games": len(df), "err_without": df["err_without"].mean(), "err_with": df["err_with"].mean(),
            "side_w": int(c.sum()), "side_l": int(len(c) - c.sum()), "rows": df}
