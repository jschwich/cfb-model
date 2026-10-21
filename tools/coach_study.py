"""Do first-year coaches (especially at teams that bottomed out, with big
transfer classes) beat the model early in the season?  Offline:
    python tools/coach_study.py
"""
import gzip, json, sys, warnings
from pathlib import Path
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))


def read(y, name):
    return json.load(gzip.open(ROOT / "data" / "history" / str(y) / f"{name}.json.gz", "rt"))


def head_coach(rows, year):
    """school -> coach id with the most games there that season."""
    best = {}
    for c in rows:
        for s in c.get("seasons") or []:
            if s.get("year") == year:
                g = s.get("games") or 0
                if s["school"] not in best or g > best[s["school"]][1]:
                    best[s["school"]] = (c["id"], g, f"{c['firstName']} {c['lastName']}")
    return best


def team_features(y):
    now, prev = head_coach(read(y, "coaches"), y), head_coach(read(y, "coaches_prev"), y - 1)
    sp = {r["team"]: r.get("rating") for r in read(y, "sp") if r.get("team")}
    portal = pd.DataFrame(read(y, "portal"))
    portal["rating"] = pd.to_numeric(portal.get("rating"), errors="coerce")
    portal["stars"] = pd.to_numeric(portal.get("stars"), errors="coerce")
    good = portal[(portal["stars"] >= 3) | (portal["rating"] >= 0.85)]
    inn = good.groupby("destination").size()
    out = good.groupby("origin").size()
    rows = []
    for school in set(now) | set(prev):
        rows.append({"year": y, "team": school,
                     "new_coach": school in now and school in prev and now[school][0] != prev[school][0],
                     "coach": now.get(school, (None, 0, ""))[2],
                     "sp_prev": sp.get(school), "in3": int(inn.get(school, 0)), "out3": int(out.get(school, 0))})
    f = pd.DataFrame(rows)
    f["net3"] = f["in3"] - f["out3"]
    return f


def main():
    import tune
    years = [2022, 2023, 2024, 2025, 2026]
    df, _ = tune.evaluate({"IGNORE_PRIOR": []}, years)
    sides = pd.concat([
        df.assign(team=df["home"], resid=df["margin"] - df["pred_margin"],
                  ats=np.sign(df["margin"] + df["mkt_spread"])),
        df.assign(team=df["away"], resid=-(df["margin"] - df["pred_margin"]),
                  ats=-np.sign(df["margin"] + df["mkt_spread"]))])
    early = sides[sides["wk"] <= 8]
    per = early.groupby(["year", "team"]).agg(games=("resid", "size"), resid=("resid", "mean"),
                                              ats_w=("ats", lambda s: int((s > 0).sum())),
                                              ats_l=("ats", lambda s: int((s < 0).sum()))).reset_index()
    F = pd.concat([team_features(y) for y in years])
    T = per.merge(F, on=["year", "team"], how="inner")
    T = T[T["games"] >= 3]
    low = T["sp_prev"] <= T.groupby("year")["sp_prev"].transform(lambda s: s.quantile(0.33))
    big = T["net3"] >= T.groupby("year")["net3"].transform(lambda s: s.quantile(0.75))

    def show(name, m):
        x = T[m]
        if not len(x):
            return
        se = x["resid"].std() / np.sqrt(len(x)) if len(x) > 1 else np.nan
        w, l = x["ats_w"].sum(), x["ats_l"].sum()
        print(f"{name:52} teams {len(x):3}  beat model by {x['resid'].mean():+5.2f} (±{se:.2f})  "
              f"ATS {w}-{l} ({w / max(w + l, 1):.0%})")
    print("Weeks 3-8, walk-forward model, residual = actual margin minus model margin (team's view)\n")
    show("all teams", T["team"].notna())
    show("returning coach", ~T["new_coach"])
    show("new coach", T["new_coach"])
    show("new coach, team bottomed out last year (bottom third SP+)", T["new_coach"] & low)
    show("new coach, bottomed out, big transfer intake (top quarter)", T["new_coach"] & low & big)
    show("new coach, NOT bottomed out", T["new_coach"] & ~low)
    show("returning coach, bottomed out, big transfer intake", ~T["new_coach"] & low & big)
    show("any coach, big transfer intake", big)
    print("\nNew coach + bottomed out + big intake, by team:")
    x = T[T["new_coach"] & low & big].sort_values(["year", "resid"], ascending=[True, False])
    print(x[["year", "team", "coach", "sp_prev", "net3", "games", "resid", "ats_w", "ats_l"]].round(1).to_string(index=False))
    T.to_csv(ROOT / "data" / "history" / "coach_study.csv", index=False)


if __name__ == "__main__":
    main()
