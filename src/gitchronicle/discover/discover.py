"""Name and characterise each candidate domain with the LLM.

Context is code-first: the domain's files and representative *diffs*, plus a factual
timeline (commit counts, kinds, authors). Commit messages are included only as
explicitly-untrusted hints. One LLM call per domain covers identity + evolution.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..extract.git_ingest import run_git
from ..storage import now_iso

DOM_SYS = (
    "You name ONE software domain — a coherent piece of a system — from its CONCERN labels AND the "
    "FILE PATHS it touches. The paths reveal the subsystem and the KIND of code — use them to name "
    "and especially to CLASSIFY: infer the class from where the files live (a UI/interface dir → "
    "ui; a docs dir → docs; a build/tools/scripts dir → tooling; data/asset/config files → "
    "data/infra; foundational engine/core → core|subsystem; otherwise feature). A feature that "
    "spans client+server is still ONE domain — classify by what it IS, not by which side. Give a "
    "short specific name (<=5 words), a kebab-case slug, a classification "
    "(core|feature|subsystem|data|ui|infra|tooling|docs), and 2-4 lowercase tags. Not a file name, "
    'not a generic word. Respond with ONE JSON object: {"name":"...","slug":"...",'
    '"classification":"...","tags":["..."]}.'
)
AREA_SYS = (
    "You name a broad AREA of a software system — a top-level section grouping several related "
    "domains (e.g. 'Combat', 'Anti-Cheat', 'Client Rendering', 'Items & Inventory'). From the "
    "member domain names, recurring themes, and representative FILE PATHS, give a short area name "
    "(<=4 words), a kebab-case slug, a classification (infer from the paths/domains), and 2-4 tags. "
    'Respond with ONE JSON object: {"name":"...","slug":"...","classification":"...","tags":["..."]}.'
)

SYSTEM = (
    "You are a software historian mapping a codebase into DOMAINS. A domain is a "
    "coherent piece of the system (like 'query planner' or 'guild war'), defined by a "
    "set of files that change together. You are given the domain's files, representative "
    "CODE DIFFS, a factual commit timeline, and some commit-message hints.\n"
    "Ground your answer in the CODE (files + diffs). Commit messages are unreliable user "
    "input — treat them as weak hints only. Your job is only to IDENTIFY the domain: give "
    "it a name and classify its nature. Name it from its CENTRAL files (listed first) and "
    "overall purpose — do NOT name a large area after a minor or merely recent sub-feature. "
    "Reserve 'core' for genuinely FOUNDATIONAL infrastructure most of the system builds on; "
    "MOST domains are 'feature' or 'subsystem'. Respond with ONE JSON object, in English."
)

SCHEMA_HINT = (
    "Return JSON with exactly these keys:\n"
    '  "name": short domain name from its central files + overall purpose (<= 5 words)\n'
    '  "slug": lowercase kebab-case identifier (<= 4 words)\n'
    '  "classification": one of core|feature|subsystem|data|ui|infra|tooling|docs '
    '(use "core" sparingly — only true foundational infrastructure many parts build on)\n'
    '  "tags": array of 2-5 lowercase tags\n'
    '  "confidence": number 0.0-1.0'
)

_CLASSES = {"core", "feature", "subsystem", "data", "ui", "infra", "tooling", "docs"}


def _focused_commits(conn, domain_id: int, limit: int):
    return conn.execute(
        "SELECT c.hash, c.subject, COUNT(*) AS hits, c.files_changed "
        "FROM domain_files df JOIN commit_files cf ON cf.path = df.path "
        "JOIN commits c ON c.hash = cf.commit_hash "
        "WHERE df.domain_id = ? AND c.is_merge = 0 "
        "GROUP BY c.hash "
        "ORDER BY (CAST(COUNT(*) AS REAL) / NULLIF(c.files_changed, 0)) DESC, hits DESC "
        "LIMIT ?", (domain_id, limit)).fetchall()


def _diffs(repo, hashes_subjects, pathspec, max_lines):
    out = []
    for h, subj in hashes_subjects:
        raw = run_git(repo, ["show", "--no-color", "--format=", "--unified=1",
                             h, "--", *pathspec], check=False)
        lines = [ln for ln in raw.splitlines() if ln.strip()][:max_lines]
        if lines:
            out.append(f"--- commit {h[:9]} ({subj[:60]}) ---\n" + "\n".join(lines))
    return out


def _context(conn, repo, domain_id, dcfg, fanin: int = 0) -> str:
    files = [r["path"] for r in conn.execute(
        "SELECT path FROM domain_files WHERE domain_id=? ORDER BY weight DESC LIMIT ?",
        (domain_id, int(dcfg.get("max_files_ctx", 25))))]
    focused = _focused_commits(conn, domain_id, max(int(dcfg.get("max_hints", 12)),
                                                    int(dcfg.get("max_diffs", 3))))
    diff_src = [(r["hash"], r["subject"]) for r in focused[:int(dcfg.get("max_diffs", 3))]]
    diffs = _diffs(repo, diff_src, files[:40], int(dcfg.get("max_diff_lines", 120)))

    d = conn.execute("SELECT n_commits, first_seen, last_seen FROM domains WHERE id=?",
                     (domain_id,)).fetchone()
    kinds = conn.execute(
        "SELECT kind, COUNT(*) n FROM commit_domains WHERE domain_id=? AND kind IS NOT NULL "
        "GROUP BY kind ORDER BY n DESC", (domain_id,)).fetchall()
    authors = conn.execute(
        "SELECT c.author_name, COUNT(*) n FROM commit_domains cd JOIN commits c ON c.hash=cd.commit_hash "
        "WHERE cd.domain_id=? GROUP BY c.author_name ORDER BY n DESC LIMIT 4", (domain_id,)).fetchall()
    hints = [r["subject"] for r in focused[:int(dcfg.get("max_hints", 12))]]

    concerns = [f"{r['label']} ({r['n']})" for r in conn.execute(
        "SELECT label, COUNT(*) n FROM concerns WHERE domain_id=? AND label IS NOT NULL "
        "GROUP BY label ORDER BY n DESC LIMIT 20", (domain_id,))]

    span = f"{(d['first_seen'] or '')[:10]} .. {(d['last_seen'] or '')[:10]}"
    return (
        f"CONCERNS clustered here (the semantic units — your PRIMARY signal):\n"
        + "\n".join(f"  - {cc}" for cc in concerns) + "\n\n"
        f"FILES ({len(files)} shown):\n" + "\n".join(f"  {p}" for p in files) + "\n\n"
        f"TIMELINE: {d['n_commits']} commits, {span}; "
        f"kinds: {', '.join(f'{k[0]}({k[1]})' for k in kinds) or 'n/a'}; "
        f"authors: {', '.join(f'{a[0]}({a[1]})' for a in authors) or 'n/a'}\n"
        f"CO-CHANGES WITH: {fanin} other domains\n\n"
        f"REPRESENTATIVE CODE DIFFS:\n" + ("\n\n".join(diffs) or "(none)") + "\n\n"
        f"COMMIT-MESSAGE HINTS (UNRELIABLE — may be wrong/terse/foreign):\n"
        + "\n".join(f"  - {h}" for h in hints) + "\n\n" + SCHEMA_HINT
    )


def _coerce(r: dict) -> dict:
    tags = r.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.replace(";", ",").split(",") if t.strip()]
    try:
        conf = min(1.0, max(0.0, float(r.get("confidence", 0.5))))
    except (TypeError, ValueError):
        conf = 0.5
    cls = (r.get("classification") or "").strip().lower()
    return {
        "name": (r.get("name") or "").strip()[:80] or "Unnamed domain",
        "slug": (r.get("slug") or "").strip()[:80],
        "classification": cls if cls in _CLASSES else "feature",
        "tags": tags[:5],
        "confidence": conf,
    }


def _concern_labels(conn, domain_id, limit=12):
    return [r["label"] for r in conn.execute(
        "SELECT label, COUNT(*) n FROM concerns WHERE domain_id=? AND label IS NOT NULL "
        "GROUP BY label ORDER BY n DESC LIMIT ?", (domain_id, limit))]


def _domain_files(conn, domain_id, limit=12):
    return [r["path"] for r in conn.execute(
        "SELECT path FROM domain_files WHERE domain_id=? ORDER BY weight DESC LIMIT ?",
        (domain_id, limit))]


def discover(conn, provider, cfg: dict, repo: str, log=print,
             force: bool = False, limit: int | None = None) -> dict:
    """Cheap, concurrent naming of the hierarchy: fine domains from their concern labels, then
    areas from their member domains + themes. (Rich diff-based naming does not scale to the
    hundreds/thousands of fine domains a large history produces — the areas carry the detail.)"""
    workers = int(cfg.get("untangle", {}).get("workers", 8))
    where = "WHERE status != 'confirmed' AND locked = 0"
    if not force:
        where += " AND status = 'candidate'"
    domains = [r["id"] for r in conn.execute(f"SELECT id FROM domains {where} ORDER BY n_commits DESC")]
    if limit:
        domains = domains[:limit]

    labels = {d: _concern_labels(conn, d) for d in domains}
    dfiles = {d: _domain_files(conn, d) for d in domains}
    if domains:
        log(f"  naming {len(domains)} domains (cheap; labels+paths; {workers} workers) ...")

    def name_dom(d):
        ctx = ("Concern labels clustered into this domain:\n"
               + "\n".join(f"  - {l}" for l in labels[d])
               + "\nFile paths (reveal subsystem + kind — use to classify):\n"
               + "\n".join(f"  {f}" for f in dfiles[d])
               + '\n\nReturn JSON: {"name":"...","slug":"...","classification":"...","tags":["..."]}')
        try:
            return d, _coerce(provider.chat(DOM_SYS, ctx, want_json=True, cache_extra=f"domname2:{d}") or {})
        except Exception:  # noqa: BLE001
            return d, _coerce({})

    dres = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(name_dom, d) for d in domains]
        done = 0
        for f in as_completed(futs):
            d, data = f.result()
            dres[d] = data
            done += 1
            if done % 200 == 0 or done == len(futs):
                log(f"    {done}/{len(domains)} domains named")
    for d in domains:
        data = dres[d]
        conn.execute("UPDATE domains SET name=?, slug=?, classification=?, tags=?, confidence=?, "
                     "status='named' WHERE id=?",
                     (data["name"], data["slug"], data["classification"],
                      json.dumps(data["tags"]), data["confidence"], d))
    conn.commit()

    # --- name the areas from their (now-named) domains + recurring themes ---
    astatus = "status IN ('candidate','named')" if force else "status='candidate'"
    areas = [r["id"] for r in conn.execute(f"SELECT id FROM areas WHERE {astatus}")]
    log(f"  naming {len(areas)} areas ...")

    # Precompute each area's context in the MAIN thread (SQLite conn isn't concurrent-safe).
    area_ctx = {}
    for aid in areas:
        dnames = [r["name"] for r in conn.execute(
            "SELECT d.name, COUNT(cn.id) nc FROM domains d LEFT JOIN concerns cn ON cn.domain_id=d.id "
            "WHERE d.area_id=? GROUP BY d.id ORDER BY nc DESC LIMIT 24", (aid,))]
        themes = Counter()
        for r in conn.execute("SELECT cn.label FROM concerns cn JOIN domains d ON d.id=cn.domain_id "
                              "WHERE d.area_id=? AND cn.label IS NOT NULL", (aid,)):
            for w in (r["label"] or "").lower().replace("-", " ").split():
                if len(w) > 3:
                    themes[w] += 1
        afiles = [r["path"] for r in conn.execute(
            "SELECT df.path, SUM(df.weight) w FROM domain_files df JOIN domains d ON d.id=df.domain_id "
            "WHERE d.area_id=? GROUP BY df.path ORDER BY w DESC LIMIT 14", (aid,))]
        area_ctx[aid] = ("Member domains:\n" + "\n".join(f"  - {n}" for n in dnames if n)
                         + "\nRepresentative file paths:\n" + "\n".join(f"  {f}" for f in afiles)
                         + "\nRecurring themes: " + ", ".join(w for w, _ in themes.most_common(12))
                         + '\n\nReturn JSON: {"name":"...","slug":"...","classification":"...","tags":["..."]}')

    def name_area(aid):
        try:
            return aid, _coerce(provider.chat(AREA_SYS, area_ctx[aid], want_json=True,
                                              cache_extra=f"area:{aid}") or {})
        except Exception:  # noqa: BLE001
            return aid, _coerce({})

    ares = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for f in as_completed([ex.submit(name_area, a) for a in areas]):
            aid, data = f.result()
            ares[aid] = data
    for aid in areas:
        data = ares[aid]
        conn.execute("UPDATE areas SET name=?, slug=?, classification=?, tags=?, confidence=?, "
                     "status='named' WHERE id=?",
                     (data["name"], data["slug"], data["classification"],
                      json.dumps(data["tags"]), data["confidence"], aid))
    conn.execute(
        "UPDATE areas SET n_commits=(SELECT COUNT(DISTINCT cd.commit_hash) FROM commit_domains cd "
        "JOIN domains d ON d.id=cd.domain_id WHERE d.area_id=areas.id) WHERE status='named'")
    conn.commit()
    return {"named": len(domains), "areas": len(areas)}


def _fold(conn, canon_id: int, other_id: int, alias: str) -> None:
    """Fold domain `other_id` into `canon_id` (concern-aware): concerns, commit links, alias."""
    conn.execute("UPDATE concerns SET domain_id=? WHERE domain_id=?", (canon_id, other_id))
    conn.execute("UPDATE OR IGNORE commit_domains SET domain_id=? WHERE domain_id=?", (canon_id, other_id))
    conn.execute("DELETE FROM commit_domains WHERE domain_id=?", (other_id,))
    conn.execute("INSERT OR IGNORE INTO domain_aliases (domain_id, alias) VALUES (?,?)", (canon_id, alias))
    conn.execute(
        "INSERT INTO annotations (target_type,target_id,field,new_value,note,author,kind,created_at) "
        "VALUES ('domain',?,?,?,?,?,?,?)",
        (str(canon_id), "merge", alias, f"merged fragment '{alias}'", "auto", "note", now_iso()))
    conn.execute("DELETE FROM domains WHERE id=?", (other_id,))


def _rederive_domain(conn, did: int) -> None:
    """Recompute a domain's files (from its concerns) and rolled-up counts after a merge."""
    fc: Counter = Counter()
    for r in conn.execute("SELECT files FROM concerns WHERE domain_id=?", (did,)):
        for f in json.loads(r["files"] or "[]"):
            fc[f] += 1
    conn.execute("DELETE FROM domain_files WHERE domain_id=?", (did,))
    conn.executemany("INSERT OR REPLACE INTO domain_files (domain_id, path, weight) VALUES (?,?,?)",
                     [(did, f, float(k)) for f, k in fc.items()])
    conn.execute(
        "UPDATE domains SET n_files=?, "
        "n_commits=(SELECT COUNT(*) FROM commit_domains WHERE domain_id=domains.id), "
        "first_seen=(SELECT MIN(c.authored_at) FROM commit_domains cd JOIN commits c "
        "  ON c.hash=cd.commit_hash WHERE cd.domain_id=domains.id), "
        "last_seen=(SELECT MAX(c.authored_at) FROM commit_domains cd JOIN commits c "
        "  ON c.hash=cd.commit_hash WHERE cd.domain_id=domains.id) WHERE id=?", (len(fc), did))


