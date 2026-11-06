"""Your manual team adjustments (config.MY_ADJUSTMENTS).

Each adjustment moves a team's rating by some points from a given week on
(optionally until a given week). Spreads always move by the full amount.
Where the points go decides what happens to totals:

  "off"  (default, config.ADJ_DEFAULT_SIDE): the team's offense. A -6 for a
         QB out means that team scores fewer points, so its team total and
         the game total drop too (by points x config.ADJ_TOTAL_SHARE).
  "def"  the team's defense: a -4 for a star pass rusher out means the
         opponent scores more, so the total goes up.
  "both" half each (coaching change, a team that's just better/worse):
         the total doesn't change.

The track record compares, on games already played, the model's line with
and without your adjustments, so you can see whether your reads add value.
"""
import numpy as np
import pandas as pd

import config


SIDES = ("off", "def", "both")


def _items():
    """Adjustments as dicts. Accepts
    ("Team", pts, from_week, "why"), optionally followed by an until-week
    and/or a side ("off", "def" or "both") in either order."""
    out = []
    default = str(getattr(config, "ADJ_DEFAULT_SIDE", "off")).lower()
    for a in getattr(config, "MY_ADJUSTMENTS", []) or []:
        a = list(a)
        side, rest = default, []
        for x in a[4:]:
            if isinstance(x, str) and x.strip().lower() in SIDES:
                side = x.strip().lower()
            else:
                rest.append(x)
        a = (a[:4] + [None] * 4)[:4] + (rest[:1] or [None])
        team, pts, start, note, end = a
        if team is None or pts is None:
            continue
        out.append({"team": str(team), "pts": float(pts), "start": int(start or 1),
                    "end": int(end) if end not in (None, "") else None, "note": note or "",
                    "side": side if side in SIDES else "off"})
    return out


def _share():
    return float(getattr(config, "ADJ_TOTAL_SHARE", 1.0))


def active(team, wk):
    """Total points of adjustment for a team in a given (running) week, with notes."""
    pts, notes = 0.0, []
    for a in _items():
        if a["team"] == team and wk >= a["start"] and (a["end"] is None or wk <= a["end"]):
            pts += a["pts"]
            notes.append(a)
    return pts, notes


def split(team, wk):
    """(offense pts, defense pts) of a team's active adjustments."""
    o = d = 0.0
    for a in active(team, wk)[1]:
        if a["side"] == "off":
            o += a["pts"]
        elif a["side"] == "def":
            d += a["pts"]
        else:
            o += a["pts"] / 2
            d += a["pts"] / 2
    return o, d


def total_shift(home, away, wk):
    """How much the adjustments move a game's projected total."""
    ho, hd = split(home, wk)
    ao, ad = split(away, wk)
    # Offense pts change what that team scores; defense pts change what the
    # opponent scores (a better defense, + pts, means fewer points allowed).
    return _share() * (ho + ao - hd - ad)


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
            o, d = split(team, wk)
            R.loc[team, "off"] += o
            R.loc[team, "def"] += d
            if "tot_off" in R:
                R.loc[team, "tot_off"] += o * _share()
                R.loc[team, "tot_def"] += d * _share()
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
                sides = {n["side"] for n in notes}
                where = {"off": " offense", "def": " defense"}.get(sides.pop(), "") if len(sides) == 1 else ""
                bits.append(f"{t} {pts:+g}{where}" + (f" ({why})" if why else ""))
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
        ts = total_shift(r["home"], r["away"], r["wk"])
        if not d and not ts:
            continue
        base = -r["model_spread"]
        adj = base + d
        actual = r["home_pts"] - r["away_pts"]
        row = {"wk": r["wk"], "game": f"{r['away']} @ {r['home']}", "shift": d,
               "err_without": abs(base - actual), "err_with": abs(adj - actual)}
        sp = r.get("market_spread")
        if d and sp is not None and not pd.isna(sp):
            cover = actual + sp
            if cover != 0:  # did the side your adjustment pushed toward cover?
                row["your_side_covered"] = float((cover > 0) == (d > 0))
        tot = r.get("model_total")
        at = r["home_pts"] + r["away_pts"]
        if ts and tot is not None and not pd.isna(tot):
            row.update(tot_shift=ts, tot_err_without=abs(tot - at), tot_err_with=abs(tot + ts - at))
            mt = r.get("market_total")
            if mt is not None and not pd.isna(mt) and at != mt:  # did the total go the way you pushed it?
                row["your_total_side_won"] = float((at > mt) == (ts > 0))
        rows.append(row)
    if not rows:
        return {"games": 0}
    df = pd.DataFrame(rows)
    c = df["your_side_covered"].dropna() if "your_side_covered" in df else pd.Series(dtype=float)
    out = {"games": len(df), "err_without": df["err_without"].mean(), "err_with": df["err_with"].mean(),
           "side_w": int(c.sum()), "side_l": int(len(c) - c.sum()), "rows": df}
    if "tot_shift" in df:
        t = df.dropna(subset=["tot_shift"])
        ou = t["your_total_side_won"].dropna() if "your_total_side_won" in t else pd.Series(dtype=float)
        out.update(tot_games=len(t), tot_err_without=t["tot_err_without"].mean(),
                   tot_err_with=t["tot_err_with"].mean(), tot_w=int(ou.sum()), tot_l=int(len(ou) - ou.sum()))
    return out
