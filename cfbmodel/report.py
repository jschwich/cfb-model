"""Writes the dashboard (docs/index.html) and the raw CSVs (docs/data/)."""
import html
from pathlib import Path

import numpy as np
import pandas as pd

import config
from .situations import against

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

E = html.escape


def _n(x, fmt="{:.1f}", dash="–"):
    try:
        if x is None or (isinstance(x, float) and np.isnan(x)) or pd.isna(x):
            return dash
    except (TypeError, ValueError):
        pass
    return fmt.format(x)


def _spread_txt(team, s):
    if pd.isna(s):
        return "–"
    return f"{E(team)} PK" if abs(s) < 0.25 else f"{E(team)} {s:+.1f}"


def _fav_line(home, away, spread):
    """'Home -7.0' style: always shown from the favorite's side."""
    if pd.isna(spread):
        return "–"
    return _spread_txt(home, spread) if spread <= 0 else _spread_txt(away, -spread)


def _when(ts):
    if pd.isna(ts):
        return "TBA"
    t = ts.tz_convert("America/New_York")
    return t.strftime("%a %-m/%-d, %-I:%M %p")


def _rec(r):
    if not r or not (r["w"] + r["l"]):
        return "–"
    return f"{r['w']}–{r['l']} ({r['pct']:.0%})"


_FLAG_CLASS = {"Letdown": "f-let", "Look-ahead": "f-look", "Sandwich": "f-sand",
               "Rest edge": "f-rest", "Long trip": "f-trip"}


def _chips(flags, with_text=False):
    if not flags:
        return ""
    out = []
    for f in flags:
        who = "vs" if against(f) else "for"
        label = f"{f['type']} ({who} {f['team']})"
        out.append(f"<span class='chip {_FLAG_CLASS.get(f['type'], '')}' title='{E(f['text'])}'>"
                   f"{E(label)}</span>")
        if with_text:
            out[-1] += f"<span class=ftxt>{E(f['text'])}</span>"
    return "<div class=chips>" + "".join(out) + "</div>"


def _adj_chip(r):
    out = ""
    t = r.get("my_adj_text") if hasattr(r, "get") else None
    if t and not (isinstance(t, float) and np.isnan(t)):
        out += f"<span class='chip f-mine'>Your adjustment: {E(t)}</span>"
    ig = [x for x in (r.get("away"), r.get("home")) if x in getattr(config, "IGNORE_PRIOR", [])]
    if ig:
        out += (f"<span class='chip f-mine'>Your setting: preseason guess ignored for "
                f"{E(' and '.join(ig))}</span>")
    return f"<div class=chips>{out}</div>" if out else ""


def _my_adj(m):
    if m is None:
        return ""
    if not m.get("games"):
        return ("<h3>Your adjustments</h3><p class=muted>None of your adjustments has covered a "
                "finished game yet.</p>")
    better = m["err_with"] < m["err_without"]
    rows = "".join(f"<tr><td>{int(r['wk'])}</td><td class=gm>{E(r['game'])}</td><td>{r['shift']:+g}</td>"
                   f"<td>{r['err_without']:.1f}</td><td>{r['err_with']:.1f}</td></tr>"
                   for _, r in m["rows"].iterrows())
    side = f"{m['side_w']}–{m['side_l']}" if m["side_w"] + m["side_l"] else "–"
    return ("<h3>Your adjustments</h3><div class=stats>"
            f"<div><span>{m['games']}</span>games affected</div>"
            f"<div><span>{m['err_without']:.1f} → {m['err_with']:.1f}</span>avg miss without → with</div>"
            f"<div><span>{side}</span>side you pushed toward, ATS</div></div>"
            f"<p class=muted>{'Your adjustments made the model more accurate so far.' if better else 'So far the model was more accurate without your adjustments.'} "
            "Small samples swing a lot.</p>"
            "<div class=tw><table><thead><tr><th>Wk</th><th>Game</th><th>Shift</th><th>Miss without</th>"
            "<th>Miss with</th></tr></thead><tbody>" + rows + "</tbody></table></div>")


def _badge(res):
    return {"W": " <b class=win>✓ W</b>", "L": " <b class=loss>✗ L</b>", "P": " <span class=muted>push</span>"}.get(res or "", "")


def _played(games):
    """Value plays from games already finished this week, with results."""
    if "final" not in games:
        return ""
    fin = games[games["final"].astype(bool)]
    items = []
    for _, r in fin.iterrows():
        sc = f"{E(r['away'])} {int(r['away_pts'])} – {E(r['home'])} {int(r['home_pts'])}"
        if r.get("spread_tier"):
            items.append(f"<li><span class='tier {r['spread_tier'].lower()}'>{r['spread_tier']}</span> "
                         f"{E(r['spread_pick_text'])}{_badge(r.get('spread_result'))} "
                         f"<span class=muted>· Final: {sc}</span></li>")
        if r.get("total_tier"):
            items.append(f"<li><span class='tier {r['total_tier'].lower()}'>{r['total_tier']}</span> "
                         f"{E(r['away'])} @ {E(r['home'])} {E(r['total_pick_text'])}{_badge(r.get('total_result'))} "
                         f"<span class=muted>· Final: {sc}</span></li>")
    if not items:
        return ""
    return "<h3>Already played this week</h3><ul class=played>" + "".join(items) + "</ul>"


