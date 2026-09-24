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
import os
import re
import subprocess
import threading
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..scope import MD_FILE, Scope
from ..taxonomy.ledger import PLAN_FILE, Ledger, LedgerError

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
        "SELECT id, name, definition, summary, tier, tier_from, classification, lifecycle, "
        "n_commits, born_at, last_seen, created_by FROM domains ORDER BY name").fetchall()
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
            # inherited upkeep is not a thing anyone built; it gets its own tier rather
            # than posing as content
            "name": r["name"],
            "tier": (r["classification"] if r["classification"] in ("inherited", "upkeep")
                     else r["tier"] or "feature"),
            "tier_from": r["tier_from"] or "auto",
            # an entry only the rules create is renamed by editing them; one the pipeline
            # proposes is renamed by a merge, because its name comes back every rebuild
            "declared": r["created_by"] == "ledger",
            "definition": (r["definition"] or "")[:300],
            "summary": (r["summary"] or "")[:600],
            "classification": r["classification"], "lifecycle": r["lifecycle"],
            "commits": r["n_commits"] or 0, "fan_in": fan[r["id"]],
            "born": (r["born_at"] or "")[:10], "last": (r["last_seen"] or "")[:10],
            # the whole territory: decomposing an entry means seeing every file of it
            "files": files, "n_files": len(files),
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


def _scope_tree(conn, repo: str, scope, depth: int = 2) -> list[dict]:
    """Every subtree to `depth`, with what it costs the analysis and what the map says.

    The scope map decides what the product IS, and it is the one input nothing in the UI
    could reach: a vendored tree left in scope put 2,897 of 17,720 concerns on Boost
    headers and cost the catalogue a framework, because its vocabulary then looked like
    someone else's. So each row carries the evidence attached to it, not only its size.
    """
    from ..extract.git_ingest import run_git

    files = [f for f in run_git(repo, ["ls-files"]).splitlines() if f.strip()]
    concerns: Counter = Counter()
    for (fs,) in conn.execute("SELECT files FROM concerns"):
        for f in json.loads(fs or "[]"):
            parts = f.split("/")
            for d in range(1, min(len(parts), depth + 1)):
                concerns["/".join(parts[:d])] += 1
    owned: Counter = Counter()
    for (p,) in conn.execute("SELECT path FROM domain_files"):
        parts = p.split("/")
        for d in range(1, min(len(parts), depth + 1)):
            owned["/".join(parts[:d])] += 1

    by_dir: Counter = Counter()
    for f in files:
        parts = f.split("/")
        for d in range(1, min(len(parts), depth + 1)):
            by_dir["/".join(parts[:d])] += 1

    rows = []
    for d, n in by_dir.items():
        glob = d + "/**"
        rows.append({"dir": d, "glob": glob, "files": n, "depth": d.count("/"),
                     "concerns": concerns.get(d, 0), "owned": owned.get(d, 0),
                     "verdict": scope.verdict(glob),
                     "siblings": []})
    kids = defaultdict(list)
    for r in rows:
        kids[r["dir"].rsplit("/", 1)[0] if "/" in r["dir"] else ""].append(r["glob"])
    for r in rows:
        parent = r["dir"].rsplit("/", 1)[0] if "/" in r["dir"] else ""
        r["siblings"] = [g for g in kids[parent] if g != r["glob"]]
    rows.sort(key=lambda r: (r["dir"].split("/")[0], r["depth"], -r["files"]))
    return rows


_WORD = re.compile(r"[a-z][a-z0-9]{3,}")


def _stem(w: str) -> str:
    """Singular/plural only — enough to match a word against an entry's name."""
    for suffix in ("ies", "es", "s"):
        if len(w) > 4 and w.endswith(suffix):
            return w[:-len(suffix)] + ("y" if suffix == "ies" else "")
    return w

