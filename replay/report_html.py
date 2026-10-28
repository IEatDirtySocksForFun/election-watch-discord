"""Render a replay as one self-contained HTML page: a summary row per race plus every post.

    python -m replay.report_html --year 2022 --races "$(cat races.txt)" --out night.html

The summary table is static HTML (readable with scripts off); each race's post feed is
embedded as JSON and drawn when its row is opened.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
from collections import defaultdict
from pathlib import Path

from electionwatch.models import Level
from electionwatch.racekeys import STATES, describe, parse
from electionwatch.settings import GuildSettings
from replay.run import CHANNEL, DEFAULT_DATA, GUILD, replay
from replay.timeline import load_year

LEVELS = {
    Level.SENATE: "Senate",
    Level.HOUSE: "House",
    Level.GOVERNOR: "Governor / statewide",
    Level.STATE: "Legislature",
    Level.LOCAL: "Local",
}


def _leader(lines: list[str]) -> tuple[str, str]:
    """('🔴 Name (REP) 1,234 · 51.2%' first line) -> (name with party, pct)."""
    first = lines[0] if lines else ""
    name = first[2:].split(" · ")[0].rsplit(" ", 1)[0].strip() if first[:1] in "🔴🔵⚪" else first
    pct = first.rsplit(" · ", 1)[-1] if " · " in first else ""
    return name, pct


def build(year: str, timelines, rep) -> str:
    per = defaultdict(list)
    for ts, p in rep.posts:
        per[p.race].append({"t": f"{ts:%H:%M}", "k": p.kind, "l": p.lines, "f": p.footer or ""})
    rows, feeds = [], {}
    order = sorted(timelines, key=lambda k: (parse(k).state, list(LEVELS).index(parse(k).level), k))
    for i, race in enumerate(order):
        key = parse(race)
        posts = per.get(race, [])
        results = [p for p in posts if p["k"] == "results"]
        call = next((p for p in posts if p["k"] == "call"), None)
        final = results[-1]["l"] if results else []
        lead, pct = _leader(final)
        party = (
            "rep"
            if final[:1] and final[0].startswith("🔴")
            else "dem"
            if final[:1] and final[0].startswith("🔵")
            else "oth"
        )
        feeds[str(i)] = posts
        rows.append(
            f'<tr data-i="{i}" data-st="{key.state}" data-lv="{key.level.value}" tabindex="0">'
            f'<td class="st">{key.state}</td><td>{html.escape(describe(key))}<span class="key">{html.escape(race)}</span></td>'
            f'<td class="lv">{LEVELS[key.level]}</td>'
            f'<td><span class="dot {party}"></span>{html.escape(lead)} <span class="num">{html.escape(pct)}</span></td>'
            f'<td class="num">{call["t"] + " UTC" if call else "—"}</td>'
            f'<td class="num">{len(results)}</td><td class="num">{sum(p["k"] == "odds_swing" for p in posts)}</td></tr>'
        )
    counts = defaultdict(int)
    for _, p in rep.posts:
        counts[p.kind] += 1
    states = sorted({parse(k).state for k in timelines})
    meta = {
        "races": len(timelines),
        "states": len(states),
        "posts": len(rep.posts),
        "results": counts["results"],
        "calls": counts["call"],
        "swings": counts["odds_swing"],
        "start": f"{rep.start:%b %d %H:%M}",
        "end": f"{rep.end:%b %d %H:%M}",
        "ticks": rep.ticks,
        "ok": rep.ok,
        "problems": len(rep.problems),
    }
    opts = "".join(f'<option value="{s}">{s} · {STATES[s]}</option>' for s in states)
    page = TEMPLATE
    for k, v in meta.items():
        page = page.replace("{{" + k + "}}", str(v))
    return (
        page.replace("{{year}}", year)
        .replace("{{rows}}", "\n".join(rows))
        .replace("{{options}}", opts)
        .replace(
            "{{check}}", "Posting rule held · no duplicate posts" if rep.ok else f"{len(rep.problems)} rule problems"
        )
        .replace("{{feeds}}", json.dumps(feeds, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
    )


TEMPLATE = r"""<title>{{year}} Election Night Replay</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;600;800&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* Layout: a ballot-style header strip, one wide results ledger, feeds open inline under each row. */
:root{--bg:#f6f7f9;--panel:#ffffff;--ink:#14213d;--muted:#5b6478;--rule:#d9dde5;--dem:#2459c9;--rep:#c9302c;--oth:#8a90a0;--hi:#fff4c2;
--display:"Public Sans",system-ui,sans-serif;--body:"Public Sans",system-ui,sans-serif;--mono:"IBM Plex Mono",ui-monospace,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0e1424;--panel:#151d31;--ink:#e7ebf3;--muted:#9aa4ba;--rule:#2a3550;--dem:#6f9cff;--rep:#ff7a73;--oth:#8f97ab;--hi:#3a3420;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#0e1424;--panel:#151d31;--ink:#e7ebf3;--muted:#9aa4ba;--rule:#2a3550;--dem:#6f9cff;--rep:#ff7a73;--oth:#8f97ab;--hi:#3a3420;color-scheme:dark}
body{background:var(--bg);color:var(--ink);font-family:var(--body);font-size:15px;line-height:1.5;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));padding-block:24px 48px}
main{max-width:1100px;margin:0 auto;display:grid;gap:20px}
h1{font-family:var(--display);font-weight:800;font-size:clamp(26px,4vw,38px);letter-spacing:-.01em;margin:0;text-wrap:balance}
.eyebrow{font-family:var(--mono);font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.lede{max-width:68ch;color:var(--muted);margin:6px 0 0}
.strip{display:flex;flex-wrap:wrap;gap:8px 28px;border-block:2px solid var(--ink);padding-block:12px}
.strip div{display:grid}.strip b{font-family:var(--mono);font-size:22px;font-weight:500;font-variant-numeric:tabular-nums}
.strip span{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.ok{font-family:var(--mono);font-size:13px;padding:2px 8px;border:1px solid var(--rule);border-radius:3px;justify-self:start}
.filters{display:flex;flex-wrap:wrap;gap:10px;align-items:end}
label{display:grid;gap:2px;font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
select,input{font:inherit;font-size:14px;color:var(--ink);background:var(--panel);border:1px solid var(--rule);border-radius:4px;padding:6px 8px;min-width:0}
.ledger{overflow-x:auto;background:var(--panel);border:1px solid var(--rule);border-radius:6px}
table{border-collapse:collapse;width:100%;min-width:720px}
th{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);text-align:left;padding:10px;border-bottom:1px solid var(--rule);position:sticky;top:0;background:var(--panel)}
td{padding:8px 10px;border-bottom:1px solid var(--rule);vertical-align:top}
tr[data-i]{cursor:pointer}tr[data-i]:hover,tr[data-i]:focus-visible{background:var(--hi);outline:none}
.st{font-family:var(--mono);font-weight:500}.key{display:block;font-family:var(--mono);font-size:12px;color:var(--muted)}
.lv{color:var(--muted);font-size:13px}.num{font-family:var(--mono);font-variant-numeric:tabular-nums;white-space:nowrap}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;background:var(--oth)}.dot.dem{background:var(--dem)}.dot.rep{background:var(--rep)}
.feed td{background:var(--bg);padding:12px 10px}
.feed ol{list-style:none;margin:0;padding:0;display:grid;gap:8px;max-height:420px;overflow:auto}
.feed li{display:grid;grid-template-columns:62px 1fr;gap:10px;font-size:13px}
.feed .t{font-family:var(--mono);color:var(--muted)}.feed .k{font-family:var(--mono);font-size:11px;text-transform:uppercase;letter-spacing:.06em}
.feed pre{margin:2px 0 0;font-family:var(--mono);font-size:12.5px;white-space:pre-wrap;min-width:0}
.k.call{color:var(--rep)}.k.odds_swing{color:var(--dem)}
footer{color:var(--muted);font-size:13px;max-width:72ch}
</style>
<main>
  <header>
    <div class="eyebrow">election-watch-discord · replay sandbox</div>
    <h1>{{year}} Election Night Replay</h1>
    <p class="lede">Every race the bot tracked through the {{year}} general election night, {{start}} to {{end}} UTC ({{ticks}} one-minute polls), with each one marked important. Open a row to see every post the bot would have made for it.</p>
  </header>
  <section class="strip" aria-label="Run summary">
    <div><b>{{races}}</b><span>races</span></div><div><b>{{states}}</b><span>states + DC</span></div>
    <div><b>{{posts}}</b><span>posts</span></div><div><b>{{results}}</b><span>results updates</span></div>
    <div><b>{{calls}}</b><span>race calls</span></div><div><b>{{swings}}</b><span>odds swings</span></div>
  </section>
  <div class="ok">{{check}}</div>
  <div class="filters">
    <label for="fst">State<select id="fst"><option value="">All states</option>{{options}}</select></label>
    <label for="flv">Category<select id="flv"><option value="">All</option><option value="senate">Senate</option><option value="house">House</option><option value="governor">Governor / statewide</option><option value="state">Legislature</option><option value="local">Local</option></select></label>
    <label for="fq">Search<input id="fq" type="search" placeholder="PA-07, Fetterman…"></label>
  </div>
  <div class="ledger"><table>
    <thead><tr><th>St</th><th>Race</th><th>Category</th><th>Final leader</th><th>Call (sim)</th><th>Updates</th><th>Swings</th></tr></thead>
    <tbody id="rows">
{{rows}}
    </tbody></table></div>
  <footer>Vote counts are the certified {{year}} results; where no minute-by-minute record exists, the order counties report in is simulated (early vote first, election day, then mail). Race calls and prediction-market odds are scripted test data, not real outlet calls. Posting follows the bot's default rule: at most every 5 minutes per race, and only when the numbers changed.</footer>
</main>
<script>
const FEEDS={{feeds}};
const rows=document.getElementById('rows');
function esc(s){return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function toggle(tr){
  const next=tr.nextElementSibling;
  if(next&&next.classList.contains('feed')){next.remove();return}
  const posts=FEEDS[tr.dataset.i]||[];
  const f=document.createElement('tr');f.className='feed';
  f.innerHTML='<td colspan="7"><ol>'+posts.map(p=>`<li><span class="t">${p.t}</span><div><span class="k ${p.k}">${p.k.replace('_',' ')}</span><pre>${esc(p.l.join('\n'))}</pre></div></li>`).join('')+'</ol></td>';
  tr.after(f);
}
rows.addEventListener('click',e=>{const tr=e.target.closest('tr[data-i]');if(tr)toggle(tr)});
rows.addEventListener('keydown',e=>{if(e.key==='Enter'){const tr=e.target.closest('tr[data-i]');if(tr)toggle(tr)}});
function filter(){
  const st=fst.value,lv=flv.value,q=fq.value.trim().toLowerCase();
  rows.querySelectorAll('tr.feed').forEach(r=>r.remove());
  rows.querySelectorAll('tr[data-i]').forEach(r=>{
    r.hidden=(st&&r.dataset.st!==st)||(lv&&r.dataset.lv!==lv)||(q&&!r.textContent.toLowerCase().includes(q));
  });
}
[fst,flv,fq].forEach(el=>el.addEventListener('input',filter));
</script>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", required=True)
    ap.add_argument("--races", required=True)
    ap.add_argument("--data", default=str(DEFAULT_DATA))
    ap.add_argument("--max-hours", type=float, default=36)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    races = [r.strip() for r in a.races.split(",") if r.strip()]
    timelines = load_year(Path(a.data).expanduser(), a.year, races)
    gs = GuildSettings(guild_id=GUILD, channel_id=CHANNEL, important=sorted(timelines), odds_alerts=True)
    rep = asyncio.run(replay(timelines, gs, None, a.max_hours))
    Path(a.out).write_text(build(a.year, timelines, rep), encoding="utf-8")
    print(f"{len(rep.posts)} posts, {len(timelines)} races -> {a.out}")


if __name__ == "__main__":
    main()
