# CFB Model

An FBS power-rating and value-finding model in the style of SP+. Every six hours it pulls fresh data, re-rates every team, projects the next slate of games, compares those projections with the betting market, and publishes a dashboard you can open on your phone.

## One-time setup (about 15 minutes, no coding)

You need two free accounts. Nothing here costs money.

**1. Get a CollegeFootballData API key**
Go to https://collegefootballdata.com/key, enter your email, and copy the key they send you.

**2. Put this project on GitHub**
- Sign up at https://github.com if you don't have an account.
- Click **+** (top right), then **New repository**. Name it `cfb-model`, choose **Public** (required for the free dashboard page), and click **Create repository**.
- On the new repo page, click **uploading an existing file**, drag in everything from this folder, and click **Commit changes**.
  - The `.github` folder is hidden on Mac and Windows. If it doesn't upload, on the repo page click **Add file**, then **Create new file**, name it `.github/workflows/update.yml`, paste in the contents of that file, and commit.

**3. Add your API key as a secret**
In the repo, go to **Settings**, then **Secrets and variables**, then **Actions**, then **New repository secret**.
Name: `CFBD_API_KEY`. Value: your key. Click **Add secret**.

**4. Turn on the dashboard page**
In **Settings**, then **Pages**, set Source to **Deploy from a branch**, Branch to **main**, folder to **/docs**, and click **Save**.

**5. Run it the first time**
On the **Actions** tab, click **Update model**, then **Run workflow**. After about a minute, your dashboard appears at
`https://<your-github-username>.github.io/cfb-model/`
Bookmark it. From then on it updates itself every 6 hours.

## What's on the dashboard

- **Value board:** games where the model disagrees with the betting line by enough to matter, ranked by size of the edge.
- **All games:** market line versus model line for the whole week, plus projected scores, the opening line and how far it has moved toward or away from the model. Games already played stay on the list with the model's pre-kickoff numbers, the final score, and whether its picks won.
- **Team totals & periods:** the model's team totals plus 1Q, 3Q, 1H and 2H spreads and totals for every game. These are model numbers only (the free data has no lines for these markets), so compare them with your book.
- **Power ratings:** every FBS team in points versus an average team, split into offense and defense, with this season's data weighted against the preseason prior.
- **Situational flags:** tags on games where a classic handicapping spot applies: letdown, look-ahead, sandwich, rest edge (bye or short week), and long trip. They are information only and never change the model's numbers. A value pick that a spot works against gets a ⚠ warning.
- **Track record:** the model graded week by week against closing lines, using only information it had before each week. This is how you know whether to trust it.

Raw numbers are in `docs/data/*.csv` if you want them in a spreadsheet.

## How the model works

1. **Per-play efficiency.** For every game it takes each offense's EPA per play and success rate (garbage time excluded) plus points scored, all from CollegeFootballData.
2. **Opponent adjustment.** A ridge regression treats each game's stats as offense strength plus opponent defense plus home field, so a team that played weak defenses doesn't look inflated. FCS opponents get their own baseline.
3. **Convert to points.** Each adjusted stat is turned into points using this season's scores, then blended. For spreads: 30% EPA, 20% success rate, 50% points. Totals use their own mix (45/25/30) and their own shrinkage, because what predicts margins best made totals worse in testing. Explosiveness was tested and left out (it hurt both).
4. **Preseason prior.** Last year's SP+, 247 team talent, and returning production give each team a starting point. It fades as games are played; after 6 games this season's data carries about two-thirds of the weight.
5. **Outside opinions.** FEI (bcftoys.com), SP+ and ESPN's FPI are each mixed in at 10% as independent second opinions. If one can't be reached, the model runs without it. Their weights can't be tuned on past seasons (only end-of-season values survive), so every run saves them to `data/snapshots/` for testing next year.
6. **Wind.** Each run pulls the forecast for every game (Open-Meteo, free; domes skipped) and takes 0.5 pts off the total for each mph of wind above 12 mph over the first three hours. Windy games get a tag. Tested on 2023–2025: below ~12 mph wind does nothing; the market already prices it, so this mainly stops false Over flags.
7. **Pace.** Opponent-adjusted tempo from full-game play counts adds about 0.2 points per extra expected play to the total (spreads unchanged).
8. **Calibration.** Shrinkage makes raw ratings too cautious, which shows up as fake underdog value. Once 100+ games have been graded, the model measures how much its past projections undershot real margins (using only games before each week) and stretches rating gaps to match. Totals are left alone. The multiplier is shown at the top of the dashboard.
9. **Projection.** Home rating minus away rating plus home field (fit from the data, about 2–3.5 pts) gives the spread; offense versus defense gives each side's points and the total.
10. **Team totals and periods.** Each team's projected points are its team total. Quarter numbers come from splitting those points by quarter: the league-average split, adjusted for how this offense spreads its scoring and how this opponent's defense spreads what it allows, trusted more as games pile up. Halves are quarters added together; overtime is left out. The track record compares this against a flat league-average split.
11. **Value.** Edges of 4+ pts on spreads or 5+ on totals are flagged Lean; 7+ and 8+ are Strong.

