"""STUDIO — the local read/write view of the catalogue, and the ledger editor.

Curation needs sight. The ledger works, but a rule can only be written about something the
owner can see, and until now the only way to notice that a 400-file tool had been smeared
across fifteen game features was for someone to print that table by hand.

So the studio leads with FRAGMENTATION: for every directory, how many entries hold files
under it and whether any of them dominates. A tree split across a dozen entries with no
majority owner is the shape of a missed standalone thing, and it sorts to the top on its
own. The catalogue view answers "what do I have", the tree view answers "what did the tool
get wrong", and the editor turns the answer into a rule.

It writes exactly one file, ``gitchronicle.plan``, and never touches the database: replay
is pure, so the preview is the same code path as the real run, and nothing is committed
until Save. Served on localhost from the stdlib — a curation tool that needed a build step
would not get used.
"""

from __future__ import annotations

import fnmatch
import json
import re
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from ..taxonomy.ledger import PLAN_FILE, Ledger, LedgerError

_SAMPLE = 40           # territory files sent per entry; the count is always exact
_MIN_TREE_FILES = 6    # a directory smaller than this cannot be meaningfully fragmented

# Directory names that describe a ROLE rather than a thing. Everything lives in one of
# these, so their being shared carries no information at all.
_STRUCTURAL = {
    "src", "source", "sources", "include", "includes", "inc", "lib", "libs", "bin",
    "build", "dist", "out", "obj", "test", "tests", "spec", "specs", "doc", "docs",
    "root", "core", "common", "shared", "util", "utils", "helpers", "internal",
    "game", "client", "server", "tools", "scripts", "config", "conf", "settings",
    "data", "assets", "static", "public", "vendor", "extern", "external", "thirdparty",
    "third_party", "node_modules", "components", "modules", "packages", "app", "apps",
    "main", "impl", "api", "ui", "gui", "web", "www", "files", "locale", "locales",
}


def _catalogue(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name, definition, tier, tier_from, classification, lifecycle, "
        "n_commits, born_at, last_seen FROM domains ORDER BY name").fetchall()
    terr: dict[int, list] = defaultdict(list)
    for r in conn.execute("SELECT domain_id, path FROM domain_files ORDER BY path"):
        terr[r["domain_id"]].append(r["path"])
    fan: Counter = Counter()
    for r in conn.execute("SELECT dst_domain FROM domain_edges"):
        fan[r["dst_domain"]] += 1
    out = []
    for r in rows:
        files = terr.get(r["id"], [])
        out.append({
            "name": r["name"], "tier": r["tier"] or "feature",
            "tier_from": r["tier_from"] or "auto",
            "definition": (r["definition"] or "")[:300],
            "classification": r["classification"], "lifecycle": r["lifecycle"],
            "commits": r["n_commits"] or 0, "fan_in": fan[r["id"]],
            "born": (r["born_at"] or "")[:10], "last": (r["last_seen"] or "")[:10],
            "files": files[:_SAMPLE], "n_files": len(files),
        })
    return out