_VOCAB_STOP = {
    "added", "fixed", "update", "updated", "system", "refactor", "removed", "changed",
    "support", "handling", "logic", "management", "implementation", "improved", "with",
    "from", "into", "when", "file", "files", "data", "code", "make", "used", "using",
    "new", "also", "more", "this", "that", "them", "they", "have", "been", "were",
    "configuration", "settings", "feature", "features", "function", "functions", "class",
    "value", "values", "type", "types", "name", "names", "list", "item", "items",
}


def _orphan_vocab(conn, ledger=None, scope=None, limit: int = 12) -> list[dict]:
    """Words that run through unattributed work and answer to no entry.

    The assembly says `2,494 residue micro-clusters left unattributed` and moves on, so a
    whole framework can be missing and nothing points at it: 121 concerns mentioned traits
    here, 106 of them filed nowhere, and no entry was named anything like it. This is the
    other half of the review queue — fragmented folders are things wrongly split, these
    are things never seen at all.
    """
    names = {r["name"].lower() for r in conn.execute("SELECT name FROM domains")}
    # "Traits System" answers for "trait": nagging about a word an entry already carries,
    # because one of them is plural, is the fastest way to make a queue worth ignoring
    named = {_stem(w) for n in names for w in _WORD.findall(n)}
    ignored = {_stem(w) for w in (getattr(ledger, "ignored", []) or [])}
    hits: dict[str, dict] = {}
    # import families carry the untangler's own words ("bulk change remainder"), not the
    # project's, so they would flood this list with its own vocabulary
    for r in conn.execute("SELECT label, files FROM concerns WHERE domain_id IS NULL "
                          "AND origin IS NULL"):
        label = (r["label"] or "").lower()
        fs = json.loads(r["files"] or "[]")
        for w in set(_WORD.findall(label)):
            if _stem(w) in named or w in _VOCAB_STOP or _stem(w) in ignored:
                continue
            h = hits.setdefault(w, {"word": w, "concerns": 0, "labels": [], "dirs": Counter()})
            h["concerns"] += 1
            if len(h["labels"]) < 6:
                h["labels"].append((r["label"] or "")[:80])
            for f in fs:
                h["dirs"]["/".join(f.split("/")[:-1])] += 1
    # An exclude an include overrides leaves a whole vendored tree in the analysis, and
    # its vocabulary then floods this list: "boost" with 339 concerns is not a missing
    # framework, it is a scope rule that does nothing. Say which, and point at the fix.
    shadow = scope.shadowed() if scope is not None else []
    out = []
    for h in sorted(hits.values(), key=lambda x: -x["concerns"])[:limit * 3]:
        dirs = [d for d, _ in h["dirs"].most_common(4) if d]
        inert = ""
        for exc, inc in shadow:
            probe = exc.rstrip("*").rstrip("/")
            if dirs and sum(d.startswith(probe) for d in dirs) * 2 > len(dirs):
                inert = exc
                break
        # the paths worth claiming are the ones that carry the word themselves
        globs = [d + "/**" for d in dirs if h["word"] in d.lower()][:3]
        out.append({"word": h["word"], "concerns": h["concerns"], "labels": h["labels"],
                    "dirs": [{"dir": d, "n": h["dirs"][d]} for d in dirs],
                    "globs": globs, "inert_rule": inert})
    out.sort(key=lambda r: (bool(r["inert_rule"]), -r["concerns"]))
    return out[:limit]


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
    dups = sorted(n for n, k in Counter(re.findall(r'^entry\s+"([^"]*)"', plan_text, re.M)).items()
                  if k > 1)
    if dups:
        warn.append(f"{len(dups)} entries are declared in several blocks, which combine into one "
                    f"({', '.join(dups[:4])}{'…' if len(dups) > 4 else ''}). Rules › Tidy folds them "
                    f"together; delete a block instead if you meant separate entries.")

    for e in led.entries:
        if e.claims and not any(any(fnmatch.fnmatch(f, g) for g in e.claims)
                                for fs in catalogue.values() for f in fs):
            warn.append(f"“{e.name}” claims nothing that exists — check the glob.")
    return warn[:12]



