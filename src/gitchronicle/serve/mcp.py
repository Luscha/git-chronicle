"""MCP server — the knowledge base as tools a coding agent can call.

The reader of a codebase knowledge base is increasingly an agent, not a person at a web page:
an assistant about to change the skill system wants to know when it was built, what it was
built on and what was tried before, and it asks in the middle of a task. Everything that
answers that is already in the KB; this exposes it over the Model Context Protocol (stdio,
JSON-RPC 2.0, one message per line) with no dependency beyond the standard library.

The tools return evidence, not conclusions: the agent is itself a language model and does
its own reasoning, so a second model summarising in between would only cost money and
lose detail. Read-only by design — curation stays with the owner, in the studio.
"""

from __future__ import annotations

import json
import sqlite3
import sys

from .search import Index

_PROTOCOL = "2025-06-18"

TOOLS = [
    {"name": "search",
     "description": "Search the project's history knowledge base: catalogue entries (features, "
                    "frameworks, tools), dated chapters narrating how each evolved, and commits. "
                    "Start here with any topic, feature name, identifier or year.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "Words, a feature name, or a year like 2021"}},
         "required": ["query"]}},
    {"name": "entry",
     "description": "Everything known about one catalogue entry: what it is, its tier, when it "
                    "was born and last touched, who worked on it, what it uses and what uses it, "
                    "its main folders, and its full dated story chapter by chapter.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "Exact entry name, as returned by search"}},
         "required": ["name"]}},
    {"name": "path_history",
     "description": "Which entry a file or folder belongs to, and the dated commits that "
                    "touched it. Use before changing code, to learn why it is the way it is.",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Repo-relative file or folder path"},
         "limit": {"type": "integer", "description": "Max commits (default 25)"}},
         "required": ["path"]}},
    {"name": "period",
     "description": "What happened in the project during a year or date range: most active "
                    "entries with their chapter titles, entries first seen, entries removed.",
     "inputSchema": {"type": "object", "properties": {
         "start": {"type": "string", "description": "YYYY or YYYY-MM-DD"},
         "end": {"type": "string", "description": "YYYY or YYYY-MM-DD (default: end of start)"}},
         "required": ["start"]}},
]


