"""Self-contained per-stage validation GUI (inspect.html). Read-only: the only output
is review-plan verb lines copied to the clipboard."""

INSPECT_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>gitchronicle · inspect</title>
<style>
:root{
  --surface:#fcfcfb; --page:#f9f9f7; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --axis:#c3c2b7; --ring:rgba(11,11,11,.10);
  --s-fast:#2a78d6; --s-llm:#1baf7a; --s-novelty:#eda100; --s-propose:#008300;
  --s-audit:#4a3aa7; --s-human:#e34948; --seq:#2a78d6;
  --warn:#fab219; --crit:#d03b3b; --good:#0ca30c;
  --chip:#f0efec;
}
@media (prefers-color-scheme: dark){
  :root{
    --surface:#1a1a19; --page:#0d0d0d; --ink:#ffffff; --ink2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --axis:#383835; --ring:rgba(255,255,255,.10);
    --s-fast:#3987e5; --s-llm:#199e70; --s-novelty:#c98500; --s-propose:#008300;
    --s-audit:#9085e9; --s-human:#e66767; --seq:#3987e5;
    --chip:#2c2c2a;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);
  font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
header{display:flex;gap:16px;align-items:baseline;padding:14px 20px 0}
header h1{font-size:17px;margin:0}
header .mm{color:var(--muted);font-size:12px}
nav{display:flex;gap:2px;padding:10px 20px 0;border-bottom:1px solid var(--grid);flex-wrap:wrap}
nav button{border:0;background:none;color:var(--ink2);padding:8px 12px;cursor:pointer;
  font:inherit;border-bottom:2px solid transparent}
nav button.on{color:var(--ink);border-bottom-color:var(--seq);font-weight:600}
main{padding:16px 20px 60px;max-width:1240px;margin:0 auto}
.tiles{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:18px}
.tile{background:var(--surface);border:1px solid var(--ring);border-radius:8px;
  padding:12px 16px;min-width:130px}
.tile b{display:block;font-size:24px}
.tile span{color:var(--ink2);font-size:12px}
.tile.warn b{color:var(--crit)}
.card{background:var(--surface);border:1px solid var(--ring);border-radius:8px;
  padding:14px 16px;margin-bottom:14px;overflow-x:auto}
.card h3{margin:0 0 10px;font-size:13px;color:var(--ink2);font-weight:600}
table{border-collapse:collapse;width:100%;font-size:13px}
th{color:var(--muted);text-align:left;font-weight:500;padding:4px 10px 4px 0;
  border-bottom:1px solid var(--grid);white-space:nowrap}
td{padding:5px 10px 5px 0;border-bottom:1px solid var(--grid);vertical-align:top}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.chip{display:inline-block;background:var(--chip);border-radius:4px;padding:1px 7px;
  font-size:11.5px;color:var(--ink2);margin:1px 3px 1px 0;white-space:nowrap}
.st{display:inline-block;border-radius:4px;padding:1px 7px;font-size:11px;font-weight:600}
.st.provisional{background:var(--warn);color:#1a1a19}
.st.named{background:var(--chip);color:var(--ink2)}
.st.confirmed{background:var(--good);color:#fff}
.src{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px;
  vertical-align:baseline}
.filters{display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap}
.filters input,.filters select{background:var(--surface);color:var(--ink);
  border:1px solid var(--grid);border-radius:6px;padding:6px 9px;font:inherit}
button.copy{border:1px solid var(--grid);background:var(--surface);color:var(--ink2);
  border-radius:5px;padding:2px 8px;font-size:11px;cursor:pointer;white-space:nowrap}
button.copy:hover{color:var(--ink);border-color:var(--axis)}
.mm{color:var(--muted)} .sm{font-size:12px}
.legend{display:flex;gap:14px;flex-wrap:wrap;margin:6px 0 2px;font-size:12px;color:var(--ink2)}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px}
#tip{position:fixed;pointer-events:none;background:var(--surface);border:1px solid var(--ring);
  border-radius:6px;padding:7px 10px;font-size:12px;box-shadow:0 2px 10px rgba(0,0,0,.18);
  display:none;z-index:9;max-width:300px}
.bar-row{display:grid;grid-template-columns:210px 1fr 88px;gap:10px;align-items:center;
  padding:3px 0;font-size:12.5px}
.bar-track{background:none;height:14px;position:relative}
.bar-fill{height:14px;border-radius:0 4px 4px 0;background:var(--seq)}
.flag{color:var(--crit);font-weight:600;font-size:11px}
svg text{fill:var(--muted);font-size:11px}
.dist{display:flex;height:12px;border-radius:3px;overflow:hidden;margin-top:3px;max-width:420px}
.dist div{height:12px}
.empty{color:var(--muted);padding:30px;text-align:center}
</style></head><body>
<header><h1>gitchronicle · inspect</h1><span class="mm" id="meta"></span></header>
<nav id="nav"></nav>
<main id="main"></main>
<div id="tip"></div>
<script>
const D = __INSPECT_DATA__;
const SRC_COLOR = {fast:'var(--s-fast)', llm:'var(--s-llm)', novelty:'var(--s-novelty)',
  propose:'var(--s-propose)', audit:'var(--s-audit)', human:'var(--s-human)'};
const SRC_ORDER = ['fast','llm','novelty','propose','audit','human'];
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const TABS = ['Overview','Ground','Features','Assignments','Coherence','Changeset','Areas','Untangle'];
let tab = 'Overview';
document.getElementById('meta').textContent =
  `${D.meta.features} features · ${D.meta.concerns} concerns · generated ${D.meta.generated_at}`;

function nav(){
  document.getElementById('nav').innerHTML = TABS.map(t =>
    `<button class="${t===tab?'on':''}" onclick="go('${t}')">${t}${badge(t)}</button>`).join('');
}
function badge(t){
  if(t==='Changeset' && D.changeset.length) return ` (${D.changeset.length})`;
  if(t==='Coherence' && D.meta.incoherent_stems) return ` (${D.meta.incoherent_stems}⚠)`;
  return '';
}
function go(t){ tab=t; nav(); render(); }
function copyTxt(s, btn){ navigator.clipboard.writeText(s).then(()=>{
  const o=btn.textContent; btn.textContent='copied'; setTimeout(()=>btn.textContent=o, 900);});}
const tip = document.getElementById('tip');
function showTip(ev, html){ tip.innerHTML=html; tip.style.display='block';
  tip.style.left=Math.min(ev.clientX+14, innerWidth-320)+'px'; tip.style.top=(ev.clientY+12)+'px'; }
function hideTip(){ tip.style.display='none'; }

// ---------- Overview ----------
function vOverview(){
  const h = D.health;
  const tiles = `
   <div class="tiles">
    <div class="tile"><b>${h.features}</b><span>features</span></div>
    <div class="tile"><b>${h.concerns}</b><span>concerns</span></div>
    <div class="tile ${h.unassigned? 'warn':''}"><b>${h.unassigned}</b><span>unassigned</span></div>
    <div class="tile ${h.incoherent_stems? 'warn':''}"><b>${h.incoherent_stems}</b><span>incoherent stems (&lt;0.6)</span></div>
    <div class="tile ${h.near_dups.length? 'warn':''}"><b>${h.near_dups.length}</b><span>near-dup pairs</span></div>
    <div class="tile ${h.orphan_stems.length? 'warn':''}"><b>${h.orphan_stems.length}</b><span>orphan stems</span></div>
    <div class="tile"><b>${h.conf_median ?? '—'}</b><span>median confidence</span></div>
   </div>`;
  return tiles + confHistogram() + coherenceCard(D.coherence.slice(0,18), true)
       + orphanCard() + nearDupCard();
}
function confHistogram(){
  // stacked histogram of assignment confidence by source (12 bins, 0.3–0.9)
  const bins = 12, lo = 0.30, hi = 0.90, W = 760, H = 190, PAD = 34;
  const counts = Array.from({length:bins}, () => ({}));
  for(const a of D.assignments){
    if(a.conf == null || !a.source) continue;
    let b = Math.floor((a.conf - lo) / ((hi - lo) / bins));
    b = Math.max(0, Math.min(bins - 1, b));
    counts[b][a.source] = (counts[b][a.source] || 0) + 1;
  }
  const totals = counts.map(c => Object.values(c).reduce((x,y)=>x+y,0));
  const maxT = Math.max(1, ...totals);
  const bw = (W - PAD) / bins;
  let bars = '';
  counts.forEach((c, i) => {
    let y = H - 22;
    const x = PAD + i * bw;
    const parts = SRC_ORDER.filter(s => c[s]);
    parts.forEach((s, pi) => {
      const hh = (c[s] / maxT) * (H - 46);
      y -= hh;
      const top = pi === parts.length - 1;   // data-end of the stack gets the rounding
      bars += `<rect x="${x+2}" y="${y + (top?0:2)}" width="${bw-6}" height="${Math.max(1,hh-(top?0:2))}"
        rx="${top?4:0}" fill="${SRC_COLOR[s]}" data-tt="${esc(`${(lo+i*(hi-lo)/bins).toFixed(2)}–${(lo+(i+1)*(hi-lo)/bins).toFixed(2)} · ${s}: ${c[s]} (bin total ${totals[i]})`)}"/>`;
    });
    if(i % 2 === 0) bars += `<text x="${x + bw/2}" y="${H-8}" text-anchor="middle">${(lo + i*(hi-lo)/bins).toFixed(2)}</text>`;
  });
  const grid = [0.25,0.5,0.75,1].map(f =>
    `<line x1="${PAD}" x2="${W}" y1="${H-22-f*(H-46)}" y2="${H-22-f*(H-46)}" stroke="var(--grid)" stroke-width="1"/>
     <text x="${PAD-6}" y="${H-19-f*(H-46)}" text-anchor="end">${Math.round(f*maxT)}</text>`).join('');
  const legend = SRC_ORDER.filter(s => D.assignments.some(a => a.source === s)).map(s =>
    `<span><i style="background:${SRC_COLOR[s]}"></i>${s}</span>`).join('');
  return `<div class="card"><h3>Assignment confidence (raw cosine) by source</h3>
    <div class="legend">${legend}</div>
    <svg width="${W}" height="${H}" role="img">${grid}
      <line x1="${PAD}" x2="${W}" y1="${H-22}" y2="${H-22}" stroke="var(--axis)"/>
      ${bars}</svg></div>`;
}
function coherenceCard(rows, compact){
  if(!rows.length) return '';
  const items = rows.map(c => {
    const w = Math.round(c.share * 100);
    const flag = c.share < 0.6 ? `<span class="flag">⚠ split</span>` : '';
    return `<div class="bar-row" onmousemove="showTip(event, distTip('${esc(c.stem)}'))" onmouseout="hideTip()">
      <span title="${esc(c.stem)}">${esc(c.stem)}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${w}%"></div></div>
      <span class="num sm">${c.share.toFixed(2)} · ${c.n_concerns}c/${c.n_features}f ${flag}</span></div>`;
  }).join('');
  return `<div class="card"><h3>Stem coherence — share of a stem's concerns in its top feature
    (low = one thing scattered over many features)</h3>${items}
    ${compact ? '<div class="sm mm" style="margin-top:6px">full list in the Coherence tab</div>' : ''}</div>`;
}
function distTip(stem){
  const c = D.coherence.find(x => x.stem === stem);
  if(!c || !c.dist) return esc(stem);
  const rows = Object.entries(c.dist).sort((a,b)=>b[1]-a[1]).slice(0,8)
    .map(([f,n]) => `${esc(f)}: ${n}`).join('<br>');
  return `<b>${esc(stem)}</b><br>${rows}`;
}
function orphanCard(){
  const o = D.health.orphan_stems;
  if(!o.length) return '';
  return `<div class="card"><h3>Orphan stems — strong vocabulary NO feature claims
    (a top entry with feature-like samples usually means a missing feature)</h3>
    <table><tr><th class="num">concerns</th><th>stem</th><th>sample files</th></tr>` +
    o.map(x => `<tr><td class="num">${x.n_concerns}</td><td><b>${esc(x.stem)}</b></td>
      <td class="sm mm">${x.samples.map(esc).join(', ')}</td></tr>`).join('') + '</table></div>';
}
function nearDupCard(){
  if(!D.health.near_dups.length) return '';
  return `<div class="card"><h3>Near-duplicate feature pairs (definition cosine ≥ 0.90)</h3>
    <table><tr><th class="num">cos</th><th>feature</th><th>feature</th><th></th></tr>` +
    D.health.near_dups.map(d => {
      const a = D.features.find(f => f.name === d.a), b = D.features.find(f => f.name === d.b);
      const verb = a && b ? `merge  [${a.n <= b.n ? a.id : b.id}] ${a.n <= b.n ? d.a : d.b} -> ${a.n <= b.n ? d.b : d.a}` : '';
      return `<tr><td class="num">${d.cos.toFixed(2)}</td><td>${esc(d.a)}</td><td>${esc(d.b)}</td>
        <td>${verb ? `<button class="copy" onclick="copyTxt('${esc(verb)}', this)">copy merge verb</button>` : ''}</td></tr>`;
    }).join('') + '</table></div>';
}

// ---------- Ground ----------
function vGround(){
  const gl = D.glossary.map(g => `<div class="card">
    <h3>${esc(g.name)} <span class="mm sm">· ${esc(g.source)}</span></h3>
    <div class="sm">${esc(g.definition)}</div>
    <div style="margin-top:6px">${g.stems.map(s=>`<span class="chip">${esc(s)}</span>`).join('')}</div>
    ${g.docs.length ? `<div class="sm mm" style="margin-top:4px">docs: ${g.docs.map(esc).join(', ')}</div>` : ''}
  </div>`).join('');
  const cs = `<div class="card"><h3>Stem census (top ${D.census.length} by activity)</h3>
    <div class="filters"><input id="cq" placeholder="filter stems…" oninput="fCensus()"></div>
    <table id="ctab"><tr><th>stem</th><th class="num">files</th><th class="num">activity</th>
    <th class="num">ubiquity</th><th>sample files</th></tr>${censusRows(D.census)}</table></div>`;
  return `<div class="tiles"><div class="tile"><b>${D.glossary.length}</b><span>glossary entities</span></div>
    <div class="tile"><b>${D.census.length}</b><span>census stems</span></div></div>` + gl + cs;
}
function censusRows(rows){
  return rows.map(c => `<tr><td>${esc(c.stem)}</td><td class="num">${c.files}</td>
    <td class="num">${c.activity}</td><td class="num">${c.ubiquity.toFixed(3)}</td>
    <td class="sm mm">${c.samples.map(esc).join(', ')}</td></tr>`).join('');
}
function fCensus(){
  const q = document.getElementById('cq').value.toLowerCase();
  document.getElementById('ctab').innerHTML =
    '<tr><th>stem</th><th class="num">files</th><th class="num">activity</th><th class="num">ubiquity</th><th>sample files</th></tr>'
    + censusRows(D.census.filter(c => c.stem.includes(q)));
}

// ---------- Features ----------
function vFeatures(){
  return `<div class="card"><h3>Taxonomy (${D.features.length} features)</h3>
   <div class="filters"><input id="fq" placeholder="filter…" oninput="fFeat()">
    <select id="fs" onchange="fFeat()"><option value="">all statuses</option>
     <option>named</option><option>provisional</option><option>confirmed</option></select></div>
   <table id="ftab">${featRows(D.features)}</table></div>`;
}
function featRows(rows){
  const head = `<tr><th>id</th><th>feature</th><th>status</th><th class="num">concerns</th>
    <th>area</th><th>definition &amp; stems</th><th></th></tr>`;
  return head + rows.map(f => `<tr>
    <td class="num mm">${f.id}</td>
    <td><b>${esc(f.name)}</b>${f.locked?' 🔒':''}</td>
    <td><span class="st ${f.status}">${f.status}</span></td>
    <td class="num">${f.n}</td><td class="sm">${esc(f.area)}</td>
    <td class="sm"><span class="mm">${esc(f.definition.slice(0,180))}</span><br>
      ${f.stems.slice(0,6).map(s=>`<span class="chip">${esc(s)}</span>`).join('')}</td>
    <td>
     <button class="copy" onclick="copyTxt('rename [${f.id}] ${esc(f.name)} -> ', this)">rename</button>
     <button class="copy" onclick="copyTxt('merge  [${f.id}] ${esc(f.name)} -> ', this)">merge</button>
     <button class="copy" onclick="copyTxt('reject [${f.id}] ${esc(f.name)}', this)">reject</button>
    </td></tr>`).join('');
}
function fFeat(){
  const q = document.getElementById('fq').value.toLowerCase();
  const s = document.getElementById('fs').value;
  document.getElementById('ftab').innerHTML = featRows(D.features.filter(f =>
    (!s || f.status === s) && (f.name.toLowerCase().includes(q) || f.definition.toLowerCase().includes(q))));
}

// ---------- Assignments ----------
function vAssign(){
  const feats = [...new Set(D.assignments.map(a => a.feature))].sort();
  return `<div class="card"><h3>Assignments (${D.assignments.length} concerns, provenance for each)</h3>
   <div class="filters">
    <input id="aq" placeholder="search label/summary/files…" oninput="fAssign()">
    <select id="af" onchange="fAssign()"><option value="">all features</option>
      ${feats.map(f=>`<option>${esc(f)}</option>`).join('')}</select>
    <select id="as" onchange="fAssign()"><option value="">all sources</option>
      ${SRC_ORDER.map(s=>`<option>${s}</option>`).join('')}</select>
    <select id="ac" onchange="fAssign()"><option value="">any conf</option>
      <option value="lt55">conf &lt; 0.55</option><option value="lt65">conf &lt; 0.65</option></select>
   </div><table id="atab">${assignRows(D.assignments.slice(0, 400))}</table>
   <div class="sm mm" id="acount"></div></div>`;
}
function assignRows(rows){
  const head = `<tr><th>concern</th><th>feature</th><th>source</th><th class="num">conf</th><th>commit</th></tr>`;
  return head + rows.map(a => `<tr>
    <td><b>${esc(a.label)}</b><div class="sm mm">${esc(a.summary)}</div>
      <div class="sm mm">${a.files.map(esc).join(', ')}</div></td>
    <td>${esc(a.feature)}</td>
    <td class="sm"><span class="src" style="background:${SRC_COLOR[a.source]||'var(--muted)'}"></span>${esc(a.source)}</td>
    <td class="num">${a.conf == null ? '—' : a.conf.toFixed(2)}</td>
    <td class="sm mm">${esc(a.commit)}</td></tr>`).join('');
}
function fAssign(){
  const q = document.getElementById('aq').value.toLowerCase();
  const f = document.getElementById('af').value, s = document.getElementById('as').value;
  const c = document.getElementById('ac').value;
  const rows = D.assignments.filter(a =>
    (!f || a.feature === f) && (!s || a.source === s) &&
    (!c || (a.conf != null && a.conf < (c === 'lt55' ? 0.55 : 0.65))) &&
    (!q || a.label.toLowerCase().includes(q) || a.summary.toLowerCase().includes(q)
       || a.files.join(' ').toLowerCase().includes(q)));
  document.getElementById('atab').innerHTML = assignRows(rows.slice(0, 400));
  document.getElementById('acount').textContent =
    rows.length > 400 ? `showing 400 of ${rows.length}` : `${rows.length} rows`;
}

// ---------- Coherence ----------
function vCoherence(){
  const rows = D.coherence.map(c => {
    const total = Object.values(c.dist||{}).reduce((x,y)=>x+y,0) || 1;
    const seg = Object.entries(c.dist||{}).sort((a,b)=>b[1]-a[1]).map(([f,n],i) =>
      `<div style="width:${(n/total*100)}%;background:${i===0?'var(--seq)':'var(--grid)'};
        border-right:2px solid var(--surface)" title="${esc(f)}: ${n}"></div>`).join('');
    return `<tr><td>${esc(c.stem)} ${c.share<0.6?'<span class="flag">⚠</span>':''}</td>
      <td class="num">${c.share.toFixed(2)}</td><td class="num">${c.n_concerns}</td>
      <td class="num">${c.n_features}</td><td>${esc(c.top_feature)}</td>
      <td style="min-width:220px"><div class="dist">${seg}</div></td></tr>`;
  }).join('');
  return `<div class="card"><h3>Stem coherence — every strong specific stem
    (blue = top feature's share; gray = scattered elsewhere)</h3>
    <table><tr><th>stem</th><th class="num">share</th><th class="num">concerns</th>
    <th class="num">features</th><th>top feature</th><th>distribution</th></tr>${rows}</table></div>`;
}

// ---------- Changeset / Areas / Untangle ----------
function vChangeset(){
  if(!D.changeset.length) return '<div class="empty">no pending proposals</div>';
  return D.changeset.map(c => `<div class="card">
    <h3>+ [${c.id}] ${esc(c.name)} <span class="mm sm">(${c.n_concerns} concerns)</span></h3>
    <div class="sm">${esc(c.definition)}</div>
    <div class="sm mm" style="margin:4px 0">e.g. ${c.samples.map(esc).join('; ')}
      ${c.nearest ? ` · nearest: ${esc(c.nearest)}` : ''}</div>
    <button class="copy" onclick="copyTxt('accept [${c.id}] ${esc(c.name)}', this)">accept</button>
    <button class="copy" onclick="copyTxt('reject [${c.id}] ${esc(c.name)}', this)">reject</button>
    <button class="copy" onclick="copyTxt('merge  [${c.id}] ${esc(c.name)} -> ', this)">merge</button>
    <button class="copy" onclick="copyTxt('rename [${c.id}] ${esc(c.name)} -> ', this)">rename</button>
  </div>`).join('');
}
function vAreas(){
  return Object.entries(D.areas).map(([a, fs]) => `<div class="card"><h3>${esc(a)}
    <span class="mm sm">(${fs.length})</span></h3>
    ${fs.map(f=>`<span class="chip">${esc(f)}</span>`).join('')}</div>`).join('');
}
function vUntangle(){
  return `<div class="card"><h3>Untangle output — newest ${D.untangle.length} commits</h3>
   <div class="filters"><input id="uq" placeholder="search…" oninput="fUnt()"></div>
   <div id="ulist">${untRows(D.untangle.slice(0,120))}</div></div>`;
}
function untRows(rows){
  return rows.map(c => `<div style="padding:7px 0;border-bottom:1px solid var(--grid)">
    <span class="mm sm">${esc(c.hash)} ${esc(c.date)}</span> <b class="sm">${esc(c.subject)}</b>
    ${c.concerns.map(k => `<div class="sm" style="margin:2px 0 0 18px">· <b>${esc(k.label)}</b>
      <span class="mm">— ${esc(k.summary)}</span>
      <span class="mm">|${k.files.map(esc).join(', ')}</span></div>`).join('')}
  </div>`).join('');
}
function fUnt(){
  const q = document.getElementById('uq').value.toLowerCase();
  document.getElementById('ulist').innerHTML = untRows(D.untangle.filter(c =>
    c.subject.toLowerCase().includes(q) ||
    c.concerns.some(k => k.label.toLowerCase().includes(q))).slice(0,120));
}

const VIEWS = {Overview:vOverview, Ground:vGround, Features:vFeatures, Assignments:vAssign,
  Coherence:vCoherence, Changeset:vChangeset, Areas:vAreas, Untangle:vUntangle};
function render(){ document.getElementById('main').innerHTML = VIEWS[tab](); }
document.addEventListener('mousemove', ev => {
  const t = ev.target.closest('[data-tt]');
  if(t) showTip(ev, esc(t.getAttribute('data-tt'))); else if(!ev.target.closest('.bar-row')) hideTip();
});
nav(); render();
</script></body></html>"""
