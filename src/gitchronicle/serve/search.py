"""SEARCH and ASK — the knowledge base answering questions, not only being browsed.

Retrieval is SQLite FTS5 over three kinds of evidence, each with its own grain: entries
(what exists), chapters (how it came to be, with dates) and commits (the raw record).
Embeddings would add recall on paraphrase, but they need a running embedding server and
an index rebuilt on every update; the names and narratives here are written in the
project's own vocabulary, which is exactly what keyword retrieval is good at.

The index lives in memory and is rebuilt when the knowledge base file changes, because
`update` replaces that file wholesale.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading

_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_VERSION = re.compile(r"v?\d+(\.\d+)+")
_STOP = {"the", "and", "for", "what", "which", "when", "where", "who", "how", "why", "does",
         "did", "was", "were", "are", "is", "has", "have", "had", "that", "this", "with",
         "from", "into", "about", "there", "their", "them", "they", "our", "your", "can",
         "could", "would", "should", "tell", "show", "list", "give", "all", "any", "some",
         "happened", "use", "used", "uses", "work", "works", "project", "system"}


def _terms(q: str) -> list[str]:
    toks = re.findall(r"[A-Za-z0-9]+", q.lower())
    out = [t for t in toks if len(t) > 2 and t not in _STOP and not _YEAR.fullmatch(t)]
    # "battle pass" is spelled battlepass in the code: offer the joined form as well
    out += [a + b for a, b in zip(out, out[1:])]
    return list(dict.fromkeys(out))


def _match(terms: list[str]) -> str | None:
    # prefix match, so "deploy" finds Deployer and deployment alike
    return " OR ".join(f'"{t}"*' for t in terms) if terms else None


class Index:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._stamp = None
        self._mem: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def conn(self) -> sqlite3.Connection:
        stamp = os.stat(self.db_path).st_mtime_ns
        with self._lock:
            if self._mem is None or stamp != self._stamp:
                self._mem = self._build()
                self._stamp = stamp
            return self._mem

    def _build(self) -> sqlite3.Connection:
        src = sqlite3.connect(self.db_path)
        src.row_factory = sqlite3.Row
        m = sqlite3.connect(":memory:", check_same_thread=False)
        m.row_factory = sqlite3.Row
        m.executescript(
            "CREATE VIRTUAL TABLE ents USING fts5(name, body, paths, tokenize='unicode61');"
            "CREATE VIRTUAL TABLE chaps USING fts5(entry, title, narrative, start UNINDEXED,"
            " end UNINDEXED, tokenize='unicode61');"
            "CREATE VIRTUAL TABLE coms USING fts5(hash UNINDEXED, subject, body,"
            " date UNINDEXED, author UNINDEXED, tokenize='unicode61');")
        files: dict[int, list[str]] = {}
        for r in src.execute("SELECT domain_id, path FROM domain_files"):
            files.setdefault(r["domain_id"], []).append(r["path"])
        names = {}
        for r in src.execute("SELECT id, name, definition, summary, tier FROM domains"):
            names[r["id"]] = r["name"]
            # path separators become spaces so a directory name is a searchable word
            paths = " ".join(re.sub(r"[/_.\-]", " ", p) for p in files.get(r["id"], [])[:400])
            m.execute("INSERT INTO ents (name, body, paths) VALUES (?,?,?)",
                      (r["name"], " ".join(filter(None, [r["definition"], r["summary"],
                                                          r["tier"]])), paths))
        for r in src.execute("SELECT target_id, title, narrative, period_start, period_end "
                             "FROM evolution_chapters WHERE target_type='domain'"):
            name = names.get(int(r["target_id"])) if r["target_id"] else None
            if name:
                m.execute("INSERT INTO chaps VALUES (?,?,?,?,?)",
                          (name, r["title"] or "", r["narrative"] or "",
                           (r["period_start"] or "")[:10], (r["period_end"] or "")[:10]))
        m.executemany("INSERT INTO coms VALUES (?,?,?,?,?)",
                      [(r["hash"], r["subject"] or "", r["body"] or "",
                        (r["authored_at"] or "")[:10], r["author_name"] or "")
                       for r in src.execute("SELECT hash, subject, body, authored_at, "
                                            "author_name FROM commits WHERE is_merge=0")])
        m.commit()
        src.close()
        return m

    def search(self, q: str, k: int = 8) -> dict:
        terms = _terms(q)
        years = sorted({y.group(0) for y in _YEAR.finditer(q)})
        m = self.conn()
        out = {"terms": terms, "years": years, "entries": [], "chapters": [], "commits": []}
        expr = _match(terms)
        if expr:
            # name matches outrank a mention buried in 400 paths, and a name that spells
            # the whole query outranks one that shares a word with it: OR-ranking alone put
            # "Exp Items" above "Battlepass System" for "battle pass"
            cand = [dict(r) for r in m.execute(
                "SELECT name, snippet(ents, 1, '<mark>', '</mark>', '…', 18) AS snip "
                "FROM ents WHERE ents MATCH ? ORDER BY bm25(ents, 12.0, 3.0, 0.5) LIMIT 40",
                (expr,))]
            flat = "".join(re.findall(r"[a-z0-9]+", q.lower()))
            words = [t for t in terms if " " not in t]

            def boost(e):
                n = "".join(re.findall(r"[a-z0-9]+", e["name"].lower()))
                return (flat in n or n in flat) * 10 + sum(t in n for t in words)
            out["entries"] = sorted(cand, key=lambda e: -boost(e))[:k]
            out["chapters"] = [dict(r) for r in m.execute(
                "SELECT entry, title, start, end, "
                "snippet(chaps, 2, '<mark>', '</mark>', '…', 30) AS snip "
                "FROM chaps WHERE chaps MATCH ? "
                + ("AND (" + " OR ".join("(start <= ? AND end >= ?)" for _ in years) + ") "
                   if years else "")
                + "ORDER BY bm25(chaps, 6.0, 3.0, 1.0) LIMIT ?",
                (expr, *[x for y in years for x in (f"{y}-12-31", f"{y}-01-01")], k))]
            out["commits"] = [dict(r) for r in m.execute(
                "SELECT hash, subject, date, author FROM coms WHERE coms MATCH ? "
                + ("AND (" + " OR ".join("date LIKE ?" for _ in years) + ") " if years else "")
                + "ORDER BY bm25(coms, 4.0, 1.0) LIMIT ?",
                (expr, *[f"{y}%" for y in years], k + 4))]
        elif years:
            # "what happened in 2021": no topic, so the year itself is the query
            span = [x for y in years for x in (f"{y}-12-31", f"{y}-01-01")]
            cond = " OR ".join("(start <= ? AND end >= ?)" for _ in years)
            out["chapters"] = [dict(r) for r in m.execute(
                f"SELECT entry, title, start, end, substr(narrative, 1, 240) AS snip "
                f"FROM chaps WHERE {cond} ORDER BY length(narrative) DESC LIMIT ?",
                (*span, k * 2))]
        return out


_ASK_SYS = """You answer questions about ONE software project from its knowledge base: \
catalogue entries (what exists), chapters (dated narrations of how each entry evolved) \
and commits. Use ONLY that evidence.

