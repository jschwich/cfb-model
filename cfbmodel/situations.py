"""Situational spot flags: letdown, look-ahead, sandwich, rest edge, long trip.

These are FLAGS ONLY. They never change the model's numbers; they mark games
where a classic handicapping angle applies so you can use them as a tiebreaker.
Each flag names the team the spot works AGAINST (the one to be wary of backing),
except rest edge, which names the team with the advantage.

Thresholds live in config.py under "Situational flags".
"""
import math
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import config

# Well-known rivalries (either order). Add your own in config.EXTRA_RIVALRIES.
RIVALRIES = [
    ("Ohio State", "Michigan"), ("Alabama", "Auburn"), ("Georgia", "Florida"),
    ("Georgia", "Georgia Tech"), ("Georgia", "Auburn"), ("Florida", "Florida State"),
    ("Florida State", "Miami"), ("Clemson", "South Carolina"), ("Texas", "Oklahoma"),
    ("Texas", "Texas A&M"), ("Oklahoma", "Oklahoma State"), ("USC", "UCLA"),
    ("USC", "Notre Dame"), ("Stanford", "California"), ("Oregon", "Oregon State"),
    ("Oregon", "Washington"), ("Washington", "Washington State"), ("Ole Miss", "Mississippi State"),
    ("LSU", "Alabama"), ("LSU", "Arkansas"), ("Tennessee", "Alabama"), ("Tennessee", "Kentucky"),
    ("Tennessee", "Vanderbilt"), ("Kentucky", "Louisville"), ("Virginia", "Virginia Tech"),
    ("North Carolina", "NC State"), ("North Carolina", "Duke"), ("Iowa", "Iowa State"),
    ("Minnesota", "Wisconsin"), ("Iowa", "Minnesota"), ("Indiana", "Purdue"),
    ("Michigan", "Michigan State"), ("Pittsburgh", "West Virginia"), ("Kansas", "Kansas State"),
    ("Kansas", "Missouri"), ("Utah", "BYU"), ("Arizona", "Arizona State"),
    ("Colorado", "Colorado State"), ("Army", "Navy"), ("Air Force", "Army"), ("Air Force", "Navy"),
    ("Texas Tech", "TCU"), ("TCU", "SMU"), ("Baylor", "TCU"), ("Houston", "Rice"),
    ("Nebraska", "Iowa"), ("Notre Dame", "Navy"), ("Boston College", "Syracuse"),
    ("Cincinnati", "Louisville"), ("Toledo", "Bowling Green"), ("Miami (OH)", "Ohio"),
    ("Fresno State", "San José State"), ("Utah State", "BYU"), ("UTEP", "New Mexico State"),
    ("Memphis", "Ole Miss"), ("Penn State", "Ohio State"), ("Wake Forest", "NC State"),
]


def _rivals():
    pairs = RIVALRIES + list(getattr(config, "EXTRA_RIVALRIES", []))
    return {frozenset(p) for p in pairs}


def _miles(lat1, lon1, lat2, lon2):
    if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in (lat1, lon1, lat2, lon2)):
        return None
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 3959 * 2 * math.asin(math.sqrt(a))


def _utc_offset(tz, when):
    try:
        return ZoneInfo(tz).utcoffset(when.to_pydatetime() if hasattr(when, "to_pydatetime") else when) \
            .total_seconds() / 3600
    except Exception:
        return None


