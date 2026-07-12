"""Static single-file KB browser: the whole feature knowledge base embedded in one
self-contained kb.html — no server, no external assets, opens from file://.

Deliberately minimal frontend (list + search + feature pages + journey); how
interactive the KB front-end should get is a post-validation product decision.
"""

from __future__ import annotations

import json
from pathlib import Path

_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>gitchronicle — feature knowledge base</title>
<style>
:root {
  --bg:#ffffff; --fg:#1a1d21; --muted:#69707a; --line:#e3e6ea; --side:#f6f7f9;
  --acc:#2860c4; --chip:#eef2f9; --code:#f1f3f5;
}
@media (prefers-color-scheme: dark) {
  :root { --bg:#14161a; --fg:#e6e8eb; --muted:#96a0ab; --line:#2a2f36; --side:#191c21;
          --acc:#7aa5f0; --chip:#20293a; --code:#1e2228; }
}
* { box-sizing:border-box; margin:0 }
body { background:var(--bg); color:var(--fg);
  font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif }
header { display:flex; align-items:baseline; gap:16px; padding:12px 20px;
  border-bottom:1px solid var(--line) }
header h1 { font-size:17px } header .n { color:var(--muted); font-size:13px }
header nav { margin-left:auto; display:flex; gap:6px }
header nav button { background:none; border:1px solid var(--line); color:var(--fg);
  border-radius:6px; padding:4px 12px; cursor:pointer; font-size:13px }
header nav button.on { background:var(--acc); border-color:var(--acc); color:#fff }
#app { display:grid; grid-template-columns:320px 1fr; height:calc(100vh - 49px) }
aside { border-right:1px solid var(--line); background:var(--side);
  display:flex; flex-direction:column; min-height:0 }
#q { margin:10px; padding:7px 10px; border:1px solid var(--line); border-radius:6px;
  background:var(--bg); color:var(--fg); font-size:14px }
#list { list-style:none; overflow-y:auto; flex:1; padding:0 6px 12px }
#list li { padding:6px 10px; border-radius:6px; cursor:pointer; font-size:14px }
#list li:hover { background:var(--chip) }
#list li.on { background:var(--acc); color:#fff }
#list li.on small { color:#dfe7f5 }
#list small { color:var(--muted); display:block; font-size:11.5px }
main { overflow-y:auto; padding:26px 40px 80px; min-width:0 }
main h2 { font-size:22px; margin-bottom:4px }
.def { font-size:15px; margin:8px 0 4px; max-width:75ch; white-space:pre-wrap }
.sum { color:var(--muted); font-style:italic; margin:6px 0; max-width:75ch }
h3 { font-size:13px; text-transform:uppercase; letter-spacing:.06em;
  color:var(--muted); margin:26px 0 8px }
.chips { display:flex; flex-wrap:wrap; gap:6px }
.chip { background:var(--chip); color:var(--acc); border-radius:14px; padding:3px 11px;
  font-size:13px; cursor:pointer; border:none }
.chip small { color:var(--muted) }
ul.files { list-style:none; column-width:340px; column-gap:30px }
ul.files li { font:12.5px/1.7 ui-monospace,Menlo,Consolas,monospace;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap }
ul.files li.hist { color:var(--muted) }
.chap { border-left:3px solid var(--line); padding:2px 0 2px 14px; margin:0 0 16px }
.chap b { font-size:15px } .chap .when { color:var(--muted); font-size:12.5px }
.chap p { margin:4px 0; max-width:75ch }
code, .hash { background:var(--code); border-radius:4px; padding:1px 6px;
  font:12px ui-monospace,Menlo,Consolas,monospace }
table.commits { border-collapse:collapse; width:100%; max-width:900px }
table.commits td { padding:3px 10px 3px 0; font-size:13.5px; vertical-align:top }
table.commits td:first-child { white-space:nowrap }
table.commits td:nth-child(2) { color:var(--muted); white-space:nowrap }
.empty { color:var(--muted); font-style:italic }
.jyear { margin:22px 0 6px; font-size:15px; font-weight:600 }
.jrow { display:flex; gap:14px; padding:3px 0; font-size:14px }
.jrow .d { color:var(--muted); font:12.5px ui-monospace,monospace; padding-top:2px }
.jrow a { color:var(--acc); cursor:pointer; text-decoration:none }
.star { color:#c99700 }
@media (max-width:760px){ #app{grid-template-columns:1fr} aside{display:none} }
</style>
</head>
<body>
<header>
  <h1>Feature knowledge base</h1><span class="n" id="counts"></span>
  <nav>
    <button id="bF" class="on">Features</button>
    <button id="bJ">Journey</button>
  </nav>
</header>
<div id="app">
  <aside>
    <input id="q" type="search" placeholder="Search features&hellip;" autocomplete="off">
    <ul id="list"></ul>
  </aside>
  <main id="main"></main>
</div>
<script>
"use strict";
const DATA = __DATA__;
const bySlug = new Map(DATA.map(r => [r.slug, r]));
const byName = new Map(DATA.map(r => [r.name, r]));
const $ = id => document.getElementById(id);
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}
let view = "features";

function renderList() {
  const q = $("q").value.trim().toLowerCase();
  const ul = $("list"); ul.textContent = "";
  const cur = location.hash.slice(1);
  for (const r of DATA) {
    if (q && !(r.name.toLowerCase().includes(q) || r.definition.toLowerCase().includes(q)))
      continue;
    const li = el("li", r.slug === cur ? "on" : "");
    li.append((r.used_by.length >= 5 ? "⭐ " : "") + r.name);
    const bits = [];
    if (r.commits.length) bits.push(r.commits.length + " commits");
    if (r.chapters.length) bits.push(r.chapters.length + " chapters");
    if (!bits.length) bits.push("register only");
    li.append(el("small", "", bits.join(" · ")));
    li.onclick = () => { location.hash = r.slug; };
    ul.append(li);
  }
}

function chipRow(parent, label, items, dir) {
  if (!items.length) return;
  parent.append(el("h3", "", label));
  const box = el("div", "chips");
  for (const u of items) {
    const b = el("button", "chip");
    b.append(dir === "in" ? "← " : "→ ", u.feature, " ");
    const s = el("small", "", "(" + u.files + ")");
    b.append(s);
    const t = byName.get(u.feature);
    if (t) b.onclick = () => { location.hash = t.slug; };
    box.append(b);
  }
  parent.append(box);
}

function renderFeature(r) {
  const m = $("main"); m.textContent = "";
  m.append(el("h2", "", (r.used_by.length >= 5 ? "⭐ " : "") + r.name));
  if (r.definition) m.append(el("div", "def", r.definition));
  if (r.summary && r.summary !== r.definition) m.append(el("div", "sum", r.summary));
  chipRow(m, "Uses", r.uses, "out");
  chipRow(m, "Used by", r.used_by, "in");
  if (r.chapters.length) {
    m.append(el("h3", "", "Story"));
    for (const c of r.chapters) {
      const d = el("div", "chap");
      d.append(el("b", "", c.title || c.period), " ", el("span", "when", c.period));
      if (c.narrative) d.append(el("p", "", c.narrative));
      const row = el("p");
      for (const h of c.commits.slice(0, 12)) row.append(el("span", "hash", h), " ");
      d.append(row);
      m.append(d);
    }
  }
  if (r.territory.length) {
    m.append(el("h3", "", "Territory (" + r.territory.length + " files)"));
    const ul = el("ul", "files");
    for (const t of r.territory)
      ul.append(el("li", t.source === "history" ? "hist" : "", t.path));
    m.append(ul);
  }
  m.append(el("h3", "", "Commits (" + r.commits.length + ")"));
  if (!r.commits.length)
    m.append(el("p", "empty",
      "No commits attributed in this run's history window — register-only entry."));
  else {
    const tb = el("table", "commits");
    for (const c of r.commits) {
      const tr = el("tr");
      const td = el("td"); td.append(el("span", "hash", c.hash)); tr.append(td);
      tr.append(el("td", "", c.date), el("td", "", c.subject));
      tb.append(tr);
    }
    m.append(tb);
  }
}

function renderJourney() {
  const m = $("main"); m.textContent = "";
  m.append(el("h2", "", "Feature journey"));
  m.append(el("p", "sum", "Features by first attributed commit; ⭐ = framework hub."));
  const dated = DATA.filter(r => r.commits.length)
    .map(r => [r.commits[0].date, r]).sort((a, b) => a[0] < b[0] ? -1 : 1);
  let year = "";
  for (const [d, r] of dated) {
    if (d.slice(0, 4) !== year) { year = d.slice(0, 4); m.append(el("div", "jyear", year)); }
    const row = el("div", "jrow");
    row.append(el("span", "d", d));
    const a = el("a", "", r.name);
    a.onclick = () => { $("bF").click(); location.hash = r.slug; };
    row.append(a);
    if (r.used_by.length >= 5) row.append(el("span", "star", "⭐"));
    row.append(el("span", "d", r.commits.length + " commits"));
    m.append(row);
  }
}

function route() {
  if (view === "journey") { renderJourney(); renderList(); return; }
  const r = bySlug.get(location.hash.slice(1)) || DATA.find(x => x.commits.length) || DATA[0];
  if (r) renderFeature(r);
  renderList();
}
$("q").addEventListener("input", renderList);
window.addEventListener("hashchange", () => { view = "features"; setNav(); route(); });
function setNav() {
  $("bF").className = view === "features" ? "on" : "";
  $("bJ").className = view === "journey" ? "on" : "";
}
$("bF").onclick = () => { view = "features"; setNav(); route(); };
$("bJ").onclick = () => { view = "journey"; setNav(); route(); };
$("counts").textContent = DATA.length + " features · "
  + DATA.filter(r => r.commits.length).length + " with history · "
  + DATA.reduce((a, r) => a + r.commits.length, 0) + " attributed commits";
route();
</script>
</body>
</html>
"""


def render_kb(records: list[dict], out_path: Path) -> None:
    data = json.dumps(records, ensure_ascii=False, separators=(",", ":"))
    out_path.write_text(_PAGE.replace("__DATA__", data.replace("</", "<\\/")))