def _fragmentation(conn, ledger=None, min_files: int = _MIN_TREE_FILES) -> list[dict]:
    """Directories that look like one thing but are catalogued as many.

    A directory a ledger rule already claims is a question the owner has ANSWERED, and
    re-asking it is worse than not asking: the rules only reach the database when `update`
    runs, so without this the review queue keeps posing questions whose answers are already
    written down, and invites the same rule to be added twice.

    Fragmentation alone is the wrong signal and ranking by it is actively misleading:
    Server/game/src is held by 49 entries and that is simply correct, because it is where
    all server code lives. What marks a MISSED thing is a directory that carries a name of
    its own which no entry answers to -- wiki_manager is split fifteen ways and nothing in
    the catalogue is called anything like it. Structural directories (src, include, game)
    are named after their role rather than their contents and are skipped for that reason.
    """
    owner: dict[str, str] = {}
    for r in conn.execute(
            "SELECT d.name, df.path FROM domain_files df JOIN domains d ON d.id = df.domain_id"):
        owner[r["path"]] = r["name"]
    entry_names = {r["name"] for r in conn.execute("SELECT name FROM domains")}
    name_toks = {t for n in entry_names for t in re.split(r"[^a-z0-9]+", n.lower()) if t}

    by_dir: dict[str, Counter] = defaultdict(Counter)
    samples: dict[str, list] = defaultdict(list)
    ruled: dict[str, Counter] = defaultdict(Counter)
    for path in sorted(owner):
        parts = path.split("/")
        by_rule = ledger.owner(path) if ledger is not None else None
        for depth in range(1, min(len(parts), 5)):       # every ancestor, to 4 levels
            d = "/".join(parts[:depth])
            by_dir[d][owner[path]] += 1
            if by_rule:
                ruled[d][by_rule] += 1
            # the filenames are the evidence: uiscript/ holds achievementpanel.py and
            # atlaswindow.py, which ARE different features and should stay apart
            if len(samples[d]) < 8:
                samples[d].append(path[len(d) + 1:])

    out = []
    for d, holders in by_dir.items():
        total = sum(holders.values())
        if total < min_files or len(holders) < 2:
            continue
        base = d.rsplit("/", 1)[-1].lower().lstrip(".")
        if base in _STRUCTURAL or len(base) < 4:
            continue
        toks = {t for t in re.split(r"[^a-z0-9]+", base) if len(t) >= 4}
        if not toks:
            continue
        top, top_n = holders.most_common(1)[0]
        # does anything in the catalogue already answer to this directory's name?
        claimed_by_name = bool(toks & name_toks)
        # a rule covering most of the tree has settled it, whatever the database still says
        rules = ruled.get(d)
        handled = None
        if ledger is not None and ledger.kept(d + "/x"):
            handled = "kept split (on purpose)"
        elif rules:
            rn, rk = rules.most_common(1)[0]
            if rk >= 0.6 * total:
                handled = rn
        out.append({"dir": d, "files": total, "entries": len(holders),
                    "top": top, "top_share": round(top_n / total, 2),
                    "unnamed": not claimed_by_name, "handled": handled,
                    "samples": samples.get(d, [])[:8],
                    "holders": holders.most_common(8)})

    # A parent and child holding the same files are the same finding stated twice
    # (Client-Files/uiscript and .../uiscript/uiscript); keep the deeper, more specific one.
    out.sort(key=lambda r: -len(r["dir"]))
    seen: dict[tuple, dict] = {}
    for r in out:
        key = (r["files"], r["entries"], r["top"])
        seen.setdefault(key, r)
    out = list(seen.values())

    # an unnamed, heavily split, substantial tree first — that is the shape of a missed
    # standalone thing; a named one is probably already modelled, just imperfectly
    out.sort(key=lambda r: (bool(r["handled"]), not r["unnamed"],
                            r["top_share"], -r["files"]))
    return out[:120]


def _preview(conn, plan_text: str) -> dict:
    """Replay a candidate ledger over the current catalogue. Pure; writes nothing."""
    try:
        led = Ledger.parse(plan_text)
    except LedgerError as exc:
        return {"error": str(exc)}

    cat: dict[str, set] = defaultdict(set)
    for r in conn.execute(
            "SELECT d.name, df.path FROM domain_files df JOIN domains d ON d.id = df.domain_id"):
        cat[r["name"]].add(r["path"])
    before = {n: set(v) for n, v in cat.items()}
    after, rep = led.apply({n: set(v) for n, v in cat.items()})

    moves = []
    for name in sorted(set(before) | set(after)):
        b, a = len(before.get(name, ())), len(after.get(name, ()))
        if b != a:
            moves.append({"name": name, "before": b, "after": a,
                          "new": name not in before, "gone": name not in after})
    moves.sort(key=lambda m: -abs(m["after"] - m["before"]))
    return {"report": rep, "moves": moves[:60],
            "warnings": _lint(led, before, plan_text),
            "tiers": led.tiers(), "entries": len(led.entries)}