def _value_cards(games):
    if not len(games):
        return "<p class=muted>No games on the slate yet.</p>"
    played = _played(games)
    if "final" in games:
        games = games[~games["final"].astype(bool)]
    items = []
    for _, r in games.iterrows():
        if r["spread_tier"]:
            items.append((r["spread_edge"], r, "spread"))
        if r["total_tier"]:
            items.append((r["total_edge"], r, "total"))
    if not items:
        return ("<p class=muted>No edges big enough to flag in the games still to play "
                f"(spreads {config.SPREAD_LEAN:g}+ pts, totals {config.TOTAL_LEAN:g}+ pts).</p>" + played)
    items.sort(key=lambda x: -x[0])
    cards = []
    for edge, r, kind in items:
        tier = r[f"{kind}_tier"]
        if kind == "spread":
            pick = E(r["spread_pick_text"])
            detail = (f"Model {_fav_line(r['home'], r['away'], r['model_spread'])} · "
                      f"Market {_fav_line(r['home'], r['away'], r['market_spread'])}")
            mv = _move(r)
            if mv:
                detail += mv.replace("<div class=muted>", " · ").replace("</div>", "")
            prob = r["spread_prob"]
        else:
            pick = E(r["total_pick_text"])
            detail = f"Model {r['model_total']:.1f} · Market {r['market_total']:g}"
            prob = r["total_prob"]
        warn = ""
        if kind == "spread" and r["flag_conflict"]:
            warn = "<div class=warn>⚠ A situational spot works against this pick</div>"
        cards.append(
            f"<div class='card {tier.lower()}'><div class=row1><span class='tier {tier.lower()}'>{tier}</span>"
            f"<span class=edge>{edge:.1f} pt edge</span></div>"
            f"<div class=pick>{pick}</div>"
            f"<div class=matchup>{E(r['away'])} @ {E(r['home'])}{' (neutral)' if r['neutral'] else ''} · {_when(r['start'])}</div>"
            f"<div class=detail>{detail} · est. win prob {_n(prob, '{:.0%}')}</div>"
            f"{warn}{_sharp_note(r, kind)}{_chips(r['flags'], with_text=True)}{_adj_chip(r)}{_wind(r) if kind == 'total' else ''}</div>")
    return "<div class=cards>" + "".join(cards) + "</div>" + played


def _wind(r):
    w = r.get("wind")
    if w is None or pd.isna(w):
        if r.get("dome") is True:
            return "<div class=chips><span class='chip f-low'>Dome</span></div>"
        return ""
    if w < getattr(config, "WIND_SHOW_MPH", 12):
        return ""
    cut = config.WIND_PTS_PER_MPH * max(w - config.WIND_START_MPH, 0)
    g = r.get("gust")
    gust = f", gusts {g:.0f}" if g is not None and not pd.isna(g) else ""
    return (f"<div class=chips><span class='chip f-trip'>Wind {w:.0f} mph{gust}"
            f"{f' · total −{cut:.1f}' if cut >= 0.1 else ''}</span></div>")


def _low_trust(r):
    if not r.get("cross_tier") or not r.get("spread_tier_raw") or r.get("spread_tier"):
        return ""
    return ("<div class=chips><span class='chip f-low'>Power 4 vs non-Power 4: model's spread view is "
            "low trust here (≈42% ATS historically)</span></div>")


def _move(r, short=False):
    """'open Texas -4.5 · moved 2.5 toward model' style note."""
    op = r.get("open_spread")
    if op is None or pd.isna(op):
        return ""
    mv = r.get("spread_move")
    base = f"open {_fav_line(r['home'], r['away'], op)}"
    if mv is None or pd.isna(mv) or mv == 0:
        return f"<div class=muted>{base}{'' if short else ' · no move'}</div>"
    tw = r.get("move_toward_model")
    cls, word = ("win", "toward") if tw and tw > 0 else ("loss", "away from")
    return f"<div class=muted>{base} · <span class={cls}>moved {abs(mv):g} {word} model</span></div>"


