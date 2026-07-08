"""Link domains by dependency — language-agnostic, LLM-judged.

Relations are AUXILIARY (the KB — domains, chronicles, queries — is the product), so
this stays deliberately cheap and capped.

No code parsing (which would only work for known languages). Candidates come from two
git-/embedding-native, language-agnostic signals:
  1. CO-CHANGE — domains edited together (they interact);
  2. SEMANTIC SIMILARITY — domains whose summaries embed close (helps disciplined
     repos where domains rarely co-change).
For each candidate the BIG model reads both domains' summaries + representative diffs
and judges whether one DEPENDS ON the other, is merely RELATED, or unrelated.
"""

from __future__ import annotations

from collections import Counter, defaultdict

import numpy as np

from ..extract.git_ingest import run_git

SYSTEM = (
    "You judge the relationship between two code DOMAINS (coherent pieces of a system). "
    "You get each domain's name, summary, key files and representative code diffs. Decide: "
    "does one DEPEND ON the other (its code needs/uses the other's), are they merely RELATED "
    "(touch adjacent areas but neither needs the other), or is there NO real relationship? "
    "Judge from the CODE, not from the fact they may have been edited together. Respond with "
    "ONE JSON object."
)
SCHEMA = ('Return JSON: {"relation":"depends_on"|"related"|"none", '
          '"direction":"a_to_b"|"b_to_a"|"none", '
          '"why":"one short sentence", "confidence":0.0-1.0}')


def cochange_pairs(conn) -> Counter:
    """Unordered domain pairs that co-change, weighted by #commits touching both."""
    commit_domains: dict[str, list[int]] = defaultdict(list)
    for r in conn.execute("SELECT commit_hash, domain_id FROM commit_domains"):
        commit_domains[r["commit_hash"]].append(r["domain_id"])
    pair: Counter = Counter()
    for dids in commit_domains.values():
        uniq = sorted(set(dids))
        for i in range(len(uniq)):
            for j in range(i + 1, len(uniq)):
                pair[(uniq[i], uniq[j])] += 1
    return pair


def coupling_degree(conn) -> dict[int, int]:
    """How many other domains each domain co-changes with (agnostic centrality hint)."""
    deg: dict[int, set] = defaultdict(set)
    for (a, b) in cochange_pairs(conn):
        deg[a].add(b)
        deg[b].add(a)
    return {d: len(s) for d, s in deg.items()}


def _embedding_candidates(conn, model, top_k=3):
    """Each domain's top-k nearest domains by summary embedding — (a, b, cosine)."""
    ids, mats = [], []
    for r in conn.execute(
        "SELECT target_id, vector FROM embeddings WHERE target_type='domain' AND model=?", (model,)):
        ids.append(int(r["target_id"]))
        mats.append(np.frombuffer(r["vector"], dtype=np.float32))
    if len(ids) < 2:
        return []
    M = np.vstack(mats)
    Mn = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
    S = Mn @ Mn.T
    out = []
    for i in range(len(ids)):
        for j in np.argsort(-S[i]):
            j = int(j)
            if j == i:
                continue
            out.append((ids[i], ids[j], float(S[i, j])))
            if len([1 for x in out if x[0] == ids[i]]) >= top_k:
                break
    return out


def _snippet(conn, repo, domain_id, max_diffs=2, max_lines=70, max_files=25):
    files = [r["path"] for r in conn.execute(
        "SELECT path FROM domain_files WHERE domain_id=? ORDER BY weight DESC LIMIT ?",
        (domain_id, max_files))]
    focused = conn.execute(
        "SELECT c.hash, c.subject, COUNT(*) hits, c.files_changed FROM domain_files df "
        "JOIN commit_files cf ON cf.path=df.path JOIN commits c ON c.hash=cf.commit_hash "
        "WHERE df.domain_id=? AND c.is_merge=0 GROUP BY c.hash "
        "ORDER BY (CAST(COUNT(*) AS REAL)/NULLIF(c.files_changed,0)) DESC, hits DESC LIMIT ?",
        (domain_id, max_diffs)).fetchall()
    diffs = []
    for r in focused:
        raw = run_git(repo, ["show", "--no-color", "--format=", "--unified=1",
                             r["hash"], "--", *files[:40]], check=False)
        lines = [ln for ln in raw.splitlines() if ln.strip()][:max_lines]
        if lines:
            diffs.append(f"# {r['subject'][:60]}\n" + "\n".join(lines))
    return files, diffs