def _lint(led: Ledger, catalogue: dict, plan_text: str = "") -> list[str]:
    """Problems a preview alone will not show, because the ledger does exactly as told.

    A rule is never wrong on its own terms: "ccc" and "CCC" make two entries because they
    are two names, and "Luna" carves files out of "Luna Scripting System" because that is
    what claiming them means. Both were real slips, and neither shows up as an error —
    only as a catalogue that quietly says something the owner did not mean.
    """
    warn = []
    names = [e.name for e in led.entries]
    known = set(catalogue)

    seen: dict[str, str] = {}
    for n in names + sorted(known):
        low = n.lower()
        if low in seen and seen[low] != n:
            warn.append(f"“{seen[low]}” and “{n}” differ only in capitalisation — "
                        f"that is two entries, not one.")
        seen.setdefault(low, n)

    for n in names:
        if n in known:
            continue
        for k in known:
            if n.lower() != k.lower() and (n.lower() in k.lower() or k.lower() in n.lower()):
                warn.append(f"“{n}” is new and takes files from “{k}”. If you meant to "
                            f"extend it, use its exact name.")
                break

    # Duplicates are invisible after parsing, because repeated blocks are merged by
    # design — so this reads the raw text, which is the only place they still exist.
    from collections import Counter as _C
    for n, k in _C(re.findall(r'^entry\s+"([^"]*)"', plan_text, re.M)).items():
        if k > 1:
            warn.append(f"“{n}” is declared {k} times — the blocks combine into ONE entry. "
                        f"Delete one if you meant them to be separate.")

    for e in led.entries:
        if e.claims and not any(any(fnmatch.fnmatch(f, g) for g in e.claims)
                                for fs in catalogue.values() for f in fs):
            warn.append(f"“{e.name}” claims nothing that exists — check the glob.")
    return warn[:12]


def build_state(conn, plan_path: str | Path = PLAN_FILE, plan_text: str | None = None) -> dict:
    p = Path(plan_path)
    if plan_text is None:
        plan_text = p.read_text(encoding="utf-8") if p.exists() else Ledger().render()
    try:
        led = Ledger.parse(plan_text)
    except LedgerError:
        led = Ledger()
    return {
        "catalogue": _catalogue(conn),
        "trees": _fragmentation(conn, led),
        "plan": plan_text,
        "plan_path": str(p),
    }


