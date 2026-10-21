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


def _ridge(rows, teams, alpha, power=None):
    """Fit stat = mu + hfa*h + off[offense] + dfn[defense] with ridge shrinkage.

    rows: DataFrame with off, dfn (team names), h (+1 home, -1 away, 0 neutral), y, w.
    power: optional 0/1 per team (Power 4 = 1). When given, the fit also learns an
    unshrunk Power 4 offense and defense level, so teams are pulled toward their
    own tier's average instead of one FBS-wide average.
    Returns (mu, hfa, off Series, dfn Series). dfn is 'stat allowed above average'.
    """
    n = len(teams)
    idx = {t: i for i, t in enumerate(teams)}
    m = len(rows)
    extra = 2 if power is not None else 0
    k = 2 + extra
    X = np.zeros((m, k + 2 * n))
    X[:, 0] = 1.0
    X[:, 1] = rows["h"].to_numpy()
    oi = rows["off"].map(idx).to_numpy()
    di = rows["dfn"].map(idx).to_numpy()
    if extra:
        pw = np.asarray(power, dtype=float)
        X[:, 2] = pw[oi]
        X[:, 3] = pw[di]
    X[np.arange(m), k + oi] = 1.0
    X[np.arange(m), k + n + di] = 1.0
    w = rows["w"].to_numpy(float)
    y = rows["y"].to_numpy(float)
    XtW = X.T * w
    pen = np.full(k + 2 * n, alpha)
    pen[:k] = 1e-6  # don't shrink the league average, home field, or tier levels
    beta = np.linalg.solve(XtW @ X + np.diag(pen), XtW @ y)
    off = pd.Series(beta[k:k + n], teams)
    dfn = pd.Series(beta[k + n:], teams)
    if extra:
        off = off + beta[2] * pd.Series(pw, teams)
        dfn = dfn + beta[3] * pd.Series(pw, teams)
    return beta[0], beta[1], off, dfn


def power_flags(data, teams):
    """1 for Power 4 teams (SEC, Big Ten, Big 12, ACC, plus Notre Dame; the
    Pac-12 counts through 2023), else 0."""
    year = data.get("year", 2026)
    confs = set(getattr(config, "POWER_CONFERENCES", ["SEC", "Big Ten", "Big 12", "ACC"]))
    if year <= getattr(config, "PAC12_POWER_THROUGH", 2023):
        confs.add("Pac-12")
    extra = set(getattr(config, "POWER_TEAMS", ["Notre Dame"]))
    out = []
    for t in teams:
        c = (data["teams"].get(t, {}) or {}).get("conference", "")
        out.append(1 if (c in confs or t in extra) else 0)
    return out


def _team(name, fbs):
    return name if name in fbs else FCS


