"""Shared concern-facet helpers: facet text, embeddings, stems, slugs.
(Extracted from the retired induction stage — these serve register + classification.)"""

from __future__ import annotations

import json
import re
from collections import Counter

import numpy as np

def facet_text(label: str, summary: str | None, files: list[str]) -> str:
    """The embedding/classification facet of one concern: what it does + where it lives."""
    paths = ", ".join(files[:3])
    parts = [label or ""]
    if summary and summary.strip() and summary.strip().lower() != (label or "").strip().lower():
        parts.append(summary.strip())
    txt = ". ".join(p for p in parts if p)
    return f"{txt} (files: {paths})" if paths else txt


def load_facets(conn) -> tuple[list[int], dict[int, str], dict[int, dict]]:
    """All labelled concerns → (ids, facet text, raw row info). Vendored-drop/remainder
    concerns (origin='import-misc') exist for completeness but are never clustered or
    classified — their labels are containers, not capabilities."""
    ids, texts, info = [], {}, {}
    for r in conn.execute(
            "SELECT id, label, summary, files, commit_hash FROM concerns "
            "WHERE label IS NOT NULL AND label != '' "
            "AND (origin IS NULL OR origin != 'import-misc') ORDER BY id"):
        files = json.loads(r["files"] or "[]")
        ids.append(r["id"])
        texts[r["id"]] = facet_text(r["label"], r["summary"], files)
        info[r["id"]] = {"label": r["label"], "summary": r["summary"] or "", "files": files,
                         "commit": r["commit_hash"]}
    return ids, texts, info


def embed_facets(conn, provider, ids, texts, log=print) -> dict[int, np.ndarray]:
    """Embed concern facets (cached per concern in the embeddings table, type 'facet')."""
    model = provider.embed_cfg["model"]
    have = {int(r["target_id"]) for r in conn.execute(
        "SELECT target_id FROM embeddings WHERE target_type='facet' AND model=?", (model,))}
    todo = [cid for cid in ids if cid not in have]
    if todo:
        log(f"  embedding {len(todo)} concern facets ...")
        # chunked write-as-you-go: embedding thousands of facets on a local backend can die
        # mid-way (measured); losing at most one chunk makes the re-run a cheap resume
        for i in range(0, len(todo), 256):
            part = todo[i:i + 256]
            vecs = provider.embed([texts[cid] for cid in part])
            dim = int(vecs.shape[1])
            for cid, v in zip(part, vecs):
                conn.execute(
                    "INSERT OR REPLACE INTO embeddings (target_type,target_id,model,dim,vector) "
                    "VALUES ('facet',?,?,?,?)",
                    (str(cid), model, dim, np.asarray(v, dtype=np.float32).tobytes()))
            conn.commit()
            if i and i % 2048 == 0:
                log(f"    {i}/{len(todo)} facets embedded")
    out = {}
    for r in conn.execute(
            "SELECT target_id, vector FROM embeddings WHERE target_type='facet' AND model=?",
            (model,)):
        cid = int(r["target_id"])
        if cid in texts:
            v = np.frombuffer(r["vector"], dtype=np.float32)
            out[cid] = v / (np.linalg.norm(v) + 1e-9)
    return out


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "feature"


def _ctx_line(info: dict) -> str:
    fs = ", ".join(f.rsplit("/", 1)[-1] for f in info["files"][:5])
    s = info["summary"][:110]
    return f"{info['label']}" + (f" — {s}" if s else "") + (f" |files: {fs}" if fs else "")


def cluster_stems(member_infos: list[dict], census: set[str] | None = None,
                  top: int = 8) -> list[str]:
    """Dominant path stems of a group of concerns — the aggregate evidence of WHAT the
    group is about. A stem counts once per concern; kept if it covers >=30% of members."""
    from .ground import path_stems
    cnt = Counter()
    for info in member_infos:
        seen = set()
        for f in info["files"]:
            seen |= path_stems(f)
        for s in seen:
            cnt[s] += 1
    n = max(1, len(member_infos))
    keep = [s for s, c in cnt.most_common(40) if c >= max(2, 0.3 * n)]
    if census:
        keep.sort(key=lambda s: (s not in census,))   # census-known stems first
    return keep[:top]
