import json, pathlib, requests
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
     "Accept": "text/html,application/json,*/*"}
URLS = {
 "an_v1": "https://api.actionnetwork.com/web/v1/scoreboard/ncaaf?period=game&bookIds=15,30,68,69,71,75,79&division=FBS&date=20261031",
 "an_v1_week": "https://api.actionnetwork.com/web/v1/scoreboard/ncaaf?period=game&bookIds=15,30&division=FBS&week=9&seasonType=reg",
 "an_v2": "https://api.actionnetwork.com/web/v2/scoreboard/ncaaf?bookIds=15,30&periods=event&division=FBS&date=20261031",
 "an_page": "https://www.actionnetwork.com/ncaaf/public-betting",
 "vsin": "https://data.vsin.com/college-football/betting-splits/",
 "vsin_dk": "https://data.vsin.com/college-football/betting-splits/?source=DK",
 "thespread": "https://www.thespread.com/ncaa-college-football-public-betting-chart/",
}
out = pathlib.Path("probe/out"); out.mkdir(exist_ok=True)
summary = {}
for k, u in URLS.items():
    try:
        r = requests.get(u, headers=H, timeout=30)
        (out / f"{k}.txt").write_text(r.text[:400000])
        summary[k] = {"status": r.status_code, "len": len(r.text), "ctype": r.headers.get("content-type")}
    except Exception as e:
        summary[k] = {"error": repr(e)}
(out / "summary.json").write_text(json.dumps(summary, indent=1))
print(json.dumps(summary, indent=1))