def serve_studio(db_path: str | Path, plan_path: str | Path = PLAN_FILE, port: int = 8765,
                 log=print) -> None:
    """Open the knowledge base per request rather than holding it.

    `update` swaps the database atomically, so a long-lived connection keeps reading the
    replaced file and the studio would show yesterday's catalogue until restarted — in a
    loop that goes curate, update, look again, that is the one thing it must not do.
    Opening is microseconds against a local file.
    """
    from ..storage import connect as db_connect

    plan = Path(plan_path)
    db = str(db_path)

    def fresh():
        return db_connect(db)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):        # the server's own chatter is not the user's news
            pass

        def _send(self, body: bytes, ctype: str, code: int = 200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200):
            self._send(json.dumps(obj).encode(), "application/json; charset=utf-8", code)

        def do_GET(self):
            if self.path == "/":
                self._send(PAGE.encode(), "text/html; charset=utf-8")
            elif self.path == "/api/state":
                c = fresh()
                try:
                    self._json(build_state(c, plan))
                finally:
                    c.close()
            else:
                self._send(b"not found", "text/plain", 404)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            text = body.get("plan", "")
            if self.path == "/api/preview":
                c = fresh()
                try:
                    r = _preview(c, text)
                    # the queue reflects the rules being edited, not only the saved ones
                    r["trees"] = (_fragmentation(c, Ledger.parse(text))
                                  if "error" not in r else None)
                finally:
                    c.close()
                self._json(r)
            elif self.path == "/api/save":
                try:
                    Ledger.parse(text)                 # never persist a file that won't load
                except LedgerError as exc:
                    self._json({"error": str(exc)}, 400)
                    return
                plan.write_text(text, encoding="utf-8")
                log(f"  saved {plan}")
                self._json({"saved": str(plan)})
            else:
                self._send(b"not found", "text/plain", 404)

    try:
        srv = HTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        # almost always an earlier studio still running — and one serving older code,
        # which is worse than no studio at all, so say what to do about it
        raise SystemExit(
            f"  port {port} is already in use — another studio is probably still running.\n"
            f"  Stop it with Ctrl-C in its terminal, or start this one elsewhere:\n"
            f"      gitchronicle studio --db <kb.db> --port {port + 1}\n"
            f"  ({exc})") from exc
    log(f"  studio on http://127.0.0.1:{port}   (editing {plan}; Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("  stopped")
    finally:
        srv.server_close()


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>gitchronicle studio</title>
<style>
:root { --bg:#fff; --fg:#1a1d21; --muted:#69707a; --line:#e3e6ea; --side:#f6f7f9;
        --acc:#2860c4; --chip:#eef2f9; --warn:#b4530a; --ok:#1e7a45; --card:#fff; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#14161a; --fg:#e6e8eb; --muted:#96a0ab; --line:#2a2f36; --side:#191c21;
          --acc:#7aa5f0; --chip:#20293a; --warn:#e2933f; --ok:#5fc98c; --card:#181b20; }
}
* { box-sizing:border-box; margin:0 }
body { background:var(--bg); color:var(--fg); font:14px/1.55 system-ui,-apple-system,sans-serif }
header { display:flex; align-items:center; gap:14px; padding:10px 20px; border-bottom:1px solid var(--line) }
header h1 { font-size:15px }
nav { margin-left:auto; display:flex; gap:6px }
nav button { background:none; border:1px solid var(--line); color:var(--fg); border-radius:6px;
  padding:5px 13px; cursor:pointer; font-size:13px }
nav button.on { background:var(--acc); border-color:var(--acc); color:#fff }
#how { background:var(--side); border-bottom:1px solid var(--line); padding:9px 20px;
  font-size:13px; color:var(--muted) }
#how b { color:var(--fg); font-weight:600 }
main { padding:18px 20px 90px; max-width:980px }
h2 { font-size:17px; margin-bottom:4px }
.sub { color:var(--muted); font-size:13px; max-width:80ch }
.card { border:1px solid var(--line); background:var(--card); border-radius:10px;
  padding:14px 16px; margin:12px 0 }
.card.done { opacity:.45 }
.card h3 { font-size:14px; font-family:ui-monospace,Menlo,monospace; margin-bottom:3px }
.facts { color:var(--muted); font-size:12.5px; margin-bottom:9px }
.mono { font-family:ui-monospace,Menlo,monospace; font-size:12px; color:var(--muted) }
.cols { display:flex; gap:26px; flex-wrap:wrap; margin:8px 0 12px }
.cols > div { min-width:220px } .cols h4 { font-size:11.5px; text-transform:uppercase;
  letter-spacing:.06em; color:var(--muted); margin-bottom:4px; font-weight:500 }
.acts { display:flex; gap:8px; align-items:center; flex-wrap:wrap }
button.p { background:var(--acc); border:1px solid var(--acc); color:#fff; border-radius:6px;
  padding:5px 13px; cursor:pointer; font-size:13px }
button.s { background:none; border:1px solid var(--line); color:var(--muted); border-radius:6px;
  padding:5px 13px; cursor:pointer; font-size:13px }
button.s:hover { color:var(--fg); border-color:var(--acc) }
input,select { background:var(--bg); color:var(--fg); border:1px solid var(--line);
  border-radius:6px; padding:5px 9px; font-size:13px; font-family:inherit }
input { min-width:240px }
table { border-collapse:collapse; width:100%; font-size:13px }
th,td { text-align:left; padding:5px 9px; border-bottom:1px solid var(--line); vertical-align:top }
th { color:var(--muted); font-weight:500; font-size:12px }
td.num,th.num { text-align:right; font-variant-numeric:tabular-nums }
.tier { display:inline-block; font-size:11px; padding:1px 8px; border-radius:99px;
  background:var(--chip); color:var(--muted) }
textarea { width:100%; height:40vh; background:var(--side); color:var(--fg); border:1px solid var(--line);
  border-radius:8px; padding:12px; font-family:ui-monospace,Menlo,monospace; font-size:12.5px }
#bar { position:fixed; left:0; right:0; bottom:0; background:var(--card);
  border-top:1px solid var(--line); padding:10px 20px; display:flex; gap:12px; align-items:center }
