"""Build the retrieval indexes over the knowledge base.

Two lightweight indexes, both inside the single SQLite file (no servers):
  - semantic: embed each domain (name + summary + rationale + tags) with bge-m3,
    stored as float32 blobs for numpy kNN;
  - full-text: SQLite FTS5 over domains and commit messages, with a graceful LIKE
    fallback if the SQLite build lacks FTS5.
"""

from __future__ import annotations

import json

import numpy as np


def fts_available(conn) -> bool:
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(x)")
        conn.execute("DROP TABLE IF EXISTS _fts_probe")
        return True
    except Exception:  # noqa: BLE001 - FTS5 not compiled in
        return False


def _domain_text(d) -> str:
    tags = " ".join(json.loads(d["tags"])) if d["tags"] else ""
    return "\n".join(filter(None, [d["name"], d["summary"], tags]))


def build_index(conn, provider, log=print) -> dict:
    model = provider.embed_cfg["model"]
    doms = conn.execute(
        "SELECT id, name, summary, tags FROM domains WHERE status != 'rejected'"
    ).fetchall()

    # --- semantic index: embed domains ---
    ids = [d["id"] for d in doms]
    texts = [_domain_text(d) for d in doms]
    conn.execute("DELETE FROM embeddings WHERE target_type='domain'")
    if texts:
        vecs = provider.embed(texts)
        dim = int(vecs.shape[1])
        for did, v in zip(ids, vecs):
            conn.execute(
                "INSERT OR REPLACE INTO embeddings (target_type, target_id, model, dim, vector) "
                "VALUES ('domain', ?, ?, ?, ?)",
                (str(did), model, dim, np.asarray(v, dtype=np.float32).tobytes()))
    conn.commit()

    # --- full-text index ---
    has_fts = fts_available(conn)
    if has_fts:
        conn.executescript(
            "DROP TABLE IF EXISTS fts_domains;"
            "DROP TABLE IF EXISTS fts_commits;"
            "CREATE VIRTUAL TABLE fts_domains USING fts5(domain_id UNINDEXED, name, text);"
            "CREATE VIRTUAL TABLE fts_commits USING fts5(commit_hash UNINDEXED, subject, body);")
        for d in doms:
            conn.execute("INSERT INTO fts_domains (domain_id, name, text) VALUES (?,?,?)",
                         (str(d["id"]), d["name"] or "", _domain_text(d)))
        conn.executemany(
            "INSERT INTO fts_commits (commit_hash, subject, body) VALUES (?,?,?)",
            [(r["hash"], r["subject"] or "", r["body"] or "")
             for r in conn.execute("SELECT hash, subject, body FROM commits")])
        conn.commit()

    log(f"  indexed {len(ids)} domain embeddings; "
        f"full-text = {'FTS5' if has_fts else 'LIKE fallback'}")
    return {"domains_embedded": len(ids), "fts": has_fts}
