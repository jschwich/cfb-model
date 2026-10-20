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
- **All games:** market line versus model line for the whole slate, plus projected scores and rough first-half numbers.
- **Power ratings:** every FBS team in points versus an average team, split into offense and defense, with this season's data weighted against the preseason prior.
- **Situational flags:** tags on games where a classic handicapping spot applies: letdown, look-ahead, sandwich, rest edge (bye or short week), and long trip. They are information only and never change the model's numbers. A value pick that a spot works against gets a ⚠ warning.
- **Track record:** the model graded week by week against closing lines, using only information it had before each week. This is how you know whether to trust it.

Raw numbers are in `docs/data/*.csv` if you want them in a spreadsheet.

## How the model works

1. **Per-play efficiency.** For every game it takes each offense's EPA per play and success rate (garbage time excluded) plus points scored, all from CollegeFootballData.
2. **Opponent adjustment.** A ridge regression treats each game's stats as offense strength plus opponent defense plus home field, so a team that played weak defenses doesn't look inflated. FCS opponents get their own baseline.
3. **Convert to points.** Each adjusted stat is turned into points using this season's scores, then blended: 45% EPA, 25% success rate, 30% points.
4. **Preseason prior.** Last year's SP+, 247 team talent, and returning production give each team a starting point. It fades as games are played; after 6 games this season's data carries 75% of the weight.
5. **FEI blend.** Brian Fremeau's FEI (bcftoys.com) is mixed in at 30% as an independent second opinion. If that site can't be reached, the model runs without it.
6. **Projection.** Home rating minus away rating plus home field (fit from the data, about 2–3.5 pts) gives the spread; offense versus defense gives each side's points and the total.
7. **Value.** Edges of 4+ pts on spreads or 5+ on totals are flagged Lean; 7+ and 8+ are Strong.

8. **Situational flags (info only).** A "big game" is one against a model top-25 team or a rivalry opponent. *Letdown*: a favored team coming off a big win (beat a top-25 team, a rivalry win, or a win as a 7+ pt underdog). *Look-ahead*: a favored team with a big game next week. *Sandwich*: a favored team between two big games. *Rest edge*: one team off a bye (13+ days) or the other on a short week (5 or fewer days). *Long trip*: a road team traveling 1,500+ miles or 2+ time zones. The track record shows how each spot has done this season.

All of these knobs live in `config.py` with plain-English comments. Edit a number on GitHub (pencil icon) and the next run uses it.

## Caveats

- Closing lines are sharp. A large edge often means the model is missing news, such as an injury, a QB change, or weather. Check before betting.
- The first two weeks of a season lean heavily on the preseason prior.
- The free API tier allows 1,000 calls a month; this uses about 3 per run, roughly 370 a month.