#bar .grow { flex:1; color:var(--muted); font-size:13px }
.err { color:var(--warn) } .ok { color:var(--ok) }
details summary { cursor:pointer; color:var(--muted); font-size:12.5px }
</style></head><body>
<header><h1>gitchronicle studio</h1>
<nav><button id="bR" class="on">Review</button><button id="bC">Catalogue</button>
<button id="bL">Rules</button></nav></header>
<div id="how"></div>
<datalist id="entrynames"></datalist>
<main id="main">loading…</main>
<div id="bar"><span class="grow" id="status"></span>
  <button class="s" id="bPrev">Preview changes</button>
  <button class="p" id="bSave">Save rules</button></div>
<script>
"use strict";
let S = null, view = "review", planText = "", dirty = 0, skipped = new Set();
const $ = id => document.getElementById(id);
const el = (t, c, x) => { const e = document.createElement(t); if (c) e.className = c;
  if (x !== undefined) e.textContent = x; return e; };

const HOW = {
  review: "<b>Step 1 of 3 — Review.</b> Each card is one directory the catalogue split "
    + "across several entries. Look at the filenames: if they are all parts of <b>one "
    + "thing you built</b>, make it one entry. If they are genuinely different features "
    + "that happen to share a folder, skip it — being split is correct there.",
  catalogue: "<b>Everything the tool found.</b> Grouped by tier. <b>set by</b> tells you "
    + "whether you decided a tier or the tool guessed. Change any guess you disagree with.",
  rules: "<b>Step 2 and 3 — Preview, then Save.</b> These rules are replayed on every run, "
    + "so they survive new commits. Preview shows exactly which files move before anything "
    + "is written. After saving, run <b>gitchronicle update</b> to rebuild."
};

async function load() {
  S = await (await fetch("/api/state")).json();
  planText = S.plan;
  const dl = $("entrynames"); dl.textContent = "";
  for (const r of S.catalogue.slice().sort((a, b) => a.name.localeCompare(b.name)))
    dl.append(new Option(r.name));
  render();
}

function setStatus(msg, cls) {
  const s = $("status");
  s.className = "grow " + (cls || "");
  s.textContent = msg || (dirty
    ? dirty + " unsaved rule" + (dirty > 1 ? "s" : "") + " — preview, then save"
    : "No unsaved changes. Editing " + S.plan_path);
}

function pendingNames() {
  const out = [];
  const re = /^entry\s+"([^"]*)"/gm;
  let m; while ((m = re.exec(planText))) out.push(m[1]);
  return out;
}

async function addRule(text) {
  planText = planText.replace(/\s*$/, "") + "\n\n" + text.trim() + "\n";
  dirty++; setStatus();
  await refreshTrees();       // a question just answered must leave the queue at once
}

async function refreshTrees() {
  const r = await (await fetch("/api/preview", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plan: planText }) })).json();
  if (r && r.trees) S.trees = r.trees;
  return r;
}

// ---------- Review ----------------------------------------------------------------
function renderReview() {
  const m = $("main"); m.textContent = "";
  const open = S.trees.filter(t => !skipped.has(t.dir) && !t.handled);
  const done = S.trees.filter(t => t.handled);
  m.append(el("h2", "", "Directories the catalogue split up"));
  m.append(el("div", "sub", open.length + " still open, most suspicious first. "
    + "A directory is suspicious when no single entry owns most of it and nothing in the "
    + "catalogue is named after it."));
  for (const t of open.slice(0, 40)) m.append(card(t));
  if (!open.length) m.append(el("p", "sub", "Nothing left to review."));

  if (done.length) {
    const d = el("details");
    d.style.marginTop = "18px";
    d.append(el("summary", "", done.length + " already covered by a rule"));
    for (const t of done) {
      const r = el("div", "sub");
      r.append(el("span", "mono", t.dir), " → ", el("b", "", t.handled));
      d.append(r);
    }
    m.append(d);
  }
}