def _games_table(games):
    if not len(games):
        return "<p class=muted>No upcoming games.</p>"
    rows = []
    for _, r in games.iterrows():
        fin = bool(r.get("final"))
        tiers = " ".join(f"<span class='tier {t.lower()}'>{t[0]}</span>"
                         for t in (r["spread_tier"], r["total_tier"]) if t)
        if fin:
            when = (f"<div class=final>Final: {E(r['away'])} {int(r['away_pts'])} – "
                    f"{E(r['home'])} {int(r['home_pts'])}</div>")
            actual = f"<div class=muted>actual {int(r['away_pts'])}–{int(r['home_pts'])}</div>"
            tot_act = f"<div class=muted>actual {int(r['away_pts'] + r['home_pts'])}</div>"
        else:
            when, actual, tot_act = f"<div class=muted>{_when(r['start'])}</div>", "", ""
        pick_s = (f"<div class=muted>model likes {E(r['spread_pick_text'])}{_badge(r.get('spread_result'))}</div>"
                  if fin and r.get("spread_pick_text") else "")
        pick_t = (f"<div class=muted>{E(r['total_pick_text'])}{_badge(r.get('total_result'))}</div>"
                  if fin and r.get("total_pick_text") else "")
        rows.append(
            f"<tr class={'fin' if fin else 'live'}>"
            f"<td class=gm><b>{E(r['away'])}</b> @ <b>{E(r['home'])}</b>"
            f"{' <span class=muted>(N)</span>' if r['neutral'] else ''}"
            f"{when}{_chips(r['flags'])}{_sharp_chips(r)}{_adj_chip(r)}{_low_trust(r)}{_wind(r)}</td>"
            f"<td>{_fav_line(r['home'], r['away'], r['market_spread'])}{_move(r, short=True)}</td>"
            f"<td><b>{_fav_line(r['home'], r['away'], r['model_spread'])}</b>{pick_s}</td>"
            f"<td>{_n(r['market_total'], '{:g}')}</td>"
            f"<td><b>{r['model_total']:.1f}</b>{pick_t}{tot_act}</td>"
            f"<td>{r['proj_away']:.0f}–{r['proj_home']:.0f}{actual}</td>"
            f"<td>{_fav_line(r['home'], r['away'], r['h1_spread'])} / {r['h1_total']:.1f}</td>"
            f"<td>{tiers}</td></tr>")
    return ("<div class=tw><table><thead><tr><th>Game</th><th>Market</th><th>Model</th>"
            "<th>Mkt total</th><th>Model total</th><th>Proj (A–H)</th><th>1H spread / total</th>"
            "<th></th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")


def _periods_table(games):
    if not len(games) or "1q_total" not in games:
        return "<p class=muted>No upcoming games.</p>"
    rows = []
    for _, r in games.iterrows():
        cells = [f"<td>{E(r['away'])} <b>{r['tt_away']:.1f}</b><br>{E(r['home'])} <b>{r['tt_home']:.1f}</b></td>"]
        for k in ("1q", "3q", "1h", "2h"):
            cells.append(f"<td>{_fav_line(r['home'], r['away'], r[k + '_spread'])}<br>"
                         f"<b>{r[k + '_total']:.1f}</b> <span class=muted>({r[k + '_away']:.1f}–{r[k + '_home']:.1f})</span></td>")
        rows.append(f"<tr><td class='gm stick'><b>{E(r['away'])}</b> @ <b>{E(r['home'])}</b>"
                    f"<div class=muted>{_when(r['start'])}</div></td>" + "".join(cells) + "</tr>")
    cards = []
    for _, r in games.iterrows():
        lines = [f"<tr><td>TT</td><td>–</td><td>–</td><td>{r['tt_away']:.1f}</td><td>{r['tt_home']:.1f}</td></tr>"]
        for k in ("1q", "3q", "1h", "2h"):
            lines.append(f"<tr><td>{k.upper()}</td><td>{_fav_line(r['home'], r['away'], r[k + '_spread'])}</td>"
                         f"<td><b>{r[k + '_total']:.1f}</b></td><td>{r[k + '_away']:.1f}</td><td>{r[k + '_home']:.1f}</td></tr>")
        cards.append(f"<div class=pcard><div><b>{E(r['away'])}</b> @ <b>{E(r['home'])}</b> "
                     f"<span class=muted>{_when(r['start'])}</span></div>"
                     f"<table><thead><tr><th></th><th>Spread</th><th>Total</th><th>{E(r['away'])[:12]}</th>"
                     f"<th>{E(r['home'])[:12]}</th></tr></thead><tbody>{''.join(lines)}</tbody></table></div>")
    phone = "<div class=pcards>" + "".join(cards) + "</div>"
    return phone + ("<div class='tw pwide'><table class=per><thead><tr><th class=stick>Game</th><th>Team totals</th>"
            "<th>1Q</th><th>3Q</th><th>1H</th><th>2H</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table></div>")


