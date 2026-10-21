"""Projections for the next slate, value flags, and the week-by-week backtest.

Spread sign convention matches the sportsbooks/CFBD: the number is from the
HOME team's side, negative = home favored (home -7 means home by 7).
"""
from math import erf, sqrt

import numpy as np
import pandas as pd

import config
from .ratings import rate, lookup, power_flags
from .situations import Situations, against
from . import periods as P


def _phi(x):
    return 0.5 * (1 + erf(x / sqrt(2)))


def market_lines(lines):
    """One line per game, using the first provider in config.LINE_PROVIDERS that has one."""
    if not len(lines):
        return pd.DataFrame(columns=["game_id", "spread", "total", "provider", "spread_open", "total_open"])
    order = {p.lower(): i for i, p in enumerate(config.LINE_PROVIDERS)}
    L = lines.assign(rank=lines["provider"].str.lower().map(order).fillna(len(order)))
    out = []
    for gid, grp in L.groupby("game_id"):
        grp = grp.sort_values("rank")
        s = grp.dropna(subset=["spread"])
        t = grp.dropna(subset=["total"])
        out.append({"game_id": gid,
                    "spread": s["spread"].iloc[0] if len(s) else np.nan,
                    "spread_open": s["spread_open"].iloc[0] if len(s) else np.nan,
                    "provider": s["provider"].iloc[0] if len(s) else (t["provider"].iloc[0] if len(t) else ""),
                    "total": t["total"].iloc[0] if len(t) else np.nan,
                    # opener: median across books that report one
                    "spread_open_any": grp["spread_open"].median() if "spread_open" in grp else np.nan,
                    "total_open": grp["total_open"].median() if "total_open" in grp else np.nan})
    return pd.DataFrame(out)


def wind_effect(g):
    """Points taken off the total for wind above config.WIND_START_MPH."""
    k = getattr(config, "WIND_PTS_PER_MPH", 0.0)
    w = g.get("wind") if hasattr(g, "get") else None
    if not k or w is None or pd.isna(w):
        return 0.0
    return k * max(float(w) - config.WIND_START_MPH, 0.0)


def project(g, R, meta):
    """Projected home/away points for one game row."""
    h, a = lookup(R, g["home"]), lookup(R, g["away"])
    hfa = 0.0 if g["neutral"] else meta["hfa"]
    mu = meta["mu_pts"]
    hp = mu + h["off"] - a["def"] + hfa / 2
    ap = mu + a["off"] - h["def"] - hfa / 2
    if "tot_off" in h and not pd.isna(h["tot_off"]) and not pd.isna(a.get("tot_off")):
        # Totals use their own stat mix; the spread (hp - ap) stays as above.
        total = 2 * mu + (h["tot_off"] - a["tot_def"]) + (a["tot_off"] - h["tot_def"])
        margin = hp - ap
        total -= wind_effect(g)
        hp, ap = (total + margin) / 2, (total - margin) / 2
    k = config.PACE_PTS_PER_PLAY  # extra points per extra play above average tempo
    if k:
        # Extra plays in the game (both teams' tempo), split evenly so pace moves
        # the total but not the spread.
        extra = (h.get("pace_off", 0.0) + a.get("pace_def", 0.0) +
                 a.get("pace_off", 0.0) + h.get("pace_def", 0.0))
        hp += k * extra / 2
        ap += k * extra / 2
    return max(hp, 0.0), max(ap, 0.0)


def _fmt_spread(team, pts):
    if abs(pts) < 0.25:
        return f"{team} PK"
    return f"{team} {pts:+.1f}"


def _tier(edge, lean, strong):
    if edge is None or np.isnan(edge):
        return ""
    return "Strong" if edge >= strong else ("Lean" if edge >= lean else "")