function card(t) {
  const c = el("div", "card");
  c.append(el("h3", "", t.dir));
  c.append(el("div", "facts", t.files + " files · split across " + t.entries
    + " entries · largest holder “" + t.top + "” has only "
    + Math.round(t.top_share * 100) + "%"));

  const cols = el("div", "cols");
  const a = el("div"); a.append(el("h4", "", "what is in it"));
  for (const s of t.samples) a.append(el("div", "mono", s));
  const b = el("div"); b.append(el("h4", "", "currently filed under"));
  for (const [n, k] of t.holders) b.append(el("div", "mono", k + "  " + n));
  cols.append(a, b); c.append(cols);

  const acts = el("div", "acts");
  const mk = el("button", "p", "These are one thing →");
  const skip = el("button", "s", "Correctly separate — skip");
  acts.append(mk, skip);
  c.append(acts);

  mk.onclick = () => {
    acts.textContent = "";
    const name = el("input");
    name.setAttribute("list", "entrynames");      // every existing entry, autocompleted
    name.value = t.top;                           // default to the entry already holding most
    const tier = el("select");
    for (const o of ["tooling", "framework", "foundation", "feature", "content"])
      tier.append(new Option(o, o));
    const go = el("button", "p", "Add rule");
    const cancel = el("button", "s", "Cancel");
    const hint = el("div", "sub");
    acts.append(el("span", "sub", "Belongs to:"), name, tier, go, cancel);
    c.append(hint);

    // Naming is where curation goes wrong silently: typing "Luna" when the catalogue says
    // "Luna Scripting System" does not extend that entry, it CARVES 19 files out of it,
    // and "ccc" beside "CCC" makes two products out of one. Say which is about to happen.
    const judge = () => {
      const v = name.value.trim();
      // Candidates are the catalogue PLUS entries declared by rules written this session:
      // "ccc" and "CCC" both look new against the database, and only collide with each
      // other, which is exactly how one backoffice became two products.
      const pending = pendingNames().filter(n => !S.catalogue.some(r => r.name === n));
      const all = S.catalogue.map(r => r.name).concat(pending);
      const exact = S.catalogue.find(r => r.name === v);
      if (exact) {
        tier.value = exact.tier;
        hint.className = "sub ok";
        hint.textContent = "↳ adds to the existing entry “" + exact.name + "” ("
          + exact.n_files + " files, " + exact.tier + ")";
        return;
      }
      if (pending.includes(v)) {
        hint.className = "sub ok";
        hint.textContent = "↳ adds to “" + v + "”, which you declared earlier in this session";
        return;
      }
      const lower = v.toLowerCase();
      const sameWord = all.filter(n => n !== v && lower && n.toLowerCase() === lower);
      if (sameWord.length) {
        hint.className = "sub err";
        hint.textContent = "⚠ “" + sameWord[0] + "” already exists and differs only in "
          + "capitalisation — names are case-sensitive, so this would make a second entry.";
        return;
      }
      const near = all.filter(n => n !== v && lower
        && (n.toLowerCase().includes(lower) || lower.includes(n.toLowerCase())));
      if (near.length) {
        hint.className = "sub err";
        hint.textContent = "⚠ creates a NEW entry and takes files away from: "
          + near.slice(0, 3).map(n => "“" + n + "”").join(", ")
          + ". Pick the exact name to extend one instead.";
      } else {
        hint.className = "sub";
        hint.textContent = v ? "+ creates a new entry “" + v + "”" : "";
      }
    };
    name.oninput = judge;
    name.focus(); name.select(); judge();

    cancel.onclick = () => { renderReview(); };
    go.onclick = async () => {
      if (!name.value.trim()) { hint.className = "sub err"; hint.textContent = "Name it first."; return; }
      await addRule('entry "' + name.value.trim() + '"\n  claim  ' + t.dir + '/**\n  tier   ' + tier.value);
      c.className = "card done";
      acts.textContent = "";
      hint.textContent = "";
      acts.append(el("span", "ok", "✓ rule added"));
    };
  };
  skip.onclick = async () => {
    await addRule("keep-split " + t.dir + "/**");
    c.className = "card done";
    c.querySelector(".acts").textContent = "";
    c.querySelector(".acts").append(el("span", "ok", "✓ recorded as correctly separate"));
  };
  return c;
}