class KB:
    def __init__(self, path: str):
        self.path = path
        self.index = Index(path)

    def db(self) -> sqlite3.Connection:
        c = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        return c

    # -- tools -------------------------------------------------------------
    def search(self, query: str) -> str:
        r = self.index.search(query, k=8)
        out = []
        if r["entries"]:
            out.append("ENTRIES")
            with self.db() as c:
                for e in r["entries"]:
                    d = c.execute("SELECT tier, definition, born_at, last_seen FROM domains "
                                  "WHERE name=?", (e["name"],)).fetchone()
                    out.append(f"- {e['name']} [{d['tier']}, {(d['born_at'] or '')[:7]}.."
                               f"{(d['last_seen'] or '')[:7]}]: {(d['definition'] or '')[:160]}")
        if r["chapters"]:
            out.append("\nCHAPTERS")
            out += [f"- {c['start']}..{c['end']} {c['entry']} — {c['title']}: "
                    f"{_plain(c['snip'])[:220]}" for c in r["chapters"]]
        if r["commits"]:
            out.append("\nCOMMITS")
            out += [f"- {c['hash'][:10]} {c['date']} {c['author']}: {c['subject'][:140]}"
                    for c in r["commits"]]
        return "\n".join(out) or "No matches. Try other words, an identifier, or a year."

    def entry(self, name: str) -> str:
        with self.db() as c:
            d = c.execute("SELECT * FROM domains WHERE name=?", (name,)).fetchone()
            if not d:
                hits = [e["name"] for e in self.index.search(name, k=5)["entries"]]
                return f"No entry named {name!r}." + (f" Did you mean: {', '.join(hits)}?"
                                                      if hits else "")
            did = d["id"]
            uses = [r[0] for r in c.execute("SELECT t.name FROM domain_edges e JOIN domains t "
                                            "ON t.id=e.dst_domain WHERE e.src_domain=?", (did,))]
            used = [r[0] for r in c.execute("SELECT s.name FROM domain_edges e JOIN domains s "
                                            "ON s.id=e.src_domain WHERE e.dst_domain=?", (did,))]
            authors = [f"{r[0]} ({r[1]})" for r in c.execute(
                "SELECT c.author_name, COUNT(*) FROM commit_domains cd JOIN commits c "
                "ON c.hash=cd.commit_hash WHERE cd.domain_id=? AND c.is_merge=0 "
                "GROUP BY 1 ORDER BY 2 DESC LIMIT 4", (did,))]
            files = [r[0] for r in c.execute("SELECT path FROM domain_files WHERE domain_id=?",
                                             (did,))]
            chapters = c.execute(
                "SELECT period_start, period_end, title, narrative, commit_hashes "
                "FROM evolution_chapters WHERE target_type='domain' AND target_id=? "
                "ORDER BY period_start", (str(did),)).fetchall()
        dirs: dict[str, int] = {}
        for f in files:
            k = "/".join(f.split("/")[:-1][:4]) or "."
            dirs[k] = dirs.get(k, 0) + 1
        top = sorted(dirs.items(), key=lambda x: -x[1])[:8]
        lines = [
            f"# {d['name']}",
            f"tier: {d['tier']}   lifecycle: {d['lifecycle']}   "
            f"{(d['born_at'] or '')[:10]} .. {(d['last_seen'] or '')[:10]}   "
            f"{d['n_commits']} commits, {len(files)} files",
            f"what: {d['summary'] or d['definition'] or '(none)'}",
            f"authors: {', '.join(authors) or 'unknown'}",
            f"uses: {', '.join(uses) or 'nothing catalogued'}",
            f"used by: {', '.join(used) or 'nothing catalogued'}",
            "main folders: " + ", ".join(f"{k}/ ({n})" for k, n in top),
            "", "## Story"]
        for ch in chapters:
            n = len(json.loads(ch["commit_hashes"] or "[]"))
            lines.append(f"### {(ch['period_start'] or '')[:10]} .. {(ch['period_end'] or '')[:10]}"
                         f" — {ch['title']} ({n} commit{'s' if n != 1 else ''})")
            lines.append(ch["narrative"] or "")
        if not chapters:
            lines.append("(not narrated yet)")
        return "\n".join(lines)

    def path_history(self, path: str, limit: int = 25) -> str:
        p = path.strip().strip("/")
        with self.db() as c:
            owners = c.execute(
                "SELECT d.name, COUNT(*) n FROM domain_files f JOIN domains d ON d.id=f.domain_id "
                "WHERE f.path = ? OR f.path LIKE ? GROUP BY d.id ORDER BY n DESC LIMIT 8",
                (p, p + "/%")).fetchall()
            commits = c.execute(
                "SELECT DISTINCT c.hash, c.authored_at, c.author_name, c.subject FROM commit_files cf "
                "JOIN commits c ON c.hash=cf.commit_hash WHERE (cf.path = ? OR cf.path LIKE ?) "
                "AND c.is_merge=0 ORDER BY c.authored_at DESC LIMIT ?",
                (p, p + "/%", int(limit or 25))).fetchall()
        out = []
        if owners:
            out.append("OWNED BY: " + ", ".join(f"{r['name']} ({r['n']} file{'s' if r['n'] != 1 else ''})"
                                                for r in owners))
        else:
            out.append("OWNED BY: no catalogue entry (inherited, generic, or out of scope)")
        if commits:
            out.append(f"\nCOMMITS (newest first, {len(commits)} shown):")
            out += [f"- {r['hash'][:10]} {(r['authored_at'] or '')[:10]} {r['author_name']}: "
                    f"{(r['subject'] or '')[:140]}" for r in commits]
        else:
            out.append("\nNo commits touch this path — check the spelling (repo-relative).")
        return "\n".join(out)

    def period(self, start: str, end: str | None = None) -> str:
        a = start if len(start) > 4 else start + "-01-01"
        b = end or start
        b = b if len(b) > 4 else b + "-12-31"
        with self.db() as c:
            n = c.execute("SELECT COUNT(*) FROM commits WHERE is_merge=0 AND authored_at >= ? "
                          "AND authored_at <= ?", (a, b + "T99")).fetchone()[0]
            active = c.execute(
                "SELECT d.name, d.tier, COUNT(DISTINCT c.hash) n FROM commit_domains cd "
                "JOIN commits c ON c.hash=cd.commit_hash JOIN domains d ON d.id=cd.domain_id "
                "WHERE c.authored_at >= ? AND c.authored_at <= ? AND c.is_merge=0 "
                "AND d.classification != 'inherited' GROUP BY d.id ORDER BY n DESC LIMIT 12",
                (a, b + "T99")).fetchall()
            born = c.execute("SELECT name, definition FROM domains WHERE born_at >= ? AND "
                             "born_at <= ? ORDER BY n_commits DESC LIMIT 12", (a, b)).fetchall()
            gone = c.execute("SELECT name FROM domains WHERE lifecycle='removed' AND "
                             "last_seen >= ? AND last_seen <= ?", (a, b)).fetchall()
            chaps = {}
            for r in c.execute(
                    "SELECT d.name, e.title FROM evolution_chapters e JOIN domains d "
                    "ON d.id = CAST(e.target_id AS INTEGER) WHERE e.target_type='domain' "
                    "AND e.period_start <= ? AND e.period_end >= ?", (b + "T99", a)):
                chaps.setdefault(r[0], []).append(r[1])
        out = [f"{a} .. {b}: {n} commits"]
        if active:
            out.append("\nMOST ACTIVE")
            out += [f"- {r['name']} [{r['tier']}] {r['n']} commits"
                    + (f" — {'; '.join(t for t in chaps.get(r['name'], []) if t)[:200]}"
                       if chaps.get(r["name"]) else "") for r in active]
        if born:
            out.append("\nFIRST SEEN")
            out += [f"- {r['name']}: {(r['definition'] or '')[:120]}" for r in born]
        if gone:
            out.append("\nREMOVED")
            out += [f"- {r['name']}" for r in gone]
        return "\n".join(out)