def _edges(conn) -> list[dict]:
    """uses-edges by NAME, so the graph survives a rebuild that renumbers domains."""
    return [{"src": r["s"], "dst": r["d"], "w": r["weight"] or 0}
            for r in conn.execute(
                "SELECT s.name s, d.name d, e.weight FROM domain_edges e "
                "JOIN domains s ON s.id = e.src_domain "
                "JOIN domains d ON d.id = e.dst_domain")]


def _story(conn, name: str) -> dict:
    """One entry's narrated evolution, oldest first, with the commits behind each chapter."""
    row = conn.execute("SELECT id, name, summary, definition, tier, born_at, last_seen, "
                       "n_commits, lifecycle FROM domains WHERE name = ?", (name,)).fetchone()
    if not row:
        return {"error": f"no entry named {name!r}"}
    chapters = []
    for c in conn.execute(
            "SELECT title, narrative, period_start, period_end, commit_hashes "
            "FROM evolution_chapters WHERE target_type='domain' AND target_id=? "
            "ORDER BY period_start", (str(row["id"]),)):
        commits = []
        for h in json.loads(c["commit_hashes"] or "[]")[:40]:
            r = conn.execute("SELECT hash, subject, authored_at, author_name FROM commits "
                             "WHERE hash >= ? AND hash < ? LIMIT 1", (h, h + "g")).fetchone()
            if r:
                commits.append({"hash": r["hash"][:10], "subject": r["subject"] or "",
                                "date": (r["authored_at"] or "")[:10],
                                "author": r["author_name"] or ""})
        chapters.append({"title": c["title"] or "", "narrative": c["narrative"] or "",
                         "start": (c["period_start"] or "")[:10],
                         "end": (c["period_end"] or "")[:10],
                         "n": len(json.loads(c["commit_hashes"] or "[]")),
                         "commits": commits})
    authors = [{"name": r[0], "n": r[1]} for r in conn.execute(
        "SELECT c.author_name, COUNT(*) FROM commit_domains cd JOIN commits c "
        "ON c.hash = cd.commit_hash WHERE cd.domain_id=? AND c.is_merge=0 "
        "GROUP BY c.author_name ORDER BY 2 DESC LIMIT 5", (row["id"],)) if r[0]]
    return {"name": row["name"], "tier": row["tier"], "summary": row["summary"] or "",
            "definition": row["definition"] or "", "born": (row["born_at"] or "")[:10],
            "last": (row["last_seen"] or "")[:10], "commits": row["n_commits"] or 0,
            "lifecycle": row["lifecycle"], "authors": authors, "chapters": chapters}


def _timeline(conn) -> dict:
    """Monthly commit counts per entry and for the whole project, plus chapter spans.

    Months are indexed from the project's first commit so the payload stays small: 194
    entries of sparse [month, count] pairs rather than a date string per cell.
    """
    first = conn.execute("SELECT MIN(authored_at), MAX(authored_at) FROM commits "
                         "WHERE is_merge=0 AND authored_at IS NOT NULL").fetchone()
    if not first or not first[0]:
        return {"start": None, "months": 0, "pulse": [], "entries": {}}
    y0, m0 = int(first[0][:4]), int(first[0][5:7])
    y1, m1 = int(first[1][:4]), int(first[1][5:7])

    def idx(d: str) -> int:
        return (int(d[:4]) - y0) * 12 + int(d[5:7]) - m0

    pulse = [0] * ((y1 - y0) * 12 + m1 - m0 + 1)
    for r in conn.execute("SELECT substr(authored_at,1,7) m, COUNT(*) FROM commits "
                          "WHERE is_merge=0 AND authored_at IS NOT NULL GROUP BY m"):
        pulse[idx(r[0])] = r[1]
    ents: dict[str, dict] = defaultdict(lambda: {"act": [], "arcs": []})
    for r in conn.execute(
            "SELECT d.name, substr(c.authored_at,1,7) m, COUNT(*) n FROM commit_domains cd "
            "JOIN commits c ON c.hash = cd.commit_hash JOIN domains d ON d.id = cd.domain_id "
            "WHERE c.is_merge=0 AND c.authored_at IS NOT NULL GROUP BY d.id, m"):
        ents[r[0]]["act"].append([idx(r[1]), r[2]])
    for r in conn.execute(
            "SELECT d.name, e.title, e.period_start, e.period_end FROM evolution_chapters e "
            "JOIN domains d ON d.id = CAST(e.target_id AS INTEGER) "
            "WHERE e.target_type='domain' ORDER BY e.period_start"):
        if r[2] and r[3]:
            ents[r[0]]["arcs"].append([idx(r[2][:7]), idx(r[3][:7]), r[1] or ""])
    return {"start": f"{y0:04d}-{m0:02d}", "months": len(pulse), "pulse": pulse,
            "entries": ents}


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
        "edges": _edges(conn),
        "timeline": _timeline(conn),
        "commits": conn.execute("SELECT COUNT(*) FROM commits WHERE is_merge=0").fetchone()[0],
        "chapters": conn.execute(
            "SELECT COUNT(*) FROM evolution_chapters").fetchone()[0],
        "trees": _fragmentation(conn, led),
        "vocab": _orphan_vocab(conn, led, Scope.load()),
        "plan": plan_text,
        "plan_path": str(p),
    }


