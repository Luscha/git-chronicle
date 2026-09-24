"""INSPECT — one file or one word, and everything the knowledge base knows about it.

Curation goes wrong at the level of a handful of files, and until now nothing could show
them: the trait framework sat in six files spread over seven entries, and the only way to
discover that was to ask a model or write SQL. The catalogue answers "what is this entry",
the chronicle answers "how did it evolve"; this answers **"what is this file, who owns it,
what was it built for, and what else belongs with it"** — which is the question a surgical
correction starts from.

Everything here is deterministic: SQL over the knowledge base plus one grep of the
worktree for includes. No model calls, so it is free to run on every keystroke and its
answers never drift.
"""

from __future__ import annotations

import fnmatch
import json
import re
from collections import Counter, defaultdict

from ..extract.git_ingest import run_git
from ..taxonomy.imports import extract_import_refs

_MAX_FILES = 400


def find(conn, query: str, limit: int = 60, scope=None) -> dict:
    """Files whose path matches, grouped by the entry that owns them.

    A word is the unit people think in ("traits", "battlepass") and a path is the unit the
    ledger writes rules about, so both are the same search: the query matches anywhere in
    the path, and the result says who owns each match.
    """
    q = query.strip().strip("/").lower()
    if not q:
        return {"query": query, "files": [], "entries": [], "unowned": []}
    owner, tier = {}, {}
    for r in conn.execute(
            "SELECT d.name, d.tier, f.path FROM domain_files f JOIN domains d ON d.id = f.domain_id"):
        owner[r["path"]] = r["name"]
        tier[r["name"]] = r["tier"]

    seen = {p for p in owner if q in p.lower()}
    # files the catalogue owns nowhere are exactly what a missing entry looks like
    for r in conn.execute("SELECT DISTINCT path FROM commit_files WHERE lower(path) LIKE ?",
                          (f"%{q}%",)):
        seen.add(r["path"])
    # commit_files remembers every path history ever had, including trees the scope map
    # excludes: without this a search for "trait" answers with 408 Boost headers
    if scope is not None:
        seen = {p for p in seen if scope(p)}

    # the name is the signal: char_traits.cpp is about traits, a file in a traits/ folder
    # may only live next to them
    def rank(p: str) -> tuple:
        base = p.rsplit("/", 1)[-1].lower()
        return (0 if q in base else 1, 0 if p in owner else 1, len(p), p)
    files = sorted(seen, key=rank)[:limit]
    by_entry: dict[str, list] = defaultdict(list)
    for p in files:
        by_entry[owner.get(p, "")].append(p)
    entries = [{"name": n or None, "tier": tier.get(n), "files": sorted(fs), "n": len(fs)}
               for n, fs in by_entry.items()]
    entries.sort(key=lambda e: (e["name"] is None, -e["n"]))
    return {"query": query, "files": files, "entries": entries,
            "total": len(seen), "shown": len(files)}


def file_card(conn, path: str, repo: str = "", ledger=None) -> dict:
    """One file: its owner, the work it was part of, its story, and its neighbours."""
    row = conn.execute(
        "SELECT d.name, d.tier, d.id, f.source FROM domain_files f "
        "JOIN domains d ON d.id = f.domain_id WHERE f.path = ?", (path,)).fetchone()
    entry = {"name": row["name"], "tier": row["tier"]} if row else None

    concerns = [{"label": r["label"], "summary": (r["summary"] or "")[:200],
                 "date": (r["authored_at"] or "")[:10], "commit": r["commit_hash"][:10],
                 "entry": r["entry"]}
                for r in conn.execute(
                    "SELECT cn.label, cn.summary, cn.commit_hash, c.authored_at, d.name AS entry "
                    "FROM concerns cn JOIN commits c ON c.hash = cn.commit_hash "
                    "LEFT JOIN domains d ON d.id = cn.domain_id "
                    "WHERE cn.files LIKE ? ORDER BY c.authored_at DESC LIMIT 40",
                    (f'%"{path}"%',))]

    commits = [{"hash": r["hash"][:10], "date": (r["authored_at"] or "")[:10],
                "author": r["author_name"], "subject": (r["subject"] or "")[:120]}
               for r in conn.execute(
                   "SELECT c.hash, c.authored_at, c.author_name, c.subject FROM commit_files f "
                   "JOIN commits c ON c.hash = f.commit_hash WHERE f.path = ? AND c.is_merge = 0 "
                   "ORDER BY c.authored_at DESC LIMIT 25", (path,))]

    chapters = []
    if row:
        for c in conn.execute(
                "SELECT title, period_start, period_end, commit_hashes FROM evolution_chapters "
                "WHERE target_type='domain' AND target_id=? ORDER BY period_start",
                (str(row["id"]),)):
            hashes = set(json.loads(c["commit_hashes"] or "[]"))
            if hashes & {m["hash"] for m in commits}:
                chapters.append({"title": c["title"], "start": (c["period_start"] or "")[:10],
                                 "end": (c["period_end"] or "")[:10]})

    # what changes with it, which is the evidence a shared purpose leaves behind
    co: Counter = Counter()
    hashes = [m["hash"] for m in commits]
    if hashes:
        qm = ",".join("?" * len(hashes))
        for r in conn.execute(
                f"SELECT f.path, COUNT(*) n FROM commit_files f JOIN commits c ON c.hash=f.commit_hash "
                f"WHERE substr(f.commit_hash,1,10) IN ({qm}) AND f.path != ? "
                "GROUP BY f.path ORDER BY n DESC LIMIT 12", (*hashes, path)):
            co[r["path"]] = r["n"]

    return {"path": path, "entry": entry, "concerns": concerns, "commits": commits,
            "chapters": chapters, "why": _why_owned(row, path, concerns, ledger),
            "cochanged": [{"path": p, "n": n, "entry": _owner_of(conn, p)} for p, n in co.most_common(8)]}


