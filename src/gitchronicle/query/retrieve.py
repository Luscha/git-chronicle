"""Retrieval over the knowledge base: semantic kNN + full-text, with a resolver."""

from __future__ import annotations

import numpy as np


def _domain_vectors(conn, model):
    ids, mats = [], []
    for r in conn.execute(
        "SELECT target_id, vector FROM embeddings WHERE target_type='domain' AND model=?", (model,)):
        ids.append(int(r["target_id"]))
        mats.append(np.frombuffer(r["vector"], dtype=np.float32))
    if not mats:
        return [], None
    return ids, np.vstack(mats)


def semantic(conn, provider, query: str, k: int = 8) -> list[tuple[int, float]]:
    """Domains most similar to the query by embedding cosine — [(domain_id, score)]."""
    ids, M = _domain_vectors(conn, provider.embed_cfg["model"])
    if not ids:
        return []
    q = provider.embed([query])[0]
    Mn = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
    qn = q / (np.linalg.norm(q) + 1e-9)
    sims = Mn @ qn
    order = np.argsort(-sims)[:k]
    return [(ids[i], float(sims[i])) for i in order]


def _fts_query(query: str) -> str:
    toks = [t for t in "".join(c if c.isalnum() else " " for c in query).split() if len(t) > 2]
    return " OR ".join(toks) if toks else query


def keyword_domains(conn, query: str, k: int = 10) -> list[int]:
    try:
        rows = conn.execute("SELECT domain_id FROM fts_domains WHERE fts_domains MATCH ? LIMIT ?",
                            (_fts_query(query), k)).fetchall()
        return [int(r["domain_id"]) for r in rows]
    except Exception:  # noqa: BLE001 - no FTS5; LIKE fallback
        like = f"%{query}%"
        return [r["id"] for r in conn.execute(
            "SELECT id FROM domains WHERE (name LIKE ? OR summary LIKE ?) AND status!='rejected' "
            "LIMIT ?", (like, like, k))]


def keyword_commits(conn, query: str, k: int = 10) -> list[str]:
    try:
        rows = conn.execute("SELECT commit_hash FROM fts_commits WHERE fts_commits MATCH ? LIMIT ?",
                            (_fts_query(query), k)).fetchall()
        return [r["commit_hash"] for r in rows]
    except Exception:  # noqa: BLE001
        like = f"%{query}%"
        return [r["hash"] for r in conn.execute(
            "SELECT hash FROM commits WHERE subject LIKE ? OR body LIKE ? LIMIT ?", (like, like, k))]


def resolve_domain(conn, provider, name: str) -> int | None:
    """Best single domain for a name/phrase: exact-ish match, else semantic nearest."""
    row = conn.execute(
        "SELECT id FROM domains WHERE status!='rejected' AND (slug=? OR name LIKE ?) "
        "ORDER BY n_commits DESC LIMIT 1", (name, f"%{name}%")).fetchone()
    if row:
        return row["id"]
    hits = semantic(conn, provider, name, k=1)
    return hits[0][0] if hits else None
