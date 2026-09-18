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



def _edges(conn) -> list[dict]:
    """uses-edges by NAME, so the graph survives a rebuild that renumbers domains."""
    return [{"src": r["s"], "dst": r["d"], "w": r["weight"] or 0}
            for r in conn.execute(
                "SELECT s.name s, d.name d, e.weight FROM domain_edges e "
                "JOIN domains s ON s.id = e.src_domain "
                "JOIN domains d ON d.id = e.dst_domain")]


def _story(conn, name: str) -> dict:
    """One entry's narrated evolution, newest arc last."""
    row = conn.execute("SELECT id, name, summary, definition, tier, born_at, last_seen, "
                       "n_commits FROM domains WHERE name = ?", (name,)).fetchone()
    if not row:
        return {"error": f"no entry named {name!r}"}
    chapters = [{
        "title": c["title"] or "", "narrative": c["narrative"] or "",
        "start": (c["period_start"] or "")[:10], "end": (c["period_end"] or "")[:10],
        "commits": json.loads(c["commit_hashes"] or "[]"),
    } for c in conn.execute(
        "SELECT title, narrative, period_start, period_end, commit_hashes "
        "FROM evolution_chapters WHERE target_type='domain' AND target_id=? "
        "ORDER BY period_start", (row["id"],))]
    return {"name": row["name"], "tier": row["tier"], "summary": row["summary"] or "",
            "definition": row["definition"] or "", "born": (row["born_at"] or "")[:10],
            "last": (row["last_seen"] or "")[:10], "commits": row["n_commits"] or 0,
            "chapters": chapters}


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
        "chapters": conn.execute(
            "SELECT COUNT(*) FROM evolution_chapters").fetchone()[0],
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
                self._send(page(), "text/html; charset=utf-8")
            elif self.path.startswith("/api/story"):
                from urllib.parse import parse_qs, urlparse
                q = parse_qs(urlparse(self.path).query)
                c = fresh()
                try:
                    self._json(_story(c, (q.get("entry") or [""])[0]))
                finally:
                    c.close()
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


_PAGE_FILE = Path(__file__).with_name("studio.html")


def page() -> bytes:
    """The page, read from disk on every request.

    It used to be a module constant, which meant an edit did nothing until the process
    restarted — and a studio serving last week's JavaScript looks exactly like a studio
    with a bug in it. Reading a 30KB file per request costs nothing and makes what is on
    disk the thing being served.
    """
    return _PAGE_FILE.read_bytes()