def _periods_grade(g):
    if g is None or not len(g):
        return ""
    def cell(m, f):
        if pd.isna(m):
            return "<td>–</td>"
        better = f - m
        return f"<td>{m:.2f} <span class=muted>vs {f:.2f}</span></td>" if not pd.isna(f) else f"<td>{m:.2f}</td>"
    rows = "".join(
        f"<tr><td>{E(r['period'])}</td><td>{int(r['games'])}</td>"
        + cell(r.get("tt_err"), r.get("flat_tt_err"))
        + cell(r.get("total_err"), r.get("flat_total_err"))
        + cell(r.get("spread_err"), r.get("flat_spread_err")) + "</tr>"
        for _, r in g.iterrows())
    return ("<h3>Team totals and periods</h3><p class=muted>Average miss in points, graded out of sample. "
            "The grey number is a flat split (every team scores the league-average share in each quarter); "
            "if the model's number isn't lower, the team-by-team quarter tendencies aren't adding anything yet.</p>"
            "<div class=tw><table><thead><tr><th>Period</th><th>Games</th><th>Team total miss</th>"
            "<th>Total miss</th><th>Spread miss</th></tr></thead><tbody>" + rows + "</tbody></table></div>")


def _picks_section(pk):
    if not pk:
        return ""
    G, S = pk
    if G is None or not len(G):
        return ""

    def tile(label, r):
        return f"<div><span>{_rec(r)}</span>{E(label)}</div>"

    head = ""
    if S:
        g = S["groups"]
        head = ("<div class=stats>" + "".join(tile(k, v) for k, v in g.items()) + "</div>")
        mk = "".join(f"<tr><td>{E(k)}</td><td>{_rec(v['agree'])}</td><td>{_rec(v['disagree'])}</td></tr>"
                     for k, v in S["by_market"].items())
        head += ("<div class=tw><table><thead><tr><th>Market</th><th>Model agreed</th><th>Model disagreed</th>"
                 "</tr></thead><tbody>" + mk + "</tbody></table></div>")

    def view(r):
        if pd.isna(r.get("model_view")):
            return "–", "<span class=muted>no model number</span>"
        if r["market"] == "Spread":
            v = r["model_view"]
            txt = f"{E(r['team'])} by {v:.1f}" if v >= 0 else f"{E(r['team'])} loses by {-v:.1f}"
        else:
            txt = f"{r['model_view']:.1f}"
        e = r["model_edge"]
        tag = (f"<span class='tier strong'>agrees +{e:.1f}</span>" if e > 0
               else f"<span class='tier lean'>disagrees {e:.1f}</span>")
        return txt, tag

    rows = []
    for _, r in G.iloc[::-1].iterrows():
        txt, tag = view(r)
        res = r.get("Result") or ""
        res_html = {"Win": "<b class=win>W</b>", "Loss": "<b class=loss>L</b>", "Push": "P"}.get(res, "<span class=muted>pending</span>")
        rows.append(f"<tr><td>{E(str(r.get('Week', '')))}</td><td class=gm>{E(r['Pick'])}</td>"
                    f"<td>{res_html}</td><td>{txt}</td><td>{tag}</td></tr>")
    table = ("<div class=tw><table><thead><tr><th>Week</th><th>Pick</th><th>Result</th><th>Model</th>"
             "<th>Model vs. your number</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")
    note = ("<p class=muted>The model's number is what it projected before that week's games, at the line "
            "you took. “Agrees +2.0” means the model had your side covering by 2 points. "
            f"{S['unmatched'] if S else 0} pick(s) couldn't be matched to a game or have no model number "
            "(early-season quarters, 2Q/4Q).</p>")
    return ("<section id=mine><h2>Your picks vs. the model</h2>" + head + note
            + "<h3>Every pick, newest first</h3>" + table + "</section>")


def _ratings_table(R):
    F = R[R["fbs"]].sort_values("rating", ascending=False)
    rows = []
    for t, r in F.iterrows():
        rows.append(
            f"<tr><td>{int(r['rank'])}</td><td class=gm><b>{E(t)}</b>"
            + (" <span class='chip f-mine'>preseason ignored</span>" if t in getattr(config, "IGNORE_PRIOR", []) else "")
            + (f" <span class='chip f-low'>new coach {r['coach_adj']:+g} preseason</span>"
               if r.get("coach_adj") and not pd.isna(r.get("coach_adj")) and t not in getattr(config, "IGNORE_PRIOR", []) else "")
            + f"<div class=muted>{E(str(r['conference'] or ''))}</div></td>"
            f"<td><b>{r['rating']:+.1f}</b></td><td>{r['off']:+.1f}</td><td>{r['def']:+.1f}</td>"
            f"<td>{int(r['games'])}</td><td>{r['data_weight']:.0%}</td>"
            f"<td>{r['data_rating']:+.1f}</td><td>{r['prior_rating']:+.1f}</td>"
            f"<td>{_n(r['fei_rating'], '{:+.1f}')}</td><td>{_n(r.get('sp_rating'), '{:+.1f}')}</td>"
            f"<td>{_n(r.get('fpi_rating'), '{:+.1f}')}</td></tr>")
    return ("<div class=tw><table id=rt><thead><tr><th>#</th><th>Team</th><th>Rating</th><th>Off</th>"
            "<th>Def</th><th>G</th><th>This yr wt</th><th>This yr</th><th>Prior</th><th>FEI</th><th>SP+</th><th>FPI</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")


