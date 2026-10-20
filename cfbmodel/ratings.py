"""Team ratings: opponent-adjusted efficiency blended with a preseason prior and FEI.

All ratings are in points versus an average FBS team on a neutral field.
off = points scored above average, def = points prevented (higher is better),
rating = off + def (projected margin vs. an average team).
"""
import numpy as np
import pandas as pd

import config
from .data import norm_name

FCS = "FCS"  # every non-FBS opponent shares this one rating


def _ridge(rows, teams, alpha):
    """Fit stat = mu + hfa*h + off[offense] + dfn[defense] with ridge shrinkage.

    rows: DataFrame with off, dfn (team names), h (+1 home, -1 away, 0 neutral), y, w.
    Returns (mu, hfa, off Series, dfn Series). dfn is 'stat allowed above average'.
    """
    n = len(teams)
    idx = {t: i for i, t in enumerate(teams)}
    m = len(rows)
    X = np.zeros((m, 2 + 2 * n))
    X[:, 0] = 1.0
    X[:, 1] = rows["h"].to_numpy()
    oi = rows["off"].map(idx).to_numpy()
    di = rows["dfn"].map(idx).to_numpy()
    X[np.arange(m), 2 + oi] = 1.0
    X[np.arange(m), 2 + n + di] = 1.0
    w = rows["w"].to_numpy(float)
    y = rows["y"].to_numpy(float)
    XtW = X.T * w
    pen = np.full(2 + 2 * n, alpha)
    pen[:2] = 1e-6  # don't shrink the league average or home field
    beta = np.linalg.solve(XtW @ X + np.diag(pen), XtW @ y)
    return beta[0], beta[1], pd.Series(beta[2:2 + n], teams), pd.Series(beta[2 + n:], teams)


def _team(name, fbs):
    return name if name in fbs else FCS


def _prior(data, fbs_list):
    """Preseason rating from last year's SP+, talent and returning production."""
    P = pd.DataFrame(index=fbs_list, data={"off": 0.0, "def": 0.0})
    sp = data["sp"].dropna(subset=["sp_off", "sp_def"]).set_index("team") if len(data["sp"]) else None
    if sp is not None and len(sp):
        sp = sp[~sp.index.duplicated()]
        known = sp.reindex(fbs_list)
        o = known["sp_off"] - sp.loc[sp.index.isin(fbs_list), "sp_off"].mean()
        d = sp.loc[sp.index.isin(fbs_list), "sp_def"].mean() - known["sp_def"]
        # New FBS programs with no SP+ last year start near the bottom.
        P["off"] = (o.fillna(o.quantile(0.1)) * config.PRIOR_REGRESS).to_numpy()
        P["def"] = (d.fillna(d.quantile(0.1)) * config.PRIOR_REGRESS).to_numpy()

    def z(df, col):
        if not len(df):
            return pd.Series(0.0, index=fbs_list)
        s = df.drop_duplicates("team").set_index("team")[col].reindex(fbs_list)
        sd = s.std()
        return ((s - s.mean()) / sd).fillna(0.0) if sd and sd > 0 else pd.Series(0.0, index=fbs_list)

    tz = z(data["talent"], "talent")
    rz = z(data["returning"], "ret_ppa")
    P["off"] += tz * config.TALENT_PTS_PER_SD / 2 + rz * config.RETURNING_PTS_PER_SD
    P["def"] += tz * config.TALENT_PTS_PER_SD / 2
    P["off"] -= P["off"].mean()
    P["def"] -= P["def"].mean()
    return P