class _Rebuild:
    """`gitchronicle update` run from the studio, one at a time, with its log kept.

    Curation is a loop — write a rule, rebuild, look — and a loop that makes you switch
    to a terminal for the middle step gets run less. The rebuild is the ordinary CLI in a
    subprocess, so there is one code path and a crash cannot take the studio down.
    """

    def __init__(self, argv: list[str]):
        self.argv = argv
        self.proc = None
        self.lines: list[str] = []
        self.code: int | None = None
        self._lock = threading.Lock()

    def start(self) -> bool:
        with self._lock:
            if self.proc is not None and self.proc.poll() is None:
                return False
            self.lines, self.code = [], None
            self.proc = subprocess.Popen(
                self.argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                env={**os.environ, "COLUMNS": "200", "NO_COLOR": "1"})
        threading.Thread(target=self._pump, daemon=True).start()
        return True

    def _pump(self):
        for line in self.proc.stdout:
            self.lines.append(line.rstrip())
        self.code = self.proc.wait()

    def status(self) -> dict:
        running = self.proc is not None and self.proc.poll() is None
        stage = next((ln.strip("▸ ").strip() for ln in reversed(self.lines)
                      if ln.startswith("▸")), "")
        return {"running": running, "code": self.code, "stage": stage,
                "tail": self.lines[-14:]}