- Answer the question directly first, then support it. Be concrete: names, dates, what \
changed.
- Cite entries as [[Entry Name]] exactly as written in the evidence, and commits by their \
short hash in backticks.
- When the question is about time, answer with dates and order events chronologically. \
Commits say when work was DONE; releases say when it SHIPPED, and "added in" usually means \
the release. When the project lists releases, give both: "built in December 2021, shipped \
in 3.0.0 (January 2022)" — the first release dated after the work is the one that carried it.
- If the evidence does not cover the question, say so plainly and say what it does cover.
- Markdown: short paragraphs, bullet lists where they help. No headings. At most ~250 words."""


def _project_facts(kb) -> str:
    """Whole-project facts no entry carries: when it began, who, and how busy each year was.
    Without them "when did the project start?" was answered from the oldest *narrated*
    entry, three years late."""
    first = kb.execute("SELECT hash, authored_at, author_name, subject FROM commits "
                       "WHERE authored_at IS NOT NULL ORDER BY authored_at LIMIT 1").fetchone()
    last = kb.execute("SELECT MAX(authored_at) FROM commits").fetchone()[0] or ""
    if not first:
        return "(no commits)"
    years = ", ".join(f"{r[0]}: {r[1]}" for r in kb.execute(
        "SELECT substr(authored_at,1,4), COUNT(*) FROM commits WHERE is_merge=0 "
        "GROUP BY 1 ORDER BY 1"))
    authors = ", ".join(f"{r[0]} ({r[1]}, {r[2][:4]}–{r[3][:4]})" for r in kb.execute(
        "SELECT author_name, COUNT(*), MIN(authored_at), MAX(authored_at) FROM commits "
        "WHERE is_merge=0 GROUP BY author_name ORDER BY 2 DESC LIMIT 8"))
    return (f"  first commit: {first['authored_at'][:10]} by {first['author_name']} "
            f"(`{first['hash'][:10]}` {first['subject'][:100]})\n"
            f"  latest commit: {last[:10]}\n  commits per year: {years}\n"
            f"  main authors (commits, active years): {authors}")


def ask(index: Index, kb_path: str, provider, question: str, sample: str = "") -> dict:
    hits = index.search(question, k=8)
    kb = sqlite3.connect(kb_path)
    kb.row_factory = sqlite3.Row
    try:
        names = [e["name"] for e in hits["entries"][:6]]
        for c in hits["chapters"]:
            if c["entry"] not in names and len(names) < 9:
                names.append(c["entry"])
        blocks = []
        for n in names:
            d = kb.execute("SELECT id, name, tier, definition, summary, born_at, last_seen, "
                           "n_commits, lifecycle FROM domains WHERE name=?", (n,)).fetchone()
            if not d:
                continue
            chaps = kb.execute(
                "SELECT title, narrative, period_start, period_end FROM evolution_chapters "
                "WHERE target_type='domain' AND target_id=? ORDER BY period_start",
                (str(d["id"]),)).fetchall()
            # the leading entries get their whole story: cutting a chapter at 300 chars
            # dropped "…and was removed in August 2021" from the battle pass
            room = 900 if len(blocks) < 3 else 320
            story = "\n".join(
                f"    {(c['period_start'] or '')[:10]}..{(c['period_end'] or '')[:10]} "
                f"{c['title']}: {(c['narrative'] or '')[:room]}" for c in chaps[:14])
            uses = [r[0] for r in kb.execute(
                "SELECT t.name FROM domain_edges e JOIN domains t ON t.id=e.dst_domain "
                "WHERE e.src_domain=? LIMIT 8", (d["id"],))]
            used = [r[0] for r in kb.execute(
                "SELECT s.name FROM domain_edges e JOIN domains s ON s.id=e.src_domain "
                "WHERE e.dst_domain=? LIMIT 8", (d["id"],))]
            blocks.append(
                f"ENTRY [[{d['name']}]] ({d['tier']}, {d['n_commits']} commits, "
                f"{(d['born_at'] or '')[:10]}..{(d['last_seen'] or '')[:10]}"
                f"{', removed' if d['lifecycle'] == 'removed' else ''})\n"
                f"  what: {(d['summary'] or d['definition'] or '(none)')[:500]}\n"
                + (f"  uses: {', '.join(uses)}\n" if uses else "")
                + (f"  used by: {', '.join(used)}\n" if used else "")
                + (f"  chapters:\n{story}" if story else ""))
        project = _project_facts(kb)
        tags = [(r[0], (r[1] or "")[:10]) for r in kb.execute(
            "SELECT name, time_start FROM eras WHERE time_start IS NOT NULL "
            "ORDER BY time_start")]
        # Only version-shaped tags are releases. void-queue tags milestones of single
        # components (ccc-rc1.14, luna-alpha-2); treated as releases, the battle pass
        # "shipped in ccc-rc1".
        releases = [t for t in tags if _VERSION.fullmatch(t[0])]
        if len(releases) < 3:
            releases = []
        milestones = [t for t in tags if t not in releases]
    finally:
        kb.close()

    def shipped(date: str) -> str:
        # the first tag at or after a commit is the release that carried it
        rel = next((f" (shipped in {n}, {d})" for n, d in releases if d >= date), "")
        return rel

    commits = "\n".join(f"  `{c['hash'][:10]}` {c['date']}{shipped(c['date'])} {c['author']}: "
                        f"{c['subject'][:140]}" for c in hits["commits"][:12])
    if releases:
        project += "\n  releases (tag, date): " + ", ".join(f"{n} {d}" for n, d in releases[-60:])
    if milestones:
        project += ("\n  other tags — milestone markers, NOT releases (tag, date): "
                    + ", ".join(f"{n} {d}" for n, d in milestones[-40:]))
    user = (f"QUESTION: {question}\n\nEVIDENCE — THE PROJECT:\n{project}\n\nEVIDENCE — ENTRIES:\n"
            + ("\n\n".join(blocks) or "(none matched)")
            + "\n\nEVIDENCE — COMMITS:\n" + (commits or "(none matched)"))
    answer = provider.chat(_ASK_SYS, user, want_json=False, role="answer",
                           cache_extra=sample, stage="ask")
    return {"answer": answer if isinstance(answer, str) else json.dumps(answer),
            "used": names, "hits": hits}