def _plain(html: str) -> str:
    return (html or "").replace("<mark>", "").replace("</mark>", "")


def serve_mcp(kb_path: str, stdin=None, stdout=None) -> None:
    kb = KB(kb_path)
    rd, wr = stdin or sys.stdin, stdout or sys.stdout

    def reply(mid, result=None, error=None):
        msg = {"jsonrpc": "2.0", "id": mid}
        msg.update({"error": error} if error else {"result": result})
        wr.write(json.dumps(msg) + "\n")
        wr.flush()

    for line in rd:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            reply(None, error={"code": -32700, "message": "parse error"})
            continue
        mid, method, params = req.get("id"), req.get("method"), req.get("params") or {}
        if mid is None:
            continue                                   # a notification wants no answer
        if method == "initialize":
            reply(mid, {"protocolVersion": params.get("protocolVersion") or _PROTOCOL,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "gitchronicle", "version": "0.5"},
                        "instructions": "Knowledge base of this project's history: what was "
                                        "built, when, and how it evolved. Use `search` first, "
                                        "then `entry` for a full story, `path_history` before "
                                        "changing a file, `period` for a year."})
        elif method == "ping":
            reply(mid, {})
        elif method == "tools/list":
            reply(mid, {"tools": TOOLS})
        elif method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            fn = getattr(kb, name, None) if name in {t["name"] for t in TOOLS} else None
            if fn is None:
                reply(mid, error={"code": -32602, "message": f"unknown tool {name!r}"})
                continue
            try:
                text = fn(**args)
                reply(mid, {"content": [{"type": "text", "text": text}], "isError": False})
            except Exception as exc:  # noqa: BLE001 - the agent should see what went wrong
                reply(mid, {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                            "isError": True})
        else:
            reply(mid, error={"code": -32601, "message": f"method not found: {method}"})