def serve_studio(db_path: str | Path, plan_path: str | Path = PLAN_FILE, port: int = 8765,
                 log=print, provider_factory=None, rebuild_argv: list[str] | None = None,
                 title: str = "", repo: str = "") -> None:
    """Open the knowledge base per request rather than holding it.

    `update` swaps the database atomically, so a long-lived connection keeps reading the
    replaced file and the studio would show yesterday's catalogue until restarted — in a
    loop that goes curate, update, look again, that is the one thing it must not do.
    Opening is microseconds against a local file.
    """
    from urllib.parse import parse_qs, urlparse

    from ..storage import connect as db_connect
    from .search import Index, ask

    plan = Path(plan_path)
    db = str(db_path)
    index = Index(db)
    rebuild = _Rebuild(rebuild_argv) if rebuild_argv else None
    provider_box: dict = {}
    ask_lock = threading.Lock()

    def fresh():
        return db_connect(db)

    def provider():
        if "p" not in provider_box:
            provider_box["p"] = provider_factory() if provider_factory else None
        return provider_box["p"]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):        # the server's own chatter is not the user's news
            pass

        def _send(self, body: bytes, ctype: str, code: int = 200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200):
            self._send(json.dumps(obj).encode(), "application/json; charset=utf-8", code)

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/":
                # the state rides along with the page, so the first paint needs no round trip
                c = fresh()
                try:
                    st = build_state(c, plan)
                finally:
                    c.close()
                st.update(project=title, can_ask=provider_factory is not None,
                          can_rebuild=rebuild is not None)
                boot = ("<script>window.__STATE__=" + json.dumps(st).replace("</", "<\\/")
                        + "</script>").encode()
                self._send(page().replace(b"<!--BOOT-->", boot), "text/html; charset=utf-8")
            elif u.path == "/api/story":
                c = fresh()
                try:
                    self._json(_story(c, (q.get("entry") or [""])[0]))
                finally:
                    c.close()
            elif u.path == "/api/state":
                c = fresh()
                try:
                    st = build_state(c, plan)
                finally:
                    c.close()
                st["project"] = title
                st["can_ask"] = provider_factory is not None
                st["can_rebuild"] = rebuild is not None
                self._json(st)
            elif u.path == "/api/scope":
                c = fresh()
                try:
                    sc = Scope.load()
                    self._json({"rows": _scope_tree(c, repo, sc) if repo else [],
                                "shadowed": sc.shadowed(), "path": MD_FILE,
                                "counts": {"include": len(sc.includes), "exclude": len(sc.excludes),
                                           "acknowledge": len(sc.acknowledges)}})
                finally:
                    c.close()
            elif u.path == "/api/inspect":
                from .inspect import file_card, find, split_hint
                c = fresh()
                try:
                    sc = Scope.load()
                    qq = (q.get("q") or [""])[0]
                    if (q.get("file") or [""])[0]:
                        self._json(file_card(c, q["file"][0], repo))
                    else:
                        out = find(c, qq, scope=sc)
                        out["hint"] = (split_hint(c, repo, qq, scope=sc)
                                       if repo and 1 < len(out["files"]) <= 80 else None)
                        self._json(out)
                finally:
                    c.close()
            elif u.path == "/api/search":
                self._json(index.search((q.get("q") or [""])[0]))
            elif u.path == "/api/rebuild":
                self._json(rebuild.status() if rebuild else {"running": False})
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
            elif self.path == "/api/scope/save":
                sc = Scope.load()
                for ch in body.get("changes") or []:
                    sc.set(ch["glob"], ch["verdict"], ch.get("siblings") or [])
                sc.save()
                log(f"  saved {MD_FILE} ({len(body.get('changes') or [])} scope changes)")
                self._json({"saved": MD_FILE, "shadowed": sc.shadowed()})
            elif self.path == "/api/ask":
                question = (body.get("q") or "").strip()
                try:
                    prov = provider()
                    if prov is None:
                        raise RuntimeError("no chat provider configured")
                    with ask_lock:                     # one paid call at a time
                        self._json(ask(index, db, prov, question))
                except Exception as exc:  # noqa: BLE001 - the page shows it, the server lives
                    self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)
            elif self.path == "/api/rebuild":
                if rebuild is None:
                    self._json({"error": "rebuild not available"}, 400)
                else:
                    self._json({"started": rebuild.start(), **rebuild.status()})
            else:
                self._send(b"not found", "text/plain", 404)

    try:
        # threaded: an LLM answer takes seconds, and the page must keep responding meanwhile
        srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        # almost always an earlier studio still running — and one serving older code,
        # which is worse than no studio at all, so say what to do about it
        raise SystemExit(
            f"  port {port} is already in use — another studio is probably still running.\n"
            f"  Stop it with Ctrl-C in its terminal, or start this one elsewhere:\n"
            f"      gitchronicle studio --port {port + 1}\n"
            f"  ({exc})") from exc
    log(f"  studio on http://127.0.0.1:{port}   (editing {plan}; Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("  stopped")
    finally:
        srv.server_close()


_PAGE_FILE = Path(__file__).with_name("studio.html")


def page() -> bytes:
    """The page, read from disk on every request.

    It used to be a module constant, which meant an edit did nothing until the process
    restarted — and a studio serving last week's JavaScript looks exactly like a studio
    with a bug in it. Reading a 30KB file per request costs nothing and makes what is on
    disk the thing being served.
    """
    return _PAGE_FILE.read_bytes()