def link(conn, provider, cfg: dict, repo: str, log=print) -> dict:
    lk = cfg.get("link", {})
    max_pairs = int(lk.get("max_pairs", 60))
    min_cochange = int(lk.get("min_cochange", 2))
    emb_top_k = int(lk.get("emb_top_k", 3))
    emb_min = float(lk.get("emb_min", 0.6))

    doms = {r["id"]: r for r in conn.execute("SELECT id, name, summary FROM domains")}
    co = cochange_pairs(conn)

    cand, seen = [], set()   # (a, b, cochange_count)
    for (a, b), c in sorted(co.items(), key=lambda kv: -kv[1]):
        if c >= min_cochange and a in doms and b in doms:
            cand.append((a, b, c))
            seen.add(frozenset((a, b)))
    n_cochange = len(cand)
    # Fill remaining budget with semantically-similar pairs (auxiliary, still capped).
    if provider is not None:
        for a, b, sim in sorted(_embedding_candidates(conn, provider.embed_cfg["model"], emb_top_k),
                                key=lambda t: -t[2]):
            if len(cand) >= max_pairs:
                break
            key = frozenset((a, b))
            if key in seen or a not in doms or b not in doms or sim < emb_min:
                continue
            cand.append((a, b, co.get((min(a, b), max(a, b)), 0)))
            seen.add(key)
    cand = cand[:max_pairs]

    conn.execute("DELETE FROM domain_edges WHERE status != 'confirmed' AND locked = 0")
    if not cand:
        log("  no candidate pairs to judge")
        conn.commit()
        return {"edges": 0}

    log(f"  judging {len(cand)} candidate pairs ({n_cochange} co-change, "
        f"{len(cand) - n_cochange} semantic) with the big model ...")
    snip: dict[int, tuple] = {}

    def get_snip(did):
        if did not in snip:
            snip[did] = _snippet(conn, repo, did)
        return snip[did]

    kept = 0
    for idx, (a, b, c) in enumerate(cand):
        fa, da = get_snip(a)
        fb, db = get_snip(b)
        A, B = doms[a], doms[b]
        hint = (f"(Edited together in {c} commits — a weak hint.)" if c
                else "(Semantically similar but NOT edited together.)")
        user = (
            f"Domain A: {A['name']} — {A['summary'] or ''}\n"
            f"A key files: {', '.join(fa[:12])}\n"
            f"A diffs:\n" + ("\n".join(da) or "(none)") + "\n\n"
            f"Domain B: {B['name']} — {B['summary'] or ''}\n"
            f"B key files: {', '.join(fb[:12])}\n"
            f"B diffs:\n" + ("\n".join(db) or "(none)") + "\n\n"
            f"{hint}\n\n" + SCHEMA
        )
        try:
            r = provider.chat(SYSTEM, user, want_json=True, large=True, cache_extra=f"link:{a}:{b}")
        except Exception as exc:  # noqa: BLE001
            log(f"    pair {a}-{b} failed ({exc})")
            continue
        r = r if isinstance(r, dict) else {}
        rel = (r.get("relation") or "none").lower()
        if rel == "none":
            continue
        why = (r.get("why") or "").strip()[:200] or None
        try:
            conf = min(1.0, max(0.0, float(r.get("confidence", 0.6))))
        except (TypeError, ValueError):
            conf = 0.6
        if rel == "depends_on" and r.get("direction") in ("a_to_b", "b_to_a"):
            s, d = (a, b) if r["direction"] == "a_to_b" else (b, a)
            etype = "depends_on"
        else:
            s, d, etype = a, b, "related"
        conn.execute(
            "INSERT INTO domain_edges (src_domain, dst_domain, type, weight, why, confidence, status) "
            "VALUES (?,?,?,?,?,?, 'candidate')",
            (s, d, etype, float(max(c, 1)), why, conf))
        kept += 1
        if (idx + 1) % 10 == 0 or idx + 1 == len(cand):
            log(f"    {idx + 1}/{len(cand)} judged")

    # fan_in = directed dependency in-degree (how many domains depend on each).
    indeg = Counter(r["dst_domain"] for r in conn.execute(
        "SELECT dst_domain FROM domain_edges WHERE type='depends_on'"))
    conn.execute("UPDATE domains SET fan_in=0")
    for did, n in indeg.items():
        conn.execute("UPDATE domains SET fan_in=? WHERE id=?", (n, did))
    # 'core' = genuinely depended-upon (honest signal), not the LLM guessing 'foundational'.
    core_indeg = int(lk.get("core_indeg", 2))
    conn.execute("UPDATE domains SET classification='core' "
                 "WHERE fan_in >= ? AND status != 'confirmed' AND locked = 0", (core_indeg,))
    conn.commit()
    n_core = conn.execute("SELECT COUNT(*) FROM domains WHERE classification='core'").fetchone()[0]
    log(f"  {kept} relations from {len(cand)} candidate pairs (LLM-judged); {n_core} domains are core")
    return {"edges": kept}