// ---------- Catalogue --------------------------------------------------------------
function renderCatalogue() {
  const m = $("main"); m.textContent = "";
  m.append(el("h2", "", "Catalogue"));
  const q = el("input"); q.placeholder = "filter by name…";
  q.style.margin = "10px 0"; m.append(q);
  const box = el("div"); m.append(box);
  const draw = () => {
    box.textContent = "";
    const f = q.value.trim().toLowerCase();
    for (const tier of ["foundation", "framework", "feature", "tooling", "content"]) {
      const rows = S.catalogue.filter(r => r.tier === tier
        && (!f || r.name.toLowerCase().includes(f)));
      if (!rows.length) continue;
      box.append(el("h2", "", tier + " (" + rows.length + ")"));
      const tb = el("table"); const hr = el("tr");
      ["entry", "files", "commits", "used by", "active", "set by", ""].forEach((h, i) =>
        hr.append(el("th", (i >= 1 && i <= 3) ? "num" : "", h)));
      tb.append(hr);
      rows.sort((x, y) => y.commits - x.commits);
      for (const r of rows) {
        const tr = el("tr");
        const td = el("td"); td.append(el("b", "", r.name));
        if (r.definition) td.append(el("div", "sub", r.definition.slice(0, 140)));
        const d = el("details"); d.append(el("summary", "", "territory"));
        for (const x of r.files) d.append(el("div", "mono", x));
        if (r.n_files > r.files.length)
          d.append(el("div", "sub", "… " + (r.n_files - r.files.length) + " more"));
        td.append(d); tr.append(td);
        tr.append(el("td", "num", String(r.n_files)), el("td", "num", String(r.commits)),
                  el("td", "num", String(r.fan_in)),
                  el("td", "", r.born + "→" + r.last + (r.lifecycle === "removed" ? " ✕" : "")),
                  el("td", "", r.tier_from));
        const act = el("td"); const sel = el("select");
        sel.append(new Option("change tier…", ""));
        for (const o of ["foundation", "framework", "feature", "tooling", "content"])
          if (o !== r.tier) sel.append(new Option("→ " + o, o));
        sel.onchange = () => { if (!sel.value) return;
          addRule('entry "' + r.name + '"\n  tier   ' + sel.value);
          sel.disabled = true; };
        act.append(sel); tr.append(act);
        tb.append(tr);
      }
      box.append(tb);
    }
  };
  q.oninput = draw; draw();
}