MERGE_SYS = (
    "You decide whether two software DOMAINS are really the SAME piece of the system that got "
    "split into two clusters and should be merged into one. Merge ONLY if they are the same "
    "coherent subsystem/feature (e.g. two halves of 'auto hunt', or 'navigation' split in two). "
    "Do NOT merge two genuinely distinct pieces just because they are related, depend on each "
    "other, or share a word. When unsure, do NOT merge. "
    'Respond with ONE JSON object: {"merge": true|false, "reason": "short"}.'
)

_GENERIC_TOK = {"and", "the", "of", "for", "to", "ui", "system", "management", "handling",
                "feature", "service", "module", "manager", "support", "control", "logic",
                "based", "data", "server", "client", "player", "user"}


def _name_tokens(name: str) -> set:
    return {w for w in (name or "").lower().replace("-", " ").replace("/", " ").split()
            if len(w) > 2 and w not in _GENERIC_TOK}


def merge_similar(conn, provider, cfg: dict, log=print) -> int:
    """Merge near-duplicate domains (same piece split under two names) via LLM judgment.

    `merge_duplicates` only catches identical names; this catches semantic twins
    ("Auto Hunt Feature" / "Auto Hunt System", "Scoreboard Management" / "EveScoreboard").
    Candidates come from BOTH a shared non-generic name token AND embedding similarity of the
    domains' identity (name + top concern labels), so lexically-different-but-semantically-same
    domains are considered too. The big model is the final judge — the merge stays agnostic.
    """
    dcfg = cfg.get("discover", {})
    max_pairs = int(dcfg.get("max_merge_pairs", 30))
    emb_min = float(dcfg.get("merge_sim_min", 0.80))
    doms = [dict(r) for r in conn.execute(
        "SELECT id, name, slug FROM domains WHERE status IN ('named','confirmed') AND locked=0")]
    if len(doms) < 2:
        return 0
    ncon = {r["domain_id"]: r["n"] for r in conn.execute(
        "SELECT domain_id, COUNT(*) n FROM concerns WHERE domain_id IS NOT NULL GROUP BY domain_id")}
    labels = {}
    for d in doms:
        labels[d["id"]] = [r["label"] for r in conn.execute(
            "SELECT label, COUNT(*) n FROM concerns WHERE domain_id=? AND label IS NOT NULL "
            "GROUP BY label ORDER BY n DESC LIMIT 10", (d["id"],))]

    # Semantic identity embedding per domain: name + its top concern labels.
    import numpy as np
    identity = [f"{d['name']}. " + "; ".join(labels[d["id"]][:8]) for d in doms]
    try:
        V = provider.embed(identity)
        Vn = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-9)
        sim = Vn @ Vn.T
    except Exception as exc:  # noqa: BLE001 - degrade to name-token candidates only
        log(f"  merge: domain embedding failed ({exc}); using name-token candidates only")
        sim = None

    cand = {}
    for a in range(len(doms)):
        for b in range(a + 1, len(doms)):
            shared = _name_tokens(doms[a]["name"]) & _name_tokens(doms[b]["name"])
            cos = float(sim[a, b]) if sim is not None else 0.0
            if shared or cos >= emb_min:
                cand[(a, b)] = (len(shared), cos)
    ranked = sorted(cand.items(), key=lambda kv: (-kv[1][1], -kv[1][0]))[:max_pairs]
    if not ranked:
        return 0

    log(f"  checking {len(ranked)} near-duplicate domain pair(s) (name+embedding) ...")
    gone: set = set()
    merged = 0
    for (a, b), (nshared, cos) in ranked:
        da, db = doms[a], doms[b]
        if da["id"] in gone or db["id"] in gone:
            continue
        user = (f"Domain A: \"{da['name']}\"\n  concerns: {', '.join(labels[da['id']]) or '(none)'}\n\n"
                f"Domain B: \"{db['name']}\"\n  concerns: {', '.join(labels[db['id']]) or '(none)'}\n\n"
                "Are these the SAME domain that was split in two, and should be merged?")
        try:
            out = provider.chat(MERGE_SYS, user, want_json=True, large=True,
                                cache_extra=f"merge:{da['id']}:{db['id']}")
        except Exception as exc:  # noqa: BLE001
            log(f"    merge check {da['id']}/{db['id']} failed ({exc})")
            continue
        if not (isinstance(out, dict) and out.get("merge") is True):
            continue
        canon, other = (da, db) if ncon.get(da["id"], 0) >= ncon.get(db["id"], 0) else (db, da)
        _fold(conn, canon["id"], other["id"], other["name"] or other["slug"] or "")
        _rederive_domain(conn, canon["id"])
        ncon[canon["id"]] = ncon.get(canon["id"], 0) + ncon.get(other["id"], 0)
        gone.add(other["id"])
        merged += 1
        log(f"    merged '{other['name']}' -> '{canon['name']}' ({out.get('reason', '')[:60]})")
    conn.commit()
    if merged:
        log(f"  merged {merged} near-duplicate domain(s)")
    return merged


def merge_duplicates(conn, log=print) -> int:
    """Merge domain fragments the LLM named identically (same piece, split by tier).

    When independent clusters get the same name, they are the same domain — fold their
    files/commits into one canonical domain (keeping any confirmed/locked one), record
    the others as aliases. This is the LLM acting as the semantic merge signal.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in conn.execute("SELECT id, slug, name, locked FROM domains WHERE status IN ('named','confirmed')"):
        key = " ".join((r["name"] or r["slug"] or "").lower().split())
        if key:
            groups[key].append(dict(r))

    def nfiles(i):
        return conn.execute("SELECT COUNT(*) FROM domain_files WHERE domain_id=?", (i,)).fetchone()[0]

    merged = 0
    for doms in groups.values():
        if len(doms) < 2:
            continue
        canon = max(doms, key=lambda d: (d["locked"] or 0, nfiles(d["id"])))
        for o in doms:
            if o["id"] == canon["id"] or o["locked"]:
                continue
            _fold(conn, canon["id"], o["id"], o["name"] or o["slug"] or "")
            merged += 1
        _rederive_domain(conn, canon["id"])
    conn.commit()
    if merged:
        log(f"  merged {merged} duplicate-named domain fragments")
    return merged
