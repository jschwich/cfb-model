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
RIDGE_ALPHA_PER_PLAY = 175   # for per-play stats (weighted by plays)
RIDGE_ALPHA_PER_GAME = 2.5   # for per-game stats (points)

# How much each opponent-adjusted stat counts in the rating (sums to 1).
# Per-play efficiency predicts future results better than points scored.
STAT_WEIGHTS = {"ppa": 0.45, "sr": 0.25, "pts": 0.30}

# ---------- Preseason prior ----------
# Starting point for each team, built from last year's SP+, roster talent,
# and returning offensive production. It fades as games are played:
#   weight on this season's data = games / (games + PRIOR_FADE_GAMES)
PRIOR_FADE_GAMES = 2.0
PRIOR_REGRESS = 0.60         # how much of last year's rating carries over
TALENT_PTS_PER_SD = 3.0      # points per 1 SD of 247 team talent
RETURNING_PTS_PER_SD = 2.5   # points (offense) per 1 SD of returning PPA %

# ---------- FEI blend ----------
# Brian Fremeau's FEI (bcftoys.com), used as an independent second opinion.
# FEI is points per possession; multiply by possessions to get points per game.
USE_FEI = True
FEI_WEIGHT = 0.30            # share of the final margin rating from FEI
FEI_POSSESSIONS = 12.0

# ---------- Home field ----------
HFA_MIN, HFA_MAX, HFA_DEFAULT = 1.5, 3.5, 2.5   # points

# ---------- Partial-game estimates ----------
# Rough share of full-game spread/total that shows up in the first half.
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