// ---------- Rules ------------------------------------------------------------------
// The plan is the WHOLE statement of your curation, not a log of actions: every run
// replays it from nothing. So there is no undo stack to unwind — changing a rule means
// changing its text, and deleting one removes its effect completely.
function parsePlan(text) {
  const lines = text.split("\n");
  const blocks = [];
  let cur = null;
  lines.forEach((raw, i) => {
    const s = raw.split("#")[0].trim();
    if (!s) return;
    const verb = s.split(/\s+/)[0].toLowerCase();
    const rest = s.slice(verb.length).trim();
    if (verb === "entry") {
      cur = { kind: "entry", name: rest.replace(/^["']|["']$/g, ""), from: i, to: i,
              claims: [], rejects: [], tier: null };
      blocks.push(cur);
    } else if (verb === "merge" || verb === "reject" && /^["']/.test(rest)
               || verb === "keep-split") {
      blocks.push({ kind: verb, text: s, from: i, to: i });
      cur = null;
    } else if (cur) {
      cur.to = i;
      if (verb === "claim") cur.claims.push(rest);
      else if (verb === "reject") cur.rejects.push(rest);
      else if (verb === "tier") cur.tier = rest;
    }
  });
  return blocks;
}

function spliceLines(from, to) {
  const lines = planText.split("\n");
  lines.splice(from, to - from + 1);
  planText = lines.join("\n").replace(/\n{3,}/g, "\n\n");
  dirty++;
}

function renderRules() {
  const m = $("main"); m.textContent = "";
  m.append(el("h2", "", "Rules"));
  m.append(el("div", "sub", "This file IS your curation — it is replayed from scratch on "
    + "every run, so changing a rule's text is how you change what it did, and deleting a "
    + "rule undoes it completely. There is nothing else to unwind."));

  const blocks = parsePlan(planText);
  const dup = {};
  for (const b of blocks) if (b.kind === "entry") dup[b.name] = (dup[b.name] || 0) + 1;

  const list = el("div");
  for (const b of blocks) {
    const c = el("div", "card");
    if (b.kind === "entry") {
      c.append(el("h3", "", b.name));
      if (dup[b.name] > 1)
        c.append(el("div", "sub err", "⚠ “" + b.name + "” is declared " + dup[b.name]
          + " times — the blocks COMBINE into one entry. Delete one to separate them."));
      const f = el("div", "facts");
      f.textContent = (b.tier ? b.tier + " · " : "")
        + b.claims.length + " claim" + (b.claims.length === 1 ? "" : "s")
        + (b.rejects.length ? " · " + b.rejects.length + " reject" : "");
      c.append(f);
      for (const g of b.claims) c.append(el("div", "mono", "claim  " + g));
      for (const g of b.rejects) c.append(el("div", "mono", "reject " + g));
    } else {
      c.append(el("div", "mono", b.text));
    }
    const acts = el("div", "acts");
    const ren = el("button", "s", "Rename");
    const del = el("button", "s", "Delete");
    if (b.kind === "entry") acts.append(ren);
    acts.append(del);
    c.append(acts);
    ren.onclick = () => {
      const inp = el("input");
      inp.setAttribute("list", "entrynames");
      inp.value = b.name;
      const go = el("button", "p", "Rename");
      acts.textContent = ""; acts.append(inp, go);
      inp.focus(); inp.select();
      go.onclick = () => {
        const lines = planText.split("\n");
        lines[b.from] = lines[b.from].replace(/".*"|'.*'/, '"' + inp.value.trim() + '"');
        planText = lines.join("\n"); dirty++; render();
      };
    };
    del.onclick = () => { spliceLines(b.from, b.to); render(); };
    list.append(c);
  }
  if (!blocks.length) list.append(el("p", "sub", "No rules yet."));
  m.append(list);

  const d = el("details");
  d.append(el("summary", "", "edit as text"));
  const ta = el("textarea"); ta.value = planText;
  ta.oninput = () => { planText = ta.value; dirty++; setStatus(); };
  d.append(ta); m.append(d);
  const pv = el("div"); pv.id = "pv"; m.append(pv);
}

async function doPreview() {
  const r = await (await fetch("/api/preview", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plan: planText }) })).json();
  if (view !== "rules") { view = "rules"; setNav(); render(); }
  if (r.error) { setStatus(r.error, "err"); return; }
  if (r.trees) S.trees = r.trees;
  setStatus(r.report.moved + " files move · " + r.report.claimed + " newly claimed · "
    + r.report.merges + " merges", "ok");
  const pv = $("pv"); if (!pv) return;
  pv.textContent = "";
  if (r.warnings && r.warnings.length) {
    pv.append(el("h2", "", "Check these first"));
    for (const w of r.warnings) pv.append(el("div", "sub err", "⚠ " + w));
  }
  if (!r.moves.length) { pv.append(el("p", "sub", "No file would change owner.")); return; }
  pv.append(el("h2", "", "What would change"));
  const tb = el("table"); const hr = el("tr");
  ["entry", "now", "after", ""].forEach((h, i) =>
    hr.append(el("th", i ? "num" : "", h)));
  tb.append(hr);
  for (const mv of r.moves) {
    const tr = el("tr");
    tr.append(el("td", "", mv.name), el("td", "num", String(mv.before)),
              el("td", "num", String(mv.after)),
              el("td", "", mv.new ? "new entry" : mv.gone ? "removed"
                 : (mv.after > mv.before ? "+" : "") + (mv.after - mv.before)));
    tb.append(tr);
  }
  pv.append(tb);
}

async function doSave() {
  const r = await (await fetch("/api/save", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plan: planText }) })).json();
  if (r.error) { setStatus(r.error, "err"); return; }
  dirty = 0;
  setStatus("Saved to " + r.saved + " — now run:  gitchronicle update", "ok");
}

const VIEWS = { review: renderReview, catalogue: renderCatalogue, rules: renderRules };
function render() { $("how").innerHTML = HOW[view]; VIEWS[view](); setStatus(); }
function setNav() {
  $("bR").className = view === "review" ? "on" : "";
  $("bC").className = view === "catalogue" ? "on" : "";
  $("bL").className = view === "rules" ? "on" : "";
}
$("bR").onclick = () => { view = "review"; setNav(); render(); };
$("bC").onclick = () => { view = "catalogue"; setNav(); render(); };
$("bL").onclick = () => { view = "rules"; setNav(); render(); };
$("bPrev").onclick = doPreview;
$("bSave").onclick = doSave;
load();
</script></body></html>
"""