12. **Situational flags (info only).** A "big game" is one against a model top-25 team or a rivalry opponent. *Letdown*: a favored team coming off a big win (beat a top-25 team, a rivalry win, or a win as a 7+ pt underdog). *Look-ahead*: a favored team with a big game next week. *Sandwich*: a favored team between two big games. *Rest edge*: one team off a bye (13+ days) or the other on a short week (5 or fewer days). *Long trip*: a road team traveling 1,500+ miles or 2+ time zones. The track record shows how each spot has done this season.

All of these knobs live in `config.py` with plain-English comments. Edit a number on GitHub (pencil icon) and the next run uses it.

## Your own adjustments

When you think the model has a team wrong (QB hurt or back, coaching change, a team that has clearly changed), open `config.py` on GitHub, click the pencil icon, and add a line under `MY_ADJUSTMENTS`:

```
MY_ADJUSTMENTS = [
    ("Ohio State", 3.0, 9, "QB back from injury"),
    ("Northwestern", -4.0, 8, "starting QB out", 10),   # last number = until week 10
]
```

Points move the team's rating (and every spread it plays in) by that much; totals don't change. Use the team name exactly as the power ratings table spells it, and the week number shown at the top of the dashboard. Commit, then run the workflow (or wait for the next 6-hour run). Games with an adjustment get a blue tag, and the track record shows whether your adjustments have made the model more or less accurate on games already played.

## Your picks

The dashboard reads your rows from the published Pick Log (`PICKS_CSV_URL` in `config.py`) and shows, for each pick, what the model projected before that week at the number you took, plus your record when the model agreed versus disagreed.

## Where the model is (and isn't) useful

- **Early in the week.** Against opening lines the model's bigger edges have run about 52–53% (2022–2026), and lines have moved toward its number about 56% of the time; against closing lines it's about 49%. Check the dashboard Sunday/Monday and act before lines move.
- **Not in Power 4 vs non-Power 4 spreads.** Its picks there covered about 42% over 2022–2026, so those games are marked low trust instead of flagged as value.
- **Shop lines.** Taking the best of the books in the data added about half a point per game.

## Caveats

- Closing lines are sharp. A large edge often means the model is missing news, such as an injury, a QB change, or weather. Check before betting.
- The first two weeks of a season lean heavily on the preseason prior.
- The free API tier allows 1,000 calls a month; this uses about 4 per run plus SP+/FPI twice a day, roughly 600 a month.
- Settings were tuned on 2022–2025 with `tools/tune.py` (each week graded using only earlier games) and checked on 2026, which tuning never saw. Spread miss improved from 12.64 to 12.51 pts on past seasons and 12.28 to 12.21 on 2026; totals from 12.88 to 12.78 and 12.73 to 12.67. The market's own miss is about 12.1 on spreads, so treat the model as a second opinion, not an edge by itself; against the spread its Lean+ picks have been about 49% on past seasons.