class Situations:
    """Builds per-team schedules once, then flags any game."""

    def __init__(self, data):
        g = data["games"]
        self.teams = data["teams"]
        self.venues = data["venues"]
        self.rivals = _rivals()
        self.lines = data.get("_line_by_game", {})
        sides = pd.concat([
            g.assign(team=g["home"], opp=g["away"], pf=g["home_pts"], pa=g["away_pts"], is_home=True),
            g.assign(team=g["away"], opp=g["home"], pf=g["away_pts"], pa=g["home_pts"], is_home=False),
        ])
        self.sched = {t: df.sort_values("start").reset_index(drop=True)
                      for t, df in sides.groupby("team")}

    # ---- helpers ----
    def _is_rivalry(self, a, b):
        return frozenset((a, b)) in self.rivals

    def _top(self, team, R):
        n = getattr(config, "BIG_GAME_TOP_N", 25)
        return team in R.index and bool(R.at[team, "fbs"]) and R.at[team, "rank"] <= n

    def _big_game(self, team, opp, R):
        return self._top(opp, R) or self._is_rivalry(team, opp)

    def _neighbors(self, team, game_id):
        s = self.sched.get(team)
        if s is None:
            return None, None
        hit = s.index[s["id"] == game_id]
        if not len(hit):
            return None, None
        i = hit[0]
        prev = s.iloc[i - 1] if i > 0 else None
        nxt = s.iloc[i + 1] if i + 1 < len(s) else None
        return prev, nxt

    def _big_win(self, team, prev, R):
        """Won last game in a way that tends to cause a letdown."""
        if prev is None or not prev["completed"] or not prev["pf"] > prev["pa"]:
            return None
        opp = prev["opp"]
        if self._top(opp, R):
            return f"beat #{int(R.at[opp, 'rank'])} {opp}"
        if self._is_rivalry(team, opp):
            return f"rivalry win over {opp}"
        sp = self.lines.get(prev["id"])
        if sp is not None and not np.isnan(sp):
            dog_by = sp if prev["is_home"] else -sp  # positive = team was the underdog
            if dog_by >= getattr(config, "UPSET_DOG_POINTS", 7.0):
                return f"won as a {dog_by:g}-pt underdog vs {opp}"
        return None

    def _home_spot(self, team):
        info = self.teams.get(team, {})
        lat, lon, tz = info.get("lat"), info.get("lon"), info.get("tz")
        v = self.venues.get(info.get("venue_id"), {})
        return lat if lat is not None else v.get("lat"), lon if lon is not None else v.get("lon"), \
            tz or v.get("tz")

    # ---- main ----
    def flags(self, game, R, model_margin):
        """Return a list of {'type','team','text'} for one game.

        model_margin: home minus away, as projected (used to tell who is favored).
        """
        out = []
        home, away, gid = game["home"], game["away"], game["id"]
        for team, opp, fav in ((home, away, model_margin > 0), (away, home, model_margin < 0)):
            if team not in R.index or not bool(R.at[team, "fbs"]):
                continue
            prev, nxt = self._neighbors(team, gid)
            this_big = self._big_game(team, opp, R)
            if this_big or not fav:
                continue  # spots only apply when a team should handle a lesser opponent
            prev_big = prev is not None and self._big_game(team, prev["opp"], R)
            next_big = nxt is not None and self._big_game(team, nxt["opp"], R)
            if prev_big and next_big:
                out.append({"type": "Sandwich", "team": team,
                            "text": f"{team} between {prev['opp']} and {nxt['opp']}"})
                continue
            why = self._big_win(team, prev, R)
            if why:
                out.append({"type": "Letdown", "team": team, "text": f"{team} {why}"})
            if next_big:
                out.append({"type": "Look-ahead", "team": team,
                            "text": f"{team} plays {nxt['opp']} next"})

        # Rest edge
        rest = {}
        for team in (home, away):
            prev, _ = self._neighbors(team, gid)
            rest[team] = (game["start"] - prev["start"]).days if prev is not None else None
        rh, ra = rest[home], rest[away]
        if rh is not None and ra is not None:
            bye = getattr(config, "BYE_DAYS", 13)
            short = getattr(config, "SHORT_WEEK_DAYS", 5)
            for t, mine, theirs, o in ((home, rh, ra, away), (away, ra, rh, home)):
                if mine >= bye and theirs < bye:
                    out.append({"type": "Rest edge", "team": t,
                                "text": f"{t} off a bye ({mine} days rest vs {theirs})"})
                elif theirs <= short and mine > short:
                    out.append({"type": "Rest edge", "team": t,
                                "text": f"{o} on a short week ({theirs} days rest vs {mine})"})

        # Long trip (true road games only)
        if not game["neutral"]:
            hlat, hlon, htz = self._home_spot(home)
            v = self.venues.get(game.get("venue_id"), {})
            vlat = v.get("lat") if v.get("lat") is not None else hlat
            vlon = v.get("lon") if v.get("lon") is not None else hlon
            vtz = v.get("tz") or htz
            alat, alon, atz = self._home_spot(away)
            miles = _miles(alat, alon, vlat, vlon)
            zones = None
            if atz and vtz and pd.notna(game["start"]):
                a, b = _utc_offset(atz, game["start"]), _utc_offset(vtz, game["start"])
                zones = abs(a - b) if a is not None and b is not None else None
            min_mi = getattr(config, "LONG_TRIP_MILES", 1500)
            min_tz = getattr(config, "LONG_TRIP_TIMEZONES", 2)
            if (miles and miles >= min_mi) or (zones and zones >= min_tz):
                bits = []
                if miles:
                    bits.append(f"{miles:,.0f} mi")
                if zones:
                    bits.append(f"{zones:g} time zone{'s' if zones != 1 else ''}")
                out.append({"type": "Long trip", "team": away,
                            "text": f"{away} travels {', '.join(bits)}"})
        return out


def against(flag):
    """Which team does this flag argue against? (Rest edge argues FOR its team.)"""
    return flag["type"] != "Rest edge"
