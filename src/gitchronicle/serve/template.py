"""Self-contained hierarchical KB browser: Areas -> Domains -> Detail (concerns, files, commits).

All CSS/JS inline, no external requests. Data injected at __GRAPH_DATA__.
Placeholders: __GRAPH_DATA__ __N_AREAS__ __N_DOMAINS__ __N_COMMITS__ __GENERATED__
"""

HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>gitchronicle</title>
<style>
  :root{ --bg:#f7f8fa; --panel:#fff; --ink:#1c2230; --muted:#5b6472; --line:#e2e6ee;
    --chip:#eef1f6; --accent:#4C78A8; --sel:#e7eef7; --shadow:0 1px 8px rgba(20,30,50,.08);}
  @media (prefers-color-scheme:dark){:root{ --bg:#12151c; --panel:#1a1f29; --ink:#e7ebf2;
    --muted:#9aa4b4; --line:#2a3340; --chip:#222a36; --accent:#7aa6d6; --sel:#243244;
    --shadow:0 1px 10px rgba(0,0,0,.3);}}
  :root[data-theme=light]{ --bg:#f7f8fa; --panel:#fff; --ink:#1c2230; --muted:#5b6472;
    --line:#e2e6ee; --chip:#eef1f6; --accent:#4C78A8; --sel:#e7eef7;}
  :root[data-theme=dark]{ --bg:#12151c; --panel:#1a1f29; --ink:#e7ebf2; --muted:#9aa4b4;
    --line:#2a3340; --chip:#222a36; --accent:#7aa6d6; --sel:#243244;}
  *{box-sizing:border-box}
  html,body{margin:0;height:100%}
  body{font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
    background:var(--bg);color:var(--ink);overflow:hidden}
  header{position:fixed;top:0;left:0;right:0;height:52px;display:flex;align-items:center;gap:14px;
    padding:0 16px;background:var(--panel);border-bottom:1px solid var(--line);z-index:10}
  header h1{font-size:15px;margin:0;font-weight:650;letter-spacing:.2px}
  header .stats{color:var(--muted);font-size:12.5px}
  header .spacer{flex:1}
  #search{background:var(--chip);border:1px solid var(--line);color:var(--ink);border-radius:8px;
    padding:8px 11px;width:280px;font-size:13px;outline:none}
  #search:focus{border-color:var(--accent)}
  .cols{position:fixed;top:52px;left:0;right:0;bottom:0;display:grid;
    grid-template-columns:300px 340px 1fr}
  .col{overflow-y:auto;border-right:1px solid var(--line);background:var(--panel)}
  .col:last-child{border-right:none;background:var(--bg)}
  .colhead{position:sticky;top:0;background:var(--panel);border-bottom:1px solid var(--line);
    padding:9px 14px;font-size:11px;letter-spacing:.7px;text-transform:uppercase;color:var(--muted);z-index:1}
  .row{padding:9px 14px;cursor:pointer;border-bottom:1px solid var(--line)}
  .row:hover{background:var(--chip)}
  .row.sel{background:var(--sel)}
  .row .nm{font-weight:600;font-size:13.5px}
  .row .mm{font-size:11.5px;color:var(--muted);margin-top:1px}
  .bar{height:3px;border-radius:2px;background:var(--accent);opacity:.5;margin-top:5px}
  .chip{display:inline-block;background:var(--chip);border:1px solid var(--line);border-radius:999px;
    padding:1px 8px;font-size:11px;color:var(--muted);margin:2px 3px 0 0}
  .kbd{border-radius:4px;padding:1px 6px;font-size:10px;font-weight:700;color:#fff;text-transform:uppercase}
  .k-feat{background:#54A24B}.k-fix{background:#E45756}.k-refactor{background:#4C78A8}
  .k-chore{background:#8C6BB1}.k-docs{background:#B279A2}.k-core{background:#E45756}
  .k-subsystem{background:#4C78A8}.k-ui{background:#F58518}.k-data{background:#72B7B2}
  .k-infra{background:#54A24B}.k-tooling{background:#B279A2}.k-other{background:#72809a}
  /* detail */
  #detail{padding:22px 28px;max-width:900px}
  #detail .empty{color:var(--muted);text-align:center;margin-top:90px}
  .dtitle{font-size:22px;font-weight:700;margin:0 0 4px}
  .crumb{color:var(--muted);font-size:12.5px;margin-bottom:10px}
  .crumb b{color:var(--accent);cursor:pointer}
  .statbar{display:flex;gap:16px;flex-wrap:wrap;color:var(--muted);font-size:12.5px;margin:8px 0}
  .statbar b{color:var(--ink)}
  h4{margin:18px 0 6px;font-size:11px;letter-spacing:.6px;text-transform:uppercase;color:var(--muted)}
  .concerns{display:flex;flex-wrap:wrap;gap:6px}
  .concern{background:var(--chip);border:1px solid var(--line);border-radius:7px;padding:3px 9px;font-size:12.5px}
  .concern .n{color:var(--muted);font-size:11px}
  .files code{display:block;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11.5px;
    color:var(--muted);padding:1px 0}
  .commit{border:1px solid var(--line);border-radius:8px;padding:8px 11px;margin:6px 0;background:var(--panel)}
  .chead{display:flex;align-items:center;gap:8px;font-size:11px;color:var(--muted);flex-wrap:wrap}
  .chead code{color:var(--accent);font-family:ui-monospace,Menlo,Consolas,monospace}
  .csub{font-size:13px;font-weight:540;margin-top:3px;white-space:pre-wrap;overflow-wrap:anywhere}
  .cbody{font-size:12px;color:var(--muted);margin-top:4px;white-space:pre-wrap;overflow-wrap:anywhere}
  .adom{font-size:12px;color:var(--muted);cursor:pointer;padding:2px 0}
  .adom:hover{color:var(--accent)}
  mark{background:#ffe08a;color:#3a2f00;border-radius:2px}
</style>
</head>
<body>
<header>
  <h1>gitchronicle</h1>
  <span class="stats">__N_AREAS__ areas · __N_DOMAINS__ domains · __N_COMMITS__ commits</span>
  <span class="spacer"></span>
  <input id="search" placeholder="search areas, domains, concerns…" autocomplete="off">
</header>
<div class="cols">
  <div class="col" id="col-areas"><div class="colhead">Areas</div><div id="areas"></div></div>
  <div class="col" id="col-domains"><div class="colhead" id="dh">Domains</div><div id="domains"></div></div>
  <div class="col" id="col-detail"><div id="detail"><div class="empty">Select an area, then a domain.</div></div></div>
</div>
<script>
const DATA = __GRAPH_DATA__;
const AREAS = DATA.areas, DOMS = DATA.domains, COMMITS = DATA.commits;
const domById = new Map(DOMS.map(d=>[d.id,d]));
const areaById = new Map(AREAS.map(a=>[a.id,a]));
const domsByArea = new Map();
for(const d of DOMS){ if(!domsByArea.has(d.area_id)) domsByArea.set(d.area_id,[]); domsByArea.get(d.area_id).push(d); }
for(const [,ds] of domsByArea) ds.sort((a,b)=>b.n_commits-a.n_commits);
let selArea=null, selDom=null;
const maxAC = Math.max(1,...AREAS.map(a=>a.n_commits));
function esc(s){return(s||"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));}
function kc(x){return "k-"+(x||"other").toLowerCase().replace(/[^a-z]/g,"");}
function hl(s,q){ s=esc(s); if(!q) return s; const i=s.toLowerCase().indexOf(q);
  return i<0?s:s.slice(0,i)+"<mark>"+s.slice(i,i+q.length)+"</mark>"+s.slice(i+q.length); }

function renderAreas(list,q){
  const el=document.getElementById("areas");
  el.innerHTML=list.map(a=>`<div class="row${a.id===selArea?' sel':''}" onclick="pickArea(${a.id})">
    <div class="nm">${hl(a.name,q)}</div>
    <div class="mm">${a.n_domains} domains · ${a.n_commits} commits</div>
    <div class="bar" style="width:${Math.max(8,100*a.n_commits/maxAC)}%"></div></div>`).join("")
    || '<div class="row mm">no matches</div>';
}
function pickArea(id){ selArea=id; selDom=null; renderAreas(curAreas(),curQ());
  const ds=domsByArea.get(id)||[]; document.getElementById("dh").textContent=(areaById.get(id).name)+" — "+ds.length+" domains";
  renderDomains(ds,curQ()); document.getElementById("detail").innerHTML='<div class="empty">Select a domain.</div>'; }
function renderDomains(list,q){
  const el=document.getElementById("domains");
  el.innerHTML=list.map(d=>`<div class="row${d.id===selDom?' sel':''}" onclick="pickDom(${d.id})">
    <div class="nm">${hl(d.name,q)}${d.status==='provisional'?' <span class="kbd" style="background:#EECA3B;color:#333" title="machine-proposed, pending review">provisional</span>':''}</div>
    <div class="mm"><span class="kbd ${kc(d.classification)}">${esc(d.classification)}</span>
      ${d.n_commits} commits · ${d.n_files} files · ${esc(d.first_seen)}→${esc(d.last_seen)}</div></div>`).join("")
    || '<div class="row mm">no domains</div>';
}
function pickDom(id){ selDom=id; const d=domById.get(id); if(d.area_id!==selArea){selArea=d.area_id; pickAreaSilent(d.area_id);}
  renderDomains(domsByArea.get(d.area_id)||[],curQ()); renderAreas(curAreas(),curQ()); renderDetail(d); }
function pickAreaSilent(id){ const ds=domsByArea.get(id)||[]; document.getElementById("dh").textContent=(areaById.get(id).name)+" — "+ds.length+" domains"; }
function renderDetail(d){
  const a=areaById.get(d.area_id);
  const concerns=(d.concerns||[]).map(c=>`<span class="concern">${esc(c.label)} <span class="n">×${c.n}</span></span>`).join("");
  const files=(d.files||[]).map(f=>`<code>${esc(f)}</code>`).join("")||'<span class="mm">—</span>';
  const commits=(d.commits||[]).map(h=>{const c=COMMITS[h]; if(!c)return"";
    return `<div class="commit"><div class="chead"><code>${esc(h)}</code>
      ${c.kind?`<span class="kbd ${kc(c.kind)}">${esc(c.kind)}</span>`:""}<span>${esc(c.date)}</span><span>· ${esc(c.author)}</span></div>
      <div class="csub">${esc(c.subject)}</div>${c.body?`<div class="cbody">${esc(c.body)}</div>`:""}</div>`;}).join("");
  const tags=(d.tags||[]).map(t=>`<span class="chip">${esc(t)}</span>`).join("");
  document.getElementById("detail").innerHTML=`
    <div class="crumb"><b onclick="pickArea(${a.id})">${esc(a.name)}</b> › domain</div>
    <p class="dtitle">${esc(d.name)}${d.status==='provisional'?' <span class="kbd" style="background:#EECA3B;color:#333">provisional</span>':''}</p>
    <div><span class="kbd ${kc(d.classification)}">${esc(d.classification)}</span> ${tags}</div>
    <div class="statbar"><span><b>${d.n_commits}</b> commits</span><span><b>${d.n_files}</b> files</span>
      <span><b>${(d.concerns||[]).length}</b> concerns</span><span>${esc(d.first_seen)} → ${esc(d.last_seen)}</span>
      <span class="kbd" style="background:${d.color}">${esc(d.lifecycle)}</span></div>
    <h4>Concerns (what was worked on)</h4><div class="concerns">${concerns||'<span class="mm">—</span>'}</div>
    <h4>Files</h4><div class="files">${files}</div>
    <h4>Commits (${(d.commits||[]).length})</h4>${commits||'<span class="mm">—</span>'}`;
  document.getElementById("col-detail").scrollTop=0;
}
/* search across areas + domains + concerns */
let Q="";
function curQ(){return Q;}
function curAreas(){
  if(!Q) return AREAS;
  return AREAS.filter(a=>a.name.toLowerCase().includes(Q) ||
    (domsByArea.get(a.id)||[]).some(d=>domMatch(d,Q)));
}
function domMatch(d,q){ return d.name.toLowerCase().includes(q) ||
  (d.tags||[]).some(t=>t.toLowerCase().includes(q)) ||
  (d.concerns||[]).some(c=>c.label.toLowerCase().includes(q)); }
document.getElementById("search").addEventListener("input",e=>{
  Q=e.target.value.trim().toLowerCase();
  renderAreas(curAreas(),Q);
  if(Q){ // show matching domains across all areas in the middle column
    const hits=DOMS.filter(d=>domMatch(d,Q)).sort((a,b)=>b.n_commits-a.n_commits).slice(0,200);
    document.getElementById("dh").textContent=hits.length+" matching domains";
    renderDomains(hits,Q);
  } else if(selArea!=null){ pickArea(selArea); }
  else { document.getElementById("dh").textContent="Domains"; document.getElementById("domains").innerHTML='<div class="row mm">pick an area</div>'; }
});
/* init */
renderAreas(AREAS,"");
document.getElementById("domains").innerHTML='<div class="row mm">pick an area →</div>';
if(AREAS.length) pickArea(AREAS[0].id);
</script>
</body>
</html>
"""
