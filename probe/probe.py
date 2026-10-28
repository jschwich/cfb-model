import json, pathlib, requests
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
     "Accept": "text/html,application/json,*/*"}
URLS = {
 "sao": "https://www.scoresandodds.com/ncaaf",
 "sao_consensus": "https://www.scoresandodds.com/ncaaf/consensus-picks",
 "sbd": "https://www.sportsbettingdime.com/ncaaf/public-betting-trends/",
 "covers_cons": "https://contests.covers.com/consensus/topconsensus/ncaaf/overall",
 "oddsshark": "https://www.oddsshark.com/ncaaf/consensus-picks",
 "vi": "https://www.vegasinsider.com/college-football/betting-trends/",
 "pregame": "https://pregame.com/game-center",
 "vsin_tomorrow": "https://data.vsin.com/college-football/betting-splits/?source=DK&view=tomorrow",
 "vsin_sat": "https://data.vsin.com/college-football/betting-splits/?source=DK&date=2026-10-31",
}
out = pathlib.Path("probe/out"); out.mkdir(exist_ok=True)
summary = {}
for k, u in URLS.items():
    try:
        r = requests.get(u, headers=H, timeout=30)
        (out / f"{k}.txt").write_text(r.text)
        summary[k] = {"status": r.status_code, "len": len(r.text), "ctype": r.headers.get("content-type")}
    except Exception as e:
        summary[k] = {"error": repr(e)}
(out / "summary.json").write_text(json.dumps(summary, indent=1))
print(json.dumps(summary, indent=1))