def _track(bt_sum):
    if not bt_sum:
        return (f"<p class=muted>Grading starts after week {config.BACKTEST_FIRST_WEEK - 1}. "
                "Check back once a few weeks are in.</p>")
    s = bt_sum
    head = (
        "<div class=stats>"
        f"<div><span>{s['lined']}</span>games graded vs. a line</div>"
        f"<div><span>{_n(s['model_mae'])}</span>model avg miss (pts)</div>"
        f"<div><span>{_n(s['market_mae'])}</span>market avg miss (pts)</div>"
        f"<div><span>{_rec(s['ats_lean'])}</span>spread Lean+ ATS</div>"
        f"<div><span>{_rec(s['ats_strong'])}</span>spread Strong ATS</div>"
        f"<div><span>{_rec(s['ou_lean'])}</span>total Lean+ O/U</div>"
        f"<div><span>{_rec(s.get('ats_open_lean'))}</span>spread {config.SPREAD_LEAN:g}+ vs. <b>opening</b> line</div>"
        f"<div><span>{_rec(s.get('ats_open_strong'))}</span>spread {config.SPREAD_STRONG:g}+ vs. opening line</div>"
        f"<div><span>{_n((s.get('clv') or {}).get('pct'), '{:.0%}')}</span>of moves went toward the model (3+ pt edge at open)</div>"
        "</div><p class=muted>Each week is re-rated using only games played before it, then compared "
        "with that week's line. Lower miss is better; beating the market's miss is very hard. "
        "Break-even against -110 is 52.4%. FEI isn't blended in here because past FEI isn't available. "
        "The model has done better against opening lines than closing ones (2022–2026: about 52–53% at "
        "5+ pt edges vs. the opener), and lines have moved toward it about 56% of the time, so its "
        "opinion is worth most early in the week, before the market catches up.</p>")
    wk = "".join(
        f"<tr><td>{w['wk']}</td><td>{w['games']}</td><td>{_n(w['model_mae'])}</td>"
        f"<td>{_n(w['market_mae'])}</td><td>{_rec(w['ats_lean'])}</td></tr>" for w in s["weekly"])
    weekly = ("<div class=tw><table><thead><tr><th>Week</th><th>Games</th><th>Model miss</th>"
              "<th>Market miss</th><th>Lean+ ATS</th></tr></thead><tbody>" + wk + "</tbody></table></div>")
    fr = s.get("flags")
    hist = {}
    try:
        import json
        hist = json.loads((ROOT / "data" / "history" / "spots.json").read_text())
    except Exception:
        pass
    hs = hist.get("spots", {})
    types = sorted(set(fr["type"]) if fr is not None and len(fr) else set() | set(hs))
    if types:
        cur = {r["type"]: r for _, r in fr.iterrows()} if fr is not None and len(fr) else {}

        def wl(w, l):
            return f"{w}–{l} ({w / (w + l):.0%})" if w + l else "–"
        frows = "".join(
            f"<tr><td>{E(t)}</td><td>{wl(int(cur[t]['w']), int(cur[t]['l'])) if t in cur else '–'}</td>"
            f"<td>{wl(hs[t]['w'], hs[t]['l']) if t in hs else '–'}</td></tr>" for t in types)
        c = hist.get("conflict", {})
        cnote = ""
        if "True" in c and "False" in c:
            cnote = (f" Over {E(hist['years'])}, value picks with a ⚠ spot against them covered "
                     f"{c['True']['w'] / c['True']['n']:.0%} ({c['True']['n']} picks) vs. "
                     f"{c['False']['w'] / c['False']['n']:.0%} without.")
        flags = ("<h3>Situational spots</h3><p class=muted>How often each spot called the cover: the "
                 "flagged-against team failed to cover, or the rested team covered. Break-even at -110 is "
                 "52.4%; with a few hundred games, anything within about 3 points of 50% is noise." + cnote + "</p>"
                 f"<div class=tw><table><thead><tr><th>Spot</th><th>This season</th>"
                 f"<th>{E(hist.get('years', 'Past seasons'))}</th></tr></thead><tbody>" + frows + "</tbody></table></div>")
    else:
        flags = ""
    return (head + weekly + _periods_grade(s.get("periods")) + flags + _sharp_track(s.get("sharp"))
            + _my_adj(s.get("my_adj")))


# ---------------------------------------------------------------- public vs. sharp

def _s(v):
    return "" if v is None or (isinstance(v, float) and np.isnan(v)) else str(v)


def _sharp_chips(r, kinds=("spread", "total")):
    out = []
    for k in kinds:
        side, types, text = _s(r.get(f"sharp_{k}_side")), _s(r.get(f"sharp_{k}_types")), _s(r.get(f"sharp_{k}_text"))
        if not side:
            continue
        out.append(f"<span class='chip f-sharp' title='{E(text)}'>Sharp side: {E(side)} "
                   f"<span class=muted>({E(types)})</span></span>")
    return f"<div class=chips>{''.join(out)}</div>" if out else ""