def _prior(data, fbs_list, regress=None):
    """Preseason rating from last year's SP+, talent and returning production."""
    P = pd.DataFrame(index=fbs_list, data={"off": 0.0, "def": 0.0})
    sp = data["sp"].dropna(subset=["sp_off", "sp_def"]).set_index("team") if len(data["sp"]) else None
    if sp is not None and len(sp):
        sp = sp[~sp.index.duplicated()]
        known = sp.reindex(fbs_list)
        o = known["sp_off"] - sp.loc[sp.index.isin(fbs_list), "sp_off"].mean()
        d = sp.loc[sp.index.isin(fbs_list), "sp_def"].mean() - known["sp_def"]
        # New FBS programs with no SP+ last year start near the bottom.
        reg = config.PRIOR_REGRESS if regress is None else regress
        P["off"] = (o.fillna(o.quantile(0.1)) * reg).to_numpy()
        P["def"] = (d.fillna(d.quantile(0.1)) * reg).to_numpy()

    def z(df, col):
        if not len(df):
            return pd.Series(0.0, index=fbs_list)
        s = df.drop_duplicates("team").set_index("team")[col].reindex(fbs_list)
        sd = s.std()
        return ((s - s.mean()) / sd).fillna(0.0) if sd and sd > 0 else pd.Series(0.0, index=fbs_list)

    # First-year head coaches (tested 2022-2026, weeks 3-8): at a program that was
    # good last year the model ran ~3 pts too high; at one that bottomed out and
    # brought in a big transfer class it ran ~2 pts too low.
    P["coach_adj"] = 0.0
    cc = data.get("coach")
    if getattr(config, "NEW_COACH_ADJUST", False) and cc is not None and len(cc) and sp is not None and len(sp):
        cc = cc.set_index("team").reindex(fbs_list)
        last = sp["sp"].reindex(fbs_list) if "sp" in sp else (sp["sp_off"] - sp["sp_def"]).reindex(fbs_list)
        lo, hi = last.quantile(1 / 3), last.quantile(2 / 3)
        big = cc["transfers_in"] >= cc["transfers_in"].quantile(0.75)
        new = cc["new_coach"].fillna(False).astype(bool)
        P.loc[new & (last >= hi), "coach_adj"] = config.NEW_COACH_WAS_GOOD_PTS
        P.loc[new & (last <= lo) & big, "coach_adj"] = config.NEW_COACH_REBUILD_PTS
        P.loc[new & last.isna() & big, "coach_adj"] = config.NEW_COACH_REBUILD_PTS
    tz = z(data["talent"], "talent")
    rz = z(data["returning"], "ret_ppa")
    P["off"] += tz * config.TALENT_PTS_PER_SD / 2 + rz * config.RETURNING_PTS_PER_SD
    P["def"] += tz * config.TALENT_PTS_PER_SD / 2
    P["off"] += P["coach_adj"] / 2
    P["def"] += P["coach_adj"] / 2
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
    power = power_flags(data, teams) if getattr(config, "TIER_LEVELS", False) else None

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
    off_tot = pd.Series(0.0, index=teams)   # same idea, weighted for totals
    def_tot = pd.Series(0.0, index=teams)
    hfa = config.HFA_DEFAULT

    if len(side) >= 10:
        tw = getattr(config, "TOTAL_STAT_WEIGHTS", None) or config.STAT_WEIGHTS
        a_game, a_play = config.RIDGE_ALPHA_PER_GAME, config.RIDGE_ALPHA_PER_PLAY
        t_game = getattr(config, "TOTAL_RIDGE_ALPHA_PER_GAME", None) or a_game
        t_play = getattr(config, "TOTAL_RIDGE_ALPHA_PER_PLAY", None) or a_play
        adv = data["adv"]
        a = None
        if len(adv):
            a = adv.merge(side[["game_id", "raw_off", "off", "dfn", "h", "pts"]],
                          left_on=["game_id", "team"], right_on=["game_id", "raw_off"])
        cache = {}

        def components(W, alpha_game, alpha_play):
            """Opponent-adjusted points per stat, at the given shrinkage."""
            key = ("pts", alpha_game)
            if key not in cache:
                _, h_pts, o_pts, d_pts = _ridge(side.assign(y=side["pts"], w=1.0), teams, alpha_game, power)
                cache[key] = (o_pts, -d_pts, h_pts)
            comps = {"pts": cache[key][:2]}
            for stat in [k for k, wt in W.items() if k != "pts" and wt > 0]:
                if a is None or stat not in a or a[stat].notna().sum() < 10:
                    continue
                key = (stat, alpha_play)
                if key not in cache:
                    aa = a.dropna(subset=[stat])
                    # points per unit of this stat, from this season's games
                    b = np.cov(aa[stat], aa["pts"])[0, 1] / max(aa[stat].var(), 1e-9)
                    _, _, o_s, d_s = _ridge(aa.assign(y=aa[stat], w=aa["plays"]), teams, alpha_play, power)
                    cache[key] = (o_s * b, -d_s * b)
                comps[stat] = cache[key]
            return comps

        for W, ag, apl, o_acc, d_acc in ((config.STAT_WEIGHTS, a_game, a_play, off_data, def_data),
                                         (tw, t_game, t_play, off_tot, def_tot)):
            comps = components(W, ag, apl)
            wsum = sum(W.get(k, 0) for k in comps) or 1.0
            for k, (o, d) in comps.items():
                o_acc += W.get(k, 0) / wsum * o
                d_acc += W.get(k, 0) / wsum * d
        if len(fbs_games) >= 150:
            hfa = float(np.clip(2 * cache[("pts", a_game)][2], config.HFA_MIN, config.HFA_MAX))

    # ---- blend with the preseason prior ----
    P = _prior(data, fbs_list)
    g = games_played.reindex(fbs_list)
    # center this season's numbers on the FBS average before blending
    od = off_data[fbs_list] - off_data[fbs_list].mean()
    dd = def_data[fbs_list] - def_data[fbs_list].mean()
    # When a team's results are far from its preseason guess, the guess fades faster.
    fade = pd.Series(config.PRIOR_FADE_GAMES, index=g.index, dtype=float)
    gp_pts = getattr(config, "PRIOR_GAP_PTS", 0)
    if gp_pts:
        big = (od + dd - (P["off"] + P["def"])).abs().reindex(g.index) >= gp_pts
        fade[big & (g >= getattr(config, "PRIOR_GAP_MIN_GAMES", 3))] = config.PRIOR_GAP_FADE
    wdata = g / (g + fade)
    # Teams you've said the preseason guess doesn't describe (new coach, rebuilt
    # roster): once they've played a few games, use only this season's data.
    skip = [t for t in getattr(config, "IGNORE_PRIOR", []) if t in wdata.index]
    need = getattr(config, "IGNORE_PRIOR_MIN_GAMES", 3)
    for t in skip:
        if g[t] >= need:
            wdata[t] = 1.0
    off = wdata * od + (1 - wdata) * P["off"]
    dfn = wdata * dd + (1 - wdata) * P["def"]
    Pt = _prior(data, fbs_list, getattr(config, "TOTAL_PRIOR_REGRESS", None))
    fade_t = getattr(config, "TOTAL_PRIOR_FADE_GAMES", None) or config.PRIOR_FADE_GAMES
    wt = g / (g + fade_t)
    for t in skip:
        if g[t] >= need:
            wt[t] = 1.0
    ot = wt * (off_tot[fbs_list] - off_tot[fbs_list].mean()) + (1 - wt) * Pt["off"]
    dt = wt * (def_tot[fbs_list] - def_tot[fbs_list].mean()) + (1 - wt) * Pt["def"]

    R = pd.DataFrame({
        "conference": [data["teams"].get(t, {}).get("conference", "") for t in fbs_list],
        "fbs": True, "games": g.astype(int),
        "off_data": od, "def_data": dd, "data_rating": od + dd,
        "off_prior": P["off"], "def_prior": P["def"], "prior_rating": P["off"] + P["def"],
        "coach_adj": P["coach_adj"] if "coach_adj" in P else 0.0,
        "data_weight": wdata, "off": off, "def": dfn, "tot_off": ot - ot.mean(), "tot_def": dt - dt.mean(),
    }, index=fbs_list)

    # ---- outside opinions: FEI, SP+, FPI ----
    sources = {}
    keys = {norm_name(t): t for t in fbs_list}
    if fei is not None and len(fei):
        f = fei.assign(team_m=fei["key"].map(keys)).dropna(subset=["team_m"]).drop_duplicates("team_m")
        sources["fei"] = f.set_index("team_m")["fei"] * config.FEI_POSSESSIONS
    for name in ("sp", "fpi"):
        df = data.get(f"{name}_now")
        if df is not None and len(df):
            m = df.assign(team_m=df["team"].map(lambda t: keys.get(norm_name(t)))).dropna(subset=["team_m"])
            sources[name] = m.drop_duplicates("team_m").set_index("team_m")["value"].astype(float)
    base = R["off"] + R["def"]
    num = pd.Series(0.0, index=fbs_list)
    wtot = pd.Series(0.0, index=fbs_list)
    for name, vals in sources.items():
        w = config.OUTSIDE_WEIGHTS.get(name, 0)
        vals = (vals - vals.mean()).reindex(fbs_list)
        R[f"{name}_rating"] = vals
        if w > 0:
            has = vals.notna()
            num[has] += w * vals[has]
            wtot[has] += w
    for name in ("fei", "sp", "fpi"):
        if f"{name}_rating" not in R:
            R[f"{name}_rating"] = np.nan
    target = (1 - wtot) * base + num
    adj = target - base
    R["off"] += adj / 2
    R["def"] += adj / 2
    R["outside_weight"] = wtot
    meta_sources = sorted(k for k in sources if config.OUTSIDE_WEIGHTS.get(k, 0) > 0)

    R["off"] -= R["off"].mean()
    R["def"] -= R["def"].mean()
    R["rating"] = R["off"] + R["def"]

    # Pooled FCS opponent
    fcs_off = off_data[FCS] - off_data[fbs_list].mean() if games_played[FCS] else -12.0
    fcs_def = def_data[FCS] - def_data[fbs_list].mean() if games_played[FCS] else -13.0
    R.loc[FCS] = {"conference": "FCS", "fbs": False, "games": int(games_played[FCS]),
                  "off": fcs_off, "def": fcs_def, "rating": fcs_off + fcs_def,
                  "off_data": fcs_off, "def_data": fcs_def, "data_rating": fcs_off + fcs_def,
                  "data_weight": 1.0,
                  "tot_off": off_tot[FCS] - off_tot[fbs_list].mean() if games_played[FCS] else -12.0,
                  "tot_def": def_tot[FCS] - def_tot[fbs_list].mean() if games_played[FCS] else -13.0}
    R["fbs"] = R["fbs"].astype(bool)
    R["rank"] = R.loc[R["fbs"], "rating"].rank(ascending=False, method="min")
    R.index.name = "team"
    # ---- pace (full-game plays) ----
    R["pace_off"], R["pace_def"] = 0.0, 0.0
    mu_plays = 0.0
    pace = data.get("pace")
    if config.PACE_PTS_PER_PLAY and pace is not None and len(pace):
        pp = pace.merge(side[["game_id", "raw_off", "off", "dfn", "h"]],
                        left_on=["game_id", "team"], right_on=["game_id", "raw_off"])
        if len(pp) >= 40:
            mu_plays, _, po, pd_ = _ridge(pp.assign(y=pp["plays"], w=1.0), teams, config.PACE_ALPHA)
            R["pace_off"] = po.reindex(R.index).fillna(0.0)
            R["pace_def"] = pd_.reindex(R.index).fillna(0.0)

    meta = {"hfa": hfa, "mu_pts": mu_pts, "through_week": through_week, "mu_plays": mu_plays,
            "sources": meta_sources,
            "games_used": int(len(done))}
    return R.sort_values("rating", ascending=False), meta


def lookup(R, team):
    return R.loc[team] if team in R.index else R.loc[FCS]
