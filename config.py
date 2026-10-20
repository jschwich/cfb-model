"""
Model settings. Everything you might want to tweak lives here.
Change a number, save, and the next run uses it.
"""

# ---------- Season ----------
YEAR = None            # None = figure out the current season automatically

# ---------- Data ----------
# Sportsbooks to use for the market line, in order of preference.
LINE_PROVIDERS = ["DraftKings", "ESPN Bet", "FanDuel", "Bovada",
                  "William Hill (New Jersey)", "consensus"]

# ---------- Opponent adjustment ----------
# How strongly each team's numbers are pulled toward average before the
# data speaks for itself. Bigger = more cautious early in the season.
# Tuned on 2022-2025 (walk-forward, ~2,800 games); see tools/tune.py.
RIDGE_ALPHA_PER_PLAY = 40    # for per-play stats (weighted by plays)
RIDGE_ALPHA_PER_GAME = 0.3   # for per-game stats (points)

# How much each opponent-adjusted stat counts in the rating (sums to 1).
# Per-play efficiency predicts future results better than points scored.
# "expl" = explosiveness (EPA on successful plays). Tested 2022-2025: it made
# spreads and totals worse at every weight tried, so it's off.
STAT_WEIGHTS = {"ppa": 0.30, "sr": 0.20, "pts": 0.50, "expl": 0.0}

# Totals are built separately: what predicts the spread best (more weight on
# points, lighter shrinkage, heavier prior) made totals worse in testing.
TOTAL_STAT_WEIGHTS = {"ppa": 0.45, "sr": 0.25, "pts": 0.30, "expl": 0.0}
TOTAL_RIDGE_ALPHA_PER_GAME = 1.0
TOTAL_RIDGE_ALPHA_PER_PLAY = 70
TOTAL_PRIOR_REGRESS = 0.6
TOTAL_PRIOR_FADE_GAMES = 2.0

# ---------- Pace (totals) ----------
# Teams that run more plays than average push totals up. Tempo is opponent-
# adjusted from full-game play counts; this is points added per extra play.
# 0 turns pace off (and skips its API call).
PACE_PTS_PER_PLAY = 0.2     # tuned: 0.15-0.25 best; moves totals only
PACE_ALPHA = 3.0             # shrinkage for tempo, in games

# ---------- Preseason prior ----------
# Starting point for each team, built from last year's SP+, roster talent,
# and returning offensive production. It fades as games are played:
#   weight on this season's data = games / (games + PRIOR_FADE_GAMES)
PRIOR_FADE_GAMES = 3.0       # tuned (was 2.0)
PRIOR_REGRESS = 0.80         # how much of last year's rating carries over (tuned; was 0.60)
TALENT_PTS_PER_SD = 3.0      # points per 1 SD of 247 team talent
RETURNING_PTS_PER_SD = 2.5   # points (offense) per 1 SD of returning PPA %

# ---------- Outside opinions ----------
# Independent ratings blended into the final margin rating, as shares.
#   fei = Brian Fremeau's FEI (bcftoys.com)   sp = Bill Connelly's SP+   fpi = ESPN's FPI
# Whatever isn't given to these stays with the model's own rating.
USE_FEI = True
# Past weekly values of these aren't available, so their weights can't be tuned
# yet. The original 30% is kept, split evenly; data/snapshots/ saves them each
# week so next season they can be tested properly.
OUTSIDE_WEIGHTS = {"fei": 0.10, "sp": 0.10, "fpi": 0.10}
OUTSIDE_REFRESH_HOURS = 12   # SP+/FPI change weekly; refetch at most this often
# FEI is points per possession; this converts it to points per game. 18 matches
# SP+ and FPI (fit on 2026 ratings: 17.9 and 19.2); the old 12 squeezed spreads.
FEI_POSSESSIONS = 18.0

# ---------- Calibration ----------
# Shrinkage makes raw ratings too cautious (spreads too narrow), which creates
# fake underdog "value". Once enough games have been graded out of sample, the
# model measures how much it undershoots real margins and stretches ratings to match.
CALIBRATE = True
CALIBRATION_MIN_GAMES = 100     # graded games needed before calibrating
CALIBRATION_MIN, CALIBRATION_MAX = 0.8, 1.6
# Since tuning, raw ratings are close to right (past-season slope ~1.04), so the
# in-season measurement only gets partial trust: weight = games / (games + this).
CALIBRATION_SHRINK_GAMES = 500

# ---------- Home field ----------
HFA_MIN, HFA_MAX, HFA_DEFAULT = 1.5, 3.5, 2.5   # points

# ---------- Team totals, quarters and halves ----------
# Each team's points are split by quarter using the league average, nudged by
# that team's offense and the opponent's defense. Their tendencies are trusted
# more as games pile up: weight = games / (games + PERIOD_SHRINK_GAMES).
PERIOD_SHRINK_GAMES = 25.0   # high on purpose: team quarter tendencies tested as mostly noise
# Fallback split, only used before there are enough quarter scores.
FIRST_HALF_SPREAD_SHARE = 0.55
FIRST_HALF_TOTAL_SHARE = 0.52

# ---------- Value flags ----------
SPREAD_LEAN, SPREAD_STRONG = 4.0, 7.0   # points of disagreement with market
TOTAL_LEAN, TOTAL_STRONG = 5.0, 8.0
# Markets are sharp, so the model's opinion is only partly trusted when
# turning an edge into a cover probability (0 = trust market, 1 = trust model).
MODEL_TRUST = 0.5
MARGIN_SD = 16.0   # typical miss of a projected margin, in points
TOTAL_SD = 17.0

# ---------- Backtest ----------
BACKTEST_FIRST_WEEK = 4   # start grading the model from this week on

# ---------- Situational flags (info only: they never change the numbers) ----------
# A "big game" is against a model top-N team or a rivalry opponent.
BIG_GAME_TOP_N = 25
UPSET_DOG_POINTS = 7.0       # a win as an underdog of this many points counts as a big win
BYE_DAYS = 13                # this many days since last game = coming off a bye
SHORT_WEEK_DAYS = 5          # this many days or fewer = short week
LONG_TRIP_MILES = 1500
LONG_TRIP_TIMEZONES = 2
# Add rivalries the built-in list misses, using CFBD team names, e.g.
# EXTRA_RIVALRIES = [("Appalachian State", "Georgia Southern")]
EXTRA_RIVALRIES = []

# ---------- Your picks ----------
# Published-to-web CSV link of the Pick Log tab (File > Share > Publish to web).
# Only rows for PICKS_PERSON are read. Leave the link blank to turn this off.
PICKS_CSV_URL = ("https://docs.google.com/spreadsheets/d/e/2PACX-1vTM0w9nlD9bsm-FkKBgZtdoANkl9CPovwThzyEgC3"
                 "-Ri7lbS-MtGUZEhW3xyL7uoTehU2vGlyI7ReNU/pub?gid=1435131979&single=true&output=csv")
PICKS_PERSON = "Jerome"

# ---------- My adjustments ----------
# When you think the model is wrong about a team (QB injury or return, coaching
# change, a team that has clearly changed), add a line here. Points move the
# team's rating, so spreads move by that much; totals don't change.
#   ("Team name", points, from week, "why", until week)   <- "until week" is optional
# Week numbers are the model's (the "Week 8" shown at the top of the dashboard).
# Team names must match the power ratings table exactly.
MY_ADJUSTMENTS = [
    # ("Ohio State", 3.0, 9, "QB back from injury"),
    # ("Northwestern", -4.0, 8, "starting QB out", 10),
]