def _evaluate(games, R, meta, M, sit):
    """Add projections, edges, tiers and situational flags to a set of games."""
    rows = []
    M = M.set_index("game_id") if len(M) else M
    for _, g in games.iterrows():
        hp, ap = project(g, R, meta)
        margin = hp - ap
        model_spread = -margin
        model_total = hp + ap
        mk = M.loc[g["id"]] if len(M) and g["id"] in M.index else None
        spread = mk["spread"] if mk is not None else np.nan
        total = mk["total"] if mk is not None else np.nan

        r = {"game_id": g["id"], "wk": g["wk"], "start": g["start"], "home": g["home"], "away": g["away"],
             "neutral": g["neutral"], "home_rating": lookup(R, g["home"])["rating"],
             "away_rating": lookup(R, g["away"])["rating"],
             "proj_home": hp, "proj_away": ap, "model_spread": model_spread, "model_total": model_total,
             "h1_spread": model_spread * config.FIRST_HALF_SPREAD_SHARE,
             "h1_total": model_total * config.FIRST_HALF_TOTAL_SHARE,
             "market_spread": spread, "market_total": total,
             "provider": mk["provider"] if mk is not None else "",
             "home_pts": g.get("home_pts"), "away_pts": g.get("away_pts"),
             "wind": g.get("wind"), "gust": g.get("gust"), "dome": g.get("dome"),
             "wind_cut": wind_effect(g)}

        # ---- spread value ----
        if not np.isnan(spread):
            edge = abs(model_spread - spread)
            take_home = model_spread < spread
            blend = config.MODEL_TRUST * margin + (1 - config.MODEL_TRUST) * (-spread)
            p_home = _phi((blend + spread) / config.MARGIN_SD)
            r.update(spread_edge=edge, spread_pick=g["home"] if take_home else g["away"],
                     spread_pick_text=_fmt_spread(g["home"], spread) if take_home
                     else _fmt_spread(g["away"], -spread),
                     spread_prob=p_home if take_home else 1 - p_home)
        else:
            r.update(spread_edge=np.nan, spread_pick="", spread_pick_text="", spread_prob=np.nan)
        # ---- opening line and movement ----
        op = mk["spread_open"] if mk is not None else np.nan
        if mk is not None and pd.isna(op):
            op = mk.get("spread_open_any", np.nan)
        ot = mk.get("total_open", np.nan) if mk is not None else np.nan
        r["open_spread"], r["open_total"] = op, ot
        r["spread_move"] = spread - op if not (pd.isna(op) or pd.isna(spread)) else np.nan
        r["total_move"] = total - ot if not (pd.isna(ot) or pd.isna(total)) else np.nan
        # +: the line has moved toward the model's number since it opened
        r["move_toward_model"] = (np.sign(r["spread_move"]) * np.sign(model_spread - op)
                                  if not pd.isna(r["spread_move"]) and r["spread_move"] != 0 else np.nan)
        r["open_edge"] = abs(model_spread - op) if not pd.isna(op) else np.nan
        r["open_pick"] = (g["home"] if model_spread < op else g["away"]) if not pd.isna(op) else ""
        r["spread_tier"] = _tier(r["spread_edge"], config.SPREAD_LEAN, config.SPREAD_STRONG)
        # Power 4 vs Group of 5: the model's spread picks have covered ~42% over
        # 2022-2026, so they're shown as low trust instead of flagged as value.
        r["cross_tier"] = bool(meta.get("power") and
                               (g["home"] in meta["power"]) != (g["away"] in meta["power"]))
        r["spread_tier_raw"] = r["spread_tier"]
        if r["cross_tier"] and not getattr(config, "FLAG_P4_VS_G5_SPREADS", True):
            r["spread_tier"] = ""

        # ---- total value ----
        if not np.isnan(total):
            edge = abs(model_total - total)
            over = model_total > total
            blend = config.MODEL_TRUST * model_total + (1 - config.MODEL_TRUST) * total
            p_over = 1 - _phi((total - blend) / config.TOTAL_SD)
            r.update(total_edge=edge, total_pick="Over" if over else "Under",
                     total_pick_text=f"{'Over' if over else 'Under'} {total:g}",
                     total_prob=p_over if over else 1 - p_over)
        else:
            r.update(total_edge=np.nan, total_pick="", total_pick_text="", total_prob=np.nan)
        r["total_tier"] = _tier(r["total_edge"], config.TOTAL_LEAN, config.TOTAL_STRONG)

        fl = sit.flags(g, R, margin) if sit is not None else []
        r["flags"] = fl
        r["flag_text"] = "; ".join(f"{f['type']}: {f['text']}" for f in fl)
        # Does a flag argue against the side the model likes?
        pick = r["spread_pick"]
        r["flag_conflict"] = bool(pick) and any(
            (against(f) and f["team"] == pick) or (not against(f) and f["team"] != pick) for f in fl)
        rows.append(r)
    return pd.DataFrame(rows)