def _sharp_note(r, kind):
    """On a value card: do the sharp signals agree with the model's pick?"""
    ag = _s(r.get(f"sharp_{kind}_agree"))
    side, types = _s(r.get(f"sharp_{kind}_side")), _s(r.get(f"sharp_{kind}_types"))
    if ag == "yes":
        return f"<div class='chips'><span class='chip f-sharp'>✓ Sharp signals agree ({E(types)})</span></div>"
    if ag == "no":
        return f"<div class=warn>⚠ Sharp signals on the other side: {E(side)} ({E(types)})</div>"
    return ""


def _pct_pair(a, b):
    if pd.isna(a) or pd.isna(b):
        return "–"
    return f"{a:.0f} / {b:.0f}"


def _sharp_section(games, note):
    head = ("<section id=sharp><h2>Public vs. sharp</h2>"
            "<p class=muted>% of bets (tickets) and % of money (handle) from ScoresAndOdds, with the line's "
            "move since it opened. A <b>Sharp side</b> is flagged when "
            f"<b>Reverse line move</b>: {config.SHARP_PUBLIC_PCT}%+ of bets on one side but the line moved "
            f"{config.SHARP_SPREAD_MOVE:g}+ (totals {config.SHARP_TOTAL_MOVE:g}+) the other way · "
            f"<b>Line holding</b>: {config.SHARP_HOLD_PCT}%+ of bets on one side and no move toward it · "
            f"<b>Money vs tickets</b>: a side with under half the bets but {config.SHARP_MONEY_GAP}+ points more of the money. "
            "Info only: these never change the model's numbers. They're new this season and untested on past "
            "seasons (no free history of splits exists), so check the track record before leaning on them.</p>")
    if note in ("off",):
        return ""
    if not len(games) or "sp_bets_a" not in games:
        return head + "<p class=muted>No games on the slate yet.</p></section>"
    g = games[~games["final"].astype(bool)] if "final" in games else games
    g = g[g["sp_bets_a"].notna() | g["tot_bets_a"].notna()]
    if not len(g):
        why = "the splits page couldn't be read this run" if note == "unavailable" else "none posted yet"
        return head + f"<p class=muted>No splits this run ({why}).</p></section>"
    g = g.assign(_f=(g["sharp_spread_key"].fillna("").astype(str) != "").astype(int) +
                 (g["sharp_total_key"].fillna("").astype(str) != "").astype(int)).sort_values(["_f", "start"], ascending=[False, True])
    rows = []
    for _, r in g.iterrows():
        def sig(k):
            side = _s(r.get(f"sharp_{k}_side"))
            if not side:
                return "<span class=muted>–</span>"
            ag = _s(r.get(f"sharp_{k}_agree"))
            mark = {"yes": " <span class=win>· model agrees</span>", "no": " <span class=loss>· model disagrees</span>"}.get(ag, "")
            return (f"<b>{E(side)}</b>{mark}<div class=muted>{E(_s(r.get(f'sharp_{k}_types')))}</div>"
                    f"<div class=ftxt style='white-space:normal;max-width:280px'>{E(_s(r.get(f'sharp_{k}_text')))}</div>")
        op = r.get("open_spread")
        sp_line = (f"{_fav_line(r['home'], r['away'], op)} → {_fav_line(r['home'], r['away'], r['market_spread'])}"
                   if not pd.isna(op) else _fav_line(r["home"], r["away"], r["market_spread"]))
        ot = r.get("open_total")
        t_line = (f"{ot:g} → {_n(r['market_total'], '{:g}')}" if not pd.isna(ot) else _n(r["market_total"], "{:g}"))
        rows.append(
            f"<tr><td class=gm><b>{E(r['away'])}</b> @ <b>{E(r['home'])}</b><div class=muted>{_when(r['start'])}</div></td>"
            f"<td>{_pct_pair(r['sp_bets_a'], r['sp_bets_b'])}<div class=muted>money {_pct_pair(r['sp_money_a'], r['sp_money_b'])}</div></td>"
            f"<td>{sp_line}</td><td>{sig('spread')}</td>"
            f"<td>{_pct_pair(r['tot_bets_a'], r['tot_bets_b'])}<div class=muted>money {_pct_pair(r['tot_money_a'], r['tot_money_b'])}</div></td>"
            f"<td>{t_line}</td><td>{sig('total')}</td></tr>")
    return (head + "<div class=tw><table><thead><tr><th>Game</th><th>Spread bets % (away / home)</th>"
            "<th>Line open → now</th><th>Spread signal</th><th>Total bets % (over / under)</th>"
            "<th>Total open → now</th><th>Total signal</th></tr></thead><tbody>" + "".join(rows) +
            "</tbody></table></div></section>")