def rate(data, fei=None, through_week=None):
    """Rate every team using completed games before `through_week` (all if None)."""
    games = data["games"]
    fbs = data["fbs"]
    done = games[games["completed"]]
    if through_week is not None:
        done = done[done["wk"] < through_week]

    fbs_list = sorted(fbs)
    teams = fbs_list + [FCS]

    # ---- team-game rows (one per offense per game) ----
    side = []
    for _, g in done.iterrows():
        hh = 0 if g["neutral"] else 1
        side.append({"game_id": g["id"], "off": _team(g["home"], fbs), "dfn": _team(g["away"], fbs),
                     "h": hh, "pts": g["home_pts"], "raw_off": g["home"]})
        side.append({"game_id": g["id"], "off": _team(g["away"], fbs), "dfn": _team(g["home"], fbs),
                     "h": -hh, "pts": g["away_pts"], "raw_off": g["away"]})
    side = pd.DataFrame(side, columns=["game_id", "off", "dfn", "h", "pts", "raw_off"])
    side = side[~((side["off"] == FCS) & (side["dfn"] == FCS))]

    gp = pd.concat([side["off"], side["dfn"]]).value_counts() / 1  # appearances
    games_played = (side.groupby("off").size()).reindex(teams).fillna(0)

    fbs_games = side[(side["off"] != FCS) & (side["dfn"] != FCS)]
    mu_pts = float(fbs_games["pts"].mean()) if len(fbs_games) >= 20 else 28.0

    off_data = pd.Series(0.0, index=teams)
    def_data = pd.Series(0.0, index=teams)
    hfa = config.HFA_DEFAULT

    if len(side) >= 10:
        # Points (per game)
        rows = side.assign(y=side["pts"], w=1.0)
        _, h_pts, o_pts, d_pts = _ridge(rows, teams, config.RIDGE_ALPHA_PER_GAME)
        if len(fbs_games) >= 150:
            hfa = float(np.clip(2 * h_pts, config.HFA_MIN, config.HFA_MAX))

        comps = {"pts": (o_pts, -d_pts)}
        adv = data["adv"]
        if len(adv):
            a = adv.merge(side[["game_id", "raw_off", "off", "dfn", "h", "pts"]],
                          left_on=["game_id", "team"], right_on=["game_id", "raw_off"])
            if len(a) >= 10:
                for stat in ("ppa", "sr"):
                    # points per unit of this stat, from this season's games
                    b = np.cov(a[stat], a["pts"])[0, 1] / max(a[stat].var(), 1e-9)
                    rows = a.assign(y=a[stat], w=a["plays"])
                    _, _, o_s, d_s = _ridge(rows, teams, config.RIDGE_ALPHA_PER_PLAY)
                    comps[stat] = (o_s * b, -d_s * b)

        wsum = sum(config.STAT_WEIGHTS[k] for k in comps)
        for k, (o, d) in comps.items():
            off_data += config.STAT_WEIGHTS[k] / wsum * o
            def_data += config.STAT_WEIGHTS[k] / wsum * d

    # ---- blend with the preseason prior ----
    P = _prior(data, fbs_list)
    g = games_played.reindex(fbs_list)
    wdata = g / (g + config.PRIOR_FADE_GAMES)
    # center this season's numbers on the FBS average before blending
    od = off_data[fbs_list] - off_data[fbs_list].mean()
    dd = def_data[fbs_list] - def_data[fbs_list].mean()
    off = wdata * od + (1 - wdata) * P["off"]
    dfn = wdata * dd + (1 - wdata) * P["def"]

    R = pd.DataFrame({
        "conference": [data["teams"].get(t, {}).get("conference", "") for t in fbs_list],
        "fbs": True, "games": g.astype(int),
        "off_data": od, "def_data": dd, "data_rating": od + dd,
        "off_prior": P["off"], "def_prior": P["def"], "prior_rating": P["off"] + P["def"],
        "data_weight": wdata, "off": off, "def": dfn,
    }, index=fbs_list)
    R["fei_rating"] = np.nan

    # ---- FEI blend ----
    if fei is not None and len(fei) and config.FEI_WEIGHT > 0:
        keys = {norm_name(t): t for t in fbs_list}
        f = fei.assign(team_m=fei["key"].map(keys)).dropna(subset=["team_m"]).drop_duplicates("team_m")
        fm = f.set_index("team_m")["fei"] * config.FEI_POSSESSIONS
        fm = fm - fm.mean()
        R.loc[fm.index, "fei_rating"] = fm
        base = R["off"] + R["def"]
        target = base.where(R["fei_rating"].isna(),
                            (1 - config.FEI_WEIGHT) * base + config.FEI_WEIGHT * R["fei_rating"])
        adj = target - base
        R["off"] += adj / 2
        R["def"] += adj / 2

    R["off"] -= R["off"].mean()
    R["def"] -= R["def"].mean()
    R["rating"] = R["off"] + R["def"]

    # Pooled FCS opponent
    fcs_off = off_data[FCS] - off_data[fbs_list].mean() if games_played[FCS] else -12.0
    fcs_def = def_data[FCS] - def_data[fbs_list].mean() if games_played[FCS] else -13.0
    R.loc[FCS] = {"conference": "FCS", "fbs": False, "games": int(games_played[FCS]),
                  "off": fcs_off, "def": fcs_def, "rating": fcs_off + fcs_def,
                  "off_data": fcs_off, "def_data": fcs_def, "data_rating": fcs_off + fcs_def,
                  "data_weight": 1.0}
    R["fbs"] = R["fbs"].astype(bool)
    R["rank"] = R.loc[R["fbs"], "rating"].rank(ascending=False, method="min")
    R.index.name = "team"
    meta = {"hfa": hfa, "mu_pts": mu_pts, "through_week": through_week,
            "games_used": int(len(done))}
    return R.sort_values("rating", ascending=False), meta


def lookup(R, team):
    return R.loc[team] if team in R.index else R.loc[FCS]
