"""Quarter and half projections, plus team totals.

Each team's full-game projected points are split across quarters. The split
starts from the league-wide average (how much of an average team's scoring
comes in each quarter) and is nudged by:
  - how this team's offense spreads its points across quarters, and
  - how this opponent's defense spreads the points it allows,
each shrunk toward average until enough games are in (config.PERIOD_SHRINK_GAMES).

So a fast-starting offense facing a defense that gets carved early projects
for more first-quarter points; a team that adjusts well at halftime projects
for more third-quarter points. Overtime is left out of every period.
"""
import numpy as np
import pandas as pd

import config

QS = ["q1", "q2", "q3", "q4"]
PERIODS = {"1Q": ["q1"], "3Q": ["q3"], "1H": ["q1", "q2"], "2H": ["q3", "q4"]}


def _quarters(ls):
    """First four quarters of a line score, or None if it isn't usable."""
    if ls is None or isinstance(ls, float):
        return None
    try:
        q = [float(x) for x in list(ls)[:4]]
    except (TypeError, ValueError):
        return None
    return q if len(q) == 4 else None


def fit_shares(data, through_week=None):
    """League quarter shares and each team's offense/defense deviation from them."""
    g = data["games"]
    g = g[g["completed"]]
    if through_week is not None:
        g = g[g["wk"] < through_week]
    fbs = data["fbs"]
    rows = []
    for _, r in g.iterrows():
        hq, aq = _quarters(r.get("home_ls")), _quarters(r.get("away_ls"))
        if hq is None or aq is None:
            continue
        rows.append({"off": r["home"], "dfn": r["away"], "final": r["home_pts"], **dict(zip(QS, hq))})
        rows.append({"off": r["away"], "dfn": r["home"], "final": r["away_pts"], **dict(zip(QS, aq))})
    if len(rows) < 40:
        return None
    df = pd.DataFrame(rows)
    df["reg"] = df[QS].sum(axis=1)
    both_fbs = df["off"].isin(fbs) & df["dfn"].isin(fbs)
    lg = df.loc[both_fbs, QS].sum()
    league = (lg / lg.sum()).to_dict()

    k = getattr(config, "PERIOD_SHRINK_GAMES", 25.0)

    def dev(col):
        out = {}
        for team, grp in df.groupby(col):
            tot = grp["reg"].sum()
            if team not in fbs or tot <= 0:
                continue
            share = grp[QS].sum() / tot
            w = len(grp) / (len(grp) + k)
            out[team] = {q: w * (share[q] - league[q]) for q in QS}
        return out

    reg_frac = float(df["reg"].sum() / max(df["final"].sum(), 1))  # strips out overtime points
    return {"league": league, "off": dev("off"), "def": dev("dfn"), "games": int(len(df) // 2),
            "reg_frac": min(reg_frac, 1.0)}


def split(team, opp, pts, shares):
    """Projected points for `team` in each quarter, given full-game points."""
    if shares is None:
        h1 = getattr(config, "FIRST_HALF_TOTAL_SHARE", 0.52) / 2
        base = {"q1": h1, "q2": h1, "q3": (1 - 2 * h1) / 2, "q4": (1 - 2 * h1) / 2}
        return {q: pts * base[q] for q in QS}
    s = {}
    for q in QS:
        v = shares["league"][q]
        v += shares["off"].get(team, {}).get(q, 0.0)
        v += shares["def"].get(opp, {}).get(q, 0.0)
        s[q] = max(v, 0.02)
    tot = sum(s.values())
    pts = pts * shares.get("reg_frac", 1.0)
    return {q: pts * s[q] / tot for q in QS}


def add_periods(games, shares):
    """Add team totals and 1Q/3Q/1H/2H spreads and totals to a games table."""
    if games is None or not len(games):
        return games
    out = games.copy()
    cols = {}
    for i, r in out.iterrows():
        # Regulation-only points: OT is a small add-on that only full-game numbers carry.
        h = split(r["home"], r["away"], r["proj_home"], shares)
        a = split(r["away"], r["home"], r["proj_away"], shares)
        row = {"tt_home": r["proj_home"], "tt_away": r["proj_away"]}
        for name, qs in PERIODS.items():
            hp, ap = sum(h[q] for q in qs), sum(a[q] for q in qs)
            key = name.lower()
            row[f"{key}_home"] = hp
            row[f"{key}_away"] = ap
            row[f"{key}_spread"] = -(hp - ap)    # home side, negative = home favored
            row[f"{key}_total"] = hp + ap
        cols[i] = row
    add = pd.DataFrame.from_dict(cols, orient="index")
    # keep the old 1H columns in sync for anything still reading them
    out["h1_spread"], out["h1_total"] = add["1h_spread"], add["1h_total"]
    return pd.concat([out, add], axis=1)


def grade(bt, games):
    """Out-of-sample check: team-specific splits vs. a flat league split.

    bt must already carry period projections (made with shares fit only on
    earlier weeks) and flat ones (prefixed 'flat_').
    """
    if bt is None or not len(bt) or "1q_total" not in bt:
        return None
    ls = games.set_index("id")[["home_ls", "away_ls"]]
    res = []
    for _, r in bt.iterrows():
        if r["game_id"] not in ls.index:
            continue
        hq, aq = _quarters(ls.at[r["game_id"], "home_ls"]), _quarters(ls.at[r["game_id"], "away_ls"])
        if hq is None or aq is None:
            continue
        H, A = dict(zip(QS, hq)), dict(zip(QS, aq))
        for name, qs in PERIODS.items():
            key = name.lower()
            ah, aa = sum(H[q] for q in qs), sum(A[q] for q in qs)
            res.append({"period": name,
                        "total_err": abs(r[f"{key}_total"] - (ah + aa)),
                        "flat_total_err": abs(r[f"flat_{key}_total"] - (ah + aa)),
                        "spread_err": abs(-r[f"{key}_spread"] - (ah - aa)),
                        "flat_spread_err": abs(-r[f"flat_{key}_spread"] - (ah - aa)),
                        "tt_err": (abs(r[f"{key}_home"] - ah) + abs(r[f"{key}_away"] - aa)) / 2,
                        "flat_tt_err": (abs(r[f"flat_{key}_home"] - ah) + abs(r[f"flat_{key}_away"] - aa)) / 2})
        res.append({"period": "Game", "tt_err": (abs(r["proj_home"] - r["home_pts"]) +
                                                abs(r["proj_away"] - r["away_pts"])) / 2})
    if not res:
        return None
    df = pd.DataFrame(res)
    order = ["Game", "1Q", "3Q", "1H", "2H"]
    g = df.groupby("period").mean(numeric_only=True).reindex(order)
    g["games"] = df.groupby("period").size().reindex(order)
    return g.reset_index()