def fit_scale(rows):
    """How much to stretch rating gaps so projections match real margins.

    Fit from games graded out of sample: actual margin = k * rating gap + home field.
    """
    if rows is not None and len(rows):
        rows = rows[rows["wk"] >= config.BACKTEST_FIRST_WEEK]
    if not getattr(config, "CALIBRATE", True) or rows is None or len(rows) < config.CALIBRATION_MIN_GAMES:
        return 1.0
    gap = rows["home_rating"] - rows["away_rating"]
    hfa = (-rows["model_spread"]) - gap * rows.get("scale", 1.0)
    y = rows["actual_margin"] - hfa
    k = float((gap * y).sum() / (gap * gap).sum())
    # A few hundred games is a noisy read, so lean toward no change until the sample grows.
    n0 = getattr(config, "CALIBRATION_SHRINK_GAMES", 0)
    k = 1 + (k - 1) * len(rows) / (len(rows) + n0)
    return float(np.clip(k, config.CALIBRATION_MIN, config.CALIBRATION_MAX))


def apply_scale(R, k):
    """Stretch every team's rating (vs. average) by k, for spreads only.

    Offense and defense each get half the change, so off - def (what drives
    totals) is untouched and projected totals don't move.
    """
    R = R.copy()
    bump = (k - 1) * (R["off"] + R["def"]) / 2
    R["off"] += bump
    R["def"] += bump
    R["rating"] = R["off"] + R["def"]
    return R


def _relevant(games, fbs):
    return games[games["home"].isin(fbs) | games["away"].isin(fbs)]


def _power_set(data):
    teams = sorted(data["fbs"])
    return {t for t, f in zip(teams, power_flags(data, teams)) if f}


def upcoming(data, R, meta):
    """Projections for the next week that still has unplayed games."""
    games = _relevant(data["games"], data["fbs"])
    recent = games["start"] > pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=3)
    todo = games[~games["completed"] & (recent | games["start"].isna())]  # skip old games never played
    if not len(todo):
        return pd.DataFrame(), "Season complete"
    wk = int(todo["wk"].min())
    slate = todo[todo["wk"] == wk]
    stype = slate["season_type"].iloc[0]
    label = f"Week {int(slate['week'].iloc[0])}" if stype == "regular" else "Postseason"
    M = market_lines(data["lines"])
    data["_line_by_game"] = dict(zip(M["game_id"], M["spread"])) if len(M) else {}
    meta = {**meta, "power": _power_set(data)}
    out = _evaluate(slate, R, meta, M, Situations(data))
    out = P.add_periods(out, P.fit_shares(data))
    out["best_edge"] = out[["spread_edge", "total_edge"]].max(axis=1)
    return out.sort_values("start").reset_index(drop=True), label


def backtest(data):
    """Grade the model week by week using only games played before each week."""
    games = _relevant(data["games"], data["fbs"])
    done = games[games["completed"]]
    if not len(done):
        return pd.DataFrame()
    M = market_lines(data["lines"])
    data["_line_by_game"] = dict(zip(M["game_id"], M["spread"])) if len(M) else {}
    sit = Situations(data)
    out = []
    for wk in sorted(done["wk"].unique()):
        if wk < 1:
            continue  # weeks before the track record starts are kept for grading your picks
        R, meta = rate(data, fei=None, through_week=wk)  # no historical FEI available
        prior = pd.concat(out, ignore_index=True) if out else None
        k = fit_scale(prior)  # only uses weeks before this one
        meta["power"] = _power_set(data)
        ev = _evaluate(done[done["wk"] == wk], apply_scale(R, k), meta, M, sit)
        ev["scale"] = k
        ev["home_rating"] /= k  # keep the raw gap for fitting the next week's scale
        ev["away_rating"] /= k
        ev["actual_margin"] = ev["home_pts"] - ev["away_pts"]
        sh = P.fit_shares(data, through_week=wk)  # quarter tendencies from earlier weeks only
        if sh is not None:
            flat = P.add_periods(ev, {**sh, "off": {}, "def": {}})
            ev = P.add_periods(ev, sh)
            for c in [c for c in flat.columns if c[:2] in ("1q", "3q", "1h", "2h")]:
                ev["flat_" + c] = flat[c]
        out.append(ev)
    if not out:
        return pd.DataFrame()
    bt = pd.concat(out, ignore_index=True)
    bt["actual_margin"] = bt["home_pts"] - bt["away_pts"]
    bt["actual_total"] = bt["home_pts"] + bt["away_pts"]
    bt["model_err"] = (-bt["model_spread"] - bt["actual_margin"]).abs()
    bt["market_err"] = (-bt["market_spread"] - bt["actual_margin"]).abs()

    def ats(row):
        if not row["spread_pick"]:
            return np.nan
        cover = row["actual_margin"] + row["market_spread"]  # >0 home covers
        if cover == 0:
            return np.nan  # push
        return float((cover > 0) == (row["spread_pick"] == row["home"]))

    def ou(row):
        if not row["total_pick"] or row["actual_total"] == row["market_total"]:
            return np.nan
        return float((row["actual_total"] > row["market_total"]) == (row["total_pick"] == "Over"))

    bt["ats_win"] = bt.apply(ats, axis=1)

    def ats_open(row):
        if not row["open_pick"] or pd.isna(row["open_spread"]) or row["open_edge"] < config.SPREAD_LEAN:
            return np.nan
        cover = row["actual_margin"] + row["open_spread"]
        if cover == 0:
            return np.nan
        return float((cover > 0) == (row["open_pick"] == row["home"]))
    bt["ats_open_win"] = bt.apply(ats_open, axis=1)
    bt["ou_win"] = bt.apply(ou, axis=1)
    graded = bt["wk"] >= config.BACKTEST_FIRST_WEEK
    bt.attrs["periods"] = P.grade(bt[graded], data["games"])
    return bt