def _why_owned(row, path: str, concerns: list, ledger=None) -> list[str]:
    """Why this entry holds this file — the question a graph of 6,000 files invites and
    nothing could answer. Territory comes from three places and they read identically in
    the catalogue: a rule you wrote, the work attached to the file, and its own name."""
    if row is None:
        return []
    why, name = [], row["name"]
    if ledger is not None and ledger.owner(path) == name:
        glob = next((g for e in ledger.entries if e.name == name for g in e.claims
                     if fnmatch.fnmatch(path, g)), path)
        why.append(f"a rule in your plan claims it: claim {glob}")
    mine = sum(1 for c in concerns if c["entry"] == name)
    if mine:
        why.append(f"{mine} work item{'s' if mine != 1 else ''} filed under “{name}” changed it")
    if all(w in _stem_words(path) for w in _name_words(name)):
        why.append(f"its name carries “{name}”")
    if not why:
        why.append("the commits it appears in are mostly this entry's"
                   if row["source"] == "history" else "it came with this entry's territory")
    return why


def _owner_of(conn, path: str) -> str | None:
    r = conn.execute("SELECT d.name FROM domain_files f JOIN domains d ON d.id=f.domain_id "
                     "WHERE f.path = ?", (path,)).fetchone()
    return r["name"] if r else None


def includes(repo: str, paths: list[str], scan: list[str] | None = None) -> dict:
    """Which of these files include which, and who else includes them.

    The direction is the whole point: the six files everything else includes are the
    framework, and the ten that include them are its clients. That distinction took a
    human one sentence and no version of the assembly ever inferred it, while the evidence
    sat in the source the entire time.
    """
    paths = paths[:_MAX_FILES]
    base = {p.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower(): p for p in paths}
    refs: dict[str, set] = {}
    for p in paths:
        try:
            text = run_git(repo, ["show", f"HEAD:{p}"])
        except Exception:  # noqa: BLE001 - deleted or binary files simply have no imports
            continue
        refs[p] = {r for r in extract_import_refs(text) if r in base and base[r] != p}

    inbound: Counter = Counter()
    for p, rs in refs.items():
        for r in rs:
            inbound[base[r]] += 1

    # who outside the set includes them: a framework is used beyond its own folder
    outside: Counter = Counter()
    names = "|".join(re.escape(b) for b in base)
    if names:
        try:
            hits = run_git(repo, ["grep", "-lE", f'(#include|require|import).*({names})',
                                  "--", "."])
            for f in hits.splitlines():
                if f and f not in base.values():
                    outside[f] += 1
        except Exception:  # noqa: BLE001 - grep exits non-zero when nothing matches
            pass
    return {"included_by_peers": dict(inbound), "outside_users": len(outside),
            "outside_sample": sorted(outside)[:8],
            "refs": {p: sorted(base[r] for r in rs) for p, rs in refs.items() if rs}}


def split_hint(conn, repo: str, query: str, scope=None) -> dict:
    """The framework/clients reading of a search, as evidence rather than a verdict.

    Proposes two buckets and says why each file is in one; deciding is still the owner's,
    because "these are the traits the skill system needs, the framework is the other six"
    is knowledge no include graph carries on its own.
    """
    found = find(conn, query, limit=_MAX_FILES, scope=scope)
    paths = found["files"]
    inc = includes(repo, paths) if repo and paths else {"included_by_peers": {}, "refs": {}}
    peers = inc["included_by_peers"]

    def stem(p: str) -> str:
        return p.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()

    # A header's own .cpp and its language binding belong WITH it, not with its callers:
    # char_traits.cpp includes char_traits.hpp, which by direction alone would file the
    # implementation as a client of its own interface.
    core = {p: peers[p] for p in peers}
    core_stems = {stem(p) for p in core}
    for p in paths:
        if p in core:
            continue
        st = stem(p)
        if any(cs in st or st in cs for cs in core_stems):
            core[p] = peers.get(p, 0)
    clients = sorted(p for p in inc.get("refs", {}) if p not in core)
    return {"query": query,
            "core": sorted(({"path": p, "included_by": n} for p, n in core.items()),
                           key=lambda x: -x["included_by"]),
            "clients": clients,
            "rest": [p for p in paths if p not in core and p not in clients],
            "outside_users": inc.get("outside_users", 0)}


