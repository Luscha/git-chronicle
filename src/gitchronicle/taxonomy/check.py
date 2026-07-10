"""Health metrics that measure SUBSTANCE, not form.

The v2 lesson: 0-vague-names / 0-near-dups / 0-unassigned all passed on a semantically
broken taxonomy. The metric that would have failed loudly is STEM COHERENCE: concerns
whose files share a strong, specific path stem overwhelmingly belong to one feature —
when the 'avatar builder' stem's concerns are spread over 12 features, the taxonomy is
wrong no matter how clean its names look. This module computes that plus the supporting
health numbers; `gitchronicle check` prints them and the inspect GUI charts them.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict

import numpy as np


def _claimed_stems(conn) -> set:
    """Stems the taxonomy itself claims: feature stems + glossary evidence stems."""
    claimed = set()
    for r in conn.execute("SELECT stems FROM domains "
                          "WHERE status IN ('named','provisional','confirmed')"):
        claimed |= set(json.loads(r["stems"] or "[]"))
    for r in conn.execute("SELECT stems FROM glossary"):
        claimed |= set(json.loads(r["stems"] or "[]"))
    return claimed


def stem_coherence(conn, min_concerns: int = 8, max_ubiquity: float = 0.05) -> list[dict]:
    """Does the taxonomy keep its OWN vocabulary together? Per claimed, specific stem:
    how concentrated are its concerns in one feature? A mechanism stem no feature claims
    ('network', 'phase') legitimately spans features and is excluded here — strong stems
    NOBODY claims are the separate orphan_stems signal (a missing feature)."""
    strong = {r["stem"] for r in conn.execute(
        "SELECT stem FROM stem_census WHERE n_concerns >= ? AND ubiquity <= ? AND is_god=0",
        (min_concerns, max_ubiquity))} & _claimed_stems(conn)
    if not strong:
        return []
    from .ground import path_stems
    per_stem: dict[str, Counter] = defaultdict(Counter)
    dom_names = {r["id"]: r["name"] for r in conn.execute(
        "SELECT id, name FROM domains WHERE status IN ('named','provisional','confirmed')")}
    for r in conn.execute("SELECT files, domain_id FROM concerns WHERE domain_id IS NOT NULL"):
        stems = set()
        for f in json.loads(r["files"] or "[]"):
            stems |= path_stems(f)
        for s in stems & strong:
            per_stem[s][r["domain_id"]] += 1
    out = []
    for s, dist in per_stem.items():
        n = sum(dist.values())
        if n < min_concerns:
            continue
        top_did, top_n = dist.most_common(1)[0]
        out.append({"stem": s, "n_concerns": n, "n_features": len(dist),
                    "top_feature": dom_names.get(top_did, "?"),
                    "share": round(top_n / n, 3)})
    out.sort(key=lambda r: r["share"])
    return out


def orphan_stems(conn, min_concerns: int = 6) -> list[dict]:
    """Strong, specific stems that NO feature or glossary entity claims — the signature
    of a feature the taxonomy is missing (the avatar failure, as a number)."""
    claimed = _claimed_stems(conn)
    out = []
    for r in conn.execute(
            "SELECT stem, n_files, n_concerns, sample_paths FROM stem_census "
            "WHERE is_god=0 AND n_concerns >= ? ORDER BY n_concerns DESC", (min_concerns,)):
        if r["stem"] in claimed:
            continue
        out.append({"stem": r["stem"], "n_files": r["n_files"], "n_concerns": r["n_concerns"],
                    "samples": [p.rsplit("/", 1)[-1]
                                for p in json.loads(r["sample_paths"] or "[]")[:3]]})
    return out[:30]


def health(conn) -> dict:
    """The full health snapshot (also embedded in the inspect GUI)."""
    feats = conn.execute("SELECT id, name, status FROM domains "
                         "WHERE status IN ('named','provisional','confirmed')").fetchall()
    n_unassigned = conn.execute(
        "SELECT COUNT(*) FROM concerns WHERE domain_id IS NULL AND label IS NOT NULL AND (origin IS NULL OR origin != 'import-misc')").fetchone()[0]
    n_concerns = conn.execute("SELECT COUNT(*) FROM concerns WHERE label IS NOT NULL").fetchone()[0]
    src = Counter(r[0] for r in conn.execute(
        "SELECT assign_source FROM concerns WHERE domain_id IS NOT NULL"))
    confs = [r[0] for r in conn.execute(
        "SELECT assign_conf FROM concerns WHERE assign_conf IS NOT NULL")]
    coh = stem_coherence(conn)
    incoherent = [c for c in coh if c["share"] < 0.6]
    # near-dup features by definition-embedding cosine
    emb = {int(r["target_id"]): np.frombuffer(r["vector"], dtype=np.float32)
           for r in conn.execute("SELECT target_id, vector FROM embeddings WHERE target_type='taxdef'")}
    ids = [f["id"] for f in feats if f["id"] in emb]
    near_dups = []
    if len(ids) > 1:
        M = np.vstack([emb[i] / (np.linalg.norm(emb[i]) + 1e-9) for i in ids])
        S = M @ M.T
        np.fill_diagonal(S, 0)
        names = {f["id"]: f["name"] for f in feats}
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                if S[i, j] >= 0.90:
                    near_dups.append({"a": names[ids[i]], "b": names[ids[j]],
                                      "cos": round(float(S[i, j]), 3)})
    return {
        "features": len(feats),
        "by_status": dict(Counter(f["status"] for f in feats)),
        "concerns": n_concerns,
        "unassigned": n_unassigned,
        "sources": dict(src),
        "conf_median": round(float(np.median(confs)), 3) if confs else None,
        "conf_p10": round(float(np.percentile(confs, 10)), 3) if confs else None,
        "stem_coherence": coh[:40],
        "incoherent_stems": len(incoherent),
        "orphan_stems": orphan_stems(conn),
        "near_dups": sorted(near_dups, key=lambda d: -d["cos"])[:20],
    }


def print_health(conn, log=print) -> dict:
    h = health(conn)
    log(f"features: {h['features']} {h['by_status']}; concerns {h['concerns']}, "
        f"unassigned {h['unassigned']}")
    log(f"sources: {h['sources']}; conf median {h['conf_median']} p10 {h['conf_p10']}")
    log(f"near-dup feature pairs (>=0.90): {len(h['near_dups'])}")
    for d in h["near_dups"][:6]:
        log(f"  {d['cos']:.2f}  {d['a']}  <->  {d['b']}")
    log(f"stem coherence (claimed vocabulary): {h['incoherent_stems']} stems below 0.6")
    for c in h["stem_coherence"][:10]:
        flag = " ⚠" if c["share"] < 0.6 else ""
        log(f"  {c['share']:.2f}  {c['stem']:26} {c['n_concerns']:4} concerns in "
            f"{c['n_features']:2} features (top: {c['top_feature']}){flag}")
    log(f"orphan stems (strong vocabulary NO feature claims — missing features?): "
        f"{len(h['orphan_stems'])}")
    for o in h["orphan_stems"][:8]:
        log(f"  {o['n_concerns']:4}c {o['stem']:26} e.g. {', '.join(o['samples'])}")
    return h