def _record(s):
    s = s.dropna()
    return {"w": int(s.sum()), "l": int(len(s) - s.sum()), "pct": float(s.mean()) if len(s) else np.nan}


def flag_record(bt):
    """How the flagged team did against the spread, by flag type."""
    if bt is None or not len(bt) or "flags" not in bt:
        return pd.DataFrame()
    rows = []
    for _, r in bt.dropna(subset=["market_spread"]).iterrows():
        cover = r["actual_margin"] + r["market_spread"]
        if cover == 0:
            continue
        for f in r["flags"]:
            team_cov = (cover > 0) == (f["team"] == r["home"])
            # "worked" = the spot called it (flagged-against team failed to cover,
            # or the rested team covered)
            rows.append({"type": f["type"], "worked": float(team_cov != against(f))})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    g = df.groupby("type")["worked"].agg(["sum", "count"]).reset_index()
    g["w"], g["l"] = g["sum"].astype(int), (g["count"] - g["sum"]).astype(int)
    g["pct"] = g["sum"] / g["count"]
    return g[["type", "w", "l", "pct"]].sort_values("type")


def summarize_backtest(bt):
    if bt is None or not len(bt):
        return None
    attrs = dict(bt.attrs)
    bt = bt[bt["wk"] >= config.BACKTEST_FIRST_WEEK]
    bt.attrs.update(attrs)
    if not len(bt):
        return None
    lined = bt.dropna(subset=["market_spread"])
    s = {"games": int(len(bt)), "lined": int(len(lined)),
         "model_mae": float(lined["model_err"].mean()) if len(lined) else float(bt["model_err"].mean()),
         "market_mae": float(lined["market_err"].mean()) if len(lined) else np.nan,
         "ats_all": _record(bt["ats_win"]),
         "ats_lean": _record(bt.loc[bt["spread_tier"] != "", "ats_win"]),
         "ats_strong": _record(bt.loc[bt["spread_tier"] == "Strong", "ats_win"]),
         "ou_lean": _record(bt.loc[bt["total_tier"] != "", "ou_win"]),
         "ou_strong": _record(bt.loc[bt["total_tier"] == "Strong", "ou_win"]),
         "ats_open_lean": _record(bt["ats_open_win"]) if "ats_open_win" in bt else None,
         "ats_open_strong": _record(bt.loc[bt.get("open_edge", pd.Series(dtype=float)) >= config.SPREAD_STRONG,
                                           "ats_open_win"]) if "ats_open_win" in bt else None,
         "clv": _record((bt["move_toward_model"].dropna()[bt["open_edge"].reindex(
             bt["move_toward_model"].dropna().index) >= 3] > 0).astype(float)) if "move_toward_model" in bt else None,
         "flags": flag_record(bt)}
    weekly = []
    for wk, grp in bt.groupby("wk"):
        l = grp.dropna(subset=["market_spread"])
        weekly.append({"wk": int(wk), "games": len(grp),
                       "model_mae": (l if len(l) else grp)["model_err"].mean(),
                       "market_mae": l["market_err"].mean() if len(l) else np.nan,
                       "ats_lean": _record(grp.loc[grp["spread_tier"] != "", "ats_win"])})
    s["weekly"] = weekly
    s["periods"] = bt.attrs.get("periods")
    return s