def _sharp_track(sh):
    if sh is None:
        return ("<h3>Sharp-side signals</h3><p class=muted>Grading starts once games with saved splits "
                "are final (splits are saved every run from week 9 of 2026 on).</p>")

    def wl(x):
        w, l = x["w"], x["l"]
        return f"{w}–{l} ({w / (w + l):.0%})" if w + l else "–"
    rows = "".join(f"<tr><td>{E(r['market'].title())}</td><td>{E(r['type'])}</td><td>{wl(r)}</td></tr>"
                   for r in sh["by_type"])
    m = sh["model"]
    tiles = "".join(f"<div><span>{wl(v)}</span>model value picks: {E(k.lower())}</div>" for k, v in m.items())
    return ("<h3>Sharp-side signals</h3><p class=muted>How the side each signal pointed to did against the "
            "line at the last check before kickoff, and how the model's Lean/Strong picks did depending on "
            f"whether the signals agreed. {sh['games']} games graded so far. Break-even at -110 is 52.4%; "
            "with under a few hundred picks, anything near 50% is noise.</p>"
            f"<div class=stats>{tiles}</div>"
            "<div class=tw><table><thead><tr><th>Market</th><th>Signal</th><th>Sharp side ATS / O-U</th></tr>"
            "</thead><tbody>" + rows + "</tbody></table></div>")


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#14181f;--mut:#667085;--line:#e3e6eb;--acc:#1d5bd8;
--lean:#b26b00;--lean-bg:#fff4e0;--strong:#0d7a3e;--strong-bg:#e2f6ea;--warn:#a1260d;--chip:#eef1f5}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0f1217;--card:#171b22;--ink:#e8ebf0;
--mut:#97a0ae;--line:#2a303a;--acc:#6f9cff;--lean:#ffb547;--lean-bg:#3a2a10;--strong:#4fd18b;
--strong-bg:#123222;--warn:#ff8a70;--chip:#232934}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{padding:20px 16px 8px;max-width:1100px;margin:auto}h1{margin:0;font-size:22px}
nav{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line)}
nav div{max-width:1100px;margin:auto;display:flex;gap:4px;overflow-x:auto;padding:6px 12px}
nav a{color:var(--ink);text-decoration:none;padding:6px 10px;border-radius:8px;white-space:nowrap;font-weight:600;font-size:14px}
nav a:hover{background:var(--chip)}
main{max-width:1100px;margin:auto;padding:0 16px 40px}section{padding-top:18px;scroll-margin-top:44px}
a{color:var(--acc)}
h2{font-size:18px;margin:8px 0 10px}h3{font-size:16px;margin:22px 0 6px}.muted{color:var(--mut);font-size:13px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;border-left-width:4px}
.card.lean{border-left-color:var(--lean)}.card.strong{border-left-color:var(--strong)}
.row1{display:flex;justify-content:space-between;align-items:center}.edge{color:var(--mut);font-size:13px}
.pick{font-size:19px;font-weight:700;margin:4px 0 2px}.matchup,.detail{font-size:13px;color:var(--mut)}
.tier{display:inline-block;font-size:11px;font-weight:700;padding:2px 7px;border-radius:999px;text-transform:uppercase;letter-spacing:.03em}
.tier.lean{background:var(--lean-bg);color:var(--lean)}.tier.strong{background:var(--strong-bg);color:var(--strong)}
.warn{color:var(--warn);font-size:13px;font-weight:600;margin-top:6px}
.chips{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}
.chip{background:var(--chip);border-radius:6px;padding:2px 7px;font-size:11.5px;font-weight:600;border-left:3px solid var(--mut)}
.ftxt{display:block;font-weight:400;color:var(--mut);font-size:11.5px}
.f-let{border-left-color:#d9480f}.f-look{border-left-color:#7048e8}.f-sand{border-left-color:#c2255c}
.f-rest{border-left-color:#2b8a3e}.f-mine{border-left-color:var(--acc)}.f-low{border-left-color:var(--mut);font-weight:400}.f-trip{border-left-color:#1971c2}.f-sharp{border-left-color:#0ca678}
.tw{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:12px}
table{border-collapse:collapse;width:100%;font-size:13.5px}th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top;white-space:nowrap}
th{font-size:12px;color:var(--mut);font-weight:600;position:sticky;top:0;background:var(--card)}
td.gm{white-space:normal;min-width:180px}tr:last-child td{border-bottom:0}
.stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin-bottom:8px}
.stats div{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px;font-size:12px;color:var(--mut)}
.stats span{display:block;font-size:18px;font-weight:700;color:var(--ink)}
.demo{background:var(--warn);color:#fff;padding:8px 16px;text-align:center;font-weight:700}
input{width:100%;max-width:320px;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);margin-bottom:8px;font-size:15px}
table.per td{white-space:nowrap}.stick{position:sticky;left:0;background:var(--card);z-index:1}
th.stick{z-index:2}table.per td.gm{min-width:150px;white-space:normal}
.pcards{display:none}.pcard{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px;margin-bottom:8px}
.pcard{overflow-x:auto}.pcard table{font-size:13px;margin-top:4px}.pcard th,.pcard td{padding:4px 6px;white-space:normal}
@media (max-width:700px){.pcards{display:block}.pwide{display:none}}
.win{color:var(--strong)}.loss{color:var(--warn)}
.final{font-weight:700;font-size:13px;margin-top:2px}tr.fin td{background:var(--chip)}
.played{margin:6px 0 0;padding-left:18px;font-size:14px}.played li{margin:4px 0}
.legend{font-size:12.5px;color:var(--mut);margin:6px 0 10px}.legend b{color:var(--ink)}
"""

JS = """
const q=document.getElementById('q');if(q){q.addEventListener('input',()=>{const v=q.value.toLowerCase();
document.querySelectorAll('#rt tbody tr').forEach(tr=>{tr.style.display=tr.textContent.toLowerCase().includes(v)?'':'none'})})}
"""


def write(R, meta, games, label, bt, bt_sum, info):
    DOCS.mkdir(exist_ok=True)
    (DOCS / "data").mkdir(exist_ok=True)
    (DOCS / ".nojekyll").write_text("")

    # ---- CSVs ----
    R.drop(columns=["fbs"]).round(2).to_csv(DOCS / "data" / "ratings.csv")
    if len(games):
        games.drop(columns=["flags"]).assign(start=games["start"].astype(str)).round(2).to_csv(DOCS / "data" / "games.csv", index=False)
    if bt is not None and len(bt):
        bt.drop(columns=["flags"]).assign(start=bt["start"].astype(str)).round(2).to_csv(DOCS / "data" / "backtest.csv", index=False)

    names = {"fei": "FEI", "sp": "SP+", "fpi": "FPI"}
    used = meta.get("sources", [])
    wanted = [k for k, w in config.OUTSIDE_WEIGHTS.items() if w > 0]
    fei_note = " · ".join(f"{names[k]} {config.OUTSIDE_WEIGHTS[k]:.0%}" for k in used) or "no outside ratings"
    missing = [names[k] for k in wanted if k not in used]
    if missing:
        fei_note += f" ({', '.join(missing)} unavailable this run)"
    legend = ("<div class=legend><b>Situational flags</b> (info only, never change the numbers): "
              "<b>Letdown</b> off a big win vs. a lesser foe · <b>Look-ahead</b> big game next week · "
              "<b>Sandwich</b> between two big games · <b>Rest edge</b> bye or opponent on a short week · "
              "<b>Long trip</b> " + f"{getattr(config, 'LONG_TRIP_MILES', 1500):,}+ miles or "
              f"{getattr(config, 'LONG_TRIP_TIMEZONES', 2)}+ time zones. "
              "“vs X” means the spot works against X.</div>")

    page = f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>CFB Model</title><style>{CSS}</style></head><body>
{info.get('demo_banner', '')}
<header><h1>CFB Model · {info['year']}</h1>
<div class=muted>Updated {E(info['updated'])} · {E(label)} · home field {meta['hfa']:.1f} pts · {fei_note} · calibration ×{meta.get('scale', 1.0):.2f}</div></header>
<nav><div><a href="#value">Value board</a><a href="#games">All games</a><a href="#sharp">Public vs. sharp</a><a href="#periods">Team totals &amp; periods</a><a href="#mine">Your picks</a><a href="#ratings">Power ratings</a><a href="#track">Track record</a></div></nav>
<main>
<section id=value><h2>Value board · {E(label)}</h2>
<p class=muted>Lean = {config.SPREAD_LEAN:g}+ pt spread / {config.TOTAL_LEAN:g}+ pt total edge; Strong = {config.SPREAD_STRONG:g}+ / {config.TOTAL_STRONG:g}+. Big edges often mean news the model can't see (injuries, QB changes, weather). Check before betting.</p>
{_value_cards(games)}</section>
<section id=games><h2>All games</h2>{legend}{_games_table(games)}</section>
{_sharp_section(games, info.get('splits', 'off'))}
<section id=periods><h2>Team totals &amp; periods</h2>
<p class=muted>Model numbers only. There's no market line for these here, so compare them with your book. Spreads are shown from the favorite's side; totals are bold, with each team's points in brackets (away–home). Team totals are full game; quarter and half numbers leave out overtime.</p>
{_periods_table(games)}</section>
{_picks_section(info.get('picks'))}
<section id=ratings><h2>Power ratings</h2>
<p class=muted>Points vs. an average FBS team on a neutral field. “This yr wt” is how much this season's games count versus the preseason prior.</p>
<input id=q placeholder="Filter teams or conferences">{_ratings_table(R)}</section>
<section id=track><h2>Track record</h2>{_track(bt_sum)}</section>
<p class=muted>Raw numbers: <a href="data/ratings.csv">ratings.csv</a> · <a href="data/games.csv">games.csv</a> · <a href="data/backtest.csv">backtest.csv</a></p>
</main><script>{JS}</script></body></html>"""
    (DOCS / "index.html").write_text(page)