# Words that name a KIND of thing rather than the thing, so they carry no identity of
# their own: "Affect System" is about affects, and every second entry ends in "System".
_GENERIC = {"system", "the", "and", "for", "with"}


def _name_words(name: str) -> list[str]:
    """The words of an entry's name a filename could plausibly carry."""
    ws = [w for w in re.split(r"[^a-z0-9]+", name.lower()) if len(w) > 2]
    keep = [w for w in ws if w not in _GENERIC]
    return keep or ws              # an entry called only "Manager" still gets to match


def _stem_words(path: str) -> set[str]:
    base = path.rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0] if "." in base[1:] else base
    return {w for w in re.split(r"[^a-z0-9]+", stem.lower()) if w}


def unclaimed(conn, repo: str = "", scope=None, ledger=None, entry: str | None = None,
              limit: int = 12) -> list[dict]:
    """Files no entry owns whose own NAME carries an entry's name.

    Both review queues ask what is *unfiled* and the Files view asks what is *misfiled*;
    neither can see a file that is simply absent. `char_affect.cpp` is in 135 commits and
    belongs to nothing, because four entries answer to the word "affect" and a contested
    word identifies none of them — the territory rule is right to refuse, and the refusal
    was silent. As a proposal it costs nothing: requiring EVERY distinctive word of the
    name ("Item Affect System" needs item *and* affect) leaves one claimant per file here,
    and cuts the queue from 2,657 loose matches to 173 worth looking at.
    """
    owned = {r[0] for r in conn.execute("SELECT DISTINCT path FROM domain_files")}
    if repo:
        alive = set(run_git(repo, ["ls-files"], check=False).splitlines())
    else:                          # no worktree: every path history remembers, ghosts and all
        alive = {r[0] for r in conn.execute("SELECT DISTINCT path FROM commit_files")}
    free = [p for p in alive
            if p not in owned and (scope is None or scope(p))
            and (ledger is None or (ledger.owner(p) is None and not ledger.kept(p)))]
    if not free:
        return []

    # by NAME, not by id: the catalogue holds two entries called "Quest System", and the
    # ledger addresses entries by name — proposing the same file to each was half the queue
    ents: dict[str, list] = {}
    for r in conn.execute("SELECT id, name, tier FROM domains "
                          "WHERE status IN ('named','confirmed','provisional')"):
        if entry is None or r["name"] == entry:
            ents.setdefault(r["name"], [r["tier"], []])[1].append(r["id"])
    words = {name: _name_words(name) for name in ents}
    hits: dict[str, list] = defaultdict(list)
    claimed: dict[str, list] = defaultdict(list)
    for p in free:
        st = _stem_words(p)
        for name in ents:
            if all(w in st for w in words[name]):
                hits[name].append(p)
                claimed[p].append(name)
    if not hits:
        return []

    # how many of the entry's OWN commits touched it — the second, weaker evidence, and
    # the one that says whether this is a file it actually worked on
    touched: Counter = Counter()
    paths = {p for v in hits.values() for p in v}
    ids = {did: name for name, (_, dids) in ents.items() for did in dids}
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS _unclaimed (path TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM _unclaimed")
    conn.executemany("INSERT INTO _unclaimed (path) VALUES (?)", [(p,) for p in paths])
    for r in conn.execute(
            "SELECT cd.domain_id d, cf.path p, COUNT(DISTINCT cf.commit_hash) n "
            "FROM commit_domains cd JOIN commit_files cf ON cf.commit_hash = cd.commit_hash "
            "JOIN _unclaimed u ON u.path = cf.path GROUP BY 1, 2"):
        if r["d"] in ids:
            touched[(ids[r["d"]], r["p"])] += r["n"]

    out = []
    for name, (tier, _) in ents.items():
        fs = hits.get(name)
        if not fs:
            continue
        files = sorted(({"path": p, "commits": touched.get((name, p), 0),
                         "also": [o for o in claimed[p] if o != name]} for p in fs),
                       key=lambda f: (-f["commits"], f["path"]))
        out.append({"entry": name, "tier": tier, "n": len(files), "files": files,
                    "worked_on": sum(1 for f in files if f["commits"])})
    out.sort(key=lambda e: (-e["worked_on"], -e["n"]))
    return out[:limit] if entry is None else out
